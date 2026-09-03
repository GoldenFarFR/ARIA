"""ARIA RADAR V1 -- observation-only Telegram alert on discovery qualification.

03/09, operator go (Chantier C, distinct from the trading pipeline itself):
fires the moment a candidate passes on-chain discovery qualification
(``onchain_pool_discovery.check_candidates()``), BEFORE the regime gate --
see ``shadow_persistent.py``'s ``robinhood_discovery_loop`` for the call
site. Deliberately upstream of any trading decision: the regime gate is a
TRADING gate, not a discovery sensor, so gating the radar on it would
throw away exactly the timing data this alert exists to capture (how many
candidates appear, which ones later matter, how long from detection to
regime opening).

Format frozen by the operator verbatim -- do not restyle without a new
explicit go. The one hard rule behind every field below: never render a
number the pipeline didn't actually measure. Day-zero discovery
(``onchain_pool_discovery.py``) never produces a real ``price_change_pct``
(it's always ``{}`` at this stage -- momentum/acceleration have no source
here), and no CHARTISTE/SOCIAL score exists at all until the Fusion Engine
(Chantier B) is built. Both render an explicit ``N/A``, never a blank or a
fabricated 0. Buy/Sell and Volume ARE real here (``onchain_activity_
observation.py``'s WS snapshot, bricks 1-3 of the on-chain sensors roadmap,
already wired into ``TrendingPool`` by ``onchain_pool_discovery.py`` at
qualification time) -- rendered when present, ``N/A`` when the snapshot
was never available for that candidate.

Pure text formatting, no I/O, no network call -- the caller owns sending
(``telegram_notify.send``, same bot/chat as every other shadow notification,
never a parallel system).
"""
from __future__ import annotations

from aria_core.services.geckoterminal import TrendingPool

_NA = "N/A — non calculé"
_SEP = "━━━━━━━━━━━━━━━━"


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}" if value >= 1 else f"${value:.6f}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:.6f}" if value < 1 else f"${value:,.4f}"


def format_qualified_candidate(
    pool: TrendingPool, *, chain: str, regime_open: bool, liquidity_floor_usd: float,
) -> str:
    """Render the ARIA RADAR V1 text for one just-qualified candidate.

    ``regime_open`` reflects the chain-level regime gate's CURRENT state --
    informational only, never gates whether this function is called (the
    caller decides that; see this module's own docstring)."""
    symbol = pool.symbol or "?"

    buy_sell = (
        f"{pool.buy_count}/{pool.sell_count}"
        if pool.buy_count is not None and pool.sell_count is not None
        else "N/A"
    )
    volume = (
        f"{pool.cumulative_volume_quote:.4g} (quote units)"
        if pool.cumulative_volume_quote is not None
        else "N/A"
    )
    regime_label = "OPEN" if regime_open else "CLOSED"

    why_now = ["• Candidat vient de passer la qualification (on-chain, day-zero)"]
    if pool.reserve_usd is not None:
        why_now.append(
            f"• Liquidité initiale {_fmt_usd(pool.reserve_usd)} "
            f"(plancher {_fmt_usd(liquidity_floor_usd)})"
        )
    if pool.buy_count is not None and pool.sell_count is not None:
        why_now.append(f"• Buy/Sell observé: {buy_sell}")

    risks = [
        "• Poche day-zero — pas de garantie de profondeur au-delà de la liquidité observée",
        "• Aucun score CHARTISTE/SOCIAL à ce stade (Fusion Engine non construit)",
        f"• Régime: {regime_label}",
    ]

    return f"""🚨 ARIA RADAR V1 — ${symbol}

STATUS: QUALIFIED
Chain: {chain}

{_SEP}
⛓️ ON-CHAIN
{_SEP}

Prix:           {_fmt_price(pool.price_usd)}
Momentum:       {_NA} (day-zero, pas de fenêtre de variation mesurée)
Liquidity:      {_fmt_usd(pool.reserve_usd)}
Reserve:        {_fmt_usd(pool.reserve_usd)}
Buy/Sell:       {buy_sell}
Volume:         {volume}
Acceleration:   {_NA} (dérive du Momentum, non disponible)

{_SEP}
🧠 OTHER SIGNALS
{_SEP}

CHARTISTE: {_NA}
SOCIAL:    {_NA}

{_SEP}
🎯 POURQUOI MAINTENANT ?
{_SEP}

{chr(10).join(why_now)}

{_SEP}
⚠️ RISQUES
{_SEP}

{chr(10).join(risks)}

{_SEP}
📊 DÉCISION
{_SEP}

QUALIFIED — OBSERVATION ONLY"""
