"""Smart-money wallet tracker — read-only, additive, never a trigger.

Method sourced from AGENTS.md: smart money is a **measurable behavior**, not
an identity or wallet size. We analyze a token's top holders (excluding known
LP) to spot convergence on the documented cross-checked criteria:
- consistency over time (not a one-off stroke of luck);
- early entries + controlled sizes (not a single massive deposit);
- disciplined exits (sells in tranches, not a full dump);
- multi-wallet concentration (several independent wallets converge).

False signals explicitly excluded: wash-trading (round-trips with the same
counterparty), contract wallets (team/vesting/LP), and missing data is never
replaced by a guess (cf. AGENTS.md).

This module only produces a **confirmation/context signal** — the absolute
rule "never copy-trade" applies: this is never a trigger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from aria_core.services.blockscout import (
    UNAVAILABLE,
    BlockscoutClient,
    TokenHoldersResult,
    TokenTransfer,
)

_MAX_WALLETS_DEFAULT = 8
_EARLY_ENTRY_WINDOW_SECONDS = 3 * 24 * 3600  # 3 days after pair creation
_LARGEST_BUY_SHARE_MAX = 0.7  # above this, the entry is judged "massive", not "controlled"
_WASH_TRADING_COUNTERPARTY_SHARE = 0.6
_MIN_TRANSFERS_FOR_WASH_CHECK = 3
_ZERO_ADDRESS = "0x" + "0" * 40

# Convergence bonus (22/07, explicit operator decision after a verified
# numeric example: "2 wallets with a high score" must dominate "10 wallets
# with a low score", never the reverse) -- originally weighted by each
# wallet's known global composite_percentile from the wallet-scoring project;
# since that project was removed entirely 25/08 (operator decision), every
# qualified wallet now uses the same flat _FALLBACK_QUALIFIED_SCORE below, so
# the signal's magnitude is driven purely by the NUMBER of convergent wallets
# (capped). The multi-wallet gate (>=2) remains a binary ENTRY gate
# (unchanged doctrine: a single convergent wallet never proves anything, cf.
# `test_single_convergent_wallet_not_enough_concentration`).
_CONVERGENCE_BONUS_PER_WALLET = 3.0
_CONVERGENCE_BONUS_MAX_WALLETS = 3  # bonus cap = 3 * 3 = 9 points max
# Base score for a wallet judged convergent by the existing lightweight
# judgment (`is_smart_candidate`, behavior observed on THIS specific token).
# Deliberately modest -- kept at its pre-25/08 value even though it's now the
# only value ever used (never re-tuned in isolation, no fresh calibration
# ran since it stopped being a fallback).
_FALLBACK_QUALIFIED_SCORE = 55.0
_MAX_SECURITY_SCORE_DELTA = 15  # cap on the delta applied to the composite security_score

# Price by exact tx_hash (14/07, complement to pool+OHLCV -- cf.
# _hash_based_price): stablecoins recognized BY CONTRACT ADDRESS (never by
# symbol -- a token can spoof a "USDC" symbol), to turn a ratio between two
# on-chain legs into a USD price without depending on pool/OHLCV. Base ONLY
# for this project (addresses individually verified against Blockscout on
# 14/07) -- a chain missing from the dict = empty registry = systematic
# fallback to pool+OHLCV, not a silent gap (cf. _hash_based_price).
_STABLECOIN_ADDRESSES_BY_CHAIN: dict[str, set[str]] = {
    "base": {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC (natif, Circle)
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC (bridged)
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI (bridged)
        "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2",  # USDT (bridged)
        # EURC (Circle, EUR) -- 24/07: found live, a real "floor" paper
        # position was opened on it. ARIA's OWN conviction diligence already
        # identified it as "a Circle official stablecoin... EUR reserves
        # 1:1" (thesis text) yet the momentum pipeline still bought it (R/R
        # 1.1, floor mode) -- a EUR-pegged stablecoin structurally cannot
        # have a momentum setup, same class of gap as the LST case below.
        "0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42",  # EURC -- confirmed live, 24/07
    },
}

# Non-trusted pegged/synthetic assets (08/05, scalping_v8 real trade): DISTINCT
# from `_STABLECOIN_ADDRESSES_BY_CHAIN` above on purpose -- that registry also
# feeds `is_recognized_reference_asset` (exempts regulated/institutional
# issuers from the owner-lever honeypot checks). msUSD proved the opposite:
# its synthetic-swap module went undercollateralized (stale-oracle MEV,
# ~$4.57M msUSD + 6367 msETH unbacked, 30% depeg) -- an anonymous DeFi
# protocol failure mode, not an institutional custodian safety feature.
# Adding it to the trusted registry would have EXEMPTED it from GoPlus
# owner-lever checks, the wrong direction entirely. This registry ONLY
# excludes candidates from momentum/scalping discovery (a peg that just broke
# is not a "fresh reversal" -- its price action depends on the issuing
# protocol's buyback/burn remediation, not market sentiment, a fundamentally
# different dynamic than the wick-reversal signal was validated on). Never
# read by any security-exemption path. A token missing here = no protection
# (documented degraded behavior, same policy as the registries above) --
# extend as new pegged/synthetic failures are found, never as a general
# "all synthetics" ban (a healthy peg is simply never a momentum candidate on
# its own, this registry only matters for the ones that get scanned anyway
# because their price has moved off 1.00).
_NON_TRUSTED_PEGGED_ASSET_ADDRESSES_BY_CHAIN: dict[str, frozenset[str]] = {
    "base": frozenset({
        "0x526728dbc96689597f85ae4cd716d4f7fccbae9d",  # msUSD (Metronome Synth USD) -- 30% depeg, underbacked module, 08/05
        # Sweep of momentum_scan_log (08/05, same-day follow-up to the msUSD
        # trade): every distinct symbol/contract ever scanned matching
        # %usd%/%eur%/%dai% (66798 scan rows), cross-checked against its most
        # recent scanned price. Kept below ONLY the ones whose peg is
        # CURRENTLY intact (price within a normal band of 1.00 USD or the
        # real EUR/USD rate ~1.08-1.15) -- these are exactly the shape that
        # fooled v8 on msUSD BEFORE it broke. Two matches (USDP, USDi) were
        # excluded from this list: their last scanned price was ~1e-7/1e-15,
        # already dead/scammed and already caught by the liquidity/honeypot
        # gates -- adding them here would add nothing (a token that never
        # trades near 1.00 was never going to be misread as a fresh peg
        # reversal in the first place). KREDAI (bonding, 3 addresses) is a
        # grep false positive (matches "dai" substring, not a stablecoin) --
        # excluded. Not each individually incident-verified like msUSD --
        # the objective criterion (currently trading near its nominal peg) is
        # what matters here, regardless of whether the contract is the real
        # issuer or an impersonator: neither is ever a legitimate wick/creux
        # reversal candidate.
        "0x409e79c96389c00fb5a46586ace2615c6d09c76e",  # AIUSD -- 0.9999 (05/08 scan)
        "0x832bcced5bd431b31663576490344ea1c0bea295",  # EUR -- 1.093 (31/07 scan)
        "0x4933a85b5b5466fbaf179f72d3de273c287ec2c2",  # EURAU -- 1.15 (01/08 scan)
        "0x55380fe7a1910dff29a47b622057ab4139da42c5",  # FXUSD (f(x) Protocol) -- 0.9999 (03/08 scan)
        "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",  # USDe (Ethena) -- 0.9996 (30/07 scan)
        "0x8210c0634ab8f273806e4b7866e9db353773c44b",  # USDf (Falcon Finance) -- 0.9965 (02/08 scan)
        "0x04d5ddf5f3a8939889f11e97f8c4bb48317f1938",  # USDz (Anzen) -- 0.9724 (01/08 scan)
        "0x14913815bcfde78baead2111f463d038ac9c2949",  # eUSD -- 1.0000049 (05/08 scan)
        "0x4154550f4db74dc38d1fe98e1f3f28ed6dad627d",  # jEUR (Jarvis Synthetic Euro) -- 1.15 (05/08 scan)
        "0x1217bfe6c773eec6cc4a38b5dc45b92292b6e189",  # oUSDT (omnichain USDT) -- 0.9991 (01/08 scan)
    }),
}


def is_non_trusted_pegged_asset(token_address: str, chain: str) -> bool:
    """True if this address is a known pegged/synthetic asset whose peg
    mechanism has already failed or is otherwise unreliable -- excluded from
    speculative discovery, never from security checks (see registry comment
    above for why the two must stay separate)."""
    chain = (chain or "").strip().lower()
    return (token_address or "").lower() in _NON_TRUSTED_PEGGED_ASSET_ADDRESSES_BY_CHAIN.get(chain, frozenset())


# "wrap/unwrap" exploit (15/07, Gemini review): a script that wraps/unwraps
# ETH<->WETH hundreds of times for a few cents of gas would artificially
# unlock WEIGHTS.min_total_swaps without ever taking on trading risk. Cheap
# and UNAMBIGUOUS detection (unlike the DeFi protocol registry documented
# below, out of scope): each chain's wrapped-native token has a SINGLE
# canonical address, and deposit()/withdraw() emit a standard Transfer
# from/to the zero address (mint/burn) -- no false positive possible. A chain
# missing from the registry = no protection (documented degraded behavior,
# same policy as `_STABLECOIN_ADDRESSES_BY_CHAIN`).
_WRAPPED_NATIVE_ADDRESSES: frozenset[str] = frozenset({
    "0x4200000000000000000000000000000000000006",  # WETH -- Base (predeploy standard)
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH -- Ethereum mainnet
})

# Liquid-staking tokens (LST) -- 24/07, 5-agent audit finding: the momentum
# pipeline's reference-token exclusion covered stablecoins and wrapped-native
# but explicitly documented the LST case (stETH<->wstETH, WBTC<->tBTC,
# rETH<->wETH) as "a real gap, out of scope" -- confirmed live: the paper
# portfolio held a real position in JitoSOL (bridged), a blue-chip staking
# derivative whose price mechanically tracks SOL, not the speculative/momentum
# profile #194 targets. Best-effort registry, same degraded-behavior policy as
# _WRAPPED_NATIVE_ADDRESSES above (a chain/token missing here = no
# protection, not a silent guarantee) -- only addresses independently
# confirmed against real on-chain data go in this set, never guessed.
_LST_ADDRESSES_BY_CHAIN: dict[str, frozenset[str]] = {
    "base": frozenset({
        "0x97be14dd8f994a5364573bc035d85309e7cb34de",  # JitoSOL (bridged) -- confirmed live, 24/07
        # 08/04 -- the exact "real gap" documented above finally recurring
        # live: 4 separate swing/scalping limit orders sourced on wstETH in
        # 3 days (contract confirmed against the real open order,
        # historical_trigger_rate("rsi_divergence_pending", wallet="swing")
        # verified at 1.2%/327 orders -- a near-deterministic ETH-staking
        # derivative producing "golden pocket"/"RSI divergence" chart
        # patterns that are really just ETH's own chart, no token-specific
        # edge, disguised as a Base momentum candidate).
        "0xc1cba3fcea344f92d9239c08c0568f6f2f0ee452",  # wstETH -- confirmed live, 08/04
    }),
}


def _is_wrap_unwrap_leg(transfer: TokenTransfer) -> bool:
    addr = (transfer.token_address or "").lower()
    if addr not in _WRAPPED_NATIVE_ADDRESSES:
        return False
    return (transfer.from_address or "").lower() == _ZERO_ADDRESS or (transfer.to_address or "").lower() == _ZERO_ADDRESS


# Extension of the wrap/unwrap exploit (15/07, Gemini review follow-up): a
# stable<->stable swap (USDC<->USDT/DAI, near-zero-fee pool, near-zero
# directional risk) allows the same WEIGHTS.min_total_swaps padding as
# wrap/unwrap, without going through a mint/burn -- not covered by
# `_is_wrap_unwrap_leg`. Reuses the stablecoin registry that ALREADY exists
# (`_STABLECOIN_ADDRESSES_BY_CHAIN`, built for exact-hash pricing) -- no new
# registry to maintain, unlike the LST/wrapped case (stETH<->wstETH,
# WBTC<->tBTC, rETH<->wETH) which would remain a real gap (peg-by-peg mapping
# registry, out of scope for this fix -- documented as a limitation below).
_ALL_RECOGNIZED_STABLECOINS: frozenset[str] = frozenset().union(*_STABLECOIN_ADDRESSES_BY_CHAIN.values())


def _is_recognized_stablecoin(token_address: str | None) -> bool:
    return (token_address or "").lower() in _ALL_RECOGNIZED_STABLECOINS


# Item #234 (30/07), operator question ("sauf pour les tokens comme btc eth et
# tous non?") while extending the momentum honeypot veto to
# slippage_modifiable/is_blacklisted/transfer_pausable (same contextualization
# doctrine already applied to the VC crible, acp_onchain_scan.py -- a
# regulated/institutional issuer legitimately uses these mechanisms, an
# anonymous memecoin deployer doesn't). `_is_recognized_stablecoin` above only
# covers USD/EUR pegs -- a wrapped BTC/ETH candidate would NOT be exempted by
# it, and these blue-chip wrapped assets structurally carry the same
# custodian-controlled mint/pause/blacklist mechanisms (e.g. Coinbase's cbBTC/
# cbETH) for the same non-malicious reasons. Reuses `_WRAPPED_NATIVE_ADDRESSES`
# for WETH (already verified) rather than duplicating it. All addresses below
# independently confirmed live (DexScreener, highest-liquidity match) on
# 30/07 -- never guessed, same doctrine as every other registry on this file.
_BLUECHIP_WRAPPED_ADDRESSES_BY_CHAIN: dict[str, frozenset[str]] = {
    "base": frozenset({
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",  # cbBTC (Coinbase) -- confirmed live, ~8.7M$ liq
        "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22",  # cbETH (Coinbase) -- confirmed live
        "0x0555e30da8f98308edb960aa94c0db47230d2b9c",  # WBTC -- confirmed live
    }),
}
_ALL_RECOGNIZED_BLUECHIPS: frozenset[str] = _WRAPPED_NATIVE_ADDRESSES.union(
    *_BLUECHIP_WRAPPED_ADDRESSES_BY_CHAIN.values()
)


def _is_recognized_bluechip(token_address: str | None) -> bool:
    return (token_address or "").lower() in _ALL_RECOGNIZED_BLUECHIPS


def is_recognized_reference_asset(token_address: str | None) -> bool:
    """Public helper (Item #234, 30/07): stablecoin OR blue-chip wrapped asset
    -- the combined exemption used wherever a mint/pause/blacklist-style
    dormant lever is contextualized (regulated/blue-chip issuer vs. anonymous
    deployer), reused as-is by both the VC crible and the momentum hard gate
    rather than each maintaining its own copy of this OR."""
    return _is_recognized_stablecoin(token_address) or _is_recognized_bluechip(token_address)


def _is_stable_to_stable_peg_swap(tx_hash: str, transfers_by_tx: dict[str, list[TokenTransfer]]) -> bool:
    """True if ALL legs touching the wallet in this transaction are recognized
    stablecoins (buy AND sell on either side) -- a stable<->stable swap, not a
    real directional bet. A single stablecoin leg (e.g. buying a memecoin PAID
    for in USDC) is never affected -- `len(legs) >= 2` requires at least one
    outgoing AND one incoming leg."""
    legs = transfers_by_tx.get(tx_hash, [])
    return len(legs) >= 2 and all(_is_recognized_stablecoin(t.token_address) for t in legs)


@dataclass
class WalletBehavior:
    address: str
    is_contract: bool | None = None
    buys: int = 0
    sells: int = 0
    distinct_days: int = 0
    coherent_over_time: bool = False
    early_and_controlled: bool = False
    disciplined_exit: bool | None = None  # None = not enough exits to judge
    wash_trading_suspected: bool = False
    available: bool = True
    error: str | None = None

    @property
    def criteria_met(self) -> int:
        return sum(
            [
                self.coherent_over_time,
                self.early_and_controlled,
                bool(self.disciplined_exit),
            ]
        )

    @property
    def is_smart_candidate(self) -> bool:
        return (
            self.available
            and not self.wash_trading_suspected
            and not self.is_contract
            and self.criteria_met >= 2
        )


@dataclass
class SmartMoneySignal:
    wallets_analyzed: int = 0
    smart_wallets: list[str] = field(default_factory=list)
    score_delta: int = 0
    # Raw quality+quantity signal (0-100, before scaling into score_delta) --
    # transparency/debug, never used directly to decide (cf. score_delta).
    quality_signal: float | None = None
    flags: list[str] = field(default_factory=list)
    available: bool = True
    error: str | None = None


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _select_top_wallets(
    holders: TokenHoldersResult, *, lp_address: str | None, max_wallets: int
) -> list[str]:
    lp = (lp_address or "").lower()
    candidates = [
        h
        for h in holders.holders
        if (h.address or "").lower() not in {lp, _ZERO_ADDRESS, ""}
    ]
    candidates.sort(key=lambda h: h.percentage or -1.0, reverse=True)
    return [h.address for h in candidates[:max_wallets]]


def _dominant_counterparty_share(
    transfers: list[TokenTransfer],
    wallet: str,
    *,
    lp_address: str | None,
    extra_exclusions: set[str] | None = None,
) -> float:
    """Share of exchanges (excluding LP/pool, buys+sells) concentrated on a
    single counterparty.

    The LP/pool is excluded from the calculation: almost all DEX buys/sells
    go through it, so counting it would make any early buyer look like a
    wash-trading case. Below `_MIN_TRANSFERS_FOR_WASH_CHECK` exchanges
    excluding the LP, there isn't enough data to judge -- no suspicion.

    ``extra_exclusions`` (#157, fix 14/07): set of additional addresses to
    exclude (beyond just ``lp_address``) -- needed when ``transfers`` covers
    SEVERAL tokens (a single static pool/LP is no longer enough, cf.
    `_build_dex_infrastructure_exclusions`). Optional parameter, does not
    affect the historical token-centric call (`_analyze_wallet_behavior`).
    """
    wallet_l = wallet.lower()
    excluded = {(lp_address or "").lower()} | {a.lower() for a in (extra_exclusions or ())}
    counterparties: dict[str, int] = {}
    total = 0
    for t in transfers:
        other = t.to_address if t.from_address.lower() == wallet_l else t.from_address
        other = (other or "").lower()
        if not other or other in excluded:
            continue
        counterparties[other] = counterparties.get(other, 0) + 1
        total += 1
    if total < _MIN_TRANSFERS_FOR_WASH_CHECK:
        return 0.0
    return max(counterparties.values()) / total


def _analyze_wallet_behavior(
    wallet: str,
    transfers: list[TokenTransfer],
    *,
    is_contract: bool | None,
    pair_created_at_ms: int | None,
    lp_address: str | None,
) -> WalletBehavior:
    wallet_l = wallet.lower()
    buys = [t for t in transfers if (t.to_address or "").lower() == wallet_l]
    sells = [t for t in transfers if (t.from_address or "").lower() == wallet_l]

    days = {
        ts.date()
        for t in (buys + sells)
        if (ts := _parse_timestamp(t.timestamp)) is not None
    }

    coherent = len(days) >= 2 and (len(buys) + len(sells)) >= 2

    early_and_controlled = False
    if buys and pair_created_at_ms is not None:
        buy_times = [ts for t in buys if (ts := _parse_timestamp(t.timestamp)) is not None]
        pair_created_at = datetime.fromtimestamp(
            pair_created_at_ms / 1000, tz=buy_times[0].tzinfo if buy_times else None
        )
        earliest = min(buy_times) if buy_times else None
        if earliest is not None:
            elapsed = (earliest - pair_created_at).total_seconds()
            amounts = [b.amount for b in buys if b.amount is not None]
            largest_share = (max(amounts) / sum(amounts)) if amounts and sum(amounts) > 0 else None
            controlled_size = (
                len(buys) >= 2 and (largest_share is None or largest_share <= _LARGEST_BUY_SHARE_MAX)
            )
            early_and_controlled = 0 <= elapsed <= _EARLY_ENTRY_WINDOW_SECONDS and controlled_size

    disciplined_exit: bool | None = None
    if sells:
        disciplined_exit = len(sells) >= 2 or (len(sells) == 1 and len(buys) >= 1)

    wash_suspected = (
        _dominant_counterparty_share(buys + sells, wallet, lp_address=lp_address)
        >= _WASH_TRADING_COUNTERPARTY_SHARE
    )

    return WalletBehavior(
        address=wallet,
        is_contract=is_contract,
        buys=len(buys),
        sells=len(sells),
        distinct_days=len(days),
        coherent_over_time=coherent,
        early_and_controlled=early_and_controlled,
        disciplined_exit=disciplined_exit,
        wash_trading_suspected=wash_suspected,
        available=True,
        error=None,
    )


async def analyze_smart_money(
    token_address: str,
    holders: TokenHoldersResult,
    *,
    client: BlockscoutClient,
    lp_address: str | None = None,
    pair_created_at_ms: int | None = None,
    max_wallets: int = _MAX_WALLETS_DEFAULT,
) -> SmartMoneySignal:
    """Read-only analysis of top holders — confirmation/context signal only."""
    if not holders.available:
        return SmartMoneySignal(available=False, error=holders.error or UNAVAILABLE)

    wallets = _select_top_wallets(holders, lp_address=lp_address, max_wallets=max_wallets)
    if not wallets:
        return SmartMoneySignal(wallets_analyzed=0, available=True)

    token_l = token_address.lower()
    smart_wallets: list[str] = []
    qualified_scores: list[float] = []
    unavailable_count = 0

    for wallet in wallets:
        info = await client.get_address_info(wallet)
        transfers_result = await client.get_token_transfers(wallet, limit=100)

        if not transfers_result.available:
            unavailable_count += 1
            continue

        matched = [
            t
            for t in transfers_result.transfers
            if (t.token_address or "").lower() == token_l
        ]

        behavior = _analyze_wallet_behavior(
            wallet,
            matched,
            is_contract=info.is_contract if info.available else None,
            pair_created_at_ms=pair_created_at_ms,
            lp_address=lp_address,
        )
        if not behavior.is_smart_candidate:
            continue
        smart_wallets.append(wallet)
        # 25/08 -- used to prioritize a wallet's already-known GLOBAL score
        # (composite_percentile from the wallet-scoring project) over this
        # flat fallback when available. Simplified to always use the
        # fallback: wallet-scoring was removed entirely (operator decision),
        # so there is no longer any "known global score" to prefer -- every
        # convergent wallet now contributes the same base score, and the
        # convergence bonus below (number of qualified wallets) is what
        # differentiates the signal.
        qualified_scores.append(_FALLBACK_QUALIFIED_SCORE)

    flags: list[str] = []
    quality_signal: float | None = None
    score_delta = 0

    if unavailable_count:
        flags.append(
            f"Smart-money : {unavailable_count}/{len(wallets)} wallet(s) non analysable(s) "
            f"({UNAVAILABLE})."
        )

    if len(qualified_scores) >= 2:
        # Binary entry gate unchanged (doctrine "1 wallet alone proves
        # nothing") -- beyond that, magnitude depends on quality (best known
        # score) AND the number of qualified wallets (CAPPED convergence
        # bonus, never dominant: 10 low-score wallets can never outrank 2
        # high-score wallets, cf. comment on the constants earlier in this
        # file).
        top_score = max(qualified_scores)
        convergence_bonus = min(len(qualified_scores) - 1, _CONVERGENCE_BONUS_MAX_WALLETS) * _CONVERGENCE_BONUS_PER_WALLET
        quality_signal = min(100.0, top_score + convergence_bonus)
        score_delta = round(quality_signal / 100.0 * _MAX_SECURITY_SCORE_DELTA)
        flags.append(
            f"Smart-money : {len(smart_wallets)} wallets parmi les top holders montrent un "
            "comportement convergent (cohérence temporelle, entrées échelonnées), meilleur "
            f"score connu {top_score:.0f}/100 — confirmation contextuelle, jamais un déclencheur."
        )
    elif len(smart_wallets) == 1:
        flags.append(
            "Smart-money : 1 seul wallet au comportement convergent détecté — "
            "concentration insuffisante pour confirmer (critère multi-wallets non atteint)."
        )

    return SmartMoneySignal(
        wallets_analyzed=len(wallets),
        smart_wallets=smart_wallets,
        score_delta=score_delta,
        quality_signal=quality_signal,
        flags=flags,
        available=True,
        error=None,
    )


# ============================================================================
# #157 -- wallet-centric multi-token evaluator (in-house "smart wallet")
#
# Extension of the module above: instead of analyzing a single token's top
# holders, we take 1-3 wallet addresses and pull their ENTIRE trade history
# across SEVERAL tokens (via `get_token_transfers`, paginated on the
# `blockscout.py` side), value it (FIFO PnL) via GeckoTerminal, and derive a
# composite score + a separate "suspect positive" flag + an LLM thesis from
# it. Always a confirmation/context signal -- never a trigger (same absolute
# rule as `analyze_smart_money` above).
#
# Four layers (sourced research, docs/aria-learning-inbox/
# 2026-07-14-recherche-equation-smart-wallet-scoring-157.md):
#   1. Hard disqualifiers (generalized wash-trading, contract wallet,
#      "convergent" wallets = same entity via deposit-address reuse,
#      funding by a known malicious wallet).
#   2. Composite score (FIFO PnL/win-rate, Sortino, multi-launch early-buyer
#      recurrence with technical entry conditions, diversification, wallet
#      drawdown).
#   3. Separate "suspect positive" flag (never folded into the average score).
#   4. Logging ready for continuous recalibration (no recalibration built
#      yet, just the write path).
# ============================================================================
#
# KNOWN STRUCTURAL LIMITATIONS (15/07, on-chain blind spots identified via
# cross external review -- deliberately DOCUMENTED, not fixed, so as not to
# blow up the complexity of the central FIFO engine):
#
# - DeFi (collateral deposit / liquidity provision): `_analyze_wallet_multi_token`
#   treats EVERY outgoing transfer of a tracked token as a market-valued FIFO
#   sell leg (cf. `sells` below, symmetric to `buys` by construction --
#   neither distinguishes "sold" from "moved"). An Aave deposit (collateral)
#   or Uniswap deposit (LP) therefore produces a fictitious realized PnL at
#   deposit time (nothing was sold), and a later withdrawal (the token comes
#   back) registers as a repurchase at a brand-new entry price, disconnected
#   from the real initial price. No cheap, reliable signal to distinguish a
#   receipt token (aToken/LP token) from a real swap without a hardcoded
#   protocol registry (permanent maintenance burden, likely false positives)
#   -- not built.
# - Cross-chain bridges: the multi-chain scan (`chain_clients`, composite key
#   "{chain}:{address}") consolidates a score PER WALLET but never links an
#   outgoing leg on one chain to the matching arrival on another. An
#   Ethereum->Arbitrum bridge registers as a FIFO sell on the source side
#   (market price at the outgoing transfer) AND an independent FIFO
#   repurchase on the destination side (market price on arrival) -- the same
#   structural flaw as the DeFi case above, plus the added difficulty of
#   correlating two legs across two different chains' datasets (net amount
#   after bridge fees, plausible time window, registry of known bridge
#   contracts).
#
# Shared impact of both: the fictitious FIFO trades thus created pollute ALL
# metrics derived from `cumulative_trades` (win_rate, PnL, Sortino, drawdown,
# health trend) on equal footing with real trades -- not an isolated margin
# of error on a single number. Affected population: more significant for
# wallets that also do yield/LP/multi-L2 than for a pure Base memecoin
# trader -- not negligible either for a genuinely serious "smart money"
# wallet. No fix planned short-term -- to be reopened if a precise business
# need (e.g. funding dossier, deep due diligence on a given wallet)
# justifies it.
#
# FOLLOW-UP (15/07, second pass -- cross review Gemini/ChatGPT/Grok + web
# search on Sybil/Nansen/Arkham). Fixed this pass (cf. code + WEIGHTS): the
# ETH<->WETH wrap/unwrap exploit on the swap threshold (`_is_wrap_unwrap_leg`),
# dilution of the anti-luck trim by trade volume (`robust_trim_pct`),
# dust/scam-pool via a confirmed-liquidity floor (`min_pool_liquidity_
# usd_for_pricing`), transparency on cost-basis confidence
# (`price_confirmation_ratio`) and on unmatched sells
# (`unmatched_sell_events`), capital-weighted diversification in addition to
# the count-based one. Verified and REJECTED (already correctly handled, not
# a real gap): Sortino division by zero (`_sortino_ratio` already returns
# `None` if `downside` is empty, BEFORE any deviation calculation -- the
# `downside_deviation == 0` guard that follows is dead defensive code, never
# reachable, but harmless); win rate not weighted by loss size (already
# compensated by construction -- Sortino/PnL remain separate axes, never
# folded with win rate, so a "99% wins + 1 catastrophic loss" stays visible
# elsewhere).
#
# Documented, DELIBERATELY not fixed this pass (too costly/complex for a
# point fix, or out of scope for a threshold adjustment):
#
# - Sybil coordination / multi-wallets (Grok review, THE most important
#   unresolved point): a single operator can run dozens of wallets that each
#   clear the sample threshold and perform in a coordinated way -- each
#   wallet has a good individual score, and collectively they bias the
#   comparative ranking (percentiles) as the pool of tracked wallets grows.
#   The anti-luck trim doesn't change anything here (a well-orchestrated
#   Sybil spreads its outliers). Confirmed by external research (15/07):
#   this is a known structural problem of any wallet-by-wallet analysis
#   without entity clustering -- Nansen/Arkham/Chainalysis/TRM rely on
#   clustering by SHARED FUNDING SOURCE (same family as our existing
#   `_pairwise_convergence`, cf. Victor FC 2020) but at the scale of a GRAPH
#   over the entire tracked population, not just a pairwise comparison
#   between the 1-3 wallets submitted TOGETHER in a single call -- our
#   current version is therefore the same family of heuristic, just much
#   narrower in scope. The most robust approaches (Chainalysis/TRM) now use
#   graph neural networks trained on labeled Sybil clusters, noticeably
#   harder to bypass than a heuristic-only clustering -- out of scope for a
#   point fix, a genuine separate project if ever undertaken.
# - Entry-threshold farming / light wash-trading (Grok review): beyond the
#   wrap/unwrap case already closed above, nothing prevents a wallet from
#   padding `min_total_swaps` with tiny round-trips on a REAL liquid token
#   (real slippage/fees each round, so costlier than wrap/unwrap, but not
#   impossible). Cheap lead identified but not built (external research
#   15/07): wash-traders typically use ROUND AMOUNTS and near-zero price
#   impact despite the volume -- a dedicated detector would be a natural
#   complement to the existing `_dominant_counterparty_share`, banked for a
#   future pass.
# - No market benchmark (alpha vs beta, Grok review): a wallet that simply
#   does pure beta (long BTC/ETH in a bull market) can produce excellent
#   win rate/Sortino/PnL with no particular skill -- the system measures the
#   quality of the on-chain footprint, not value added relative to the
#   market. Would require a reference return series (BTC/ETH/DeFi index) and
#   a dedicated alpha calculation -- a real feature to scope separately, not
#   a threshold tweak.
# - Structural gaming of the robustness tests (Grok review): a wallet can
#   deliberately take its worst trades very early in its activity (before
#   the history really counts) to "consume" the anti-luck trim budget, or
#   structure its activity so the 2nd half of the health curve looks
#   artificially better. Easier just above the minimum thresholds (30 trades
#   for the trim, 10 for the health curve) -- a limitation inherent to any
#   static threshold, not an isolated fixable bug.
# - MEV / atomic arbitrage / flash loans (Grok review): these near-zero-risk
#   strategies can produce excellent win rate and Sortino (near-nonexistent
#   downside by construction) and pass the anti-luck trim easily (uniformly
#   good trades, no outlier to remove). The system treats them as normal
#   trades -- distinguishing them would require transaction-level
#   atomicity/flash-loan detection (bytecode/call-trace), data that
#   Blockscout doesn't provide natively -- out of scope without a new
#   dedicated data source.
# - Survivorship bias of the sample gate (ChatGPT review): the
#   `min_wallet_age_days`/`min_total_swaps` threshold selects wallets that
#   SURVIVED long enough to reach it -- catastrophic wallets often die
#   before that, and the best traders may rotate wallets regularly (opsec).
#   The ranking therefore becomes a ranking of SURVIVING wallets, not
#   necessarily of the best traders. Inherent to any minimum sample gate --
#   not a bug, an accepted trade-off (same doctrine as
#   `docs/protocole-argent-reel.md`: minimum sample before trusting, even at
#   the cost of excluding valid cases).
# - FIFO methodological choice (ChatGPT review): all metrics use a single
#   FIFO model to ensure COMPARABILITY across wallets -- a LIFO/HIFO model
#   would give a different PnL on repeated partial buy/sell sequences. This
#   is not a tax choice (no claim of tax compliance, only a comparable
#   performance measure) -- accepted, not a defect.
# - Percentile paradox / non-representative comparison population (Gemini +
#   ChatGPT review): the comparative ranking compares THIS wallet to the
#   OTHER wallets already run through `/walletscore` -- not a representative
#   market sample. If the tool becomes massively used by amateurs, an
#   average trader ends up artificially in a high percentile; if only pros
#   use it, the opposite. The same wallet's percentile can therefore move
#   over time WITHOUT any of its own trades changing -- purely because the
#   demographics of the tracked base evolved. A fixed benchmark (a
#   representative random blockchain sample, e.g. 5000 active wallets) would
#   fix the problem but would be expensive (running this same
#   multi-network-call pipeline on thousands of wallets, continuously) --
#   not built. `compared_against_n_wallets` stays displayed next to the
#   percentile to at least signal the order of magnitude of the comparison
#   population (never hidden).
# - Chronological split by trade COUNT for the health curve (ChatGPT
#   review): `_health_trend` compares the 1st to the 2nd half by trade
#   count, not by calendar window -- a wallet active for 3 years then
#   dormant for 1 year can have its "trend" dominated by a recent comeback
#   rather than reflecting a real change in skill. A calendar-window split
#   (midpoint of total duration, not of trade count) would be more robust to
#   this case -- lead identified, not built this pass (function rewrite,
#   effect on existing behavior to validate separately).
#
# THIRD PASS (15/07, same evening -- cross review round 2/3, Gemini x2 +
# ChatGPT + Grok). Fixed this pass: stable<->stable swaps excluded from the
# swap counter (extension of the wrap/unwrap exploit above, cf.
# `_is_stable_to_stable_peg_swap`); recent-window metrics
# (`_recent_window_metrics`, response to the time bias -- ChatGPT); clarified
# and test-locked that the fail-open on unknown liquidity is never reached
# by the real GeckoTerminal client (cf. comment on `pool_liquid_enough`
# further below). Verified and REJECTED (repeated twice by Gemini, still
# false against the code): Sortino division by zero -- `_sortino_ratio`
# returns `None` as soon as `downside` is empty, before any deviation
# calculation, locked by `test_no_losses_unavailable_not_infinite`.
#
# Documented, DELIBERATELY not fixed this pass:
#
# - Near-perfectly-correlated LST/wrapped pairs (Gemini review): beyond the
#   now-closed stable<->stable case, WBTC<->tBTC, stETH<->wstETH, rETH<->wETH
#   allow the same padding at near-zero cost/risk. No existing registry to
#   reuse here (unlike stablecoins) -- building and maintaining a
#   peg-by-peg mapping registry is the same kind of burden as the DeFi
#   protocol registry already dismissed above. A narrower gap than before
#   (the stable<->stable sub-case, probably the most used in practice, is
#   closed), but real.
# - Anti-luck trim dilution by micro-trades (Gemini review, refinement):
#   `_robust_pnl_check` sorts by PnL IN DOLLARS, not by % return. An
#   attacker who wants to push a legendary trade (e.g. +10,000% on a tiny
#   position) out of the trim must pad with trades whose PnL IN DOLLARS is
#   comparable or larger -- not simple few-cent micro-trades, which then
#   stay below the legendary trade in the sort order and keep getting
#   trimmed first. The real vulnerability is therefore narrower than
#   "spamming free micro-trades": it requires the legendary trade itself to
#   be small IN DOLLARS despite a huge percentage, AND real capital
#   deployment on the padding trades to exceed that amount -- a more
#   constrained case, not eliminated either way. Refinement lead identified,
#   not built: a standard-deviation/z-score trim (removing trades more than
#   X standard deviations from the median) would be insensitive to the $ vs
#   % axis chosen, but changes the methodology more deeply (z-score
#   instability itself on a small sample to manage) -- candidate for a
#   future pass, not tonight.
# - Equal per-trade (not per-capital) weighting of win_rate/trim/health_trend/
#   SORTINO (ChatGPT review, clarified 15/07 -- external review: Sortino had
#   been omitted from this list by mistake, even though it shares exactly
#   the same flaw, cf. below): only diversification now has a
#   capital-weighted variant (cf. above). Win rate, anti-luck trim, health
#   curve AND Sortino remain counted/computed PER TRADE in % return -- a
#   $500,000 trade weighs the same as a $10 trade. This choice is ACCEPTED
#   for win_rate/trim/health_trend (per-trade counting measures something
#   else: the ability to find winners on independent bets) -- but for
#   SORTINO specifically, the consequence is more misleading than a simple
#   methodological choice: a ratio presented as "risk-adjusted return" can
#   show a POSITIVE number while the real PnL in dollars is NEGATIVE.
#   Verified numeric demonstration (5 trades, minimum threshold
#   `WEIGHTS.min_closed_trades_for_sortino` reached): 4 micro-trades at
#   +100% on a $1 stake each (+$4 total) + 1 major trade at -50% on a $1000
#   stake (-$500) -- real PnL = -$496 (net loss), but mean(return_i) = 0.7,
#   downside_deviation = 0.5, Sortino = 1.4 (positive, "honorable").
#   **Partially fixed (15/07)**: `sortino_pnl_contradiction` detects and
#   VISIBLY flags the most flagrant and reliably verifiable case (a SIGN
#   contradiction between Sortino and real PnL, never a nuance to
#   interpret), displayed as a WARNING next to the Sortino -- but does NOT
#   fix the underlying bias itself (a Sortino weighted by position size,
#   computed on the portfolio value curve rather than on unit returns, would
#   be a deeper methodological rewrite -- not undertaken, same trade-off as
#   the other unweighted metrics above).
# - Manipulation of the health-curve pivot point (Grok review, refinement of
#   an already-noted limitation): beyond the simple split by trade count
#   rather than calendar window, a wallet can deliberately speed up or slow
#   down its activity to place the pivot point at a favorable moment on its
#   own PnL curve -- an active manipulation lever, not just a passive blind
#   spot. Same candidate rewrite as already noted (calendar split), not
#   built.
# - Sybil coordination, absence of a market benchmark, structural gaming of
#   robustness tests, MEV/atomic arbitrage, entry-threshold farming,
#   protocol coverage asymmetry: reconfirmed by the round 2/3 review (Grok)
#   as still unresolved -- nothing new that would change the assessment
#   already written above, no duplicate entry.
#
# FOURTH PASS (15/07, round 4 review -- ChatGPT + Grok). Clarification
# provided (not a new mechanism, a scope clarification):
#
# - Token migrations (v1->v2), redenominations, mergers/splits, replacement
#   airdrops (ChatGPT review): verified -- these events do NOT create a
#   third gap mechanism, they fall back to the TWO categories already
#   documented above depending on their on-chain implementation: (a)
#   migration via a NEW contract (most common case, e.g. a v1 sent/burned +
#   a v2 received separately) = exactly the same flaw as the DeFi
#   deposit/cross-chain bridge case (two legs on two different token
#   addresses, never linked, fictitious PnL on both sides); (b)
#   redenomination/split WITHOUT an address change (balance
#   reinterpretation on the same contract) = exactly the same flaw as
#   rebasing (already captured, without being credited, by
#   `unmatched_sell_events`). Documented here as additional concrete
#   examples of the two limitations already written, not a new limitation.
# - The "suspect positive" flag as a reverse-manipulation target (Grok
#   review): because this flag is VISIBLE and can be read as a strong
#   signal, a sophisticated actor can deliberately calibrate their activity
#   to simultaneously clear the thresholds on >=3 axes (win rate, Sortino,
#   diversification, recurrence) without any real edge -- the flag then
#   becomes an optimization target itself rather than a reliable signal.
#   Limitation inherent to any VISIBLE threshold indicator (making it
#   visible serves transparency but creates the target) -- no defense
#   without making it more costly to trigger artificially (e.g. requiring
#   an independent confirmation), not built.
# - Layer-2 selection bias (Grok review): the priority "confirmed round-trip
#   -> recency -> trade count" (`_select_tokens_for_deep_analysis`)
#   structurally under-represents, at a given instant T (before
#   `full_coverage=True`), long-term holders of many small positions in
#   favor of very active traders on few tokens -- not a bug, an accepted
#   priority order (round-trip first because a still-open position can
#   never produce a closed trade), but a real bias as long as coverage isn't
#   complete. The cumulative incremental scan eventually covers everything,
#   but a score consulted BEFORE full coverage remains built on a
#   non-representative subset -- already partially disclosed
#   (`full_coverage`/`tokens_scanned_cumulative` displayed), not eliminated
#   either way.
#
# FIFTH PASS (15/07, Gemini review -- final audit). Two points, HANDLED
# DIFFERENTLY after verification:
#
# - FIFO distortion from OUT-OF-TRANSACTION supply fluctuations -- POSITIVE
#   **AND NEGATIVE** rebases (explicit renaming requested by Gemini, a
#   limitation already partly handled): the positive case (balance
#   increases with no transfer, e.g. stETH yield) was already documented and
#   captured without being credited (`unmatched_sell_events`). The NEGATIVE
#   case (balance divided with no transfer, e.g. an AMPL-like negative
#   rebase) is the exact mirror and was NOT explicitly named: the FIFO queue
#   keeps carrying "ghost" tokens (never purged for lack of an on-chain
#   event to react to), which then get consumed by a later sell at a stale
#   buy price -- an economically neutral trade can then register as a
#   fictitious profit. Same family of cause as the positive case (balance
#   changing out-of-transaction), symmetric in direction. Documented here as
#   is, not fixed -- same trade-off as the rest of the
#   rebasing/DeFi/bridges cases.
# - "Fictitious-loss collapse" via targeted dusting on a manipulated pool
#   (Gemini review) -- VERIFIED AS REAL against the code: a pool created
#   just above the liquidity floor ($35k > $30k) with a manipulated
#   point-in-time price can make an inflated acquisition cost (OHLCV) get
#   accepted on a dusted token, then a normal/crashed exit price closes the
#   trade at a massive fictitious loss -- confirmed plausible line by line
#   (the liquidity floor alone only protects against a durably thin pool,
#   not against a point-in-time price spike on a pool that clears the
#   floor). **First fix candidate tested and REJECTED after verification**:
#   reusing `_pool_is_plausible` (already existing, geckoterminal.py) to
#   also filter this case -- does NOT work here: this function deliberately
#   returns `True` (plausible) when 24h volume is zero or near-zero ("a
#   legitimate token may simply have had no recent trade", cf. its
#   docstring) -- exactly the profile of a scam pool traded little/never by
#   anyone but the attacker. A robust correction rule (comparing a specific
#   candle's price to its time-neighbors to detect an isolated spike, or
#   requiring independent market corroboration before trusting an OHLCV
#   cost-basis on a non-swap transfer) remains a genuine design project --
#   risk of new false positives (a legitimately volatile memecoin, or a
#   legitimate CEX withdrawal whose counterparty is never the pool) not
#   resolved tonight with the rigor this point deserves. **Not fixed,
#   flagged as the most serious limitation currently open** (attack cost
#   ~$50 of gas, deterministic, targetable on any tracked wallet) -- to be
#   handled as a dedicated project, not an end-of-evening fix.
# ============================================================================
#
# SIXTH PASS (15/07, converging Gemini + Grok review). Fixed this
# pass: rug-pull immunity (the liquidity floor is now ASYMMETRIC -- gates
# only the buy legs, never the sells, cf. the comment on
# `pool_liquid_enough`/`_price_lookup` above -- a real bug in fix #160, not
# just a residual limitation); percentile pollution by partial scores
# (`_latest_scored_wallets` now excludes `full_coverage=False` records from
# the comparison population).
# **Honest scope of the rug-pull fix**: does NOT solve all cases -- only the
# one where the BUY leg has a price established independently of current
# liquidity (exact tx_hash price, cf. `TestRugPullAsymmetricFloor`). If both
# the buy AND the sell depend SOLELY on the pool's current liquidity
# snapshot (the majority of legs, no stablecoin in the tx), the buy remains
# blocked by the floor (unchanged behavior, anti-dust protection intact) --
# the trade then still never closes (FIFO requires both edges to be valued),
# so the loss stays invisible in this precise sub-case. Root cause shared
# with the dusting vulnerability above: no HISTORICAL liquidity data (by
# timestamp) is available, only a snapshot at scan time -- same structural
# limitation, not resolved.
#
# Documented, not fixed -- small coordinated cluster wash-trading (2-5
# wallets, converging Gemini + Grok review): the layer-1 disqualifier
# (single counterparty >=60%) and pairwise convergence (same funding source)
# are BOTH simultaneously BYPASSABLE by an actor who spreads their
# complaisant volume across 2-4 DISTINCT COUNTERPARTIES (e.g. wallet A sends
# 30% to B, 30% to C, 40% legitimate trades -- no single counterparty
# crosses 60%) while using different or time-spread funding sources for
# each wallet in the cluster (avoids strict pairwise convergence). Each
# wallet then individually clears all disqualifiers and the 100-swap
# threshold, enters the comparative ranking, and the cluster can
# collectively bias the percentiles or raise the "suspect positive" flag in
# a coordinated way. Intermediate coordination level between intra-wallet
# wash-trading (already covered) and large-scale industrial Sybil (already
# documented above) -- same family of gap (no entity clustering beyond
# pairwise convergence), to be closed by the same dedicated project if
# undertaken (not a point threshold fix: widening the 60% threshold or the
# number of wallets checked pairwise only shifts the minimum cluster size
# required to bypass, never eliminates it).
#
# SEVENTH PASS (15/07, DeepSeek review -- 4th external AI). One point
# corrects an overstatement in my own comment (cf. `buy_blocked_thin_
# liquidity` above -- symmetric fictitious gains via selling on a
# manipulated pool, now honestly reworded). The others, verified real and
# new (no duplicate with previous passes):
#
# - Drawdown/Sortino computed ONLY on REALIZED PnL (`_max_drawdown_pct`/
#   `_sortino_ratio` only read `closed_trades`, never `open_position_
#   amount`): a wallet carrying a massively-underwater open position
#   (bought then never sold, so never "realized") shows a null or very low
#   drawdown while its real risk is enormous -- the risk measure is
#   structurally optimistic as long as a position stays open. Fixing this
#   would require a real mark-to-market feature (reliable current price per
#   open token + weighted average cost of the remaining FIFO queue +
#   redefinition of what "drawdown" measures -- realized+unrealized equity
#   curve rather than realized only): same family of dedicated project as
#   the alpha benchmark/Sybil case already deferred, not a threshold
#   addition. Not built.
# - `price_confirmation_ratio`/`price_confidence_low` measure METHOD
#   confidence (price by exact stablecoin ratio vs. estimated OHLCV
#   fallback), NOT resistance to market manipulation -- an orthogonal axis.
#   A leg "confirmed" 100% by exact hash remains true (a ratio actually
#   executed in ITS OWN transaction), but a purely-OHLCV leg can be exact
#   (healthy market) or manipulated (low-volume pool, cf. the dusting
#   vulnerability already documented) -- the flag doesn't distinguish these
#   two cases among the estimated legs. Documented here as a scope
#   clarification, not a new mechanism to fix (the underlying vulnerability
#   is already the dusting/manipulated-pool case above).
# - Anti-luck trimming and false negative on a legitimately concentrated
#   style (barbell/conviction sizing): `_robust_pnl_check` sorts by PnL in
#   dollars and removes the extreme `robust_trim_pct` on both sides before
#   checking that the rest is positive -- designed to neutralize an isolated
#   stroke of luck (cf. previous passes), but a trader whose real edge COMES
#   precisely from a small number of extreme gains (a few accepted
#   multi-baggers, many small losses/positions cut quickly) may see their
#   best legitimate trades trimmed and the rest artificially judged "not
#   robust" -- a false negative on a real trading style, not just a true
#   positive on luck. Distinguishing "isolated luck" from "accepted
#   conviction sizing" would require an independent signal (e.g.
#   pre-decided position size, documented thesis) that the plain on-chain
#   history doesn't provide -- not built, an accepted tension between the
#   two possible readings of the same signal.
# - `max_tokens_analyzed` cap / exhaustive coverage (DeepSeek review, same
#   angle as the "layer-2 selection bias" already documented in the FOURTH
#   PASS): verified -- already presented as an explicit completeness
#   limitation (`full_coverage`/`tokens_scanned_cumulative` displayed in the
#   report, and since fix #172, `full_coverage=False` now excludes the
#   wallet from the percentile comparison population). Not an additional
#   blind spot, the partial coverage is already disclosed and neutralized
#   where it would matter most (the comparative ranking).
# ============================================================================
#
# CHECKPOINT NOTE (15/07): at this stage, successive rounds of external
# review overwhelmingly reconfirm the same structural limitations already
# written (Sybil, market benchmark, MEV, threshold/test gaming) rather than
# revealing new ones -- a signal that the ground has been correctly mapped.
# The items still open are, by nature, separate PROJECTS (entity clustering,
# reference return series, transaction atomicity detection), not additional
# point fixes -- to be reopened on an explicit decision if one of them
# becomes a priority.
# ============================================================================
#
# EIGHTH PASS (15/07, Gemini + DeepSeek round 2 review). One real bug fixed
# (not a residual limitation), one real blind spot documented:
#
# - Freezing of transient errors (Gemini review) -- FIXED for the most
#   impactful layer: a GeckoTerminal INFRASTRUCTURE failure (timeout/429/
#   server error, already retried several times by `_get_json` before giving
#   up) during a token's pool resolution could freeze into a PERMANENT scar
#   -- the persistent incremental scan (checkpoint) only retries a token
#   already "seen" if its on-chain activity has changed, never on the simple
#   resolution of an API error. A one-off network outage during ONE
#   background scan thus doomed a leg to stay "priceless" forever in the
#   archives (`wallet_archived_trade`), durably skewing the wallet's PnL AND
#   `price_confirmation_ratio`, with no automatic correction path. Fixed:
#   `resolve_primary_pool` already distinguishes, IN TEXT, a DATA verdict
#   ("no pool found for this token"/"no plausible pool...") from an
#   infrastructure failure (prefixed by the `UNAVAILABLE` constant from
#   `geckoterminal.py` in ALL `_get_json` failure cases) -- a signal already
#   present, never exploited until now. `_analyze_wallet_multi_token` now
#   classifies each token that failed to resolve
#   (`transient_pricing_error_tokens`), and `score_wallets` excludes these
#   tokens from `checkpoint.scanned_tokens` -- they remain eligible for a
#   new attempt on the next call, EVEN with no new on-chain activity.
#   **Honest scope, NOT a universal fix**: only covers the POOL resolution
#   layer (GeckoTerminal), where the error text properly separates the two
#   cases. The OHLCV layers (`services/ohlcv.py`, a client shared with
#   `vc_predictions`/`weekly_training`/`pump_dump_autopsy`) and CoinMarketCap
#   (3rd-layer triangulation) DO conflate transient failure and legitimate
#   absence of data under THE SAME prefix convention
#   (`f"{UNAVAILABLE} (pool absent)"`/`f"{UNAVAILABLE} (no candle...)"` read
#   textually like a real outage) -- distinguishing them properly would
#   require either a dedicated typed field threaded through these shared
#   clients (regression risk on their OTHER callers), or fragile filtering
#   by a diagnostic substring never designed for this use. The same failure
#   mode (silent freeze) therefore remains possible if the failure occurs at
#   THESE layers rather than at pool resolution -- narrower residual than
#   before (the most frequent entry point is closed), but real, documented,
#   not fixed. 3 new tests (including a contrast test: a token with NO pool
#   at all, a legitimate verdict, is still correctly marked "scanned" --
#   unchanged historical behavior).
# - Selection bias induced by the `price_confidence_low` exclusion (DeepSeek
#   round 2 review) -- DOCUMENTED, an accepted tension, not fixed. Fix #175
#   (excluding a low-price-confidence wallet from the percentile comparison
#   population) protects the INTEGRITY of the OTHER wallets' percentile
#   (avoiding anchoring a comparison on numbers potentially skewed by an
#   unreliable price estimate) -- but mechanically introduces a SELECTION
#   bias into the reference population itself: a wallet that trades
#   low-liquidity tokens, with no direct stablecoin pair, or via an
#   aggregator/smart-account (routing that escapes `_hash_based_price`
#   detection, cf. its docstring) will STRUCTURALLY have a low
#   `price_confirmation_ratio` -- not because it cheats or performs badly,
#   but because ITS trading style produces fewer hash-exact legs. Such a
#   wallet is still scored (with its own warning displayed), but is never
#   again used as a REFERENCE POINT to compare other wallets -- the
#   comparison population narrows around wallets that trade via direct
#   stablecoin pairs, NOT around a representative sample of "smart money" in
#   the broad sense. **Tension particularly relevant to ARIA's own thesis**
#   (sourcing builders on often-illiquid Base microcaps, cf. CLAUDE.md
#   "Vision & strategy"): these are precisely the traders most at risk of
#   being under-represented in the reference group. Adds to the percentile
#   paradox already documented (round 2/3, non-representative market
#   population) -- same family of limitation, an ADDITIONAL and distinct
#   bias axis (trading style, not just the tool's user demographics). **No
#   code fix proposed**: reverting exclusion #175 would directly
#   reintroduce the bug it fixed (anchoring a percentile on unreliable
#   numbers) -- a trade-off between two known defects, not an error to fix
#   one way or the other without a finer mechanism (e.g. weighting a
#   wallet's contribution to the comparison population by its confidence
#   rather than all-or-nothing) -- separate project if picked up again.
# ============================================================================
#
# NINTH PASS (15/07, external review -- the equation summarized to the
# operator was itself audited line by line). Two corrections made to the
# CODE (smoothed percentile + flagged Sortino/PnL contradiction, cf. above),
# one external claim verified and REFUTED, one real blind spot documented:
#
# - Diversification -- the AXIS is named "diversification" but does NOT
#   MEASURE portfolio width/dispersion (Herfindahl/entropy-style): `D =
#   diversification_profitable_tokens / diversification_total_tokens` is
#   actually a PER-TOKEN SUCCESS RATE (how many distinct tokens end up net
#   positive), an axis closer to a second win_rate than a dispersion
#   measure. Verified consequence: a wallet trading a SINGLE profitable
#   token gets D=1 (perfect score) -- a wallet trading 20 of which 15 are
#   profitable gets D=0.75 (lower), even though it is objectively MORE
#   diversified. The name therefore literally pushes toward extreme
#   concentration rather than the spread it's supposed to reward. Verified
#   nuance: `_suspect_positive_flag` (layer 3, distinct from the
#   percentile/composite) ALREADY requires
#   `diversification_total_tokens >= WEIGHTS.suspect_diversification_min_tokens`
#   before counting this axis as "suspect" -- a guardrail therefore exists
#   against this specific gaming, but ONLY for the "suspect positive" flag,
#   never for the `percentile_diversification`/`composite_percentile` axis
#   itself, which remains with no token-count floor at all. Not fixed
#   (renaming the axis or adding a floor to it changes the very meaning of
#   the metric displayed since this project began -- a methodology
#   decision, not a point threshold adjustment).
# - Equation completeness -- clarification (not a bug): `diversification_
#   capital_weighted_ratio` (#163) is NOT combined with the count-based ratio
#   above into a single weighted formula -- the two remain two SEPARATE
#   fields (same "axes never merged" doctrine as the rest of this module);
#   only the COUNT ratio feeds into `percentile_diversification`/
#   `composite_percentile`, the capital-weighted variant remains a
#   DISPLAY-ONLY diagnostic (`_format_card_for_prompt`), never used in the
#   percentile calculation.
# - REFUTED after verification (external review): the claim that a "linear"
#   raw PnL would crush every other wallet's percentile toward 0 as soon as
#   one wallet has an outsized PnL. Verified against `_percentile`: it is a
#   RANK percentile (counts other wallets strictly below / population),
#   never a min-max normalization nor a calculation on the raw magnitude --
#   a single $10M outlier changes NOTHING about other wallets' percentiles
#   (it only counts for its own rank, at the top). This class of distortion
#   ("one extreme crushes everything else") would apply to a value-based
#   average/normalization, not a rank percentile -- not applicable here.
# - Gas fees never deducted from PnL (external review) -- verified real, not
#   already handled elsewhere: `ClosedTrade.pnl_usd` subtracts no
#   transaction cost (`qty * (sell_price - buy_price)` alone); no gas data
#   (gas_used/gas_price per leg) is even fetched in this module. A wallet
#   that accumulates many micro-trades winning IN PERCENTAGE but whose every
#   swap costs more in gas than the gain itself would therefore be presented
#   as performant while actually being gas-negative. Not fixed: would
#   require an extra network call per transaction (transaction receipt,
#   gas_used * gas_price) for EVERY FIFO leg -- a new data type never
#   fetched here, significant network cost on an active wallet -- separate
#   project if ever undertaken, not a point fix.
# ============================================================================
#
# TENTH PASS (15/07, external review -- 2 batches). One real bug fixed,
# three false alarms verified and REFUTED, two nuances documented:
#
# - **Transfer history truncated with no signal (FIXED)**: `client.
#   get_token_transfers(wallet, limit=2000, max_pages=10, ...)` can stop
#   pagination while Blockscout STILL had data (`next_page_params` present)
#   -- a very active wallet (more than 2000 lifetime ERC-20 transfers) had
#   its oldest transfers silently missing, risking bias on ALL axes
#   (W/PnL/S/D) and the percentile, not just `unmatched_sell_events`
#   (already documented above, but which doesn't say WHETHER the history
#   itself was complete). `TokenTransfersResult.truncated` (new field,
#   default `False`, backward-compatible) now distinguishes "history
#   genuinely exhausted" (no `next_page_params`) from "stopped before the
#   end" (network error/malformed response mid-pagination, OR the
#   max_pages/limit cap reached while data still remained) --
#   `card.transfer_history_truncated` displays it as a WARNING next to the
#   rest.
# - **REFUTED (external review) -- "trim evasion via unit
#   desynchronization"**: the claim that the anti-luck trim (sorted in $)
#   would let an extreme-%-return micro-trade through, which would then
#   "contaminate" the Sortino. Verified against the code: `_robust_pnl_check`
#   (the trim) and `card.sortino` are two INDEPENDENT calculations on the
#   SAME list of closed trades -- the trim never filters the trades used for
#   Sortino/win_rate/PnL, it's a SEPARATE robustness verdict
#   (`robust_pnl_positive`), never a pre-filter. There is therefore nothing
#   "let through" by the trim toward the Sortino -- Sortino ALWAYS sees 100%
#   of trades, trim or not. **The real substance behind this critique
#   remains valid, though**: a dust trade (e.g. $0.10 buy, $10 sell, +9900%
#   return, +$9.90 PnL) can on its own dominate mean(return_i) and therefore
#   the Sortino -- same family as "Sortino never weighted by size" already
#   documented (ChatGPT review/#178), this dust/airdrop-like sub-case is an
#   additional concrete example, not a 3rd mechanism.
# - **REFUTED -- division by zero on `return_i` if `buy_price<=0`**: already
#   guarded. `ClosedTrade.return_pct` explicitly returns `None` if
#   `buy_price <= 0`, BEFORE any division -- never a crash nor an infinity.
#   A token received for free (buy_price=0, e.g. airdrop) and resold
#   produces a correct positive `pnl_usd` (`qty * sell_price`, the entire
#   sale proceeds are a real profit) but a `return_pct=None` -- excluded
#   from the Sortino calculation, never an outlier value sneaking in.
# - **REFUTED -- percentile division by zero on an empty population**:
#   already doubly guarded. `_apply_comparative_ranking` returns early if
#   `others` is empty (`if not others: return`), AND `_percentile` itself
#   re-checks `if value is None or not population: return None` -- no path
#   reaches the division. Behavior documented and LOCKED by a dedicated
#   test (`test_first_wallet_ever_scored_has_no_comparison_population`) --
#   not just a design coincidence.
# - Documented (minor nuance, not a bug): the tie smoothing (#178) assumes
#   ties are the EXCEPTION -- on a population with very rounded or discrete
#   values (e.g. many wallets with win_rate exactly 0.5), ties can become
#   the NORM, making the percentile less discriminating (still correct,
#   just less granular). A statistical property inherent to average rank on
#   a small population/discrete values -- not a code defect, no simple
#   better alternative without fundamentally changing the ranking method.
# ============================================================================

# All tunable weights/thresholds for this project live in
# wallet_scoring_weights.py (isolated at the operator's request, 14/07 --
# provisional status, cf. this module's docstring for the pending decision
# on its final location). No numeric value hardcoded here: always via
# WEIGHTS.<champ>.




# Bounded funding-source lookup, kept for aria_core.skills.sybil_cluster's
# injectable default (_funding_source) -- the wallet-scoring machinery that
# used to own this constant (wallet_scoring_weights.WEIGHTS) was removed
# 25/08 (operator decision, entire wallet-scoring mechanism retired).
_FUNDING_SOURCE_MAX_PAGES = 5

async def _funding_source(client: BlockscoutClient, wallet: str) -> tuple[str | None, bool]:
    """First native entry found in the wallet's bounded history -- a BOUND,
    never a guarantee it's the real first transaction (Blockscout doesn't
    offer a cheap "oldest first" sort, verified live). Returns
    (source or None, history_truncated)."""
    result = await client.get_transactions_bounded(wallet, max_pages=_FUNDING_SOURCE_MAX_PAGES)
    if not result.available:
        return None, False
    wallet_l = wallet.lower()
    dated = [
        (t, ts)
        for t in result.transactions
        if (t.to_address or "").lower() == wallet_l
        and (t.value_native or 0) > 0
        and (ts := _parse_timestamp(t.timestamp)) is not None
    ]
    if not dated:
        return None, result.truncated
    earliest_t, _ = min(dated, key=lambda pair: pair[1])
    source = (earliest_t.from_address or "").lower()
    return (source or None), result.truncated


