from __future__ import annotations

from aria_core.locale import (
    LANG_FR,
    portfolio_buy_signals,
    portfolio_empty,
    portfolio_failed,
    portfolio_header,
    portfolio_neutral,
    portfolio_sell_warnings,
)
from aria_core.locale import LANG_FR, portfolio_empty
from aria_core.integrations.host_hooks import run_portfolio_analysis as _host_portfolio


async def execute_portfolio_analysis(lang: str = LANG_FR) -> tuple[str, dict]:
    """DEX portfolio scan — implemented by dexpulse host (yeux marché)."""
    if _host_portfolio is None:
        return portfolio_empty(lang), {"items": 0, "error": "host_integration_missing"}
    return await _host_portfolio(lang)