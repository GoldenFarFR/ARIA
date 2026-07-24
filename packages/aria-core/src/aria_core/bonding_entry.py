"""Virtuals bonding-curve entry -- a SEPARATE decision engine from
``momentum_entry.py``, wired into the SAME active 1M$ paper-trading test
(operator's explicit go, 24/07).

Why this exists as its own module instead of parametrizing
``evaluate_momentum_entry``: that function depends ENTIRELY on
DexScreener (pairs/liquidity/price) and GeckoTerminal (OHLCV candles) from
its very first step -- neither exists for a token still on a Virtuals
bonding curve (no DEX pool yet). Verified live, 24/07: a bonding token's
"liquidity" is the protocol's own BondingV5 contract reserve, never an
external LP the deployer could drain -- the whole "retrait de LP" risk that
``_MIN_LIQUIDITY_USD`` (momentum_entry.py) guards against doesn't apply the
same way here (same doctrine already established for a recognized launchpad
in ``knowledge/launchpads.yaml``: the mint/LP authority is the PROTOCOL's,
not an individual dev's).

Gates, deliberately DIFFERENT from the standard momentum pipeline (operator
go, 24/07 -- confirmed list from a live discussion on this exact tension):
  - DROPPED: GoPlus honeypot check (Base-DEX-oriented, structurally
    irrelevant to a bonding-curve trade -- there is no separate token
    contract logic to exploit here beyond the protocol's own, audited by
    virtue of being used by every Virtuals token).
  - DROPPED: the $50,000 liquidity floor (a Uniswap-pool-drain guard that
    doesn't apply to a protocol-owned bonding reserve).
  - DROPPED: golden-pocket/RSI computed on DexScreener/GeckoTerminal candles
    (don't exist) -- REPLACED, not simply dropped: real OHLCV IS
    reconstructible from ``vp-api.virtuals.io``'s individual-trade history
    (``services/virtuals.py::fetch_recent_trades`` +
    ``aggregate_trades_to_candles``), so the SAME ``entry_signals.detect_entry``
    engine as the standard pipeline is reused here, not abandoned.
  - KEPT/ADDED, Virtuals-native (found during diligence, 24/07 -- these
    fields are already returned by the SAME list endpoint
    ``fetch_by_address``/``fetch_by_pretoken`` already call, just never
    captured before): ``dev_holding_pct`` (the team-rug risk the operator
    asked to keep a guard on -- "le filtre de rug d'équipe") and
    ``top10_holder_pct`` (the Virtuals-native equivalent of
    ``_check_holder_concentration``, no Blockscout call needed).
  - KEPT: a minimum "market" floor -- ``liquidity_usd`` (already in USD,
    already returned by the API) used as the proxy, per the operator's own
    observation that liquidity and market cap track closely on a bonding
    curve ("liquidité quasiment 1 pour 1 avec le market cap").

Sizing: reuses ``paper_trader.compute_entry_alloc`` (same risk/ATR formula
as the standard pipeline) -- the caller (``paper_trader.py``) then applies
``BONDING_SIZE_REDUCTION`` on top, a dedicated extra reduction reflecting the
structurally higher risk of this path (no honeypot check, thinner overall
market), per the operator's explicit request for a more conservative sizing
here than the standard momentum tier.

Currency, a real gap found while writing this module (never shipped with the
bug): a bonding-curve trade is priced in $VIRTUAL per token
(``VirtualTrade.price``), never USD directly -- but ``paper_trader.py``'s
whole portfolio is 100% USD. Every price level returned here (entry/target/
invalidation) is converted through ``virtuals.virtual_usd_rate()`` before
being handed back -- ``entry_atr_pct`` is the one exception, deliberately
left unconverted: it's a RATIO (ATR / price, both computed in the same
$VIRTUAL unit from the same candles), the conversion factor cancels out
algebraically, converting it would be a no-op at best and a needless second
point of failure at worst.
"""
from __future__ import annotations

import logging

from aria_core.conviction_research import ConvictionResearch, research_project_potential
from aria_core.skills.entry_signals import detect_entry
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

# A marker, not a real EVM chain id -- lets paper_trader.py/position records
# distinguish this path from a standard Base momentum entry (bonding-aware
# price lookup, see paper_trader._default_pair_lookup) without a separate
# boolean column. Imported by paper_trader.py rather than re-typed there, to
# keep the two files from silently drifting apart on this literal.
CHAIN_MARKER = "virtuals-bonding"

# Extra sizing reduction applied ON TOP of the standard risk/ATR formula
# (paper_trader.compute_entry_alloc) -- structurally higher risk (no
# honeypot-class check exists for this path), operator-requested caution.
BONDING_SIZE_REDUCTION = 0.5

# Native Virtuals dev-rug guard (replaces dev_wallet.py/GoPlus, irrelevant
# here -- see module docstring). Generous relative to the confirmed real
# example (0.08%, "Zero team tokens") -- still catches a genuinely
# team-heavy launch without false-positiving on the common zero-team norm.
_MAX_DEV_HOLDING_PCT = 5.0

# Native Virtuals concentration guard (replaces
# momentum_entry._check_holder_concentration's 80% Blockscout-based check --
# same threshold, Virtuals-native field, no extra network call).
_MAX_TOP10_HOLDER_PCT = 80.0

# 24/07 -- real gap found live, right after deploy (operator: "ton truc marche
# pas"): tested against the 100 real bonding prototypes at the time, the gate
# above rejected EVERY SINGLE ONE, including the token with the most holders
# (33). Verified why: top10_holder_pct is computed over genuine EXTERNAL
# buyers only (confirmed live -- a token with 0 holders reads 0%, not 100%,
# ruling out the bonding pool's own reserve being counted). With only a
# handful of distinct buyers, the top 10 are mechanically ~100% of whatever
# has been bought so far -- not a rug signal, just too few participants for
# the ratio to mean anything (the operator's own screenshot of a GRADUATED
# token, 317 real holders, confirms the gate is meaningful once there's
# enough of a sample: top 10 = 54.2%, comfortably under this threshold).
# Below this floor the concentration ratio is treated as uninformative
# (neither for nor against, same "measure before I act" doctrine as
# momentum_entry._technical_alignment's None handling) -- the real anti-rug
# guard at this stage is dev_holding_pct (computed over the FULL supply, so
# it stays meaningful regardless of buyer count) plus the fact that a BUY
# signal already requires enough real trades to exist at all.
_MIN_HOLDERS_FOR_CONCENTRATION_CHECK = 15

# "Market" floor, expressed as the bonding pool's own liquidity (already in
# USD) -- proxy for market cap per the operator's own observation that the
# two track closely on a bonding curve. Deliberately far below
# momentum_entry._MIN_LIQUIDITY_USD (50,000$): that floor guards against an
# LP-drain rug which structurally doesn't apply here (see module docstring).
_MIN_LIQUIDITY_USD = 10_000.0

# Real trades are grouped into fixed-size buckets (never fixed time
# intervals -- see aggregate_trades_to_candles's own docstring for why).
_TRADES_PER_CANDLE = 5
_TRADES_FETCH_LIMIT = 200

# Same deterministic BUY threshold as the standard pipeline
# (momentum_entry._RR_MIN_FOR_DIRECT_BUY) -- no LLM tie-break branch here
# (V1, deliberately simpler scope): a positive but sub-2.0 R/R is a HOLD,
# never an ambiguous-path LLM call. Revisit once real trade data on this
# path justifies the extra complexity (same "measure before I act" doctrine
# already applied elsewhere in this pipeline).
_RR_MIN_FOR_DIRECT_BUY = 2.0
_ALIGN_SCORE_MIN_FOR_DIRECT_BUY = 2


def _hold(reason: str, hold_reason: str, *, symbol: str | None = None, price: float | None = None) -> dict:
    return {
        "action": "HOLD", "chain": CHAIN_MARKER, "symbol": symbol, "price": price,
        "reasons": [reason], "hold_reason": hold_reason,
    }


# Virtuals-native label -> the exact labels conviction_research.py looks for
# (its known_links parsing was written against dexscreener.py's own labels).
# "Site officiel"/"X (Twitter)" pick the primary website/X handle; "GitHub"/
# "Farcaster"/"Telegram" each trigger a REAL substance check in
# conviction_research._describe_other_known_link (repo age/activity via
# services/project_activity.py, Warpcast follower/anti-spam label, channel
# activity) -- not just "a link exists". Getting this mapping right is the
# whole point of this chantier (operator, 24/07): on a token this young, the
# only way to judge "philosophie du produit, comment ça a été construit" is
# by actually reading the GitHub repo, not by counting holders. Any other
# label passes through unmapped -- still weighed by the LLM synthesis as a
# declared link, just without a dedicated verification client.
_SOCIAL_LABEL_REMAP = {
    "website": "Site officiel",
    "twitter": "X (Twitter)",
    "x": "X (Twitter)",
    "github": "GitHub",
    "telegram": "Telegram",
    "farcaster": "Farcaster",
    "warpcast": "Farcaster",
}


def _socials_to_known_links(socials: list[dict]) -> list[dict]:
    """``VirtualToken.socials`` (real label seen live: "TWITTER", "WEBSITE")
    -> the label shape ``conviction_research.research_project_potential``
    already parses (built against ``dexscreener.PairSnapshot.project_links``)
    -- so a bonding token's own declared site/X link is used directly rather
    than re-discovered by heuristic, same shortcut the standard momentum
    pipeline already gets from DexScreener."""
    out: list[dict] = []
    for link in socials or []:
        if not isinstance(link, dict):
            continue
        url = link.get("url")
        if not url:
            continue
        label = str(link.get("label") or "").strip().lower()
        out.append({"label": _SOCIAL_LABEL_REMAP.get(label, link.get("label") or ""), "url": url})
    return out


async def evaluate_bonding_entry(
    token_address: str, *, weekly_context: dict | None = None, current_regime: str | None = None,
) -> dict | None:
    """Entry decision for a Virtuals bonding-curve candidate. Returns a dict
    compatible with ``paper_trader.run_paper_cycle``'s analyzer contract
    (``action``/``symbol``/``price``/``target``/``invalidation``/``chain``),
    or ``None`` if the token can't be resolved at all (never a fabricated
    signal -- same semantics as ``momentum_entry.evaluate_momentum_entry``).

    ``chain`` is always the literal string ``"virtuals-bonding"`` on the
    returned dict -- a marker, not a real EVM chain, so
    ``paper_trader``/position records can distinguish this path from a
    standard Base momentum entry without a separate boolean field."""
    from aria_core.services.virtuals import (
        aggregate_trades_to_candles, is_in_bonding, virtual_usd_rate, virtuals_client,
    )
    from aria_core.skills.indicators import atr_series
    from aria_core.momentum_entry import _technical_alignment

    token = await virtuals_client.fetch_by_address(token_address, chain="BASE")
    if token is None or (not token.token_address and not token.pre_token_address):
        return None
    if not is_in_bonding(token):
        # Already graduated (or status unknown) -- out of this module's
        # scope; the standard momentum pipeline takes over once a real DEX
        # pool exists, never duplicated here.
        return None

    symbol = token.symbol or "?"

    # Fail-CLOSED on an unknown value, same doctrine as the VC crible's own
    # dev-rug/concentration gates ("une donnée manquante bloque aussi -- jamais
    # OK par défaut") -- this gate exists specifically to replace dev_wallet.py/
    # GoPlus's concentration check for this path, so it inherits the same
    # seriousness, not a looser one.
    if token.dev_holding_pct is None or token.dev_holding_pct > _MAX_DEV_HOLDING_PCT:
        return _hold(
            f"détention équipe inconnue ou trop élevée ({token.dev_holding_pct}, seuil "
            f"{_MAX_DEV_HOLDING_PCT:.0f}%) -- risque de rug d'équipe",
            "dev_holding_too_high", symbol=symbol,
        )
    enough_holders_to_judge = (
        token.holder_count is not None and token.holder_count >= _MIN_HOLDERS_FOR_CONCENTRATION_CHECK
    )
    if enough_holders_to_judge and (
        token.top10_holder_pct is None or token.top10_holder_pct > _MAX_TOP10_HOLDER_PCT
    ):
        return _hold(
            f"concentration top 10 holders inconnue ou trop élevée ({token.top10_holder_pct}, "
            f"seuil {_MAX_TOP10_HOLDER_PCT:.0f}%, {token.holder_count} holders)",
            "holder_concentration", symbol=symbol,
        )
    if token.liquidity_usd is None or token.liquidity_usd < _MIN_LIQUIDITY_USD:
        return _hold(
            f"liquidité de la bonding pool insuffisante "
            f"({(token.liquidity_usd or 0.0):,.0f}$ < {_MIN_LIQUIDITY_USD:,.0f}$)",
            "insufficient_liquidity", symbol=symbol,
        )

    trades = await virtuals_client.fetch_recent_trades(token_address, limit=_TRADES_FETCH_LIMIT)
    candles: list[Candle] = aggregate_trades_to_candles(trades, trades_per_candle=_TRADES_PER_CANDLE)
    if not candles:
        return _hold(
            "aucun historique de trades exploitable -- R/R non calculable, pas d'entrée",
            "ohlcv_unavailable", symbol=symbol,
        )

    # All candle/signal levels below are in $VIRTUAL per token (the trades'
    # native unit) -- converted to USD only at the very end, once, right
    # before being returned. Unconverted here so entry_atr_pct's ratio stays
    # computed within a single consistent unit (see module docstring).
    execution_price_virtual = trades[0].price if trades else None
    signal = detect_entry(candles, execution_price=execution_price_virtual)
    reasons: list[str] = list(signal.reasons)
    if not signal.present or signal.rr is None or signal.rr <= 0:
        reasons.append("pas de setup golden pocket + divergence RSI avec R/R positif (bonding)")
        return {
            "action": "HOLD", "chain": CHAIN_MARKER, "symbol": symbol,
            "price": execution_price_virtual, "reasons": reasons, "hold_reason": "no_entry_signal",
        }

    align_score, align_reasons = _technical_alignment(candles)
    reasons.extend(align_reasons)

    if signal.rr < _RR_MIN_FOR_DIRECT_BUY or align_score < _ALIGN_SCORE_MIN_FOR_DIRECT_BUY:
        reasons.append(
            f"R/R ou alignement technique insuffisant ({signal.rr:.1f}, align {align_score}) "
            "-- pas de confirmation LLM sur ce chemin (V1, portée volontairement simple)"
        )
        return {
            "action": "HOLD", "chain": CHAIN_MARKER, "symbol": symbol,
            "price": execution_price_virtual, "reasons": reasons, "hold_reason": "rr_below_direct_threshold",
        }

    reasons.append(f"R/R franc ({signal.rr:.1f}) + alignement technique -- décision directe (bonding)")

    # Ratio -- unit-independent (ATR and price both in $VIRTUAL, from the same
    # candles), never converted (see module docstring).
    entry_atr_pct = None
    atr_values = atr_series(candles)
    last_atr = atr_values[-1] if atr_values else None
    if last_atr is not None and execution_price_virtual:
        entry_atr_pct = last_atr / execution_price_virtual

    usd_rate = await virtual_usd_rate()
    if usd_rate is None:
        reasons.append("taux $VIRTUAL/USD indisponible -- prix non convertible, pas d'entrée (jamais un prix inventé)")
        return {
            "action": "HOLD", "chain": CHAIN_MARKER, "symbol": symbol,
            "price": None, "reasons": reasons, "hold_reason": "usd_rate_unavailable",
        }

    price_usd = execution_price_virtual * usd_rate
    target_usd = signal.target * usd_rate if signal.target is not None else None
    invalidation_usd = signal.invalidation * usd_rate if signal.invalidation is not None else None

    # 24/07 -- operator's own reasoning, right after seeing the concentration
    # gate fail on the entire real bonding market: on a token this young,
    # on-chain metrics (holders, concentration) are structurally too thin to
    # mean anything -- the real edge is a bet on the PRODUCT/TEAM/adoption
    # potential, same conviction diligence already live on the standard
    # momentum pipeline (conviction_research.py, ARIA_CONVICTION_RESEARCH_
    # ENABLED). Reused as-is here, never duplicated -- AFTER everything else,
    # only on a candidate already about to be bought (same doctrine as
    # momentum_entry.py, preserves this path's speed). Sizing-only, NEVER a
    # gate: paper_trader.compute_entry_alloc already reads potential_score
    # from this dict with no further change needed.
    #
    # Operator's explicit nuance (24/07, same segment): a discreet/quiet team
    # is not the same as a worthless one -- some legitimate builders are quiet
    # right up until real traction makes them visible. A low posting cadence
    # or few sources found must NOT read as a negative signal -- it stays
    # `potential_score=None` (fail-open, neither a bonus nor a malus) rather
    # than being scored down for lack of noise.
    potential_score = None
    conviction_process_trail: str | None = None
    conviction_website_corroborated: bool | None = None
    conviction_posting_cadence: str | None = None
    research = await research_project_potential(
        # "base" (not CHAIN_MARKER): a cleaner Tavily search query (Virtuals
        # bonding tokens are Base contracts) and shares the SAME cache entry
        # with the standard momentum pipeline's own diligence on this same
        # contract once it graduates -- never a redundant re-search.
        token_address, symbol, "base", known_links=_socials_to_known_links(token.socials),
    )
    if research.available:
        if research.process_trail:
            reasons.append("diligence de conviction : " + " -> ".join(research.process_trail))
            conviction_process_trail = " -> ".join(research.process_trail)
        conviction_website_corroborated = research.contract_corroborated
        conviction_posting_cadence = research.posting_cadence
        if research.potential_score is not None:
            potential_score = research.potential_score
            reasons.append(
                f"potentiel fondamental {potential_score:.1f}/10 "
                f"(site {'trouvé' if research.website_url else 'introuvable'}, "
                f"cadence X {research.posting_cadence})"
                + (f" : {research.rationale}" if research.rationale else "")
            )

    return {
        "action": "BUY",
        "chain": CHAIN_MARKER,
        "symbol": symbol,
        "price": price_usd,
        "target": target_usd,
        "invalidation": invalidation_usd,
        "potential_score": potential_score,
        "conviction_process_trail": conviction_process_trail,
        "conviction_website_corroborated": conviction_website_corroborated,
        "conviction_posting_cadence": conviction_posting_cadence,
        "rr": signal.rr,
        "align_score": align_score,
        "liquidity_usd": token.liquidity_usd,
        "entry_atr_pct": entry_atr_pct,
        "strategy": "momentum",
        "reasons": reasons,
        "hold_reason": None,
        "regime": current_regime or "neutre",
        "these": (
            f"Bonding Virtuals -- dev holding {token.dev_holding_pct:.2f}%, "
            f"top10 holders {token.top10_holder_pct:.1f}%, "
            f"prix converti au taux $VIRTUAL/USD {usd_rate:.4f}."
        ),
    }
