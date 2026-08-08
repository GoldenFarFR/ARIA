"""Burn-mechanism detector — separates a real, active burn from marketing narrative.

08/08 -- real incident that motivated this: gitlawb's own site advertised a
"REVENUE FLYWHEEL... LIVE" (inference revenue market-buys and burns
$GITLAWB, "every run public"). The project's own public burn leaderboard
told a different story: $8,010 cumulative burned against a $2.46M market
cap on a 100B supply (~0.33% of supply) -- negligible, not the structural
mechanism the marketing implied. Manually cross-checking a token's own
claims against its real burn leaderboard doesn't scale; this module answers
the question on-chain instead of trusting what a token's own site says.

Reads Blockscout's real transfer history for the token and looks for
transfers TO a known burn address (0x0, 0x...dEaD, and the same
community-burn address already excluded from holder concentration in
``skills/acp_onchain_scan.py``'s ``_BURN_ADDRESSES``). Blockscout's
``/tokens/{address}/transfers`` endpoint accepts no destination filter
(confirmed live), so this only ever covers the MOST RECENT window of
transfers (``max_pages`` x ``limit``) -- never a full history. A
high-volume token's window may span only hours; a quiet one may span
months. The window actually covered (``window_start``/``window_end``) is
always returned so a caller never mistakes a narrow window for an
exhaustive one.

Thresholds below (0.5% of supply burned in-window, burns spread across >=2
distinct weeks) are an initial empirical calibration from this session's
real comparisons (gitlawb ~0.33% cumulative = negligible; RAY's recent
trailing-12mo burn ~3.7% of max supply per Tokenomist's own numbers =
real), not a settled science -- recalibrate as more real tokens are
observed through this detector.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# Same set as acp_onchain_scan.py's _BURN_ADDRESSES -- kept as a separate
# copy deliberately (this module must stay usable standalone, without
# importing the VC-crible module and its much heavier dependency graph).
_BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0xdead000000000000000042069420694206942069",
}

# Initial calibration (08/08, see module docstring) -- a real, currently-
# active mechanism should burn a non-trivial share of supply AND do so
# more than once, spread over time (distinguishes an automated recurring
# flywheel from a single one-off marketing burn).
MIN_SUPPLY_BURNED_PCT_IN_WINDOW = 0.5
MIN_DISTINCT_WEEKS_WITH_BURN = 2

VERDICT_SIGNIFICANT_RECURRING = "significant_recurring"
VERDICT_MINOR_OR_MARKETING_ONLY = "minor_or_marketing_only"
VERDICT_NONE_DETECTED_IN_WINDOW = "none_detected_in_window"
VERDICT_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class BurnMechanismAssessment:
    """Real burn activity found for a token, on-chain -- never a token's
    own marketing claim taken at face value."""

    contract: str
    available: bool
    verdict: str = VERDICT_UNAVAILABLE
    error: str | None = None
    total_burned: float | None = None
    burn_events: int = 0
    distinct_weeks_with_burn: int = 0
    supply_burned_pct_in_window: float | None = None
    window_start: str | None = None  # oldest transfer timestamp actually covered
    window_end: str | None = None    # newest transfer timestamp actually covered
    window_truncated: bool = False   # True if more history existed beyond max_pages
    note: str = ""


def _iso_week_key(timestamp: str) -> str | None:
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


async def assess_burn_mechanism(
    contract: str, *, client=None, max_pages: int = 10, limit: int = 500,
) -> BurnMechanismAssessment:
    """Best-effort, never raises -- any Blockscout failure degrades to
    ``VERDICT_UNAVAILABLE`` with the real error, never a guessed verdict."""
    if client is None:
        from aria_core.services.blockscout import blockscout_client as client

    metadata = await client.get_token_metadata(contract)
    if not metadata.available or not metadata.total_supply:
        return BurnMechanismAssessment(
            contract=contract, available=False,
            error=metadata.error or "supply totale indisponible",
            note="mécanisme de burn non évaluable (supply totale indisponible)",
        )

    transfers_result = await client.get_token_transfers_for_token(
        contract, limit=limit, max_pages=max_pages,
    )
    if not transfers_result.available:
        return BurnMechanismAssessment(
            contract=contract, available=False,
            error=transfers_result.error or "historique des transferts indisponible",
            note="mécanisme de burn non évaluable (historique des transferts indisponible)",
        )

    transfers = transfers_result.transfers
    if not transfers:
        return BurnMechanismAssessment(
            contract=contract, available=True, verdict=VERDICT_NONE_DETECTED_IN_WINDOW,
            note="aucun transfert trouvé pour ce token",
        )

    timestamps = [t.timestamp for t in transfers if t.timestamp]
    window_start = min(timestamps) if timestamps else None
    window_end = max(timestamps) if timestamps else None

    burns = [t for t in transfers if t.to_address.lower() in _BURN_ADDRESSES and t.amount]
    if not burns:
        return BurnMechanismAssessment(
            contract=contract, available=True, verdict=VERDICT_NONE_DETECTED_IN_WINDOW,
            window_start=window_start, window_end=window_end,
            window_truncated=transfers_result.truncated,
            note=(
                "aucun burn détecté dans la fenêtre récente observée "
                f"({len(transfers)} transferts, {_span_note(window_start, window_end)})"
            ),
        )

    total_burned = sum(t.amount for t in burns if t.amount is not None)
    distinct_weeks = {wk for t in burns if t.timestamp and (wk := _iso_week_key(t.timestamp))}
    supply_burned_pct = (total_burned / metadata.total_supply) * 100 if metadata.total_supply else None

    significant = (
        supply_burned_pct is not None
        and supply_burned_pct >= MIN_SUPPLY_BURNED_PCT_IN_WINDOW
        and len(distinct_weeks) >= MIN_DISTINCT_WEEKS_WITH_BURN
    )
    verdict = VERDICT_SIGNIFICANT_RECURRING if significant else VERDICT_MINOR_OR_MARKETING_ONLY

    pct_str = f"{supply_burned_pct:.3f}%" if supply_burned_pct is not None else "?"
    if significant:
        note = (
            f"burn actif : {pct_str} de la supply brûlée sur {len(burns)} événements "
            f"répartis sur {len(distinct_weeks)} semaines distinctes "
            f"({_span_note(window_start, window_end)})"
        )
    else:
        note = (
            f"burn négligeable ou ponctuel : {pct_str} de la supply brûlée sur "
            f"{len(burns)} événement(s), {len(distinct_weeks)} semaine(s) distincte(s) "
            f"({_span_note(window_start, window_end)}) — sous le seuil de "
            f"{MIN_SUPPLY_BURNED_PCT_IN_WINDOW}% / {MIN_DISTINCT_WEEKS_WITH_BURN} semaines"
        )

    return BurnMechanismAssessment(
        contract=contract, available=True, verdict=verdict,
        total_burned=total_burned, burn_events=len(burns),
        distinct_weeks_with_burn=len(distinct_weeks),
        supply_burned_pct_in_window=supply_burned_pct,
        window_start=window_start, window_end=window_end,
        window_truncated=transfers_result.truncated, note=note,
    )


def _span_note(window_start: str | None, window_end: str | None) -> str:
    if not window_start or not window_end:
        return "fenêtre temporelle inconnue"
    try:
        start = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
        end = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "fenêtre temporelle inconnue"
    span = end - start
    if span < timedelta(hours=1):
        return "fenêtre < 1h — volume de trading très élevé, jamais un historique complet"
    if span < timedelta(days=1):
        return f"fenêtre ~{span.total_seconds() / 3600:.1f}h"
    return f"fenêtre ~{span.days}j"
