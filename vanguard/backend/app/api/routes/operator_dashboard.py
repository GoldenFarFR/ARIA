"""Operator-only trading dashboard (06/08, operator request): open paper-
trading positions (contract, entry/TP/SL, thesis) + real OHLCV candles, for
a private DexScreener-like panel with TP/SL/entry lines drawn on the chart.

Deliberately reuses the SAME session auth as the mobile operator channel
(operator_mobile.require_operator_or_session) -- NEVER the diagnostics
static token (aria_core.diagnostics_access.verify_diagnostic_access): that
token is meant for server-to-server/curl calls (Claude Code sessions), never
safe to ship inside frontend JS where it would be inspectable by anyone
opening devtools.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request

from aria_core import paper_trader
from aria_core.services.ohlcv import ohlcv_client

from app.api.routes.operator_mobile import require_operator_or_session

router = APIRouter(prefix="/aria/ops/dashboard", tags=["operator-dashboard"])

# Fields worth shipping to the dashboard -- deliberately NOT the full
# _POS_FIELDS list (internal scoring/telemetry columns like align_ema/
# align_macd/conviction_process_trail stay server-side, no reason to grow
# the frontend contract every time an internal field is added).
_DASHBOARD_POSITION_FIELDS = (
    "id", "contract", "chain", "symbol", "wallet", "pocket", "mode",
    "entry_price", "target_price", "invalidation_price", "high_water_price",
    "qty", "cost_usd", "opened_at", "thesis", "rr", "strategy",
)


@router.get("/positions")
async def open_positions(request: Request):
    """Every currently open paper position, across all pockets -- the
    dashboard's "favorites" list is exactly this (a position IS the
    favorite, no separate bookmark concept needed)."""
    await require_operator_or_session(request)
    positions = await paper_trader.get_open_positions()
    return {
        "positions": [
            {k: p.get(k) for k in _DASHBOARD_POSITION_FIELDS} for p in positions
        ],
    }


@router.get("/candles")
async def candles(request: Request, contract: str, chain: str = "base", pool_address: str = ""):
    """Real OHLCV series for one contract -- same client ARIA's own decision
    pipeline uses internally (services/ohlcv.py), never exposed via any API
    before this route. ``pool_address`` optional: resolved live via
    DexScreener when absent (same lookup paper_trader's own price/risk loop
    already uses, no duplicated logic)."""
    await require_operator_or_session(request)
    pool = pool_address.strip()
    if not pool:
        pair = await paper_trader._default_pair_lookup(contract, chain=chain)
        if pair is None:
            return {"available": False, "error": "aucun pool liquide trouvé", "candles": []}
        pool = pair.pair_address
    result = await ohlcv_client.get_ohlcv(pool, network=chain)
    return {
        "available": result.available,
        "error": result.error,
        "timeframe": result.timeframe,
        "pool_address": pool,
        "candles": [asdict(c) for c in result.candles],
    }
