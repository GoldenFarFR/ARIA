"""Signal BTC pour agents de trading tiers (seam #60, Arena Virtuals/Shekel).

Expose les analyses BTC RÉELLES et déjà existantes d'ARIA (cycle macro halving,
RSI) sous une forme compacte consommable par un endpoint HTTP public en lecture
seule (contrat "Custom Data Endpoint" de Shekel : GET, JSON, aucune auth). Ne
recalcule rien : réutilise `btc_cycles` (cache 1h) et le RSI de Wilder déjà
câblé pour le rapport `/vc` (`entry_signals.rsi_series`), jamais un doublon de
client externe.

Facts-only, dégradation honnête : un champ manquant (RSI si l'historique est
trop court, cycle si CoinGecko est indisponible) est omis (``None``), jamais
remplacé par une valeur inventée — même doctrine que `btc_cycles`/`entry_signals`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aria_core.skills.btc_cycles import fetch_btc_history, fetch_current_macro_phase
from aria_core.skills.entry_signals import rsi_series

NOTE = (
    "Deterministic ARIA signals only (no LLM guess). Missing fields mean the "
    "underlying data was unavailable right now, not zero. RSI is computed from "
    "real daily BTC/USD closes (CoinGecko); no OHLC candle source is wired for "
    "BTC yet, so Fibonacci/golden-pocket/divergence signals are not included "
    "here (would require fabricating candles from close-only data)."
)


async def fetch_btc_arena_signal(*, client=None) -> dict:
    """Point d'entrée compact pour l'endpoint public `/api/aria/arena-signal/btc`."""
    phase = await fetch_current_macro_phase(client=client)

    prices = await fetch_btc_history(client=client)
    rsi_14: float | None = None
    if prices:
        closes = [p for _, p in sorted(prices, key=lambda x: x[0])]
        series = rsi_series(closes)
        for value in reversed(series):
            if value is not None:
                rsi_14 = round(value, 1)
                break

    return {
        "btc_cycle_phase": phase.get("label") if phase else None,
        "btc_cycle_change_pct": phase.get("change_pct") if phase else None,
        "btc_cycle_since": phase.get("since") if phase else None,
        "btc_rsi_14": rsi_14,
        "note": NOTE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
