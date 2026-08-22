"""Real-time pump.fun trade stream with the BUYER'S ADDRESS (20/08).

**What this unlocks.** The bonding-curve feed (`pumpfun_bonding_ws.py`) can
count trades but never says WHO traded -- an `accountNotification` carries the
account state, not the signer. That capped the entry signal at raw velocity.
This module closes that gap: pump.fun's program emits a `TradeEvent` as a
`Program data:` log line on every buy and sell, and that event carries the
user's pubkey. Subscribing to the program's logs therefore yields
`(mint, sol_amount, is_buy, user)` per trade with NO extra RPC call and no
per-trade lookup -- verified live before this module was written.

Alternatives checked and rejected first, on evidence rather than preference:
  - PumpPortal `subscribeTokenTrade`: METERED (0.01 SOL / 10k events per its
    own docs) and returned ZERO events across 18 real fresh tokens in a live
    30s test.
  - `logsSubscribe` + `getTransaction(signature)` per trade: gives the signer
    but adds one RPC round-trip PER TRADE, exactly the latency cost the
    pockets' design exists to avoid.

**Why DISTINCT wallets and not trade count.** Trade velocity alone is
forgeable: one wallet can fire ten buys in a second and look like a crowd.
Counting distinct buyers is what actually separates real traction from a
single actor making noise -- and it is the one thing this stream can measure
that the bonding-curve counters cannot.

**Layout of the TradeEvent** (offsets AFTER the 8-byte Anchor discriminator
`bddb7fd34ee661ee`, confirmed by decoding real live events, never guessed):
`mint` pubkey@8, `sol_amount` u64@40, `token_amount` u64@48, `is_buy` bool@56,
`user` pubkey@57. Events shorter than that are skipped rather than
mis-parsed.

Read-only: subscribes, decodes, counts. Never signs, never writes.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import time
from dataclasses import dataclass, field

# 20/08 -- IMPORTED, never redefined: this stream must ride the SAME endpoint
# as every other Solana feed in the dome (ARIA_SOLANA_RPC_WS, the paid Helius
# websocket in production). Hardcoding the free public RPC here would have put
# the dome's highest-volume subscription -- ~6650 trades / 100s measured live,
# by far the busiest thing ARIA runs on Solana -- on the endpoint with the
# tightest per-IP limits, while the paid one sat unused.
from aria_core.services.pumpswap_ws import RPC_WS_DEFAULT, require_solana_rpc_ws

logger = logging.getLogger(__name__)

# Program id IMPORTED from the bonding-curve feed rather than restated --
# two copies of the same address is exactly the drift the architectural
# coherence rule forbids.
from aria_core.services.pumpfun_bonding_ws import PUMPFUN_PROGRAM_ID  # noqa: E402

# Anchor discriminator of the TradeEvent, taken from real decoded events.
TRADE_EVENT_DISCRIMINATOR = bytes.fromhex("bddb7fd34ee661ee")

OFF_MINT = 8
OFF_SOL_AMOUNT = 40
OFF_TOKEN_AMOUNT = 48
OFF_IS_BUY = 56
OFF_USER = 57
TRADE_EVENT_MIN_LEN = OFF_USER + 32

# How long a mint's buyer set is kept after its last trade. Long enough to
# cover the pockets' whole pre-entry tracking window (MAX_POOL_AGE_MINUTES=5),
# short enough that an all-day stream cannot grow unbounded in memory.
BUYER_SET_TTL_SECONDS = 600.0
_PRUNE_EVERY_SECONDS = 60.0




@dataclass
class FoundingCohort:
    """Who bought first, whether they are still in, and whether they arrived
    together in one slot."""

    mint: str
    first_slot: int | None = None
    created_at: float = 0.0
    # ordered, capped at FOUNDING_COHORT_SIZE -- the first buyers, in order
    buyers: list = field(default_factory=list)
    # subset of `buyers` observed selling afterwards
    exited: set = field(default_factory=set)
    # distinct wallets that bought in the very FIRST slot seen for this mint
    first_slot_buyers: set = field(default_factory=set)

    @property
    def bundle_size(self) -> int:
        """Distinct wallets buying atomically in the first slot. 1 is an
        ordinary launch; several means they were bundled together, which on
        Solana effectively means Jito."""
        return len(self.first_slot_buyers)

    @property
    def exit_ratio(self) -> float | None:
        """Share of the tracked founding buyers that has already sold.
        ``None`` when none are tracked -- never a fabricated 0.0, because
        "nobody sold" and "we were not watching" must stay distinguishable."""
        if not self.buyers:
            return None
        return round(len(self.exited) / len(self.buyers), 3)

    def as_dict(self) -> dict:
        return {
            "tracked": len(self.buyers),
            "exited": len(self.exited),
            "exit_ratio": self.exit_ratio,
            "bundle_size": self.bundle_size,
            "first_slot": self.first_slot,
        }


@dataclass
class TokenTradeFlow:
    """Per-mint live trade flow. `distinct_buyers` is the signal that matters
    -- see the module docstring on why it beats a raw trade count."""

    mint: str
    distinct_buyers: int = 0
    distinct_sellers: int = 0
    buy_count: int = 0
    sell_count: int = 0
    buy_sol_volume: float = 0.0
    first_trade_at: float | None = None
    last_trade_at: float | None = None
    # 20/08 -- SCAM-SHAPE metrics. Operator's framing: "si on comprend les
    # scam alors on comprend le jeu". Distinct buyers alone can still be
    # manufactured -- these expose the two cheapest fakes:
    #   top_buyer_share  : one wallet supplying most of the buy volume is
    #                      wash trading, not demand (a real crowd spreads out)
    #   sell_pressure    : more sellers than buyers means the exit has already
    #                      started, whatever the buy count says
    top_buyer_sol: float = 0.0
    # 22/08, operator's catch ("attention au addresse de contrat qui detienne
    # le token"): `top_buyer_share` says HOW concentrated the buy volume is,
    # never WHO holds it. A wallet taking 85% of the volume is a real dump
    # risk; a router or aggregator taking the same share is not a risk at all.
    # The address was already computed and thrown away by `max(values())` --
    # keeping it is what makes the two cases separable.
    top_buyer_address: str | None = None

    @property
    def top_buyer_share(self) -> float | None:
        """Share of ALL buy volume coming from the single biggest buyer.
        ``None`` when nothing was bought -- never a fabricated 0.0."""
        if self.buy_sol_volume <= 0:
            return None
        return round(self.top_buyer_sol / self.buy_sol_volume, 4)

    @property
    def sell_pressure(self) -> float | None:
        """Distinct sellers per distinct buyer. Above 1.0 means more wallets
        are leaving than arriving."""
        if not self.distinct_buyers:
            return None
        return round(self.distinct_sellers / self.distinct_buyers, 3)

    @property
    def sol_velocity(self) -> float | None:
        """SOL entering the curve per second.

        21/08 -- the missing predictor. A bonding curve advances with the SOL
        paid into it, so this IS the speed at which the token is travelling
        toward graduation, and graduation is the ONLY factor that separates
        this pocket's real winners from everything else (43% of the >=+50%
        closures graduated, against 6% of the rest -- a 7x gap where every
        other measured criterion sat under 15%).

        Until now the pocket recorded WHERE a curve was (70%, 85%...) but
        never how fast it was moving, so a token climbing 70->80% in two
        minutes and one stuck at 72% for an hour looked identical at entry.

        ``None`` rather than 0.0 when nothing is measurable yet -- a token we
        have not watched long enough is not a slow token.

        CAVEAT, deliberately not hidden: ``seconds_active`` runs from the
        first trade WE saw, not from the token's creation, so a mint first
        seen late reads faster than it truly was. Directionally sound,
        absolutely biased -- fine for ranking candidates against each other in
        the same window, wrong for any absolute threshold."""
        secs = self.seconds_active
        if not secs or self.buy_sol_volume <= 0:
            return None
        return round(self.buy_sol_volume / secs, 6)

    @property
    def seconds_active(self) -> float | None:
        if self.first_trade_at is None or self.last_trade_at is None:
            return None
        return max(0.0, self.last_trade_at - self.first_trade_at)


# 20/08 -- how far back the per-mint trade log is kept for the time-window
# derivatives (buyer acceleration, sell-pressure slope). Bounded on purpose:
# the live stream runs at ~50 trades/s across ~170 tokens, so an unbounded
# log would be the biggest memory consumer in the process within minutes.
# 120s covers the pockets' whole pre-entry window with room for a
# before/after comparison.
TRADE_LOG_WINDOW_SECONDS = 120.0

# 21/08, operator-directed -- FOUNDING COHORT. His framing: "regarde le
# createur et surtout si il y a un bundle jito associe, sa devrait se voir",
# and "les premiers ceux qui sont entres quand le token a ete cree, car cest
# de la que demarre les scam souvent si les premiers acheteurs ont deja
# vendu".
#
# Both are answerable from the stream we ALREADY consume, with no extra API
# call and no added entry latency: every TradeEvent carries the buyer's
# wallet, and the notification carries the slot its transaction landed in.
#
# A Jito bundle is several transactions forced into the SAME slot atomically.
# So a creator seeding a launch with his own wallets does not merely look
# suspicious -- he is structurally visible as N distinct buyers sharing the
# very first slot. That is a fact about how the chain works, not a heuristic.
#
# Kept SEPARATE from `_MintState` on purpose: that one is pruned after
# BUYER_SET_TTL_SECONDS=600 because it holds a full trade log, while this must
# survive from the token's creation until it reaches the pocket's 70% entry
# band -- a far longer trip. Only the first few buyers are retained, so the
# per-mint cost stays tiny.
FOUNDING_COHORT_SIZE = 10

# Hard ceiling on how many mints keep a founding cohort, evicted oldest-first.
# pump.fun launches thousands of tokens a day and this process peaked at 629MB
# on a 3.8GB VPS, so an unbounded registry would be the first thing to sink
# it. At ~10 wallets per mint this stays around 12MB.
FOUNDING_MAX_MINTS = 8000

# Beyond this a mint is dropped whatever the ceiling says: a token that has
# not reached the entry band in 6 hours never will on this pocket's cadence.
FOUNDING_TTL_SECONDS = 21_600.0


@dataclass
class _MintState:
    buyers: set[str] = field(default_factory=set)
    sellers: set[str] = field(default_factory=set)
    buy_count: int = 0
    sell_count: int = 0
    buy_sol_volume: float = 0.0
    buy_sol_by_wallet: dict = field(default_factory=dict)
    first_trade_at: float | None = None
    last_trade_at: float | None = None
    # (timestamp, user, is_buy) trimmed to TRADE_LOG_WINDOW_SECONDS.
    recent: list = field(default_factory=list)
    last_trade_price_sol_raw: float | None = None
    last_trade_at_monotonic: float | None = None


# Measured 2026.08.21 against the live Helius websocket: 120 targeted mint
# subscriptions carried 11 286 MB/day, i.e. ~94 MB per watched mint per day.
# Helius bills 20 credits/MB, so the 1M credit/month plan pays for ~50 GB/month
# (~1667 MB/day) -- 17.7 mints watched continuously would consume the ENTIRE
# monthly quota, leaving nothing for the bonding account subscriptions, the
# getMultipleAccounts reads or anything else on the same key. The default is
# therefore set so this stream claims ~two thirds of the budget and no more:
# 12 mints x 94 MB = 1128 MB/day = ~677k credits/month. Raise it only against a
# fresh measurement of what the OTHER consumers on this key actually spend.
MAX_WATCHED_MINTS = 12

# How often the sender task looks for new watch/unwatch requests. 0.2 s is far
# below the ~40 s of history buyer_acceleration needs, so it never delays a
# metric; it only costs an idle wakeup five times a second.
_WATCH_QUEUE_POLL_SECONDS = 0.2


# Public endpoint, NO API KEY, flat-rate free tier with no credit metering --
# which is the whole point. Measured live 2026.08.21: the full program-wide
# pump.fun feed runs at ~240 notifications/s, 32 GB/day. The same volume costs
# ~646 000 credits/day on Helius (20 credits/MB), seven times the entire
# remaining monthly quota. Here it costs nothing, because the plan bills a flat
# rate rather than usage.
#
# Their own terms warn the free tier is "not intended for production" and may
# see unscheduled downtime, which is exactly why it is PRIMARY and not SOLE:
# the paid Helius endpoint stays wired as fallback. Cheap where it works,
# reliable when it does not.
#
# Not a secret (public URL, no key), so it lives in code rather than .env --
# but overridable for tests and for the day the endpoint moves.
STREAM_WS_PUBLIC_DEFAULT = "wss://public.rpc.solanavibestation.com"


def stream_ws_endpoints() -> list[str]:
    """Streaming endpoints in priority order: free flat-rate first, metered
    fallback second. Duplicates and blanks removed so a misconfigured env can
    never make the same provider be tried twice."""
    import os as _os

    primary = (_os.environ.get("ARIA_SOLANA_RPC_WS_STREAM", "") or "").strip() \
        or STREAM_WS_PUBLIC_DEFAULT
    out = []
    for url in (primary, RPC_WS_DEFAULT):
        if url and url not in out:
            out.append(url)
    return out


def decode_trade_event(raw: bytes) -> tuple[str, float, bool, str, int] | None:
    """``(mint, sol_amount, is_buy, user, token_amount)`` or ``None`` when the
    payload is not a TradeEvent (or is too short to trust). Never raises on
    malformed input -- this parses attacker-influenceable bytes off a public
    chain.

    21/08 -- ``token_amount`` decoded after the operator asked why we do not
    read the raw data directly instead of a price. `OFF_TOKEN_AMOUNT` had been
    mapped in the constants from the start and never read, exactly like
    `creator` earlier the same day. It matters because SOL divided by tokens
    IS a price: this stream listens at `processed` commitment, i.e. as early
    as the chain allows, while the account subscription that currently prices
    positions waits for `confirmed` -- 400-800ms later. The earliest usable
    price was already arriving here and being discarded."""
    try:
        if len(raw) < TRADE_EVENT_MIN_LEN or raw[:8] != TRADE_EVENT_DISCRIMINATOR:
            return None
        import base58

        mint = base58.b58encode(raw[OFF_MINT:OFF_MINT + 32]).decode()
        sol_amount = struct.unpack_from("<Q", raw, OFF_SOL_AMOUNT)[0] / 1e9
        is_buy = bool(raw[OFF_IS_BUY])
        user = base58.b58encode(raw[OFF_USER:OFF_USER + 32]).decode()
        token_amount = struct.unpack_from("<Q", raw, OFF_TOKEN_AMOUNT)[0]
        return mint, sol_amount, is_buy, user, token_amount
    except Exception:  # noqa: BLE001 -- malformed on-chain data is never fatal
        return None


def extract_trade_events(logs: list[str]) -> list[tuple[str, float, bool, str, int]]:
    """Pulls every decodable TradeEvent out of one notification's log lines."""
    out = []
    for line in logs or []:
        if not isinstance(line, str) or not line.startswith("Program data: "):
            continue
        try:
            raw = base64.b64decode(line.split("Program data: ", 1)[1])
        except Exception:  # noqa: BLE001
            continue
        decoded = decode_trade_event(raw)
        if decoded is not None:
            out.append(decoded)
    return out


class PumpFunTradeStream:
    """Single program-wide log subscription, shared by every caller.

    Two subscription modes. The default listens to the whole pump.fun program
    (one subscription, every token's trades). `targeted=True` instead
    subscribes per mint, on the SAME single connection.

    The program-wide mode was long justified by the claim that per-token
    subscriptions would "multiply connections for strictly the same data".
    Both halves were measured false on 2026.08.21: 120 per-mint subscriptions
    were accepted on one connection, and the data is far from the same --
    Helius bills streamed BYTES (20 credits/MB), and program-wide streaming
    carries 74 GB/day of which 74.7% holds no decodable trade at all. The
    same measurement over 120 targeted mints carried 11.3 GB/day, a 6.6x cut
    for strictly the same signal. See MAX_WATCHED_MINTS for the budget."""

    # rpc_ws_url defaults to None, NOT to RPC_WS_DEFAULT: a non-empty default
    # pins the metered endpoint and the cascade in current_stream_url() is
    # never consulted. That exact mistake sent the full feed back to Helius on
    # 2026.08.21 at 2.4M credits/day -- the whole remaining quota in minutes.
    def __init__(self, *, rpc_ws_url: str | None = None, connect_fn=None,
                 targeted: bool = False, max_watched: int = MAX_WATCHED_MINTS):
        self._rpc_ws_url = rpc_ws_url
        self._connect_fn = connect_fn
        self._targeted = targeted
        self._max_watched = max_watched
        # mint -> server subscription id (None while the ack is in flight).
        self._watched: dict[str, int | None] = {}
        # Requests raised by callers between reconnects; drained by the sender.
        self._watch_queue: list[tuple[str, bool]] = []
        self._req_mints: dict[int, str] = {}
        self._next_req_id = 100
        self._refused = 0
        self._endpoint_index = 0
        self.endpoint_failures = 0
        self._state: dict[str, _MintState] = {}
        # Separate from `_state` and far longer-lived -- see FOUNDING_COHORT_SIZE.
        self._founding: dict[str, FoundingCohort] = {}
        self._task: asyncio.Task | None = None
        self._last_prune = time.time()
        self.connected = False

    def get_flow(self, mint: str) -> TokenTradeFlow:
        """Never returns None -- an unseen mint is an empty flow (zero buyers),
        which is itself the honest answer: nobody has bought it."""
        st = self._state.get(mint)
        if st is None:
            return TokenTradeFlow(mint=mint)
        return TokenTradeFlow(
            mint=mint,
            distinct_buyers=len(st.buyers), distinct_sellers=len(st.sellers),
            buy_count=st.buy_count, sell_count=st.sell_count,
            buy_sol_volume=round(st.buy_sol_volume, 6),
            top_buyer_sol=round(max(st.buy_sol_by_wallet.values()), 6) if st.buy_sol_by_wallet else 0.0,
            top_buyer_address=(
                max(st.buy_sol_by_wallet, key=st.buy_sol_by_wallet.get)
                if st.buy_sol_by_wallet else None
            ),
            first_trade_at=st.first_trade_at, last_trade_at=st.last_trade_at,
        )

    def round_trip_wallets(self, mint: str) -> int:
        """Wallets that BOTH bought and sold this mint in the observed window.

        The clean signature of wash trading: a wallet trading against itself
        inflates volume and transaction count without bringing any real demand.
        Added 2026.08.21 after a diagnostic could NOT settle whether wash
        trading explained the collapses -- the transactions/distinct-buyers
        ratio showed no gradient and capped at 5.3, where genuine wash trading
        would show 20-100. The stream already tracked buyers and sellers
        separately; only their intersection was missing.

        Counts wallets, not volume: one wallet cycling ten times is one
        round-tripper, and that is the honest unit for "how many participants
        are faking it".
        """
        st = self._state.get(mint)
        if st is None:
            return 0
        return len(st.buyers & st.sellers)

    def round_trip_share(self, mint: str) -> float | None:
        """Round-trippers as a share of distinct buyers, or None when there is
        nothing to divide -- never a fabricated zero, which would read as
        "clean" instead of "unmeasured"."""
        st = self._state.get(mint)
        if st is None or not st.buyers:
            return None
        return len(st.buyers & st.sellers) / len(st.buyers)

    def window_stats(self, mint: str, *, seconds: float, now: float | None = None) -> tuple[int, int]:
        """``(distinct_buyers, distinct_sellers)`` over the last ``seconds``.
        The building block for every derivative below."""
        st = self._state.get(mint)
        if st is None:
            return (0, 0)
        cutoff = (now if now is not None else time.time()) - seconds
        buyers, sellers = set(), set()
        for ts, user, is_buy in st.recent:
            if ts < cutoff:
                continue
            (buyers if is_buy else sellers).add(user)
        return (len(buyers), len(sellers))

    def buyer_acceleration(self, mint: str, *, window: float = 20.0, now: float | None = None) -> float | None:
        """Distinct buyers in the LAST ``window`` seconds divided by those in
        the window before it. >1 means the crowd is still arriving, <1 means it
        has started to thin out.

        This is the LURE-PHASE signal: a rug's price climbs steadily while new
        buyers keep coming, and the climb ends when they stop -- visible here
        BEFORE it shows in the price. ``None`` when the previous window is
        empty (nothing to compare against) rather than a fabricated ratio."""
        now = now if now is not None else time.time()
        recent_buyers, _ = self.window_stats(mint, seconds=window, now=now)
        prior_buyers, _ = self.window_stats(mint, seconds=window * 2, now=now)
        prior_only = prior_buyers - recent_buyers
        if prior_only <= 0:
            return None
        return round(recent_buyers / prior_only, 3)

    def sell_pressure_slope(self, mint: str, *, window: float = 20.0, now: float | None = None) -> float | None:
        """Change in sellers-per-buyer between the previous window and the
        current one. POSITIVE means the exit is accelerating -- the ultra-early
        exit signal, which fires while the price is still holding.

        ``None`` when either window has no buyers to normalise against; an
        undefined ratio must never read as "calm"."""
        now = now if now is not None else time.time()
        rb, rs = self.window_stats(mint, seconds=window, now=now)
        tb, ts_ = self.window_stats(mint, seconds=window * 2, now=now)
        prior_b, prior_s = tb - rb, ts_ - rs
        if rb <= 0 or prior_b <= 0:
            return None
        return round((rs / rb) - (prior_s / prior_b), 3)

    def active_mints(self, *, min_buyers: int = 1, seen_within_seconds: float = 60.0,
                     now: float | None = None) -> list[str]:
        """Mints with real recent buy activity, most-bought first.

        This is what lets the LATE-BONDING pocket source candidates without a
        scanning loop or a second subscription: the program-wide stream already
        sees every actively-traded token, so "which tokens are alive right now"
        is a local read. Filtering on distinct BUYERS (not trades) keeps a
        single wallet from putting a dead token on the list."""
        now = now if now is not None else time.time()
        out = []
        for mint, st in self._state.items():
            if st.last_trade_at is None or now - st.last_trade_at > seen_within_seconds:
                continue
            if len(st.buyers) < min_buyers:
                continue
            out.append((len(st.buyers), mint))
        out.sort(reverse=True)
        return [m for _, m in out]

    def _record_founding(self, mint: str, is_buy: bool, user: str, slot: int | None) -> None:
        """Maintains the founding cohort. Cheap by construction: after the
        first FOUNDING_COHORT_SIZE buys a mint only ever does set lookups."""
        c = self._founding.get(mint)
        if c is None:
            if len(self._founding) >= FOUNDING_MAX_MINTS:
                # oldest-first eviction; dicts preserve insertion order
                self._founding.pop(next(iter(self._founding)), None)
            c = self._founding[mint] = FoundingCohort(mint=mint, created_at=time.time())
        if is_buy:
            if c.first_slot is None and slot is not None:
                c.first_slot = slot
            if slot is not None and slot == c.first_slot:
                c.first_slot_buyers.add(user)
            if len(c.buyers) < FOUNDING_COHORT_SIZE and user not in c.buyers:
                c.buyers.append(user)
        elif user in c.buyers:
            c.exited.add(user)

    def founding_cohort(self, mint: str) -> dict | None:
        """What we know about who launched this token, or ``None`` when the
        mint was never seen from early enough to know anything.

        ``None`` matters: the stream only knows a token from the moment this
        process connected, so a mint created before that has no cohort. Fail
        LOUD rather than reporting a clean-looking zero -- reading "0 founders
        sold" off a token we simply never watched is exactly the kind of
        false comfort that gets acted on."""
        c = self._founding.get(mint)
        return c.as_dict() if c is not None else None

    def founder_sold(self, mint: str, wallet: str) -> bool:
        """Whether one specific founding wallet -- typically the token's own
        creator -- was seen selling. Public so callers never reach into the
        registry directly."""
        c = self._founding.get(mint)
        return bool(c and wallet in c.exited)

    def _prune_founding(self, *, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        stale = [m for m, c in self._founding.items() if now - c.created_at > FOUNDING_TTL_SECONDS]
        for m in stale:
            self._founding.pop(m, None)
        return len(stale)

    def _record(self, mint: str, sol_amount: float, is_buy: bool, user: str,
                token_amount: int = 0) -> None:
        st = self._state.get(mint)
        if st is None:
            st = self._state[mint] = _MintState()
        now = time.time()
        if st.first_trade_at is None:
            st.first_trade_at = now
        st.last_trade_at = now
        st.recent.append((now, user, is_buy))
        # SOL per RAW token unit for this trade. Raw on purpose: converting to
        # a per-token price needs the mint's decimals, which this stream does
        # not carry -- fabricating a default here would be a silent unit bug,
        # so the conversion is left to the caller that knows them.
        if token_amount > 0 and sol_amount > 0:
            st.last_trade_price_sol_raw = sol_amount / token_amount
            st.last_trade_at_monotonic = now
        if len(st.recent) > 8:  # amortised trim, never on every single trade
            cutoff = now - TRADE_LOG_WINDOW_SECONDS
            if st.recent[0][0] < cutoff:
                st.recent = [t for t in st.recent if t[0] >= cutoff]
        if is_buy:
            st.buyers.add(user)
            st.buy_count += 1
            st.buy_sol_volume += sol_amount
            st.buy_sol_by_wallet[user] = st.buy_sol_by_wallet.get(user, 0.0) + sol_amount
        else:
            st.sellers.add(user)
            st.sell_count += 1

    def _prune(self, *, now: float | None = None) -> int:
        """Drops mints idle past the TTL. Without this an always-on stream
        accumulates every mint pump.fun ever emitted."""
        now = now if now is not None else time.time()
        stale = [m for m, st in self._state.items()
                 if st.last_trade_at is not None and now - st.last_trade_at > BUYER_SET_TTL_SECONDS]
        for m in stale:
            self._state.pop(m, None)
        self._last_prune = now
        return len(stale)

    def watch(self, mint: str) -> bool:
        """Registers ``mint`` for a targeted subscription, subject to the budget
        cap. Returns False when the cap is already full, so the caller learns it
        was refused instead of silently believing the mint is covered.

        A no-op returning True in program-wide mode: every mint is already
        covered there, so callers can call this unconditionally."""
        if not self._targeted or mint in self._watched:
            return True
        if len(self._watched) >= self._max_watched:
            self._refused += 1
            return False
        self._watched[mint] = None
        self._watch_queue.append((mint, True))
        return True

    def unwatch(self, mint: str) -> None:
        """Releases a budget slot. Safe to call for an unwatched mint."""
        if self._targeted and mint in self._watched:
            self._watch_queue.append((mint, False))

    def watched_mints(self) -> list[str]:
        return sorted(self._watched)

    @property
    def refused_watches(self) -> int:
        """How many watch() calls the cap turned down since start. A rising
        number means the cap is too tight for the pocket's real candidate
        rate -- surface it, never let the budget silently blind the metrics."""
        return self._refused

    def _handle_ack(self, msg: dict) -> bool:
        """Binds a subscription id to its mint. Returns True if the message was
        an ack (and therefore not a notification)."""
        rid = msg.get("id")
        if not isinstance(rid, int) or "result" not in msg:
            return False
        mint = self._req_mints.pop(rid, None)
        if mint is not None and isinstance(msg.get("result"), int) and mint in self._watched:
            self._watched[mint] = msg["result"]
        return True

    async def _drain_watch_queue(self, ws) -> None:
        """Sends subscribe/unsubscribe frames while run_forever blocks on recv.
        websockets allows a concurrent send from another task on the same
        connection, which is what keeps this to ONE connection."""
        while True:
            if not self._watch_queue:
                await asyncio.sleep(_WATCH_QUEUE_POLL_SECONDS)
                continue
            mint, add = self._watch_queue.pop(0)
            try:
                if add:
                    req = self._next_req_id
                    self._next_req_id += 1
                    self._req_mints[req] = mint
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": req, "method": "logsSubscribe",
                        "params": [{"mentions": [mint]}, {"commitment": "processed"}],
                    }))
                else:
                    sub_id = self._watched.pop(mint, None)
                    if isinstance(sub_id, int):
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": self._next_req_id,
                            "method": "logsUnsubscribe", "params": [sub_id],
                        }))
                        self._next_req_id += 1
            except Exception:  # noqa: BLE001 -- the reconnect path re-subscribes
                self._watch_queue.insert(0, (mint, add))
                return

    def handle_notification(self, msg: dict) -> int:
        """Feeds one raw websocket message in. Returns how many trades it held
        (0 for anything that is not a log notification)."""
        try:
            value = (msg.get("params") or {}).get("result", {}).get("value") or {}
        except AttributeError:
            return 0
        if value.get("err"):
            return 0  # a failed transaction is not a trade
        # The slot is what makes bundle detection possible at all: several
        # buys sharing one slot were submitted atomically.
        try:
            slot = (msg.get("params") or {}).get("result", {}).get("context", {}).get("slot")
        except AttributeError:
            slot = None
        slot = slot if isinstance(slot, int) else None
        events = extract_trade_events(value.get("logs") or [])
        for mint, sol_amount, is_buy, user, token_amount in events:
            self._record(mint, sol_amount, is_buy, user, token_amount)
            self._record_founding(mint, is_buy, user, slot)
        if time.time() - self._last_prune > _PRUNE_EVERY_SECONDS:
            self._prune()
            self._prune_founding()
        return len(events)

    def current_stream_url(self) -> str:
        """Endpoint for the next connection attempt, cycling through
        stream_ws_endpoints() as failures accumulate.

        The old comment here said "no public fallback by design -- fail rather
        than stream the dome's busiest subscription over the free endpoint".
        That was written when "free endpoint" meant the public Solana RPC with
        its punishing per-IP limits. It no longer applies: this endpoint is a
        flat-rate plan that carries the full 240 notifications/s feed measured
        on 2026.08.21, for nothing, where the metered one costs seven times the
        monthly quota per day. The reasoning inverted with the facts.
        """
        urls = stream_ws_endpoints()
        if not urls:
            return require_solana_rpc_ws()
        return urls[self._endpoint_index % len(urls)]

    def _connect(self):
        url = self._rpc_ws_url or self.current_stream_url()
        if self._connect_fn is not None:
            return self._connect_fn(url)
        import websockets

        return websockets.connect(url, ping_interval=20, ping_timeout=40)

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Reconnects with backoff forever. Same resilience shape as the other
        feeds in this dome: one connection failure never ends the stream."""
        backoff = 1.0
        while not (stop_event is not None and stop_event.is_set()):
            try:
                async with self._connect() as ws:
                    sender: asyncio.Task | None = None
                    if self._targeted:
                        # Re-arm every mint the callers still care about: a
                        # reconnect drops server-side subscriptions, and a
                        # silently unsubscribed mint would read as "no buyers"
                        # rather than "not measured".
                        self._req_mints.clear()
                        self._watch_queue = [(m, True) for m in self._watched]
                        for m in self._watched:
                            self._watched[m] = None
                        sender = asyncio.create_task(self._drain_watch_queue(ws))
                    else:
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                            "params": [{"mentions": [PUMPFUN_PROGRAM_ID]}, {"commitment": "processed"}],
                        }))
                    self.connected = True
                    backoff = 1.0
                    try:
                        while not (stop_event is not None and stop_event.is_set()):
                            raw = await ws.recv()
                            try:
                                msg = json.loads(raw)
                                if not (self._targeted and self._handle_ack(msg)):
                                    self.handle_notification(msg)
                            except Exception as exc:  # noqa: BLE001 -- one bad frame never kills the stream
                                logger.debug("pumpfun_trade_stream: bad frame (%s)", exc)
                    finally:
                        if sender is not None:
                            sender.cancel()
            except Exception as exc:  # noqa: BLE001
                self.connected = False
                self.endpoint_failures += 1
                # Rotate to the next provider. The free flat-rate tier warns of
                # unscheduled downtime, so a failure is expected occasionally --
                # it must cost a reconnect, never the feed.
                if len(stream_ws_endpoints()) > 1 and not self._rpc_ws_url:
                    self._endpoint_index += 1
                logger.info(
                    "pumpfun_trade_stream: disconnected (%s), retrying in %.0fs on %s",
                    exc, backoff, self.current_stream_url().split("//")[-1].split("/")[0],
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
