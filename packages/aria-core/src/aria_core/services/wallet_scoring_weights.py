"""Tunable weights and thresholds for the wallet-centric evaluator (#157) — ISOLATED
in this SINGLE module at the operator's explicit request (14/07): these are
the parameters that make up part of the in-house formula's competitive
edge, not an interchangeable implementation detail.

Deliberate distinction:
- Generic FORMULAS (FIFO computation, Sortino ratio, Maximum Drawdown)
  stay in the public code (`services/smart_money.py`) — these are
  standard finance calculations, publicly documented, not a secret.
- The threshold/weight VALUES below (from what win rate a
  wallet is "suspiciously positive", how many trades minimum before
  trusting the Sortino ratio, how many tokens to analyze in depth, etc.) are
  grouped HERE, in one identifiable place, so they can
  be easily moved/hidden if command decides they shouldn't
  be publicly readable on GitHub.

OPERATOR DECISION (14/07): this module stays in the PUBLIC ARIA repo as
is, with the dataclass default values below — but these
default values are NOT the real tuned production values. These are
simple, reasonable starting values so the code works without
external configuration (dev/local/tests). The real production values
will be deposited manually later by the operator in a private file
on the VPS, outside this repo and this project.

At startup, `WEIGHTS` attempts to load its real values from an
external YAML/JSON file designated by the environment variable
`ARIA_WALLET_SCORING_WEIGHTS_PATH` — same pattern as existing secrets
(`.env` never committed, read via an environment variable): not a new
doctrine, an application of the existing one. If the variable isn't set, or
if the file is not found/unreadable/invalid, explicit fallback (logged, not
silent) to the dataclass default values — current behavior
unchanged in local/dev/tests, no external configuration required to run
the test suite.

Scope: ONLY the thresholds introduced by project #157 (the
multi-token wallet-centric evaluator). The pre-existing constants of
`smart_money.py` on the token-centric side (`_LARGEST_BUY_SHARE_MAX`,
`_EARLY_ENTRY_WINDOW_SECONDS`, `_WASH_TRADING_COUNTERPARTY_SHARE`,
`_MIN_TRANSFERS_FOR_WASH_CHECK`) are a separate question, explicitly
left aside by the operator for now — not touched here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields

import yaml

logger = logging.getLogger(__name__)

_WEIGHTS_PATH_ENV_VAR = "ARIA_WALLET_SCORING_WEIGHTS_PATH"


@dataclass(frozen=True)
class WalletScoringWeights:
    # Cap on distinct tokens analyzed in depth per wallet (recency/
    # trade count) -- operator decision from 14/07: 20->50 then lowered to
    # 10 the same day (faster scans by default); raised back to 50 on 15/07
    # once the background queue (`wallet_scan_queue.py`)
    # was built -- a heavier pass is acceptable since repeated
    # scans no longer block a synchronous Telegram reply. The
    # ``max_tokens`` parameter of ``score_wallets`` still allows passing a
    # different cap on a one-off basis without touching this default value.
    max_tokens_analyzed: int = 50

    # Below this number of closed trades, the Sortino ratio is judged too
    # noisy to present as reliable (research doc #157) -- unavailable
    # rather than a misleading number.
    min_closed_trades_for_sortino: int = 5

    # "Suspiciously positive" flag (layer 3, #157) -- minimum number of
    # independent axes exceeded simultaneously before raising the flag, and the
    # per-axis thresholds.
    suspect_positive_min_axes: int = 3
    suspect_win_rate_min: float = 0.7
    suspect_sortino_min: float = 1.5
    suspect_diversification_min_tokens: int = 3
    suspect_diversification_ratio_min: float = 0.6
    suspect_recurrence_min: int = 3

    # Bounded pagination of transaction history (wallet age /
    # funding source) -- Blockscout has no cheap "oldest
    # first" sort (verified live, #157): page cap before
    # presenting the result as a bound rather than an exact value.
    funding_source_max_pages: int = 5

    # Candle lookback window used to qualify an early entry as
    # "informed" (low volume + chart pattern right before the buy).
    technical_entry_lookback_candles: int = 20

    # Wash-trading anti-false-positive (#157, fix 14/07): a counterparty
    # that recurs on at least this many DISTINCT tokens is treated as
    # a DEX infrastructure component (pool/router, mechanically shared
    # across many pairs), NOT a wash-trading partner (typically
    # tied to A SINGLE token/scheme) -- excluded from the dominant-counterparty
    # calculation.
    wash_trading_infra_min_distinct_tokens: int = 2

    # Exact tx_hash pricing (14/07, pool+OHLCV supplement): cap on
    # distinct tx_hash attempted per token before falling back to pool+OHLCV for
    # the rest. An active wallet (the very profile this scoring tries to
    # spot) can have dozens/hundreds of distinct tx on a single
    # token -- without a cap, a `/walletscore` request would trigger just as many
    # additional sequential Blockscout calls. Reasonable starting
    # value, precision/cost tradeoff -- not carved in stone,
    # adjustable like any `WEIGHTS.*` via the override file.
    max_hash_priced_legs_per_token: int = 20

    # Minimum sample before presenting a ranking as reliable (15/07,
    # explicit operator decision) -- same doctrine as the "real money
    # green light" protocol applied to ARIA herself (`docs/protocole-argent-
    # reel.md`, minimum sample before trusting): a wallet with
    # little history isn't scored "bad", just marked "insufficient
    # sample" -- never a score presented with false confidence.
    min_wallet_age_days: int = 90
    min_total_swaps: int = 100

    # Anti-luck robustness (15/07, operator decision; fixed the same day
    # after an external cross-review by Gemini/ChatGPT/Grok, convergent on this point):
    # removes a PERCENTAGE (not a fixed count) of the best AND
    # worst ranked trades (both extremes) before checking whether the
    # remaining PnL stays positive. A fixed count (the old ``robust_trim_count=10``)
    # dilutes as the sample grows (10/30 = 33% removed at the
    # minimum threshold, but only 10/20000 = 0.05% on a very active wallet) --
    # an attacker could then drown a single "lucky" trade behind
    # enough insignificant micro-trades that it's no longer within
    # the removed absolute top-N. A percentage scales with N and closes this vector.
    # Only computed if the wallet has enough trades that the removal
    # leaves a significant remainder (otherwise unavailable, never a number on
    # an emptied-out sample).
    robust_trim_pct: float = 0.10
    robust_trim_min_closed_trades: int = 30

    # Health trend over time (15/07): compares the performance (average PnL
    # per trade) of the chronological second half of closed trades to the
    # first half -- an "improving"/"stable"/"deteriorating" signal, never computed
    # below this minimum trade count (statistical noise on small samples).
    # Known limitation (ChatGPT review, 15/07): the split is done by trade
    # COUNT, not by calendar window -- a wallet active for 3 years then dormant
    # for 1 year could see its "trend" dominated by a recent comeback. Documented
    # in smart_money.py, not fixed this pass (deeper rework).
    health_trend_min_closed_trades: int = 10
    health_trend_stable_band_pct: float = 0.15

    # Cost-basis confidence (15/07, following Gemini review): minimum share
    # of legs priced by an EXACT execution price (tx_hash ratio against
    # a stablecoin leg) rather than the OHLCV market fallback, before
    # raising a low-confidence flag alongside the score (never by hiding
    # win_rate/PnL -- same doctrine as ``sample_size_sufficient``, not
    # fully masking Sortino/robust_pnl/health_trend). Starting value,
    # adjustable like any ``WEIGHTS.*``.
    min_price_confirmation_ratio: float = 0.30

    # Anti-dust/scam-pool defense (15/07, Gemini review): a pool RESOLVED by
    # GeckoTerminal but whose real liquidity (``reserve_usd``) is below this
    # floor isn't reliable enough to value a real PnL (pool
    # trivially manipulable, e.g. dust token sent by a scammer with
    # artificial "liquidity") -- treated as non-priced rather than
    # trusting a fabricated market price. Same floor already
    # used elsewhere in ARIA for VC candidate screening
    # (``safety_screen``/``liquidity_depth``, ~$30k) -- not a new
    # arbitrary number. Unknown ``reserve_usd`` (``None``, e.g. existing tests or
    # a chain without this data) is treated as "trust it" (fail-open),
    # never as zero liquidity -- only a CONFIRMED value below this
    # floor blocks the valuation.
    min_pool_liquidity_usd_for_pricing: float = 30_000.0

    # Temporal bias (15/07, ChatGPT review): recent window computed IN ADDITION
    # to (never instead of) the full historical metrics -- a recently
    # degraded wallet would otherwise stay masked by a favorable aggregate history.
    # Starting value, adjustable like any `WEIGHTS.*`.
    recent_window_days: int = 90


def _load_weights() -> WalletScoringWeights:
    """Loads the real weights from the private file designated by
    `ARIA_WALLET_SCORING_WEIGHTS_PATH` (YAML or JSON — `yaml.safe_load` reads
    both). Explicit fallback to the dataclass default values if the
    variable isn't set or if loading fails in any way
    whatsoever -- never a startup crash for a missing private file."""
    path = os.environ.get(_WEIGHTS_PATH_ENV_VAR)
    if not path:
        return WalletScoringWeights()

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(
            "%s défini (%s) mais illisible/invalide (%s) -- repli sur les "
            "valeurs par défaut de WalletScoringWeights.",
            _WEIGHTS_PATH_ENV_VAR,
            path,
            exc,
        )
        return WalletScoringWeights()

    if not isinstance(raw, dict):
        logger.warning(
            "%s (%s) ne contient pas un mapping clé/valeur -- repli sur les "
            "valeurs par défaut de WalletScoringWeights.",
            _WEIGHTS_PATH_ENV_VAR,
            path,
        )
        return WalletScoringWeights()

    known_fields = {f.name for f in fields(WalletScoringWeights)}
    unknown_keys = set(raw) - known_fields
    if unknown_keys:
        logger.warning(
            "%s (%s) contient des clés inconnues ignorées : %s",
            _WEIGHTS_PATH_ENV_VAR,
            path,
            sorted(unknown_keys),
        )
    overrides = {k: v for k, v in raw.items() if k in known_fields}

    try:
        weights = WalletScoringWeights(**{**WalletScoringWeights().__dict__, **overrides})
    except TypeError as exc:
        logger.warning(
            "%s (%s) contient des valeurs invalides (%s) -- repli sur les "
            "valeurs par défaut de WalletScoringWeights.",
            _WEIGHTS_PATH_ENV_VAR,
            path,
            exc,
        )
        return WalletScoringWeights()

    logger.info("Wallet-scoring weights loaded from %s (%s)", path, _WEIGHTS_PATH_ENV_VAR)
    return weights


WEIGHTS = _load_weights()
