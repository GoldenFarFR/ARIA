"""Tracks pump.fun bonding-curve progress by BATCHED POLLING, not streaming.

Why polling wins here, measured live on 2026.08.21 rather than assumed:

  * ``programSubscribe`` over the whole pump.fun program carries 4.9 GB/day.
    Helius bills streamed bytes at 20 credits/MB, so that is ~98 000
    credits/day against a 1M/month plan -- roughly 3M/month, three times the
    entire budget, for the curve data alone.
  * ``getMultipleAccounts`` costs **1 credit per CALL**, whatever it carries,
    up to 100 accounts. The same 500 mints therefore cost 5 credits a pass.

So the cheap shape is not "stream less", it is "stop streaming". This module
applies the funnel doctrine on top: the mass of tokens that never leave the
low curve is polled rarely, and only the few approaching the entry window are
polled often.

Banded cadence and its measured budget (populations from the same 300 s live
sample: ~250 mints below 30%, ~60 between 30 and 50%, ~50 between 50 and 70%):

    band          cadence   batches   credits/day
    below 30%      60 s        3         4 320
    30 to 50%      20 s        1         4 320
    50 to 70%      10 s        1         8 640
                                        -------
                                         17 280   (~518k/month)

Above 70% the pocket takes over with its own targeted trade subscription --
this module deliberately stops there rather than duplicating that job.

Two economies are baked in and both matter:

  * **Mint decimals are cached forever.** ``resolve_bonding_curves`` spends a
    SECOND getMultipleAccounts call resolving them on every resolution. They
    are a property of the mint and never change, so caching them halves the
    per-pass cost of a repeated poll.
  * **Batches are capped at 100**, the Solana per-call limit. The shared
    helper does not chunk on its own, so a caller handing it 300 keys would
    get an RPC error rather than three calls.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

from aria_core.services.pumpswap_ws import (
    RPC_HTTP_DEFAULT,
    require_solana_rpc_http,
    _rpc_get_multiple_accounts,
)
from aria_core.services.pumpfun_bonding_ws import (
    OFF_COMPLETE,
    OFF_REAL_TOKEN_RESERVES,
    INITIAL_CURVE_TOKENS,
)

logger = logging.getLogger(__name__)

# Dedicated endpoint for the polling workload, separate from the dome's main
# Solana RPC on purpose. Batched reads and real-time streaming are billed on
# completely different models -- Helius charges 1 credit per CALL but 20 per MB
# streamed, so the cheap provider for one is not the cheap provider for the
# other. Splitting them lets each workload sit where it costs least instead of
# forcing a single provider to be good at both.
#
# Falls back to the main endpoint when unset, so nothing breaks if it is not
# configured: the tracker simply keeps polling wherever it polled before.
POLLING_RPC_HTTP_ENV = "ARIA_SOLANA_RPC_HTTP_POLLING"


def default_polling_rpc_url() -> str:
    """Read at instantiation, not at import, so a test (or a redeploy that
    changes the env) sees the current value rather than a frozen one."""
    return (os.environ.get(POLLING_RPC_HTTP_ENV, "") or "").strip() or RPC_HTTP_DEFAULT


# Solana's hard per-call limit for getMultipleAccounts. The shared helper does
# NOT chunk, so this module must.
MAX_ACCOUNTS_PER_CALL = 100

# Per-provider rate limits. Each number is the PLAN's real cap, and each
# throttle targets ~90% of it -- the project norm is to use most of the real
# sustained rate and never to guess it.
#
#   Chainstack Developer : 25 req/s, stated on the node dashboard, enforced
#                          with HTTP 429. Throttled to 22.
#   Helius Free          : 10 req/s, from their published plan. Throttled to 9.
#
# Two SEPARATE throttles on purpose: a shared one would pace the fast provider
# down to the slow one's limit, wasting more than half of Chainstack's capacity
# for no reason.
#
# Added 2026.08.21 after this module went to production with NO throttle at
# all. With 588 mints tracked the poll fired its batches back to back, drew
# 429s, and candidate evaluation collapsed from 34 to 18 detections/hour. The
# symptom read as "fewer trades"; the cause was a missing rate limit.
CHAINSTACK_MAX_RPS = 22.0
HELIUS_MAX_RPS = 9.0

# Kept for callers/tests that reason about the primary path.
MAX_REQUESTS_PER_SECOND = CHAINSTACK_MAX_RPS
_MIN_INTERVAL_SECONDS = 1.0 / MAX_REQUESTS_PER_SECOND


@dataclass
class _Endpoint:
    """One provider, with its own pacing state.

    `failures` is informational: it says which provider is struggling without
    ever disabling it. A provider that failed once may be fine on the next
    sweep, and permanently sidelining it on a transient error would be worse
    than retrying.
    """

    url: str
    name: str
    max_rps: float
    last_call_at: float = 0.0
    calls: int = 0
    failures: int = 0

    @property
    def min_interval(self) -> float:
        return 1.0 / self.max_rps if self.max_rps > 0 else 0.0

# Band edges as curve progress (0.0 -> 1.0) and their polling cadence.
# Deliberately NOT a single interval: the population below 30% is five times
# the one approaching entry, and polling it at entry cadence would spend the
# whole budget watching tokens that mostly die where they are.
BAND_EDGES: tuple[tuple[float, float, float], ...] = (
    # (lower bound, upper bound, seconds between polls)
    #
    # Slowed on 2026.08.21 when the live cost came in at 63 360 credits/day
    # against 90 000 remaining for 29 days. The low band carries the bulk of
    # the population (413 tracked, most of it below 30%) and is the cheapest
    # to slow: a token down there is minutes away from the entry window at
    # best, and most never get there at all.
    #
    # The 50-70% band is deliberately NOT slowed. It is the pre-arm window:
    # tokens cross it to the entry threshold in ~26s measured, so a slower
    # cadence would miss the subscription that populates buyer history, and
    # every candidate would be rejected on MIN_DISTINCT_BUYERS -- the exact
    # failure of the 18:49 switchover. Cheapness stops where correctness does.
    # Back to 60s on 2026.08.21 once the polling workload moved to a provider
    # that bills 1 unit per call with 3M free per month. It had been slowed to
    # 180s purely to survive a nearly exhausted Helius quota, at the cost of
    # seeing the low band five times less often. The constraint is gone, so the
    # coverage comes back: nothing about 180s was ever better, it was cheaper.
    (0.00, 0.30, 60.0),
    # RESTORED to 20s on 2026.08.21 after it broke entries. At 45s a token can
    # go from 45% to 75% between two polls, skipping the pre-arm window
    # entirely -- it is then never subscribed, reads zero buyers, and is
    # rejected. Zero entries for 13 minutes. This band feeds the pre-arm
    # trigger, so it is correctness, not economy.
    (0.30, 0.50, 20.0),
    (0.50, 0.70, 10.0),
)

# Past this the pocket's own targeted trade subscription takes over.
HANDOVER_PROGRESS = 0.70

# A mint that has not moved at all for this long is dropped: pump.fun creates
# thousands a day and the vast majority die within minutes. Without this the
# tracked set grows without bound and the "cheap" poll stops being cheap.
# Back to 900s with the budget constraint lifted. pump.fun creates thousands
# of tokens a day and most die within minutes, so this still matters -- a
# tracked corpse is polled like a live token -- but 480s was cutting tokens
# that were merely slow, not dead.
STALE_AFTER_SECONDS = 900.0

# pump.fun mints are minted with 6 decimals. Kept as the fallback ONLY, never
# as a substitute for the real value: a wrong exponent silently scales the
# progress by a factor of a million.
DEFAULT_MINT_DECIMALS = 6


@dataclass
class TrackedMint:
    mint: str
    pool_address: str
    progress: float | None = None
    last_polled_at: float = 0.0
    last_change_at: float = field(default_factory=time.monotonic)
    decimals: int | None = None


def decode_curve_progress(raw: bytes, decimals: int) -> float | None:
    """Progress from 0.0 at creation to 1.0 at graduation, derived from
    ``real_token_reserves`` exactly as ``pumpfun_bonding_ws`` does -- same
    constant, same field, so the two readings stay comparable.

    Returns None rather than a guess when the account is too short or the
    reserves exceed the initial allocation (which would mean this is not the
    account we think it is)."""
    if len(raw) < OFF_COMPLETE + 1:
        return None
    if raw[OFF_COMPLETE] != 0:
        return 1.0
    left = int.from_bytes(raw[OFF_REAL_TOKEN_RESERVES:OFF_REAL_TOKEN_RESERVES + 8], "little")
    total = INITIAL_CURVE_TOKENS * (10 ** decimals)
    if total <= 0 or left > total:
        return None
    return 1.0 - left / total


def band_for(progress: float | None) -> tuple[float, float, float] | None:
    """The band a progress value falls in, or None once it is past handover
    (or unknown, which is polled at the slowest cadence by the caller)."""
    if progress is None:
        return BAND_EDGES[0]
    for band in BAND_EDGES:
        if band[0] <= progress < band[1]:
            return band
    return None


class PumpFunCurveTracker:
    """Holds the tracked set and decides, on each tick, which mints are due.

    Deliberately has no loop of its own: the caller drives it. That keeps the
    cadence auditable from the host process and makes the whole thing
    testable without a clock or a network.
    """

    def __init__(self, *, rpc_http_url: str | None = None,
                 max_tracked: int = 600, fallback_http_url: str | None = None):
        self._rpc_http_url = rpc_http_url or default_polling_rpc_url()
        # Chainstack first, Helius second. Chainstack is the better primary on
        # measured facts: 3M units/month against 1M, 25 req/s against 10, and a
        # flat 1 unit per call instead of Helius's per-megabyte streaming rate,
        # which is what burned a month of quota in hours on 2026.08.21.
        # The fallback exists because a failed batch is not neutral: its mints
        # are simply never measured that round.
        fb = fallback_http_url if fallback_http_url is not None else RPC_HTTP_DEFAULT
        self._endpoints: list[_Endpoint] = [
            _Endpoint(url=self._rpc_http_url, name="primary", max_rps=CHAINSTACK_MAX_RPS)
        ]
        if fb and fb != self._rpc_http_url:
            self._endpoints.append(
                _Endpoint(url=fb, name="fallback", max_rps=HELIUS_MAX_RPS))
        self._max_tracked = max_tracked
        self._tracked: dict[str, TrackedMint] = {}
        self._decimals_cache: dict[str, int] = {}
        self.credits_spent = 0
        self._last_call_at = 0.0
        self.throttled_waits = 0
        self.refused_adds = 0

    def add(self, mint: str, pool_address: str) -> bool:
        """Registers a mint, typically straight off PumpPortal's free creation
        feed. Returns False when the tracked set is full -- refused loudly
        rather than silently dropped."""
        if mint in self._tracked:
            return True
        if len(self._tracked) >= self._max_tracked:
            self.refused_adds += 1
            return False
        self._tracked[mint] = TrackedMint(mint=mint, pool_address=pool_address,
                                          decimals=self._decimals_cache.get(mint))
        return True

    def drop(self, mint: str) -> None:
        self._tracked.pop(mint, None)

    def tracked_count(self) -> int:
        return len(self._tracked)

    def progress_of(self, mint: str) -> float | None:
        entry = self._tracked.get(mint)
        return entry.progress if entry else None

    def due(self, *, now: float | None = None) -> list[TrackedMint]:
        """Mints whose band cadence says they are due for a poll."""
        now = time.monotonic() if now is None else now
        out = []
        for entry in self._tracked.values():
            band = band_for(entry.progress)
            if band is None:
                continue  # past handover -- the pocket owns it now
            if now - entry.last_polled_at >= band[2]:
                out.append(entry)
        return out

    def prune(self, *, now: float | None = None) -> int:
        """Drops mints that have not moved in STALE_AFTER_SECONDS. Returns how
        many went. This is what keeps the poll cheap over a full day."""
        now = time.monotonic() if now is None else now
        dead = [m for m, e in self._tracked.items()
                if now - e.last_change_at > STALE_AFTER_SECONDS]
        for m in dead:
            del self._tracked[m]
        return len(dead)

    async def poll_due(self, http_client: httpx.AsyncClient, *,
                       now: float | None = None) -> list[tuple[str, float | None, float]]:
        """Polls every due mint in batches of at most 100.

        Returns ``(mint, previous_progress, new_progress)`` for each mint whose
        progress actually moved, so the caller can act on a threshold crossing
        without re-reading the whole set.
        """
        now = time.monotonic() if now is None else now
        due = self.due(now=now)
        if not due:
            return []
        crossings: list[tuple[str, float | None, float]] = []

        for start in range(0, len(due), MAX_ACCOUNTS_PER_CALL):
            chunk = due[start:start + MAX_ACCOUNTS_PER_CALL]
            # Try each provider in order, each paced by ITS OWN limit. A shared
            # throttle would drag the fast one down to the slow one's rate.
            accounts = None
            for ep in self._endpoints:
                wait = ep.min_interval - (time.monotonic() - ep.last_call_at)
                if wait > 0:
                    self.throttled_waits += 1
                    await asyncio.sleep(wait)
                ep.last_call_at = time.monotonic()
                try:
                    accounts = await _rpc_get_multiple_accounts(
                        http_client, ep.url, [e.pool_address for e in chunk])
                    ep.calls += 1
                    self.credits_spent += 1  # 1 credit per CALL, not per account
                    break
                except Exception as exc:  # noqa: BLE001 -- fall through to the next provider
                    ep.failures += 1
                    logger.info("pumpfun_curve_tracker: batch failed on %s (%s)", ep.name, exc)
            if accounts is None:
                continue  # every provider refused this batch; its mints wait for the next sweep
            for entry, acc in zip(chunk, accounts):
                entry.last_polled_at = now
                if not acc or not acc.get("data"):
                    continue
                try:
                    raw = base64.b64decode(acc["data"][0])
                except Exception:  # noqa: BLE001
                    continue
                decimals = entry.decimals or self._decimals_cache.get(entry.mint)
                if decimals is None:
                    # Never silently assume: a wrong exponent scales progress
                    # by a million. The default is used, and recorded as such,
                    # only because pump.fun mints are uniformly 6 decimals.
                    decimals = DEFAULT_MINT_DECIMALS
                    self._decimals_cache[entry.mint] = decimals
                    entry.decimals = decimals
                new = decode_curve_progress(raw, decimals)
                if new is None:
                    continue
                previous = entry.progress
                if previous is None or abs(new - previous) > 1e-9:
                    entry.last_change_at = now
                    entry.progress = new
                    crossings.append((entry.mint, previous, new))
        return crossings

    def save_state(self, path: str) -> int:
        """Persists the tracked set so a restart does not start blind.

        Real incident 2026.08.21: a process restart emptied the tracker, and a
        switchover done in the minute that followed found it with nothing to
        offer -- it only knows tokens created AFTER it connects, and they need
        minutes to climb. Persisting turns a 10-minute sourcing hole into none.

        Wall-clock is stored, not the monotonic clock, because monotonic does
        not survive a reboot. Returns how many entries were written.
        """
        import json
        import time as _time

        now_mono = _time.monotonic()
        wall = _time.time()
        rows = [
            {
                "mint": e.mint,
                "pool": e.pool_address,
                "progress": e.progress,
                # Seconds of staleness, resolved back against wall clock on load.
                "idle_for": max(0.0, now_mono - e.last_change_at),
                "saved_at": wall,
                "decimals": e.decimals,
            }
            for e in self._tracked.values()
        ]
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        # Atomic swap: a half-written state file would be worse than none.
        import os as _os

        _os.replace(tmp, path)
        return len(rows)

    def load_state(self, path: str) -> int:
        """Restores a persisted set, dropping anything already stale.

        A mint that was idle before the save PLUS the time the process was down
        is very likely dead -- pump.fun creates thousands a day and most die in
        minutes. Reloading those would spend credits polling corpses, so the
        same STALE_AFTER_SECONDS rule that prunes at runtime applies here.
        Returns how many entries were actually restored.
        """
        import json
        import time as _time

        try:
            with open(path, encoding="utf-8") as fh:
                rows = json.load(fh)
        except FileNotFoundError:
            return 0
        except Exception:  # noqa: BLE001 -- a corrupt state file is not fatal
            logger.info("pumpfun_curve_tracker: unreadable state at %s, starting empty", path)
            return 0

        now_wall = _time.time()
        now_mono = _time.monotonic()
        restored = 0
        for row in rows if isinstance(rows, list) else []:
            mint, pool = row.get("mint"), row.get("pool")
            if not mint or not pool or mint in self._tracked:
                continue
            if len(self._tracked) >= self._max_tracked:
                self.refused_adds += 1
                continue
            downtime = max(0.0, now_wall - float(row.get("saved_at") or now_wall))
            idle = float(row.get("idle_for") or 0.0) + downtime
            if idle > STALE_AFTER_SECONDS:
                continue
            entry = TrackedMint(mint=mint, pool_address=pool)
            entry.progress = row.get("progress")
            entry.decimals = row.get("decimals")
            # Rebased onto this process's monotonic clock, preserving how long
            # the mint has actually been idle.
            entry.last_change_at = now_mono - idle
            self._tracked[mint] = entry
            if entry.decimals:
                self._decimals_cache[mint] = entry.decimals
            restored += 1
        return restored

    @staticmethod
    def crossed(previous: float | None, new: float, threshold: float) -> bool:
        """True only on the pass that actually crosses ``threshold`` upward.
        A mint first seen already above it does NOT count as a crossing: we
        never watched it climb, so we have no history to act on."""
        return previous is not None and previous < threshold <= new
