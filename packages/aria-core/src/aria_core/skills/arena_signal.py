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

Contrainte découverte le 09/07 (testée en direct sur l'API réelle, error_code
10012) : le tier gratuit CoinGecko refuse toute requête portant sur des données
de plus de 365 jours, quelle que soit la taille de la fenêtre demandée (pas un
souci de découpage). Le RSI (qui n'a besoin que de quelques semaines) utilise
donc une fenêtre RÉCENTE dédiée (``_RSI_WINDOW_DAYS``), distincte de
l'historique complet 10 ans de `btc_cycles` (qui, lui, reste structurellement
hors de portée du tier gratuit — cf. tâche #62 pour une source alternative).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aria_core.skills.btc_cycles import BTC_COIN_ID, fetch_current_macro_phase
from aria_core.skills.entry_signals import rsi_series

NOTE = (
    "Deterministic ARIA signals only (no LLM guess). Missing fields mean the "
    "underlying data was unavailable right now, not zero. RSI is computed from "
    "real daily BTC/USD closes (CoinGecko, last ~90 days — CoinGecko's free "
    "tier hard-caps historical queries at 365 days); no OHLC candle source is "
    "wired for BTC yet, so Fibonacci/golden-pocket/divergence signals are not "
    "included here (would require fabricating candles from close-only data)."
)

_RSI_WINDOW_DAYS = 90  # marge large au-dessus du minimum RSI-14 (~15j), reste << 365j


async def _fetch_recent_btc_closes(*, client=None) -> list[float] | None:
    """Fenêtre RÉCENTE (≤365j, contrainte CoinGecko gratuit) pour le RSI —
    distincte de l'historique complet 10 ans utilisé par `btc_cycles`."""
    if client is None:
        from aria_core.services.coingecko import coingecko_client as client

    now = datetime.now(timezone.utc)
    start_ts = int((now - timedelta(days=_RSI_WINDOW_DAYS)).timestamp())
    end_ts = int(now.timestamp())
    result = await client.get_market_chart_range(BTC_COIN_ID, start_ts, end_ts)
    if not result.available or not result.prices:
        return None
    return [p for _, p in sorted(result.prices, key=lambda x: x[0])]


async def fetch_btc_arena_signal(*, client=None) -> dict:
    """Point d'entrée compact pour l'endpoint public `/api/aria/arena-signal/btc`."""
    phase = await fetch_current_macro_phase(client=client)

    closes = await _fetch_recent_btc_closes(client=client)
    rsi_14: float | None = None
    if closes:
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
