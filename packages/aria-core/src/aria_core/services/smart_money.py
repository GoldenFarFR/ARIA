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

import json
import logging
import math
import os
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import fmean

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.services.blockscout import (
    UNAVAILABLE,
    AddressInfo,
    BlockscoutClient,
    TokenHoldersResult,
    TokenTransfer,
)
from aria_core.services.wallet_scoring_weights import WEIGHTS

logger = logging.getLogger(__name__)

_MAX_WALLETS_DEFAULT = 8
_EARLY_ENTRY_WINDOW_SECONDS = 3 * 24 * 3600  # 3 days after pair creation
_LARGEST_BUY_SHARE_MAX = 0.7  # above this, the entry is judged "massive", not "controlled"
_WASH_TRADING_COUNTERPARTY_SHARE = 0.6
_MIN_TRANSFERS_FOR_WASH_CHECK = 3
_ZERO_ADDRESS = "0x" + "0" * 40

# Quality-first signal (22/07, explicit operator decision after a verified
# numeric example: "2 wallets with a high score" must dominate "10 wallets
# with a low score", never the reverse) -- replaces the old flat bonus (+8 as
# soon as 2 wallets converge, identical for 2 or 8 wallets). The multi-wallet
# gate (>=2) remains a binary ENTRY gate (unchanged doctrine: a single
# convergent wallet never proves anything, cf.
# `test_single_convergent_wallet_not_enough_concentration`) -- once that gate
# is cleared, the signal's MAGNITUDE depends on quality (real
# composite_percentile if known, cf. `latest_score_for_wallet`) and the number
# of qualified wallets, never a single flat bonus.
_CONVERGENCE_BONUS_PER_WALLET = 3.0
_CONVERGENCE_BONUS_MAX_WALLETS = 3  # bonus cap = 3 * 3 = 9 points max
# Fallback score for a wallet with NO known composite_percentile (never scored
# by the wallet-scoring project) but judged convergent by the existing
# lightweight judgment (`is_smart_candidate`, behavior observed on THIS
# specific token) -- deliberately modest: it must never compete with a real
# high composite score (e.g. 90+), only let the signal work before
# `wallet_score_log` is well populated (progressive coverage, cf. CLAUDE.md).
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
    },
}

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
        # Quality-first (22/07): this wallet's already-known GLOBAL score
        # (composite_percentile, independent of this specific token) takes
        # priority over the simple boolean judgment on THIS token -- a good
        # historical trader who just entered this candidate is a richer
        # signal than a "convergent yes/no" judgment limited to this one
        # token. Modest fallback if never scored elsewhere (partial coverage
        # of the wallet-scoring project).
        known_score = await latest_score_for_wallet(wallet)
        qualified_scores.append(known_score if known_score is not None else _FALLBACK_QUALIFIED_SCORE)

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


def wallet_scoring_enabled() -> bool:
    return os.environ.get("ARIA_WALLET_SCORING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# Same convention as pump_dump_autopsy.py (DB_PATH bound once at import) --
# tests isolate by monkeypatching `smart_money.DB_PATH` (cf.
# test_aria_track_record.py for the same pattern on screened_pool.DB_PATH).
DB_PATH = str(aria_db_path())


@dataclass
class ClosedTrade:
    """A buy->sell leg matched in FIFO, valued in USD on each edge via
    GeckoTerminal. Never built without a price on both sides (cf.
    `_fifo_match` -- a leg with no available price is counted separately,
    not valued at zero)."""

    token_address: str
    buy_ts: datetime
    sell_ts: datetime
    token_amount: float
    buy_price: float
    sell_price: float
    # Cost-basis confidence (15/07, Gemini review): ``True`` if THIS specific
    # edge was valued by an exact execution ratio (tx_hash + stablecoin leg in
    # the same transaction), ``False`` if it fell back to the OHLCV market
    # price -- never the reverse of a judgment on the trade's quality itself,
    # only the CONFIDENCE in the price used to compute it (cf.
    # ``price_confirmation_ratio`` on ``WalletScoreCard``).
    buy_price_exact: bool = False
    sell_price_exact: bool = False

    @property
    def pnl_usd(self) -> float:
        return self.token_amount * (self.sell_price - self.buy_price)

    @property
    def return_pct(self) -> float | None:
        if self.buy_price <= 0:
            return None
        return (self.sell_price - self.buy_price) / self.buy_price


@dataclass
class _TokenFIFOResult:
    token_address: str
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    unpriced_legs: int = 0
    open_position_amount: float = 0.0
    # Sells whose FIFO buy queue ran out before being fully consumed (15/07,
    # Gemini review): a possible signal of a rebase/DeFi yield (stETH,
    # aTokens -- the balance grows with no matching incoming transfer) or of
    # a buy that predates the fetched transfer window. Never credited as
    # profit (impossible to distinguish the two cases without guessing) --
    # just counted for transparency, cf. `unmatched_sell_events` on
    # `WalletScoreCard`.
    unmatched_sell_events: int = 0


def _fifo_match(
    token_address: str,
    buys: list[tuple[datetime, float, str]],
    sells: list[tuple[datetime, float, str]],
    price_lookup,
    *,
    exact_hashes: frozenset[str] = frozenset(),
) -> _TokenFIFOResult:
    """Strict FIFO: each sell consumes the oldest buys first.
    ``price_lookup(ts, tx_hash) -> float | None`` -- a leg with no available
    price on EITHER side (buy AND sell) is counted in ``unpriced_legs``,
    never valued at zero nor silently ignored (facts-only doctrine).
    ``buys``/``sells`` carry the original ``tx_hash`` of each leg (14/07,
    exact-hash pricing) -- stays synchronous: resolving a price by hash (a
    network call) is done UPSTREAM by the caller, which supplies an
    already-resolved ``price_lookup`` (dict/closure), never inside this
    function. ``exact_hashes`` (15/07, additive -- empty default,
    backward-compatible with any existing caller/test): set of tx_hash
    values already resolved by an EXACT execution price (cf. ``hash_prices``
    in ``_analyze_wallet_multi_token``) -- only used to flag
    ``ClosedTrade.buy_price_exact``/``sell_price_exact``, no effect on the
    price actually used (always the one returned by ``price_lookup``)."""
    buy_queue: deque[list] = deque(sorted(([ts, amt, tx_hash] for ts, amt, tx_hash in buys), key=lambda b: b[0]))
    closed: list[ClosedTrade] = []
    unpriced = 0
    unmatched_sell_events = 0

    for sell_ts, sell_amount, sell_hash in sorted(sells, key=lambda s: s[0]):
        remaining = sell_amount
        while remaining > 1e-12 and buy_queue:
            buy_ts, buy_amount, buy_hash = buy_queue[0]
            matched = min(remaining, buy_amount)
            buy_price = price_lookup(buy_ts, buy_hash)
            sell_price = price_lookup(sell_ts, sell_hash)
            if buy_price is None or sell_price is None:
                unpriced += 1
            else:
                closed.append(
                    ClosedTrade(
                        token_address=token_address,
                        buy_ts=buy_ts,
                        sell_ts=sell_ts,
                        token_amount=matched,
                        buy_price=buy_price,
                        sell_price=sell_price,
                        buy_price_exact=buy_hash in exact_hashes,
                        sell_price_exact=sell_hash in exact_hashes,
                    )
                )
            buy_queue[0][1] -= matched
            if buy_queue[0][1] <= 1e-12:
                buy_queue.popleft()
            remaining -= matched
        # Sell with no matching pending buy (queue exhausted): ignored -- this
        # cannot be an ARIA-observable trade (the wallet acquired the token
        # before the fetched transfer window, or via a non-transfer mechanism
        # such as a direct mint); not an "unpriced" leg, just out of FIFO
        # scope. Counted (not credited) -- cf. `unmatched_sell_events` above.
        if remaining > 1e-12:
            unmatched_sell_events += 1

    open_amount = sum(amt for _, amt, _ in buy_queue)
    return _TokenFIFOResult(
        token_address=token_address, closed_trades=closed, unpriced_legs=unpriced,
        open_position_amount=open_amount, unmatched_sell_events=unmatched_sell_events,
    )


def _sortino_ratio(returns: list[float]) -> float | None:
    """Sortino-style ratio on per-closed-trade returns. Below
    `WEIGHTS.min_closed_trades_for_sortino`, judged too noisy for an
    individual wallet (cf. research doc #157) -- unavailable, never an
    unreliable number presented as reliable. No loss observed -> ratio
    undefined (not an artificial infinity)."""
    if len(returns) < WEIGHTS.min_closed_trades_for_sortino:
        return None
    downside = [r for r in returns if r < 0]
    if not downside:
        return None
    downside_deviation = math.sqrt(fmean([r * r for r in downside]))
    if downside_deviation == 0:
        return None
    return fmean(returns) / downside_deviation


def _max_drawdown_pct(closed_trades: list[ClosedTrade]) -> float | None:
    """Drawdown applied to the wallet's own realized CUMULATIVE value (not to
    the market) -- peak cumulative PnL reached vs. worst retracement since
    that peak, trades sorted chronologically by sell date."""
    if not closed_trades:
        return None
    ordered = sorted(closed_trades, key=lambda t: t.sell_ts)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in ordered:
        cumulative += t.pnl_usd
        peak = max(peak, cumulative)
        if peak > 0:
            max_dd = max(max_dd, (peak - cumulative) / peak)
    return max_dd


def _avg_holding_period_days(closed_trades: list[ClosedTrade]) -> float | None:
    """Average holding period (buy -> sell) in days, on closed trades -- a
    conviction-vs-quick-rotation signal, methodology sourced (external
    research 15/07: "an increasing share of coins held over longer
    durations... indicates strong conviction", cf. docs/aria-learning-inbox).
    No extra network call: buy_ts/sell_ts are already in each ``ClosedTrade``,
    this calculation is free."""
    if not closed_trades:
        return None
    days = [(t.sell_ts - t.buy_ts).total_seconds() / 86_400 for t in closed_trades]
    return fmean(days)


def _wallet_age_days(all_flat_transfers: list[TokenTransfer]) -> float | None:
    """Wallet age -- from the FIRST observed transfer (within the fetched
    window, cf. Blockscout pagination limits) to NOW. A wallet inactive for
    a while remains "old" (age measures how long it has existed/traded, not
    how long it has been active)."""
    timestamps = [ts for t in all_flat_transfers if (ts := _parse_timestamp(t.timestamp)) is not None]
    if not timestamps:
        return None
    return (datetime.now(timezone.utc) - min(timestamps)).total_seconds() / 86_400


def _count_total_swaps(all_flat_transfers: list[TokenTransfer], wallet: str) -> int:
    """Total number of transfers touching the wallet (buy OR sell) within
    the fetched window -- a raw activity measure, distinct from the number
    of CLOSED trades (which requires a buy AND a sell matched in FIFO).

    Excludes ETH<->WETH wrap/unwrap legs (15/07, Gemini review, cf.
    `_is_wrap_unwrap_leg`) and stable<->stable swaps (15/07, Gemini review
    follow-up, cf. `_is_stable_to_stable_peg_swap`) -- otherwise a repeated
    wrapping/peg-swapping script would unlock WEIGHTS.min_total_swaps
    without ever having taken on real trading risk."""
    wallet_l = wallet.lower()
    by_tx: dict[str, list[TokenTransfer]] = {}
    touching: list[TokenTransfer] = []
    for t in all_flat_transfers:
        if (t.to_address or "").lower() == wallet_l or (t.from_address or "").lower() == wallet_l:
            touching.append(t)
            by_tx.setdefault(t.tx_hash, []).append(t)

    return sum(
        1 for t in touching
        if not _is_wrap_unwrap_leg(t) and not _is_stable_to_stable_peg_swap(t.tx_hash, by_tx)
    )


def _robust_pnl_check(closed_trades: list[ClosedTrade], *, trim_pct: float, min_required: int) -> bool | None:
    """Anti-luck robustness (15/07, operator decision; fixed the same day
    after cross external review Gemini/ChatGPT/Grok): removes a PERCENTAGE
    (``trim_pct`` from each end, not a fixed count) of the BEST and WORST
    trades by PnL, then checks whether the remaining PnL stays positive. A
    fixed count dilutes as the sample grows (10 trades out of 30 = 33%
    removed, but only 0.05% on 20,000 trades) -- a percentage scales with N
    and prevents drowning a single "lucky" trade behind enough insignificant
    micro-trades to push it out of the absolute top-N removed. Only applies
    if the wallet has at least ``min_required`` closed trades (otherwise the
    removal would empty or unbalance an already-small sample) -- an explicit
    ``None`` rather than a number on a non-significant remainder."""
    if len(closed_trades) < min_required:
        return None
    ordered = sorted(closed_trades, key=lambda t: t.pnl_usd)
    trim_count = max(1, round(len(ordered) * trim_pct)) if trim_pct > 0 else 0
    if trim_count * 2 >= len(ordered):
        return None  # removal would empty or invert the sample -- never return a result on that
    trimmed = ordered[trim_count:-trim_count] if trim_count > 0 else ordered
    if not trimmed:
        return None
    return sum(t.pnl_usd for t in trimmed) > 0


def _health_trend(
    closed_trades: list[ClosedTrade], *, min_required: int, stable_band_pct: float,
) -> str | None:
    """Health curve over time (15/07): compares the average PnL per trade of
    the CHRONOLOGICALLY second half (sorted by sell date) to the first --
    "amélioration" [improvement] (clearly better), "dégradation"
    [degradation] (clearly worse), or "stable" (gap below
    ``stable_band_pct`` -- never presenting noise as a signal). ``None``
    below ``min_required`` trades (signal judged too noisy on a small
    sample, same doctrine as Sortino). NOTE: the three return values below
    stay in French verbatim -- they are compared with ``==`` by
    `test_smart_money_wallet_scoring.py` and displayed as-is in Telegram/LLM
    output."""
    if len(closed_trades) < min_required:
        return None
    ordered = sorted(closed_trades, key=lambda t: t.sell_ts)
    mid = len(ordered) // 2
    first_half, second_half = ordered[:mid], ordered[mid:]
    if not first_half or not second_half:
        return None
    first_avg = fmean(t.pnl_usd for t in first_half)
    second_avg = fmean(t.pnl_usd for t in second_half)
    reference = max(abs(first_avg), abs(second_avg), 1e-9)
    delta = (second_avg - first_avg) / reference
    if delta > stable_band_pct:
        return "amélioration"
    if delta < -stable_band_pct:
        return "dégradation"
    return "stable"


def _recent_window_metrics(
    closed_trades: list[ClosedTrade], *, window_days: int,
) -> tuple[float | None, float | None, int]:
    """Time bias (15/07, ChatGPT review): a wallet excellent for 3 years then
    degraded for the last 6 months keeps excellent historical
    win_rate/Sortino/PnL for a very long time -- the health curve
    (`_health_trend`) helps (2nd half vs. 1st) but does NOT fix the main
    score, which is still computed on the entire history. Computes
    win_rate/realized PnL on ONLY the trades closed (sold) in the last
    ``window_days`` days -- as a COMPLEMENT, never a replacement for the
    full historical metrics (same cumulative_trades, just a recent subset).
    Returns ``(None, None, 0)`` if no trade closed within the window --
    never a number on an empty set."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    recent = [t for t in closed_trades if t.sell_ts >= cutoff]
    if not recent:
        return None, None, 0
    wins = sum(1 for t in recent if t.pnl_usd > 0)
    return wins / len(recent), sum(t.pnl_usd for t in recent), len(recent)


def _group_transfers_by_token(transfers: list[TokenTransfer], *, chain: str = "base") -> dict[str, list[TokenTransfer]]:
    """Composite key ``"{chain}:{address}"`` (#157 multi-chain, 14/07) --
    never the address alone, so that two tokens with the same hex address on
    two different EVM chains (independent address spaces) are never merged
    by mistake. Historical behavior unchanged for any single-chain caller
    (``chain="base"`` by default)."""
    grouped: dict[str, list[TokenTransfer]] = {}
    for t in transfers:
        addr = (t.token_address or "").lower()
        if not addr:
            continue
        grouped.setdefault(f"{chain}:{addr}", []).append(t)
    return grouped


def _build_dex_infrastructure_exclusions(
    grouped: dict[str, list[TokenTransfer]], wallet: str,
) -> set[str]:
    """#157, fix 14/07 -- real bug found on review: generalizing
    `_dominant_counterparty_share` to ALL of the wallet's tokens while
    excluding only a SINGLE pool (as the existing token-centric path does)
    creates an almost systematic false positive. A wallet active on
    `WEIGHTS.max_tokens_analyzed` distinct tokens goes through SEVERAL
    different DEX pools/routers (Base has several active DEXes -- Uniswap
    V3, Aerodrome, etc.); if the generalized calculation only excludes a
    single address, a router/pool that frequently comes back as a
    counterparty would wrongly disqualify most normal active traders.

    Heuristic chosen, deliberately general (requires NO hardcoded
    router/pool address, so it automatically adapts to any DEX
    infrastructure present or future on Base): a counterparty that recurs
    across at least `WEIGHTS.wash_trading_infra_min_distinct_tokens`
    DISTINCT tokens is structurally an infrastructure building block (pool
    or router -- both are mechanically shared across many pairs by
    construction), NOT a wash-trading partner (typically tied to a SINGLE
    token/coordinated scheme). Complements (does not replace) the
    per-token resolved-pool exclusion -- cf. `_analyze_wallet_multi_token` /
    `resolve_primary_pool`, which additionally covers the case of a wallet
    whose history still only covers a single token (not enough recurrence
    for this heuristic to trigger on its own).
    """
    wallet_l = wallet.lower()
    tokens_per_counterparty: dict[str, set[str]] = {}
    for token_addr, transfers in grouped.items():
        for t in transfers:
            other = t.to_address if (t.from_address or "").lower() == wallet_l else t.from_address
            other = (other or "").lower()
            if not other or other == wallet_l:
                continue
            tokens_per_counterparty.setdefault(other, set()).add(token_addr)
    return {
        addr
        for addr, tokens in tokens_per_counterparty.items()
        if len(tokens) >= WEIGHTS.wash_trading_infra_min_distinct_tokens
    }


_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _select_tokens_for_deep_analysis(
    grouped: dict[str, list[TokenTransfer]], *, wallet: str = "", cap: int = WEIGHTS.max_tokens_analyzed,
) -> tuple[list[str], int, int]:
    """Sorts by (buy+sell round-trip present, recency of last transfer,
    trade count) descending -- caps at ``cap`` tokens analyzed in depth
    (operator decision, #157). Returns (selected addresses, total number of
    distinct tokens found, number skipped by the cap) -- the caller MUST
    explicitly log if the 3rd element is > 0, never a silent truncation.

    ``wallet`` (15/07, real fix): "recency alone" priority systematically
    biased the sample toward STILL-OPEN positions on very active wallets
    (the most recent token is, by construction, more often a buy not yet
    resold) -- a buy+sell round-trip can NEVER form on an open position, so
    the `cap` token limit sometimes filled up entirely with unclosable
    positions, leaving win rate/PnL/Sortino "unavailable" even on a very
    active wallet with real closed trades elsewhere in its history. Tokens
    with a confirmed round-trip (at least one incoming AND one outgoing
    transfer) now come first; recency/frequency only breaks ties within
    each group. ``wallet=""`` (default) preserves historical behavior (no
    round-trip ever detected, pure recency sort) -- backward-compatible for
    any caller that doesn't know the wallet.
    """
    wallet_l = wallet.lower()

    def _has_round_trip(token_transfers: list[TokenTransfer]) -> bool:
        if not wallet_l:
            return False
        has_buy = any((t.to_address or "").lower() == wallet_l for t in token_transfers)
        has_sell = any((t.from_address or "").lower() == wallet_l for t in token_transfers)
        return has_buy and has_sell

    def _sort_key(item: tuple[str, list[TokenTransfer]]):
        _addr, token_transfers = item
        timestamps = [ts for t in token_transfers if (ts := _parse_timestamp(t.timestamp)) is not None]
        latest = max(timestamps) if timestamps else _EPOCH_UTC
        return (_has_round_trip(token_transfers), latest, len(token_transfers))

    ranked = sorted(grouped.items(), key=_sort_key, reverse=True)
    selected = [addr for addr, _ in ranked[:cap]]
    total = len(grouped)
    skipped = max(0, total - cap)
    return selected, total, skipped


def _is_informed_entry(ohlcv, entry_ts: datetime) -> bool:
    """Qualifies an early entry as "informed" (low volume + chart pattern
    just before the buy) vs "quick/FOMO" (no particular technical signal) --
    a refinement requested by the operator, reuses `ta_levels`/
    `candlestick_patterns` as-is, no new detection heuristic."""
    from aria_core.skills import candlestick_patterns

    entry_epoch = int(entry_ts.timestamp())
    window = [c for c in ohlcv.candles if c.ts <= entry_epoch]
    if len(window) < 3:
        return False
    window = window[-WEIGHTS.technical_entry_lookback_candles:]
    entry_candle = window[-1]
    prior = window[:-1]
    avg_prior_volume = fmean([c.volume for c in prior]) if prior else 0.0
    low_volume = avg_prior_volume > 0 and entry_candle.volume < avg_prior_volume
    patterns = candlestick_patterns.detect_patterns(window)
    pattern_just_before = any(p.index >= len(window) - 2 for p in patterns)
    return low_volume and pattern_just_before


@dataclass
class _MultiTokenResult:
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    unpriced_legs: int = 0
    early_entry_tokens: list[str] = field(default_factory=list)
    informed_entry_tokens: list[str] = field(default_factory=list)
    pool_lookup_errors: int = 0
    gecko_dexscreener_gap_tokens: list[str] = field(default_factory=list)
    cmc_recovered_tokens: list[str] = field(default_factory=list)
    resolved_pool_addresses: set[str] = field(default_factory=set)
    thin_liquidity_tokens: list[str] = field(default_factory=list)  # 15/07, Gemini review -- anti-dust/scam-pool defense
    unmatched_sell_events: int = 0  # 15/07, Gemini review -- rebasing transparency, cf. `_TokenFIFOResult`
    # Freezing of transient errors (15/07, Gemini review -- layer 2/3 blind
    # spot): composite key ("{chain}:{address}") of tokens whose GeckoTerminal
    # pool resolution failed this pass for an INFRASTRUCTURE cause
    # (timeout/429/server error -- already retried several times by
    # `_get_json` before giving up) rather than a DATA verdict ("no pool
    # found for this token", legitimate). Used by `score_wallets` to NEVER
    # mark such a token as definitively "scanned" in the incremental
    # checkpoint -- otherwise a one-off network outage freezes into a
    # permanent scar on the wallet's score (the incremental scan only
    # retries a token already seen if its on-chain activity has changed,
    # never on the simple resolution of an API error).
    transient_pricing_error_tokens: set[str] = field(default_factory=set)


async def _hash_based_price(
    client: BlockscoutClient | None,
    tx_hash: str,
    token_address: str,
    wallet: str,
    *,
    chain: str,
) -> float | None:
    """USD price of a leg deduced from the ratio actually executed within ITS
    OWN transaction (14/07, complement to ``resolve_primary_pool``+
    ``get_ohlcv``+``price_at``) -- execution truth, not a candle
    approximation at a rounded timestamp. Method: ratio between the amount
    of the targeted token and a stablecoin amount, both on a leg of the SAME
    transaction that directly touches ``wallet`` (``from``/``to``) -- not a
    decoding of the raw ``Swap`` log (cf. [VPS Secondaire] report 14/07: the
    Task A feasibility proof already used this transfer ratio, not the raw
    log amounts).

    Falls back to ``None`` (never an exception) in every case where the
    price cannot be established WITHOUT guessing:
    - chain with no known stablecoin registry (cf.
      ``_STABLECOIN_ADDRESSES_BY_CHAIN``, Base only for this project) or no
      Blockscout client;
    - transaction unavailable (timeout/429/error -- already handled by
      ``_get_json``);
    - no leg of the targeted token touching the wallet in this tx (swap
      routed via an aggregator/smart-account that redirects the output
      elsewhere -- a real pattern observed 14/07 on a test wallet, NOT a
      rare marginal case);
    - no stablecoin leg touching the wallet in this tx (non-stable
      token<->token swap, or non-stable multi-hop output) -- an expected
      fallback for the majority of legs, not an error case;
    - SEVERAL legs of the targeted token OR several stablecoin legs touching
      the wallet in the same tx (ambiguous composite/batch tx) -- never an
      arbitrary choice (same doctrine as ``_fifo_match``: never valued at
      zero nor guessed);
    - zero/negative token amount (division guard).
    """
    stables = _STABLECOIN_ADDRESSES_BY_CHAIN.get(chain)
    if not stables or client is None or not tx_hash:
        return None

    result = await client.get_transaction_token_transfers(tx_hash)
    if not result.available:
        return None

    wallet_l = wallet.lower()
    token_l = token_address.lower()
    token_amount: float | None = None
    stable_amount: float | None = None
    for t in result.transfers:
        if wallet_l not in ((t.from_address or "").lower(), (t.to_address or "").lower()):
            continue
        if not t.amount:
            continue
        addr = (t.token_address or "").lower()
        if addr == token_l:
            if token_amount is not None:
                return None  # ambiguous token leg -- never guess which one
            token_amount = t.amount
        elif addr in stables:
            if stable_amount is not None:
                return None  # ambiguous stable leg -- never guess which one
            stable_amount = t.amount

    if not token_amount or token_amount <= 0 or not stable_amount:
        return None
    return stable_amount / token_amount


async def _analyze_wallet_multi_token(
    wallet: str,
    transfers_by_token: dict[str, list[TokenTransfer]],
    *,
    gecko,
    chain_clients: dict[str, BlockscoutClient] | None = None,
) -> _MultiTokenResult:
    """``transfers_by_token`` is keyed by a composite key
    ``"{chain}:{address}"`` (cf. ``_group_transfers_by_token``, #157
    multi-chain 14/07) -- never the token address alone, so as to never
    mistakenly merge two tokens with identical addresses on two different
    chains (address spaces independent by EVM construction). ``chain_clients``
    (14/07, exact tx_hash pricing): chain -> Blockscout client registry, used
    to query the right client during the per-token ``_hash_based_price``
    lookup (already known per chain via the composite key); ``None``/an
    incomplete registry for a chain degrades cleanly to pool+OHLCV for all
    its tokens (same policy as no client in ``_hash_based_price``)."""
    from aria_core.services.coinmarketcap import CMC_NETWORK_SLUGS
    from aria_core.services.coinmarketcap import get_ohlcv as _cmc_get_ohlcv
    from aria_core.services.coinmarketcap import resolve_primary_pool as _cmc_resolve_primary_pool
    from aria_core.services.dexscreener import has_any_pair as _dexscreener_has_any_pair
    from aria_core.services.geckoterminal import GECKO_NETWORK_SLUGS
    from aria_core.services.geckoterminal import UNAVAILABLE as _gecko_unavailable

    wallet_l = wallet.lower()
    chain_clients = chain_clients or {}
    result = _MultiTokenResult()

    for composite_key, token_transfers in transfers_by_token.items():
        chain, _, token_addr = composite_key.partition(":")
        network = GECKO_NETWORK_SLUGS.get(chain, "base")

        buys = [
            (ts, t.amount, t.tx_hash)
            for t in token_transfers
            if (t.to_address or "").lower() == wallet_l and t.amount and (ts := _parse_timestamp(t.timestamp)) is not None
        ]
        sells = [
            (ts, t.amount, t.tx_hash)
            for t in token_transfers
            if (t.from_address or "").lower() == wallet_l and t.amount and (ts := _parse_timestamp(t.timestamp)) is not None
        ]
        if not buys:
            continue

        # Resolves the token's REAL pool (not the token contract itself --
        # two different things in an AMM, cf. `resolve_primary_pool`). Serves
        # both OHLCV valuation and multi-token wash-trading exclusion (#157,
        # fix 14/07). ``network`` (#157 multi-chain, 14/07): queries the
        # RIGHT GeckoTerminal chain, never Base hardcoded for a token found
        # on Ethereum/BNB.
        pool_meta = await gecko.resolve_primary_pool(token_addr, network=network)
        # Anti-dust/scam-pool defense (15/07, Gemini review): a resolved pool
        # whose CONFIRMED liquidity is under the floor isn't reliable enough
        # to value a real PnL (trivially manipulable pool -- e.g. a dust
        # token sent by a scammer with "artificial" liquidity on a tiny
        # pool). ``reserve_usd is None`` remains fail-open -- EXPLICITLY
        # VERIFIED POINT (15/07, Gemini review follow-up, objection "what if
        # liquidity is *unknown* right after a scam is deployed, before
        # indexing?"): `GeckoTerminalClient.resolve_primary_pool` (the real
        # client, cf. geckoterminal.py) NEVER returns `None` for a missing
        # reserve -- a `reserve_in_usd` absent from the API response falls
        # back to `0.0` (`float(attrs.get(...) or 0.0)`), which ALREADY
        # fails the floor. The `None` case is therefore only reachable by a
        # test double/an alternative interface that doesn't populate this
        # field -- never by the real production path. The fail-open remains
        # an INTERFACE safety net (backward compat for existing tests), not
        # an active security hole. Locked by
        # `test_missing_reserve_data_defaults_to_zero_not_none`
        # (geckoterminal).
        pool_liquid_enough = pool_meta.available and (
            pool_meta.reserve_usd is None or pool_meta.reserve_usd >= WEIGHTS.min_pool_liquidity_usd_for_pricing
        )
        if pool_meta.available:
            # BARE pool address (never prefixed by chain): compared as-is to
            # raw counterparty addresses in
            # `_hard_disqualifiers`/`_dominant_counterparty_share` -- an
            # accidental collision between chains (independent EVM address
            # spaces, ~2^160) is negligible, not a real risk. Added even if
            # too illiquid for valuation -- still a real DEX infra building
            # block for wash-trading exclusion.
            result.resolved_pool_addresses.add(pool_meta.pool_address.lower())
            # "Rug-pull immunity" paradox (15/07, Gemini review -- real BUG
            # confirmed in fix #160): the CONFIRMED liquidity above is a
            # snapshot taken AT SCAN TIME, not historical. A token bought
            # when the pool had $100k then victim of a rug pull (pool
            # collapsed to $1k at scan time) would have its SELL blocked by
            # the same floor designed to block dust at the BUY -- the real
            # rug-pull loss would then disappear from the statistics instead
            # of being accounted for (the exact opposite of the anti-dust
            # goal). OHLCV is therefore ALWAYS fetched as soon as the pool is
            # resolved; only `pool_liquid_enough` now gates confidence on the
            # BUY side in `_price_lookup` below (never the sell) -- a sell
            # can never be exploited to fabricate a gain (it only reveals a
            # real price, possibly a bad one), so there's nothing to protect
            # on that side.
            # min_useful_candles=1 (#182, 15/07, speed fix): wallet-scoring
            # only ever consumes a single candle via `price_at` (the closest
            # one to a given timestamp) -- the default threshold of 20
            # candles (designed for /vc, which needs enough candles for
            # support/resistance) makes no sense here and costs up to 2 extra
            # GeckoTerminal calls per token for a young/microcap token that
            # doesn't yet have 20 daily candles -- exactly the frequent
            # profile of an active wallet on Base.
            ohlcv = await gecko.get_ohlcv(pool_meta.pool_address, network=network, min_useful_candles=1)
            if not pool_liquid_enough:
                result.thin_liquidity_tokens.append(token_addr)
        else:
            # `pool_liquid_enough` is always False here (`pool_meta.available`
            # is its first factor) -- but that does NOT mean "too little
            # liquidity", it means "GeckoTerminal found NO pool at all", a
            # DIFFERENT case handled separately below (DexScreener/CMC
            # triangulation). If CMC recovers a price, `buy_blocked_thin_
            # liquidity` (computed after this block) must NEVER block buys on
            # that basis -- only a GeckoTerminal pool that WAS RESOLVED but
            # confirmed too thin should block the buy.
            ohlcv = None
            # Freezing transient errors (15/07, Gemini review): `pool_meta.error`
            # already distinguishes, in text, a DATA verdict ("no pool found
            # for this token"/"no plausible pool...", cf. `resolve_primary_pool`)
            # from an INFRASTRUCTURE failure (`_get_json` ALWAYS prefixes the
            # latter with the `UNAVAILABLE` constant -- timeout/429/server
            # error/malformed response, already retried several times before
            # giving up). Only the 2nd category should prevent this token
            # from being marked "scanned" in the incremental checkpoint (cf.
            # `score_wallets`) -- a real "no pool" stays, itself, permanently
            # covered (nothing to retry, the verdict won't change on its own).
            if pool_meta.error is not None and pool_meta.error.startswith(_gecko_unavailable):
                result.transient_pricing_error_tokens.add(composite_key)
            # Triangulation (#157, 14/07): GeckoTerminal didn't resolve a
            # pool -- before concluding "illiquid token", cross-check with
            # DexScreener. `True` = a real gap between the two sources
            # (DexScreener sees a pair that GeckoTerminal misses -- a signal
            # worth digging into, not a wallet defect); `False`/`None` (no
            # confirmed pair, or the check itself unavailable) adds nothing
            # beyond what `pool_lookup_errors` already says.
            if await _dexscreener_has_any_pair(token_addr, chain=chain) is True:
                result.gecko_dexscreener_gap_tokens.append(token_addr)

            # 3rd tier (#157, 14/07): CoinMarketCap attempts its OWN pool
            # resolution, INDEPENDENTLY of the DexScreener result above -- the
            # "gap between sources" diagnostic and the CMC pricing attempt
            # are not the same thing. Even when DexScreener confirms a pair
            # (`True`), it provides no historical price (no OHLCV method on
            # that client) -- CMC is still attempted, otherwise the token
            # stays unpriced even though a pair is confirmed to exist.
            cmc_network = CMC_NETWORK_SLUGS.get(chain, "base")
            cmc_pool = await _cmc_resolve_primary_pool(token_addr, network_slug=cmc_network)
            if cmc_pool.available:
                cmc_ohlcv = await _cmc_get_ohlcv(cmc_pool.pool_address, network_slug=cmc_network)
                if cmc_ohlcv.available and cmc_ohlcv.candles:
                    ohlcv = cmc_ohlcv
                    result.cmc_recovered_tokens.append(token_addr)

        # Exact tx_hash pricing (14/07): attempted for every DISTINCT tx_hash
        # of this token, in chronological order (consistent with the FIFO
        # that follows), capped at WEIGHTS.max_hash_priced_legs_per_token --
        # never an unbounded loop on a very active wallet. Beyond the cap,
        # the remaining legs fall straight back to pool+OHLCV (below), never
        # a silent abandonment of the rest of the token.
        chain_client = chain_clients.get(chain)
        seen_hashes: set[str] = set()
        ordered_hashes: list[str] = []
        for ts, _amt, tx_hash in sorted(buys + sells, key=lambda leg: leg[0]):
            if tx_hash and tx_hash not in seen_hashes:
                seen_hashes.add(tx_hash)
                ordered_hashes.append(tx_hash)

        hash_prices: dict[str, float] = {}
        for tx_hash in ordered_hashes[: WEIGHTS.max_hash_priced_legs_per_token]:
            price = await _hash_based_price(chain_client, tx_hash, token_addr, wallet, chain=chain)
            if price is not None:
                hash_prices[tx_hash] = price

        if (ohlcv is None or not ohlcv.available or not ohlcv.candles) and not hash_prices:
            result.unpriced_legs += len(buys) + len(sells)
        else:
            from aria_core.services.geckoterminal import price_at

            # Asymmetric floor (15/07, Gemini review -- rug-pull immunity):
            # `buy_tx_hashes` identifies the BUY legs -- only those are
            # blocked if the GeckoTerminal pool was RESOLVED but confirmed
            # too illiquid (``pool_meta.available and not
            # pool_liquid_enough`` -- NOT just ``not pool_liquid_enough``,
            # which is also True when GeckoTerminal found NO pool at all, a
            # different case where CMC may have recovered a valid price that
            # must then never be blocked). A SELL leg uses OHLCV even if the
            # pool's current liquidity is below the floor (rug pull confirmed
            # after a legitimate buy) -- this choice remains correct for the
            # case it targets (blocking the sell too would just reintroduce
            # the old rug-pull immunity bug in the other direction).
            # CLARIFICATION (15/07, DeepSeek review -- corrects an
            # overstatement in this comment): this does NOT mean this
            # reading is immune to any manipulation -- a SELL price read on a
            # pool with manipulated liquidity (a one-off pump rather than a
            # dump) can just as easily inflate a realized PnL fictitiously.
            # This is the exact mirror of the dusting vulnerability already
            # documented below (fictitious loss), symmetric on the gain side
            # -- neither one is fixed, see the limitations block.
            buy_tx_hashes = {b_hash for _ts, _amt, b_hash in buys}
            buy_blocked_thin_liquidity = pool_meta.available and not pool_liquid_enough

            def _price_lookup(
                ts, tx_hash, _ohlcv=ohlcv, _hash_prices=hash_prices,
                _buy_hashes=buy_tx_hashes, _blocked=buy_blocked_thin_liquidity,
            ):
                cached = _hash_prices.get(tx_hash)
                if cached is not None:
                    return cached
                if _ohlcv is None or not _ohlcv.available or not _ohlcv.candles:
                    return None
                if tx_hash in _buy_hashes and _blocked:
                    return None
                return price_at(_ohlcv, int(ts.timestamp()))

            fifo = _fifo_match(token_addr, buys, sells, _price_lookup, exact_hashes=frozenset(hash_prices))
            result.closed_trades.extend(fifo.closed_trades)
            result.unpriced_legs += fifo.unpriced_legs
            result.unmatched_sell_events += fifo.unmatched_sell_events

        if pool_meta.available and pool_meta.created_at:
            earliest_buy_ts = min(ts for ts, _amt, _hash in buys)
            # 22/07 -- copy-trading/bot detection (skills/copy_trading_detection.py):
            # records the first-entry timestamp already computed above FOR FREE
            # (zero extra network call). Never blocking for the calling scoring
            # -- a write failure must never fail an otherwise valid smart-money
            # analysis.
            try:
                from aria_core.skills.copy_trading_detection import record_entry

                await record_entry(wallet_l, token_addr, chain, earliest_buy_ts)
            except Exception:  # noqa: BLE001
                pass
            elapsed = (earliest_buy_ts - pool_meta.created_at).total_seconds()
            amounts = [a for _, a, _ in buys]
            largest_share = (max(amounts) / sum(amounts)) if amounts and sum(amounts) > 0 else None
            controlled = largest_share is None or largest_share <= _LARGEST_BUY_SHARE_MAX
            if 0 <= elapsed <= _EARLY_ENTRY_WINDOW_SECONDS and controlled:
                result.early_entry_tokens.append(token_addr)
                if ohlcv is not None and ohlcv.available and ohlcv.candles and _is_informed_entry(ohlcv, earliest_buy_ts):
                    result.informed_entry_tokens.append(token_addr)
        else:
            # DexScreener diagnostic (`gecko_dexscreener_gap_tokens`) and CMC
            # attempt (`cmc_recovered_tokens`) are already handled above, at
            # the moment the GeckoTerminal failure is observed -- this
            # counter stays Gecko-only by construction (counts every token
            # with no resolved Gecko pool, whether or not CMC recovered a
            # price afterwards).
            result.pool_lookup_errors += 1

    return result


async def _funding_source(client: BlockscoutClient, wallet: str) -> tuple[str | None, bool]:
    """First native entry found in the wallet's bounded history -- a BOUND,
    never a guarantee it's the real first transaction (Blockscout doesn't
    offer a cheap "oldest first" sort, verified live). Returns
    (source or None, history_truncated)."""
    result = await client.get_transactions_bounded(wallet, max_pages=WEIGHTS.funding_source_max_pages)
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


def _pairwise_convergence(addresses: list[str], funding_sources: dict[str, str]) -> list[tuple[str, str]]:
    """Wallets submitted TOGETHER that share the same initial funding source
    (deposit-address reuse heuristic, Victor FC 2020) -- a cross-check
    signal, never an automatic disqualifier outside this pairwise context."""
    pairs: list[tuple[str, str]] = []
    for i in range(len(addresses)):
        for j in range(i + 1, len(addresses)):
            a, b = addresses[i].lower(), addresses[j].lower()
            fa, fb = funding_sources.get(a), funding_sources.get(b)
            if fa and fb and fa == fb:
                pairs.append((addresses[i], addresses[j]))
    return pairs


async def _ensure_wallet_scoring_tables() -> None:

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_score_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                scored_at TEXT NOT NULL,
                report_json TEXT NOT NULL
            )
            """
        )
        # Dynamic TVL ranking of scanned chains (#157, 14/07) -- one row per
        # chain (PRIMARY KEY), replaced in bulk on every successful refresh
        # (cf. `refresh_chain_ranking_cache`), never an append-only log like
        # `wallet_score_log` above.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_scoring_chain_ranking (
                chain TEXT PRIMARY KEY,
                tvl_usd REAL NOT NULL,
                rank INTEGER NOT NULL,
                refreshed_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def _log_wallet_score(wallet: str, report_json: str) -> None:
    """Layer 4 (#157) -- pure write, no scoring logic depends on it. Enables
    a future recalibration against ARIA's real track record, not built now."""

    await _ensure_wallet_scoring_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_score_log (wallet, scored_at, report_json) VALUES (?, ?, ?)",
            (wallet.lower(), datetime.now(timezone.utc).isoformat(), report_json),
        )
        await db.commit()


async def _latest_scored_wallets(exclude_wallet: str) -> list[dict]:
    """Latest known record of every OTHER already-scored wallet (`wallet_score_log`,
    layer 4) -- one row per wallet, the most recent one. Comparison base for
    the percentile ranking (15/07): never the wallet against itself.

    Excludes `full_coverage=False` records (15/07, Gemini review -- asymmetric
    percentile pollution): a wallet scanned only once, of which only a few
    priority tokens (recent/profitable, cf. `_select_tokens_for_
    deep_analysis`) were analyzed in depth, produces a temporarily more
    favorable score than a fully-covered wallet -- comparing them on equal
    footing skews the distribution (a moderately active but fully covered
    wallet would be penalized against lucky partial-scan ghosts). A record
    with no `full_coverage` field at all (old format, before #157 follow-up)
    is treated as uncovered -- excluded out of caution, never a data gap
    that sneaks into the comparison.

    Also excludes `price_confidence_low=True` (15/07, ChatGPT review --
    comparability blind spot): a wallet whose cost-basis mostly relies on
    ESTIMATED prices must not serve as a reference to judge another wallet
    whose prices are mostly CONFIRMED -- same doctrine as `full_coverage`,
    symmetric."""
    await _ensure_wallet_scoring_tables()
    exclude_l = exclude_wallet.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                """
                SELECT report_json FROM wallet_score_log w1
                WHERE wallet != ? AND scored_at = (
                    SELECT MAX(scored_at) FROM wallet_score_log w2 WHERE w2.wallet = w1.wallet
                )
                """,
                (exclude_l,),
            )
        ).fetchall()
    parsed: list[dict] = []
    for (report_json,) in rows:
        try:
            entry = json.loads(report_json)
        except (TypeError, ValueError):
            continue  # corrupted row/old format -- skipped, never a ranking crash
        if not entry.get("full_coverage"):
            continue
        if entry.get("price_confidence_low"):
            # Comparability blind spot (15/07, ChatGPT review): a wallet
            # whose cost-basis mostly relies on ESTIMATED prices (not
            # confirmed by exact execution) must not pollute the comparison
            # population of OTHER wallets -- same doctrine as full_coverage
            # above (a record with dubious data quality isn't a reliable
            # reference to judge another wallet).
            continue
        parsed.append(entry)
    return parsed


async def latest_score_for_wallet(wallet: str) -> float | None:
    """Latest known ``composite_percentile`` for THIS specific wallet
    (read-only in ``wallet_score_log``, continuously fed by ``/walletqueue``
    -- no new network computation here, just a local SELECT).

    Distinct from ``_latest_scored_wallets`` (which excludes this wallet to
    build the comparison POPULATION for others) -- here we want the
    opposite: this wallet's OWN score, regardless of whether it served as a
    reference for others. ``None`` if the wallet was never scored (partial
    coverage of the wallet-scoring project, cf. CLAUDE.md) or if its
    ``composite_percentile`` doesn't have a value yet (empty comparison
    population at the time of its scan) -- never an invented value,
    fail-open on unknown (the caller falls back to its own fallback)."""
    await _ensure_wallet_scoring_tables()
    wallet_l = wallet.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT report_json FROM wallet_score_log WHERE wallet = ? "
                "ORDER BY scored_at DESC LIMIT 1",
                (wallet_l,),
            )
        ).fetchone()
    if row is None:
        return None
    try:
        entry = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    percentile = entry.get("composite_percentile")
    return float(percentile) if isinstance(percentile, (int, float)) else None


def _diversification_ratio(entry: dict) -> float | None:
    total = entry.get("diversification_total_tokens")
    profitable = entry.get("diversification_profitable_tokens")
    if not total:
        return None
    return profitable / total


async def _apply_comparative_ranking(card: WalletScoreCard) -> None:
    """Comparative ranking (15/07, operator decision): percentile of THIS
    wallet among all OTHER already-scored wallets, by axis then composite.
    Never a percentile on an empty population -- explicit `None`, not a
    default 50% that would suggest a comparison that never happened.

    `composite_percentile` ONLY averages the performance/skill axes (win
    rate, Sortino, PnL, diversification) -- holding period is a behavioral
    trait (conviction vs. rotation), not an unambiguous "better if higher"
    axis (cf. external research 15/07), so it's displayed separately, never
    folded into the composite average."""
    others = await _latest_scored_wallets(card.address)
    card.compared_against_n_wallets = len(others)
    if not others:
        return

    def _percentile(value: float | None, population: list[float]) -> float | None:
        """AVERAGE rank percentile (15/07, external review -- tie smoothing):
        a wallet whose value was counted only against OTHERS strictly lower
        wrongly placed every wallet tied with the majority at the 0th
        percentile (e.g. many wallets at exactly win_rate=0.5) --
        indistinguishable from a wallet genuinely worse than everyone else.
        Standard statistical convention (average rank percentile, cf.
        `scipy.stats.percentileofscore(kind='mean')`): ties count for half a
        position rather than zero."""
        if value is None or not population:
            return None
        below = sum(1 for p in population if p < value)
        tied = sum(1 for p in population if p == value)
        return round(100.0 * (below + 0.5 * tied) / len(population), 1)

    win_rate_pop = [o["win_rate"] for o in others if o.get("win_rate") is not None]
    sortino_pop = [o["sortino"] for o in others if o.get("sortino") is not None]
    pnl_pop = [o["realized_pnl_usd"] for o in others if o.get("realized_pnl_usd") is not None]
    holding_pop = [o["avg_holding_period_days"] for o in others if o.get("avg_holding_period_days") is not None]
    diversification_pop = [r for o in others if (r := _diversification_ratio(o)) is not None]

    card.percentile_win_rate = _percentile(card.win_rate, win_rate_pop)
    card.percentile_sortino = _percentile(card.sortino, sortino_pop)
    card.percentile_pnl = _percentile(card.realized_pnl_usd, pnl_pop)
    card.percentile_holding_period = _percentile(card.avg_holding_period_days, holding_pop)
    card.percentile_diversification = _percentile(_diversification_ratio(asdict(card)), diversification_pop)

    skill_axes = [
        p for p in (
            card.percentile_win_rate, card.percentile_sortino, card.percentile_pnl, card.percentile_diversification,
        )
        if p is not None
    ]
    card.composite_percentile = round(fmean(skill_axes), 1) if skill_axes else None


# Cap on the dynamic TVL ranking (#157, 14/07, operator decision) --
# inert today (13 confirmed chains in total, all < 20), kept generic in
# case the confirmed list grows later.
_MAX_RANKED_CHAINS = 20

# Fallback if the TVL cache never ran (first deployment) or if
# DefiLlama is unavailable -- never a /walletscore that breaks for lack of
# an up-to-date ranking. "bnb" absent (removed from blockscout.CHAIN_IDS,
# 14/07, Blockscout doesn't serve it).
_FALLBACK_SCAN_CHAINS: tuple[str, ...] = ("base", "ethereum")


async def refresh_chain_ranking_cache() -> bool:
    """Refreshes `wallet_scoring_chain_ranking` from the DefiLlama TVL
    ranking (#157, 14/07) -- called by the monthly heartbeat
    (`wallet_scoring_chain_ranking_refresh`), never by an individual
    `/walletscore` scan. On DefiLlama failure, the table is NEVER cleared --
    the last successful ranking keeps serving until the next successful
    refresh. Returns `True` if the cache was updated, `False` otherwise."""
    from aria_core.services.defillama import fetch_chain_tvl_ranking

    ranking = await fetch_chain_tvl_ranking()
    if ranking is None:
        logger.warning("refresh_chain_ranking_cache: DefiLlama unavailable -- TVL cache unchanged")
        return False

    ranking = ranking[:_MAX_RANKED_CHAINS]
    refreshed_at = datetime.now(timezone.utc).isoformat()

    await _ensure_wallet_scoring_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM wallet_scoring_chain_ranking")
        await db.executemany(
            "INSERT INTO wallet_scoring_chain_ranking (chain, tvl_usd, rank, refreshed_at) VALUES (?, ?, ?, ?)",
            [(chain, tvl, rank, refreshed_at) for rank, (chain, tvl) in enumerate(ranking, start=1)],
        )
        await db.commit()

    logger.info("refresh_chain_ranking_cache: %s chains cached (%s)", len(ranking), refreshed_at)
    return True


# Emergency Base-only restriction (16/07, explicit operator decision) -- the
# 13-chain sweep of DEFAULT_SCAN_CHAINS() (below) was identified as the main
# quantified cause of Blockscout Pro quota exhaustion (100k credits/4h):
# every wallet being caught up redoes the full 13-chain sweep on EVERY pass,
# up to ~14 passes for a very active wallet -- quantified at ~5,460 credits
# for the `get_token_transfers` loop alone for ONE wallet (docs/HANDOFF,
# operator exchange 16/07). Verified before this fix: neither
# `momentum_entry.py` nor `paper_trader.py` consume the wallet-scoring
# multi-chain signal today -- no trading decision depends on it (threshold
# #199 requires ~500 scored wallets before even considering using it).
# Zero real functional loss from cutting this here, fully reversible.
#
# EXPLICIT short-circuit, not via `_MAX_RANKED_CHAINS` (which would give
# chain #1 by DefiLlama TVL -- very likely Ethereum, not Base): the dynamic
# TVL ranking is NOT removed, just never consulted while this flag is
# active. To lift once the multi-chain signal is actually consumed by a
# trading decision (#199, not yet decided) -- flipping
# `_BASE_ONLY_OVERRIDE` back to `False` restores the existing TVL ranking
# without rewriting anything. The plan to retain confirmed-empty chains
# (#157 follow-up, designed 16/07) remains valid as-is for that day, just
# deferred.
_BASE_ONLY_OVERRIDE = True
_BASE_ONLY_CHAINS: tuple[str, ...] = ("base",)


async def DEFAULT_SCAN_CHAINS() -> tuple[str, ...]:
    """Chains scanned by default by `/walletscore` -- reads the cached TVL
    ranking (#157, 14/07), sorted by rank. Falls back to
    `_FALLBACK_SCAN_CHAINS` if the cache is empty (never ran) OR
    inaccessible -- never an exception that breaks a scan for lack of an
    up-to-date ranking.

    Base-only restriction (16/07) at the top of the function: early return
    before any TVL cache read while `_BASE_ONLY_OVERRIDE` is active (see
    comment above)."""
    if _BASE_ONLY_OVERRIDE:
        return _BASE_ONLY_CHAINS
    try:
        await _ensure_wallet_scoring_tables()
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT chain FROM wallet_scoring_chain_ranking ORDER BY rank ASC"
            )
            rows = await cursor.fetchall()
    except Exception:
        logger.warning("DEFAULT_SCAN_CHAINS: TVL cache inaccessible -- falling back to %s", _FALLBACK_SCAN_CHAINS)
        return _FALLBACK_SCAN_CHAINS

    if not rows:
        return _FALLBACK_SCAN_CHAINS
    return tuple(row[0] for row in rows)


@dataclass
class HardDisqualifiers:
    is_contract: bool = False
    wash_trading_suspected: bool = False
    financed_by_known_malicious: bool = False
    financing_check_note: str | None = None  # NON-disqualifying info (e.g. GoPlus check unavailable)
    reasons: list[str] = field(default_factory=list)

    @property
    def disqualified(self) -> bool:
        return self.is_contract or self.wash_trading_suspected or self.financed_by_known_malicious


async def _hard_disqualifiers(
    wallet: str,
    info: AddressInfo,
    all_transfers: list[TokenTransfer],
    funding_source: str | None,
    *,
    extra_exclusions: set[str] | None = None,
    goplus_client=None,
    funding_source_chain: str | None = None,
) -> HardDisqualifiers:
    reasons: list[str] = []

    is_contract = bool(info.is_contract) if info.available else False
    if is_contract:
        reasons.append("Wallet-contrat (équipe/vesting/LP), pas un trader individuel.")

    wash = (
        _dominant_counterparty_share(all_transfers, wallet, lp_address=None, extra_exclusions=extra_exclusions)
        >= _WASH_TRADING_COUNTERPARTY_SHARE
    )
    if wash:
        reasons.append("Wash-trading suspecté (échanges concentrés sur une seule contrepartie, tous tokens confondus).")

    # Funding by a wallet already known to be malicious -- GoPlus Malicious
    # Address API (AML), #157 (14/07). HONEST CAVEAT (research doc from
    # 14/07, verified live, EXTENDED tonight to the 13 chains of the
    # multi-chain scan): the 13 confirmed chain_ids all respond "code: 1, ok"
    # with the same format -- FORMAT coverage confirmed everywhere.
    #
    # Deepened the same night (2nd test, ACTIVE/known addresses this time --
    # WETH predeploy on unichain/soneium/mode, native CELO token, WRBTC on
    # rootstock -- not burn addresses) on celo/rootstock/unichain/soneium/mode:
    # - `contract_address` stays "-1" (undetermined) on all 5 chains EVEN with
    #   a very active, known address -- so this was NOT an artifact of the
    #   burn-address choice in the first test: this specific field simply
    #   never resolves on these 5 chains, whatever the address.
    # - BUT `data_source`/`honeypot_related_address` (the fields actually
    #   tied to security analysis, not `contract_address`) behave
    #   differently depending on the chain: on Unichain/Soneium/Mode,
    #   `data_source` goes from `""` (burn address) to `"GoPlus"` (active
    #   address) and `honeypot_related_address` from `"0"` to `"1"` -- a real
    #   analysis runs on these 3 chains once there's activity to analyze. On
    #   Celo/Rootstock, `data_source` stays `""` even with an active, known
    #   address -- no sign of analysis engaged on these 2 chains, on both
    #   addresses tested tonight (burn and active).
    # - Caveat to keep explicit: only one active address tested per chain
    #   tonight, never an address actually flagged malicious anywhere -- this
    #   documents a coverage HINT (Unichain/Soneium/Mode probably better
    #   covered than Celo/Rootstock), NOT definitive proof of a lack of data
    #   on Celo/Rootstock.
    #
    # Unchanged practical conclusion: an additional probabilistic filter,
    # never presented as exhaustive, regardless of chain -- the real density
    # of malicious-address data probably varies by chain, with a weaker
    # coverage hint on Celo/Rootstock. Strict fail-closed: an unavailable
    # check stays "unavailable" (an informative note, NOT a disqualification,
    # NOR a silent false negative that would say "not malicious" without
    # saying so).
    #
    # `funding_source_chain` (#157, fix 14/07): the chain where
    # `funding_source` was ACTUALLY found (cf. score_wallets) -- never
    # assumed to be Base by default now that the scan covers 13 chains; a
    # funding address can legitimately live on a chain different from the
    # one the wallet trades on. `CHAIN_IDS` (blockscout.py) is the ONLY
    # source of truth for translating a chain name into a GoPlus chain_id --
    # no duplicated registry. Missing/unknown chain: falls back to
    # `get_address_security`'s default (Base), never an invented chain_id.
    financed_by_malicious = False
    financing_check_note: str | None = None
    if funding_source:
        if goplus_client is None:
            from aria_core.services.goplus import goplus_client as _default_goplus_client

            goplus_client = _default_goplus_client
        from aria_core.services.blockscout import CHAIN_IDS

        chain_id = CHAIN_IDS.get(funding_source_chain) if funding_source_chain else None
        if chain_id is not None:
            security = await goplus_client.get_address_security(funding_source, chain_id=str(chain_id))
        else:
            security = await goplus_client.get_address_security(funding_source)
        if security.available:
            financed_by_malicious = security.is_malicious
            if financed_by_malicious:
                reasons.append(
                    f"Financé par un wallet marqué à risque par GoPlus AML ({funding_source}, "
                    f"catégories : {', '.join(sorted(security.flags)) or 'non précisées'})."
                )
        else:
            financing_check_note = (
                f"Vérification GoPlus AML de la source de financement indisponible ({security.error}) "
                "-- disqualifiant non évalué, jamais un faux négatif silencieux."
            )

    return HardDisqualifiers(
        is_contract=is_contract,
        wash_trading_suspected=wash,
        financed_by_known_malicious=financed_by_malicious,
        financing_check_note=financing_check_note,
        reasons=reasons,
    )


@dataclass
class WalletScoreCard:
    address: str
    display_name: str | None = None  # ENS/Basename -- COSMETIC, never read by any score calculation
    available: bool = True
    error: str | None = None

    disqualified: bool = False
    disqualification_reasons: list[str] = field(default_factory=list)
    financing_check_note: str | None = None  # NON-disqualifying info (e.g. GoPlus AML check unavailable)

    tokens_found: int = 0
    tokens_analyzed: int = 0
    tokens_skipped_capped: bool = False
    chains_scanned: list[str] = field(default_factory=list)  # chains where real activity was found (#157, 14/07)
    # 15/07, external review -- history truncated by the Blockscout
    # pagination cap (2000 transfers/10 pages): the API STILL had data
    # beyond what was fetched (never when the history is genuinely
    # exhausted). A very active wallet can therefore be missing its oldest
    # transfers -- risk of bias on ALL axes (W/PnL/S/D) and the percentile,
    # not just `unmatched_sell_events`.
    transfer_history_truncated: bool = False

    # Persistent incremental scan (#157 follow-up, 15/07): `tokens_analyzed`
    # above stays "analyzed THIS pass" -- these two fields give the
    # cumulative view (real wallet coverage across successive calls, cf.
    # wallet_scan_state.py). `full_coverage=True` = every token known as of
    # today has been seen at least once; a future call then only refreshes
    # new activity since the last scan.
    tokens_scanned_cumulative: int = 0
    full_coverage: bool = False
    # Permanent tracking (15/07, #157 follow-up 2): last REAL on-chain
    # activity ever seen (max of observed transfer timestamps), never
    # regressed from one pass to the next -- used to measure genuine
    # inactivity (e.g. wallet silent for 3 months) for `wallet_scan_queue.py`,
    # distinct from the simple date of the last SCAN (which advances even
    # without new activity).
    last_activity_at: datetime | None = None

    closed_trades_count: int = 0
    unpriced_legs: int = 0
    pool_lookup_errors: int = 0  # tokens with no resolved GeckoTerminal pool (#157, 14/07 -- diagnostic)
    gecko_dexscreener_gap_count: int = 0  # among them, DexScreener sees a pair GeckoTerminal missed (#157, 14/07)
    cmc_price_recovery_count: int = 0  # among them, priced via CoinMarketCap after GeckoTerminal failed (#157, 14/07)
    # Anti-dust/scam-pool defense (15/07, Gemini review): tokens whose pool
    # was resolved but whose confirmed liquidity is below
    # WEIGHTS.min_pool_liquidity_usd_for_pricing -- not priced (diagnostic
    # PER PASS, same convention as the two counters above).
    thin_liquidity_pricing_skipped_count: int = 0
    # Sells whose FIFO buy queue ran dry (15/07, Gemini review) -- a possible
    # rebase/DeFi-yield signal never credited as profit, just counted for
    # transparency. Diagnostic PER PASS (not cumulative).
    unmatched_sell_events: int = 0
    # Freezing transient errors (15/07, Gemini review): among this pass's
    # tokens, how many failed for an infrastructure reason (never marked
    # "scanned" -- retried on the next call). Diagnostic PER PASS.
    transient_pricing_errors: int = 0
    win_rate: float | None = None
    realized_pnl_usd: float | None = None
    sortino: float | None = None
    # Sortino/PnL contradiction (15/07, external review -- size-asymmetry
    # bias): `sortino` is computed on `return_i` (return IN %), never
    # weighted by capital committed on the trade -- a wallet can show an
    # "honorable" positive Sortino (average of % returns) while its realized
    # PnL IN DOLLARS is negative (a big loss in $ but small in %, several
    # small % gains on tiny stakes). This flag captures the most flagrant
    # case, reliably verifiable (a SIGN contradiction between the two, never
    # a nuance to interpret) -- it doesn't fix the underlying bias (not
    # weighted by size, cf. the limitations block), it makes its most
    # misleading manifestation visible.
    sortino_pnl_contradiction: bool = False
    max_drawdown_pct: float | None = None
    avg_holding_period_days: float | None = None  # 15/07 -- conviction vs. fast rotation (sourced methodology)

    # Recent window (15/07, ChatGPT review -- temporal bias): IN ADDITION to
    # the full historical metrics above, never in their place.
    win_rate_recent: float | None = None
    realized_pnl_usd_recent: float | None = None
    recent_window_trades_count: int = 0

    # Cost-basis confidence (15/07, Gemini review): share of legs (buy +
    # sell) priced by an EXACT execution price rather than the OHLCV market
    # fallback. Displayed ALONGSIDE the score (never hiding win_rate/PnL),
    # same doctrine as `sample_size_sufficient` -- not a full masking of
    # Sortino/robust_pnl/health_trend.
    price_confirmation_ratio: float | None = None
    price_confidence_low: bool = False

    diversification_profitable_tokens: int = 0
    diversification_total_tokens: int = 0
    # Capital-weighted diversification (15/07, ChatGPT review): share of
    # total deployed capital that ended up in a profitable position --
    # complements (doesn't replace) the counting ratio above, measures
    # capital CONCENTRATION rather than the breadth of independent bets.
    diversification_capital_weighted_ratio: float | None = None

    early_entry_recurrence_count: int = 0
    informed_entry_count: int = 0

    funding_source: str | None = None
    funding_source_truncated: bool = False

    # Cross-token holder (21/07, Blockscout Pro x402 extraction pipeline,
    # `token_holder_intel.py`): on how many tokens ALREADY EXTRACTED by ARIA
    # (partial coverage -- 147 Base tokens as of 21/07, never an exhaustive
    # chain scan) does this wallet appear as a notable holder. POSSIBLE
    # coordination signal (legitimate market maker OR Sybil cluster) -- a
    # different category from trading skill, never mixed into
    # `composite_percentile` (same doctrine as `funding_source`/
    # `convergence_pairs`: informational, never a score).
    cross_token_holdings: list[dict] = field(default_factory=list)
    cross_token_holder_count: int = 0

    # Copy-trading/bot (22/07, skills/copy_trading_detection.py): does it
    # systematically enter 5-15 min after ANOTHER already-scored wallet, on
    # several distinct tokens? Same doctrine as cross_token_holdings above --
    # informational, NEVER mixed into composite_percentile (design validated
    # by the operator 22/07 -- Option 1: the composite stays pure performance).
    copy_trading_flag: str | None = None  # copy_trading_suspected/independent/unknown
    copy_trading_points: list[str] = field(default_factory=list)

    # Minimum sample size + anti-luck robustness + trend over time (15/07,
    # operator decision). All computed on `cumulative_trades` (the full
    # archived history, not just this batch) -- refine themselves over
    # successive scans, same doctrine as the rest of the cumulative score.
    wallet_age_days: float | None = None
    total_swaps: int = 0
    sample_size_sufficient: bool = False  # age >= min_wallet_age_days AND swaps >= min_total_swaps
    robust_pnl_positive: bool | None = None  # None = not enough trades for this test
    health_trend: str | None = None  # "amélioration" / "stable" / "dégradation" / None (not enough trades)

    # Comparative ranking (15/07): percentile of THIS wallet among all
    # already-scored wallets (wallet_score_log), by axis then composite. None
    # as long as there are no other scored wallets to compare against (never
    # an invented percentile on an empty/single-entry population).
    percentile_win_rate: float | None = None
    percentile_sortino: float | None = None
    percentile_pnl: float | None = None
    percentile_diversification: float | None = None
    percentile_holding_period: float | None = None
    composite_percentile: float | None = None
    compared_against_n_wallets: int = 0

    suspect_positive: bool = False
    thesis: str | None = None


@dataclass
class WalletScoringReport:
    wallets: list[WalletScoreCard] = field(default_factory=list)
    convergence_pairs: list[tuple[str, str]] = field(default_factory=list)
    synthesis: str | None = None
    available: bool = True
    error: str | None = None


# Telegram/heartbeat display labels (15/07 follow-up -- factored out of
# telegram_bot.py so `wallet_scan_queue.py` reuses EXACTLY the same text as
# `/walletscore`, never a second, diverging format). Only for chain names
# where plain capitalization gives a misleading/ugly result -- everything
# else derives from blockscout.CHAIN_IDS.keys() via .capitalize(), never a
# 2nd static list of the 13 names to keep in sync.
_CHAIN_LABEL_OVERRIDES = {"zksync": "zkSync Era"}


def chain_display_label(chain: str) -> str:
    return _CHAIN_LABEL_OVERRIDES.get(chain, chain.capitalize())


def format_wallet_score_card_lines(card: WalletScoreCard) -> list[str]:
    """Formats a wallet record for Telegram display -- reused by
    `/walletscore` (immediate analysis) AND `wallet_scan_queue.py`
    (background analysis), never a second diverging text for the same
    content."""
    lines = [f"\n— {card.address}" + (f" ({card.display_name})" if card.display_name else "")]
    if not card.available:
        lines.append(f"  Indisponible : {card.error}")
        return lines
    if card.disqualified:
        lines.append("  🔴 DISQUALIFIÉ : " + "; ".join(card.disqualification_reasons))
    if card.financing_check_note:
        lines.append(f"  ⚠️ {card.financing_check_note}")
    scanned = ", ".join(chain_display_label(c) for c in card.chains_scanned) or "aucune"
    lines.append(f"  Chaînes avec activité trouvée : {scanned}")
    lines.append(
        f"  Tokens analysés cette passe : {card.tokens_analyzed}/{card.tokens_found}"
        + (f" (plafond de {WEIGHTS.max_tokens_analyzed} atteint)" if card.tokens_skipped_capped else "")
    )
    # 15/07, operator observation -- the LLM thesis below already receives the
    # cumulative count (`_format_card_for_prompt`) and mentions it in its prose ("X/Y
    # covered in total"), but the card itself never displayed this cumulative count --
    # two different numbers in the same Telegram message (e.g. "50/806" in the card,
    # "118/806" in the thesis text just below), with no way to know which to trust.
    # Always displayed (not only when capped) so the cumulative count is visible
    # from the very first pass.
    lines.append(
        f"  Couverture cumulée : {card.tokens_scanned_cumulative}/{card.tokens_found}"
        + (" (complète)" if card.full_coverage else "")
    )
    if card.unpriced_legs or card.pool_lookup_errors:
        lines.append(
            f"  Diagnostic prix : {card.unpriced_legs} jambe(s) sans prix, "
            f"{card.pool_lookup_errors} token(s) sans pool GeckoTerminal résolu"
            + (
                f" (dont {card.gecko_dexscreener_gap_count} vu(s) par DexScreener -- écart entre sources)"
                if card.gecko_dexscreener_gap_count
                else ""
            )
        )
    lines.append(f"  Win rate : {card.win_rate:.0%}" if card.win_rate is not None else "  Win rate : indisponible")
    lines.append(
        f"  PnL réalisé : ${card.realized_pnl_usd:,.2f}"
        if card.realized_pnl_usd is not None
        else "  PnL réalisé : indisponible"
    )
    lines.append(
        f"  Sortino : {card.sortino:.2f}" if card.sortino is not None else "  Sortino : indisponible"
    )
    lines.append(f"  Récurrence entrée précoce (multi-lancements) : {card.early_entry_recurrence_count} token(s)")
    if card.cross_token_holder_count > 0:
        tags = sorted({t for h in card.cross_token_holdings for t in (h.get("tags") or [])})
        lines.append(
            f"  🔎 Détenteur croisé : présent parmi les gros holders de {card.cross_token_holder_count} "
            "autre(s) token(s) déjà couvert(s) par ARIA"
            + (f" (labels connus : {', '.join(tags[:4])})" if tags else " (aucun label d'entité connu)")
        )
    if card.suspect_positive:
        lines.append("  🟢 Suspect positif (exceptionnel sur plusieurs axes à la fois) — à surveiller de près.")
    if card.thesis:
        lines.append(f"  Thèse : {card.thesis}")
    return lines


def format_wallet_scoring_report(report: WalletScoringReport) -> str:
    """Full Telegram text for a `score_wallets` report -- same content as
    the synchronous `/walletscore` reply, reusable as-is by the background
    cycle (`wallet_scan_queue.py`)."""
    lines = ["🕵️ Évaluation smart-wallet — confirmation/contexte, JAMAIS un signal de copy-trade."]
    for card in report.wallets:
        lines.extend(format_wallet_score_card_lines(card))
    if report.convergence_pairs:
        lines.append("\n⚠️ Wallets soumis ensemble partageant une source de financement (suspects même entité) :")
        lines.extend(f"  {a} <-> {b}" for a, b in report.convergence_pairs)
    if report.synthesis:
        lines.append(f"\nSynthèse : {report.synthesis}")
    return "\n".join(lines)


def _suspect_positive_flag(card: WalletScoreCard) -> bool:
    """Layer 3 (#157) -- SEPARATE from the composite score, never folded into
    an average. True if the wallet exceeds a static threshold on at least
    `WEIGHTS.suspect_positive_min_axes` independent axes simultaneously.
    Static starting thresholds (no real percentiles until there's an ARIA
    track record -- cf. layer 4), revisable."""
    axes = 0
    if card.win_rate is not None and card.win_rate >= WEIGHTS.suspect_win_rate_min:
        axes += 1
    if card.sortino is not None and card.sortino >= WEIGHTS.suspect_sortino_min:
        axes += 1
    if (
        card.diversification_total_tokens >= WEIGHTS.suspect_diversification_min_tokens
        and card.diversification_profitable_tokens / card.diversification_total_tokens
        >= WEIGHTS.suspect_diversification_ratio_min
    ):
        axes += 1
    if card.early_entry_recurrence_count >= WEIGHTS.suspect_recurrence_min:
        axes += 1
    return axes >= WEIGHTS.suspect_positive_min_axes


_WALLET_THESIS_SYSTEM = (
    "Tu es ARIA. On te montre un ou plusieurs wallets déjà notés par un pipeline "
    "déterministe (FIFO PnL, Sortino, drawdown, durée moyenne de détention, "
    "récurrence d'entrée précoce -- "
    "AUCUN chiffre ci-dessous n'est de toi, tu synthétises, tu n'en inventes jamais "
    "un nouveau). Si une donnée est marquée indisponible, dis-le explicitement, ne "
    "la comble jamais. Rappel absolu : ce score sert de confirmation/contexte, "
    "jamais un signal de copy-trade -- ne recommande jamais d'imiter ces wallets. "
    "Réponds STRICTEMENT en JSON : {\"wallets\": [{\"address\": \"0x...\", "
    "\"thesis\": \"<3-5 phrases factuelles>\"}], \"synthesis\": \"<note globale si "
    "plusieurs wallets soumis ensemble, sinon chaîne vide>\"}"
)


def _format_card_for_prompt(card: WalletScoreCard) -> str:
    lines = [f"Wallet {card.address}" + (f" ({card.display_name})" if card.display_name else "")]
    if not card.available:
        lines.append(f"Données indisponibles : {card.error or UNAVAILABLE}")
        return "\n".join(lines)
    if card.disqualified:
        lines.append("DISQUALIFIÉ : " + "; ".join(card.disqualification_reasons))
    if card.financing_check_note:
        lines.append(card.financing_check_note)
    lines.append(
        f"Tokens tradés trouvés : {card.tokens_found} (analysés cette passe : {card.tokens_analyzed}"
        + (f", plafond de {WEIGHTS.max_tokens_analyzed} atteint -- {card.tokens_scanned_cumulative}/{card.tokens_found} couverts au total" if card.tokens_skipped_capped else "")
        + ")"
    )
    lines.append(
        "Couverture complète du portefeuille atteinte." if card.full_coverage
        else f"Scan progressif en cours ({card.tokens_scanned_cumulative}/{card.tokens_found} tokens couverts à ce jour) -- "
             "relancer /walletscore plus tard pour poursuivre et affiner la note."
    )
    if card.transfer_history_truncated:
        lines.append(
            "ATTENTION : historique de transferts tronqué par le plafond de pagination -- ce wallet est très "
            "actif, des transferts plus anciens que ceux récupérés existent peut-être encore et ne sont pas "
            "couverts (risque de biais sur le PnL/win rate/Sortino/diversification)."
        )
    lines.append(
        f"Trades clôturés valorisés (cumulé) : {card.closed_trades_count} (jambes sans prix cette passe : "
        f"{card.unpriced_legs}, tokens sans pool GeckoTerminal résolu cette passe : {card.pool_lookup_errors})"
    )
    if card.thin_liquidity_pricing_skipped_count:
        lines.append(
            f"Dont {card.thin_liquidity_pricing_skipped_count} token(s) avec un pool trop peu liquide pour faire "
            f"confiance à son prix (< ${WEIGHTS.min_pool_liquidity_usd_for_pricing:,.0f}) -- non valorisé(s), "
            "défense anti-dust/scam-pool."
        )
    if card.unmatched_sell_events:
        lines.append(
            f"{card.unmatched_sell_events} vente(s) dont la quantité dépasse ce qui a été acheté dans la fenêtre "
            "récupérée (rendement de rebase/DeFi possible, ou achat antérieur) -- jamais créditée comme profit."
        )
    if card.transient_pricing_errors:
        lines.append(
            f"{card.transient_pricing_errors} token(s) non couvert(s) cette passe suite à une panne d'API "
            "temporaire (timeout/rate-limit) -- retenté(s) automatiquement au prochain scan, jamais figé(s)."
        )
    if card.gecko_dexscreener_gap_count:
        lines.append(
            f"Dont {card.gecko_dexscreener_gap_count} avec une paire DexScreener trouvée que GeckoTerminal "
            "n'a pas résolue (écart entre sources, pas forcément un token illiquide)."
        )
    if card.cmc_price_recovery_count:
        lines.append(
            f"Dont {card.cmc_price_recovery_count} valorisé(s) via CoinMarketCap après échec GeckoTerminal "
            "(3e couche de pricing, #157)."
        )
    lines.append(f"Win rate : {card.win_rate:.0%}" if card.win_rate is not None else "Win rate : indisponible")
    lines.append(
        f"PnL réalisé : ${card.realized_pnl_usd:,.2f}"
        if card.realized_pnl_usd is not None
        else "PnL réalisé : indisponible"
    )
    lines.append(
        f"Sortino : {card.sortino:.2f}"
        if card.sortino is not None
        else "Sortino : indisponible (trop peu de trades clôturés ou aucune perte observée)"
    )
    if card.sortino_pnl_contradiction:
        lines.append(
            "ATTENTION : Sortino positif mais PnL réalisé négatif -- le Sortino se base sur le "
            "rendement en % par trade, jamais pondéré par la taille de la position (un petit gain "
            "en % sur une grosse perte en $ peut gonfler ce ratio) -- ne pas lire seul, croiser avec "
            "le PnL réalisé en dollars ci-dessus."
        )
    lines.append(
        f"Max drawdown (wallet) : {card.max_drawdown_pct:.0%}"
        if card.max_drawdown_pct is not None
        else "Max drawdown : indisponible"
    )
    lines.append(
        f"Diversification : {card.diversification_profitable_tokens}/{card.diversification_total_tokens} tokens profitables"
    )
    lines.append(
        f"Diversification pondérée par capital : {card.diversification_capital_weighted_ratio:.0%} du capital "
        "déployé a fini dans une position profitable"
        if card.diversification_capital_weighted_ratio is not None
        else "Diversification pondérée par capital : indisponible"
    )
    lines.append(
        f"Durée moyenne de détention : {card.avg_holding_period_days:.1f} jour(s)"
        if card.avg_holding_period_days is not None
        else "Durée moyenne de détention : indisponible"
    )
    if card.recent_window_trades_count:
        lines.append(
            f"Fenêtre récente ({WEIGHTS.recent_window_days}j, {card.recent_window_trades_count} trade(s) "
            f"clôturé(s)) : win rate {card.win_rate_recent:.0%}, PnL ${card.realized_pnl_usd_recent:,.2f} "
            "-- en complément de l'historique complet ci-dessus, jamais à sa place."
        )
    else:
        lines.append(f"Fenêtre récente ({WEIGHTS.recent_window_days}j) : aucun trade clôturé -- indisponible")
    lines.append(
        f"Récurrence acheteur précoce multi-lancements : {card.early_entry_recurrence_count} token(s) "
        f"(dont {card.informed_entry_count} avec conditions techniques jugées informées)"
    )
    lines.append(f"Suspect positif (multi-axes) : {'oui' if card.suspect_positive else 'non'}")
    lines.append(
        f"Échantillon suffisant pour un classement fiable ({WEIGHTS.min_wallet_age_days}j+/"
        f"{WEIGHTS.min_total_swaps}+ swaps) : {'oui' if card.sample_size_sufficient else 'non'} "
        f"(âge : {card.wallet_age_days:.0f}j, swaps : {card.total_swaps})"
        if card.wallet_age_days is not None
        else "Ancienneté du wallet : indisponible"
    )
    lines.append(
        f"Robustesse anti-chance (retrait des {WEIGHTS.robust_trim_pct:.0%} meilleurs ET "
        f"{WEIGHTS.robust_trim_pct:.0%} pires trades) : PnL restant "
        f"{'positif' if card.robust_pnl_positive else 'négatif'}"
        if card.robust_pnl_positive is not None
        else "Robustesse anti-chance : indisponible (pas assez de trades clôturés)"
    )
    lines.append(
        f"Tendance de santé dans le temps : {card.health_trend}"
        if card.health_trend is not None
        else "Tendance de santé dans le temps : indisponible (pas assez de trades clôturés)"
    )
    if card.price_confirmation_ratio is not None:
        lines.append(f"Confiance du cost-basis : {card.price_confirmation_ratio:.0%} des prix confirmés par exécution exacte")
        if card.price_confidence_low:
            lines.append(
                "Attention : une partie importante des prix d'entrée de ce wallet est estimée via les prix de "
                "marché historiques (transferts type CEX ou swaps complexes sans stablecoin direct) -- le PnL "
                "réel peut différer."
            )
    if card.compared_against_n_wallets > 0:
        lines.append(
            f"Classement comparatif (vs {card.compared_against_n_wallets} autre(s) wallet(s) suivi(s)) : "
            f"percentile composite {card.composite_percentile:.0f}e" if card.composite_percentile is not None
            else f"Classement comparatif : pas assez d'axes communs avec les {card.compared_against_n_wallets} "
                 "autre(s) wallet(s) suivi(s)"
        )
        if card.composite_percentile is not None and card.price_confidence_low:
            # Comparability blind spot (15/07, ChatGPT review): the
            # low-confidence flag lived elsewhere in the report, never
            # attached to the percentile figure itself -- a reader (human or
            # synthesis LLM) could present an excellent ranking as reliable
            # without linking it to a mostly-estimated cost-basis.
            lines.append(
                "ATTENTION : ce percentile repose majoritairement sur des prix estimés "
                f"(confiance du cost-basis {card.price_confirmation_ratio:.0%}, sous le seuil de "
                f"{WEIGHTS.min_price_confirmation_ratio:.0%}) -- à interpréter avec prudence."
            )
        if card.percentile_holding_period is not None:
            lines.append(f"Percentile durée de détention (contextuel, hors composite) : {card.percentile_holding_period:.0f}e")
    else:
        lines.append("Classement comparatif : indisponible (aucun autre wallet encore suivi pour comparer)")
    if card.cross_token_holder_count > 0:
        tags = sorted({t for h in card.cross_token_holdings for t in (h.get("tags") or [])})
        lines.append(
            f"Détenteur croisé (hors composite, jamais un score de performance) : présent parmi les gros "
            f"holders de {card.cross_token_holder_count} autre(s) token(s) déjà couvert(s) par ARIA"
            + (f" -- labels connus : {', '.join(tags[:4])}" if tags else " -- aucun label d'entité connu, "
               "coordination anonyme possible")
        )
    return "\n".join(lines)


async def _generate_thesis(
    cards: list[WalletScoreCard], convergence_pairs: list[tuple[str, str]], *, llm=None,
) -> str | None:
    if llm is None:
        from aria_core.llm import chat_with_context as llm

    from aria_core.runtime import settings
    from aria_core.spark_config import DEFAULT_MODEL_DEVELOP

    develop_model = (getattr(settings, "aria_llm_model_develop", None) or "").strip() or DEFAULT_MODEL_DEVELOP

    prompt_parts = [_format_card_for_prompt(c) for c in cards]
    if convergence_pairs:
        prompt_parts.append(
            "Wallets soumis ensemble partageant une source de financement initiale "
            "(suspects d'être la même entité) : "
            + ", ".join(f"{a} <-> {b}" for a, b in convergence_pairs)
        )
    prompt = "\n\n".join(prompt_parts)

    raw = await llm(prompt, _WALLET_THESIS_SYSTEM, max_tokens=800, model=develop_model, depth="wallet_scoring")
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    wallet_theses = {
        str(w.get("address", "")).lower(): str(w.get("thesis", ""))
        for w in (data.get("wallets") or [])
        if isinstance(w, dict)
    }
    for card in cards:
        card.thesis = wallet_theses.get(card.address.lower()) or None

    return str(data.get("synthesis") or "") or None


def _valid_address(address: str) -> bool:
    return bool(address) and address.startswith("0x") and len(address) == 42


async def _resolve_copy_trading(card: "WalletScoreCard") -> None:
    """Correlates this wallet's entries across EVERY chain where real
    activity was found (`card.chains_scanned`) and merges the result -- a
    multi-chain wallet must never be judged on a single arbitrary chain.
    Best-effort, never blocking for the calling scoring."""
    from aria_core.skills.copy_trading_detection import (
        CopyTradingFacts,
        gather_copy_trading_facts,
        judge_copy_trading,
    )

    chains = card.chains_scanned or ["base"]
    try:
        total_distinct_tokens = 0
        followed: set[str] = set()
        any_available = False
        for chain in chains:
            facts = await gather_copy_trading_facts(card.address, chain)
            if facts.available:
                any_available = True
                total_distinct_tokens += facts.distinct_tokens_followed
                followed.update(facts.followed_wallets)
        if not any_available:
            card.copy_trading_flag = "unknown"
            return
        merged = CopyTradingFacts(
            distinct_tokens_followed=total_distinct_tokens, followed_wallets=sorted(followed), available=True,
        )
        verdict = judge_copy_trading(merged)
        card.copy_trading_flag = verdict.flag
        card.copy_trading_points = verdict.points
    except Exception:  # noqa: BLE001 — bonus signal, never blocking
        card.copy_trading_flag = "unknown"


async def score_wallets(
    addresses: list[str],
    *,
    client: BlockscoutClient | None = None,
    chains: dict[str, BlockscoutClient] | None = None,
    gecko,
    llm=None,
    goplus=None,
    max_tokens: int | None = None,
) -> WalletScoringReport:
    """Wallet-centric entry point (#157): 1 to 3 addresses -> hard
    disqualifiers, composite score, suspect-positive flag, LLM thesis.
    Always a confirmation/context signal, never a trigger (same absolute
    rule as `analyze_smart_money`).

    EVM multi-chain (#157, 14/07, explicit operator decision): the same 0x
    address is valid on every EVM chain -- ARIA tries each chain and
    CONSOLIDATES into a single score (not one score per chain), the
    analyzed-tokens cap applied globally on the consolidated whole.
    ``chains`` (dict chain -> client) allows injecting an explicit registry
    (tests, or a subset of chains); failing that, ``client`` alone falls
    back to a STRICTLY unchanged single-chain "base" behavior (historical
    path, all existing tests); if neither is provided, the dynamic TVL
    ranking (`DEFAULT_SCAN_CHAINS()`, #157 14/07 -- DefiLlama, refreshed
    monthly by the heartbeat, falls back to Base/Ethereum if the cache never
    ran) is used. Solana is NOT EVM (separate project, out of scope) --
    never in this registry.

    SCOPE CLARIFICATION (15/07, ChatGPT review -- inconsistency spotted
    between this docstring and the "cross-chain bridges" limitation
    documented above): "consolidated" here means that trades/metrics from
    ALL scanned chains are aggregated into A SINGLE set of numbers
    (win_rate/PnL/Sortino/etc. mix a given wallet's Base and Ethereum
    trades, for instance) -- NOT that ONE position's cost-basis follows a
    continuity across a bridge. A buy on Base then a bridge to Arbitrum then
    a sell on Arbitrum (economically ONE single trade) is seen as TWO
    independent, unlinked FIFO events (cf. "cross-chain bridges" limitation
    above) -- consolidation of METRICS per wallet, never cost-basis
    continuity across bridges.
    """
    if not addresses:
        return WalletScoringReport(available=False, error="aucune adresse fournie")
    if len(addresses) > 3:
        return WalletScoringReport(available=False, error="maximum 3 adresses par appel")
    if not all(_valid_address(a) for a in addresses):
        return WalletScoringReport(available=False, error="adresse invalide -- attendu 0x + 40 caractères hexadécimaux")

    if chains is not None:
        chain_clients = chains
    elif client is not None:
        chain_clients = {"base": client}
    else:
        from aria_core.services.blockscout import get_blockscout_client

        chain_clients = {c: get_blockscout_client(c) for c in await DEFAULT_SCAN_CHAINS()}

    cards: list[WalletScoreCard] = []
    funding_sources: dict[str, str] = {}

    for wallet in addresses:
        card = WalletScoreCard(address=wallet)

        grouped: dict[str, list[TokenTransfer]] = {}
        chains_with_data: list[str] = []
        all_flat_transfers: list[TokenTransfer] = []
        primary_info: AddressInfo | None = None
        funding_source: str | None = None
        funding_truncated = False
        funding_source_chain: str | None = None
        any_chain_available = False
        last_error: str | None = None
        # 15/07, external review -- history truncated by the pagination cap
        # (2000 transfers/10 pages): `TokenTransfersResult.
        # truncated` signals when Blockscout STILL had data
        # (`next_page_params`) but we stopped because of the cap, never
        # when the history is genuinely exhausted. Without this signal, a
        # very active wallet (> 2000 lifetime ERC-20 transfers) would have
        # its earliest buys silently missing from the FIFO -- later sells
        # in the history would wrongly become `unmatched_sell_events`,
        # potentially biasing W/PnL/S/D and the percentile.
        transfers_truncated = False

        for chain, chain_client in chain_clients.items():
            # 22/07 -- explicit operator decision ("let's relieve Blockscout
            # as much as possible"): tries Alchemy/Moralis first on "base"
            # (the only verified chain), falls back to Blockscout (STRICTLY
            # unchanged historical behavior) if the gate is OFF, the chain
            # isn't "base", or both fast providers fail -- never a behavior
            # change for a session that doesn't enable the gate.
            transfers_result = None
            if chain == "base":
                from aria_core.services import wallet_transfers_fast

                fast_result = await wallet_transfers_fast.get_fast_token_transfers(
                    wallet, chain, limit=2000, max_pages=10,
                )
                if fast_result.available:
                    transfers_result = fast_result
            if transfers_result is None:
                transfers_result = await chain_client.get_token_transfers(
                    wallet, limit=2000, max_pages=10, token_type="ERC-20",
                )
            if not transfers_result.available:
                last_error = transfers_result.error or UNAVAILABLE
                continue
            any_chain_available = True
            transfers_truncated = transfers_truncated or transfers_result.truncated
            if transfers_result.transfers:
                chains_with_data.append(chain)
                all_flat_transfers.extend(transfers_result.transfers)
            grouped.update(_group_transfers_by_token(transfers_result.transfers, chain=chain))

            if primary_info is None or not primary_info.available:
                info = await chain_client.get_address_info(wallet)
                if info.available:
                    primary_info = info

            if funding_source is None:
                fs, trunc = await _funding_source(chain_client, wallet)
                if fs:
                    funding_source, funding_truncated = fs, trunc
                    # REAL chain where funding_source was found (#157,
                    # fix 14/07) -- never assumed to be Base by default: a
                    # funding address can live on a chain different from the
                    # one the wallet trades its tokens on.
                    funding_source_chain = chain

        if not any_chain_available:
            card.available = False
            card.error = last_error or UNAVAILABLE
            cards.append(card)
            continue

        card.display_name = primary_info.ens_domain_name if primary_info else None
        card.chains_scanned = chains_with_data
        card.transfer_history_truncated = transfers_truncated

        # Cross-token holder (21/07) -- pure local read (no network call,
        # never blocking: a SQLite read failure must never break a scoring
        # already in progress). Deferred import, same pattern as
        # wallet_scan_state above (anti-cycle).
        from aria_core import token_holder_intel

        try:
            card.cross_token_holdings = await token_holder_intel.wallet_cross_token_holdings(wallet)
        except Exception:  # noqa: BLE001 -- informational, never blocking
            logger.warning("score_wallets: cross_token_holdings read failed for %s", wallet)
            card.cross_token_holdings = []
        card.cross_token_holder_count = len(card.cross_token_holdings)

        # Persistent incremental scan (#157 follow-up, 15/07): only
        # re-analyzes tokens never seen, or whose activity evolved since the
        # last scan (a new transfer later than `checkpoint.last_scan_at`) --
        # never all 680 tokens at once, nor a useless re-analysis of a token
        # already covered and unchanged.
        from aria_core.services import wallet_scan_state

        checkpoint = await wallet_scan_state.get_checkpoint(wallet)
        # `get_token_transfers` is capped (2000 transfers/10 pages) -- for a
        # very active wallet, the "N latest transfers" window captured on
        # THIS pass may differ from the previous pass (new activity pushing
        # older tokens out of the window), making the total appear SMALLER
        # than before. Never let this total go down: (1) it would make the
        # displayed progress ("X/Y tokens covered") inconsistent from one
        # cycle to the next, (2) more importantly, it could trigger a false
        # "100% coverage" if the apparent total drops below what's already
        # scanned, even though tokens never seen still exist in reality,
        # just outside this particular window.
        total_found = max(len(grouped), checkpoint.tokens_found_total)

        def _needs_scan(key: str, transfers: list[TokenTransfer]) -> bool:
            if key not in checkpoint.scanned_tokens:
                return True
            if checkpoint.last_scan_at is None:
                return False
            return any(
                (ts := _parse_timestamp(t.timestamp)) is not None and ts > checkpoint.last_scan_at
                for t in transfers
            )

        pending = {k: v for k, v in grouped.items() if _needs_scan(k, v)}

        cap = max_tokens if max_tokens is not None else WEIGHTS.max_tokens_analyzed
        selected_tokens, _pending_total, skipped = _select_tokens_for_deep_analysis(pending, wallet=wallet, cap=cap)
        card.tokens_found = total_found
        card.tokens_analyzed = len(selected_tokens)
        card.tokens_skipped_capped = skipped > 0
        if card.tokens_skipped_capped:
            logger.info(
                "score_wallets: wallet %s -- cap of %s tokens reached (%s remaining to cover, "
                "selection by round-trip then recency/trade count, all chains combined)",
                wallet, cap, skipped,
            )

        selected_transfers = {key: grouped[key] for key in selected_tokens}

        card.funding_source = funding_source
        card.funding_source_truncated = funding_truncated
        if funding_source:
            funding_sources[wallet.lower()] = funding_source

        # Multi-token analysis BEFORE hard disqualifiers: provides the pools
        # actually resolved per token, used below to generalize the
        # wash-trading exclusion without a false positive (#157, fix 14/07).
        multi = await _analyze_wallet_multi_token(wallet, selected_transfers, gecko=gecko, chain_clients=chain_clients)
        card.unpriced_legs = multi.unpriced_legs
        card.pool_lookup_errors = multi.pool_lookup_errors
        card.gecko_dexscreener_gap_count = len(multi.gecko_dexscreener_gap_tokens)
        card.cmc_price_recovery_count = len(multi.cmc_recovered_tokens)
        card.thin_liquidity_pricing_skipped_count = len(multi.thin_liquidity_tokens)
        card.unmatched_sell_events = multi.unmatched_sell_events
        card.transient_pricing_errors = len(multi.transient_pricing_error_tokens)

        # Persistence: this batch replaces the archived trades of the tokens
        # it covers (the FIFO is fully recomputed from the token's complete
        # history, never an append that would duplicate the same historical
        # trades).
        batch_addresses = {key.partition(":")[2] for key in selected_tokens}
        await wallet_scan_state.replace_archived_trades(wallet, batch_addresses, multi.closed_trades)

        now = datetime.now(timezone.utc)
        # Freezing transient errors (15/07, Gemini review): a token whose
        # pool resolution failed THIS PASS for an infrastructure reason
        # (`transient_pricing_error_tokens`) is NEVER marked "scanned" -- it
        # stays eligible for a new attempt on the next call, even without
        # new on-chain activity (`_needs_scan` will re-select it). Without
        # this, a one-off timeout/429 would have frozen into a permanent
        # scar in the archives (a leg never re-priced, never retried).
        new_scanned = checkpoint.scanned_tokens | (set(selected_tokens) - multi.transient_pricing_error_tokens)
        full_coverage_at = checkpoint.full_coverage_at
        if full_coverage_at is None and len(new_scanned) >= total_found:
            full_coverage_at = now

        # Permanent tracking (15/07, #157 follow-up 2): max of the
        # timestamps of ALL transfers seen this pass (not just the selected
        # batch) -- `all_flat_transfers` covers the full history fetched
        # this pass, never regressed (max with the value already known at
        # the checkpoint).
        observed_activity = [
            ts for t in all_flat_transfers if (ts := _parse_timestamp(t.timestamp)) is not None
        ]
        last_activity_at = checkpoint.last_activity_at
        if observed_activity:
            newest_observed = max(observed_activity)
            if last_activity_at is None or newest_observed > last_activity_at:
                last_activity_at = newest_observed

        await wallet_scan_state.save_checkpoint(
            wallet, scanned_tokens=new_scanned, last_scan_at=now,
            tokens_found_total=total_found, full_coverage_at=full_coverage_at,
            last_activity_at=last_activity_at,
        )
        card.tokens_scanned_cumulative = len(new_scanned)
        card.full_coverage = full_coverage_at is not None
        card.last_activity_at = last_activity_at

        # Final score based on ALL closed trades ever archived for this
        # wallet (cumulative), not just this batch's -- the score refines
        # itself over successive passes rather than starting from zero on
        # every call.
        cumulative_trades = await wallet_scan_state.list_archived_trades(wallet)
        card.closed_trades_count = len(cumulative_trades)

        dex_exclusions = _build_dex_infrastructure_exclusions(grouped, wallet) | multi.resolved_pool_addresses
        disq = await _hard_disqualifiers(
            wallet, primary_info or AddressInfo(address=wallet, available=False), all_flat_transfers, funding_source,
            extra_exclusions=dex_exclusions, goplus_client=goplus, funding_source_chain=funding_source_chain,
        )
        card.disqualified = disq.disqualified
        card.disqualification_reasons = disq.reasons
        card.financing_check_note = disq.financing_check_note

        if cumulative_trades:
            wins = sum(1 for t in cumulative_trades if t.pnl_usd > 0)
            card.win_rate = wins / len(cumulative_trades)
            card.realized_pnl_usd = sum(t.pnl_usd for t in cumulative_trades)
            card.max_drawdown_pct = _max_drawdown_pct(cumulative_trades)
            returns = [r for t in cumulative_trades if (r := t.return_pct) is not None]
            card.sortino = _sortino_ratio(returns)
            card.sortino_pnl_contradiction = (
                card.sortino is not None and card.sortino > 0 and card.realized_pnl_usd < 0
            )
            card.avg_holding_period_days = _avg_holding_period_days(cumulative_trades)
            card.win_rate_recent, card.realized_pnl_usd_recent, card.recent_window_trades_count = (
                _recent_window_metrics(cumulative_trades, window_days=WEIGHTS.recent_window_days)
            )

            by_token: dict[str, float] = {}
            capital_by_token: dict[str, float] = {}
            for t in cumulative_trades:
                by_token[t.token_address] = by_token.get(t.token_address, 0.0) + t.pnl_usd
                capital_by_token[t.token_address] = (
                    capital_by_token.get(t.token_address, 0.0) + t.token_amount * t.buy_price
                )
            card.diversification_total_tokens = len(by_token)
            card.diversification_profitable_tokens = sum(1 for v in by_token.values() if v > 0)

            # Capital-weighted diversification (15/07, ChatGPT review): the
            # counting ratio above treats a $5 bet like a $50,000 bet -- a
            # wallet can artificially inflate its "counted" diversification
            # via 200 tiny positions while a single big bet actually
            # dominates its capital. Complements (doesn't replace) the
            # counting ratio -- the two measure different things (breadth
            # of independent bets vs. real capital concentration), same
            # "separate axes, never merged" doctrine as the rest of this
            # module.
            total_capital = sum(capital_by_token.values())
            card.diversification_capital_weighted_ratio = (
                round(
                    sum(v for addr, v in capital_by_token.items() if by_token.get(addr, 0.0) > 0) / total_capital, 4,
                )
                if total_capital > 0
                else None
            )

            card.robust_pnl_positive = _robust_pnl_check(
                cumulative_trades,
                trim_pct=WEIGHTS.robust_trim_pct,
                min_required=WEIGHTS.robust_trim_min_closed_trades,
            )

            # Cost-basis confidence (15/07, Gemini review): share of LEGS
            # (buy + sell counted separately) priced by an exact execution
            # price rather than the OHLCV market fallback.
            exact_legs = sum(
                (1 if t.buy_price_exact else 0) + (1 if t.sell_price_exact else 0) for t in cumulative_trades
            )
            total_legs = len(cumulative_trades) * 2
            card.price_confirmation_ratio = round(exact_legs / total_legs, 4) if total_legs else None
            card.price_confidence_low = (
                card.price_confirmation_ratio is not None
                and card.price_confirmation_ratio < WEIGHTS.min_price_confirmation_ratio
            )
            card.health_trend = _health_trend(
                cumulative_trades,
                min_required=WEIGHTS.health_trend_min_closed_trades,
                stable_band_pct=WEIGHTS.health_trend_stable_band_pct,
            )

        # Minimum sample size (15/07, operator decision): on `all_flat_transfers`
        # (the raw history, not just closed trades) -- a wallet can be
        # "young" or "not very active" regardless of having closed trades.
        card.wallet_age_days = _wallet_age_days(all_flat_transfers)
        card.total_swaps = _count_total_swaps(all_flat_transfers, wallet)
        card.sample_size_sufficient = (
            card.wallet_age_days is not None and card.wallet_age_days >= WEIGHTS.min_wallet_age_days
            and card.total_swaps >= WEIGHTS.min_total_swaps
        )

        card.early_entry_recurrence_count = len(multi.early_entry_tokens)
        card.informed_entry_count = len(multi.informed_entry_tokens)
        card.suspect_positive = _suspect_positive_flag(card)

        await _resolve_copy_trading(card)

        await _apply_comparative_ranking(card)

        cards.append(card)

    convergence_pairs = _pairwise_convergence(addresses, funding_sources)

    synthesis = None
    if any(c.available for c in cards):
        synthesis = await _generate_thesis(cards, convergence_pairs, llm=llm)

    for card in cards:
        try:
            await _log_wallet_score(card.address, json.dumps(asdict(card), default=str))
        except Exception:  # noqa: BLE001 -- the log must never break the scoring
            logger.warning("score_wallets: wallet_score_log write failed for %s", card.address)

    return WalletScoringReport(
        wallets=cards, convergence_pairs=convergence_pairs, synthesis=synthesis, available=True, error=None,
    )
