"""Aria Market host plugins registered into aria-core at boot (yeux marché)."""

from __future__ import annotations

from aria_core.locale import (
    LANG_FR,
    portfolio_buy_signals,
    portfolio_failed,
    portfolio_header,
    portfolio_neutral,
    portfolio_sell_warnings,
)
from aria_core.memory import append_memory
from app.analysis.engine import analysis_engine
from app.database import get_watchlist
from app.models.schemas import SignalType
from app.services.dexscreener import dexscreener_client


async def run_portfolio_analysis(lang: str = LANG_FR) -> tuple[str, dict]:
    watchlist = await get_watchlist()
    if not watchlist:
        from aria_core.locale import portfolio_empty

        return portfolio_empty(lang), {"items": 0}

    results: list[dict] = []
    buy_signals: list[str] = []
    sell_warnings: list[str] = []
    total_score = 0.0
    analyzed = 0

    for item in watchlist[:5]:
        pair = await dexscreener_client.get_pair(item.chain_id, item.pair_address)
        if not pair:
            continue
        analysis = await analysis_engine.analyze_pair(pair, timeframes=None)
        if not analysis.timeframes:
            continue

        analyzed += 1
        total_score += analysis.global_score
        entry = {
            "symbol": pair.base_token.symbol,
            "score": analysis.global_score,
            "consensus": analysis.consensus.value,
            "summary": analysis.summary,
        }
        results.append(entry)

        if analysis.consensus == SignalType.BUY:
            buy_signals.append(f"{pair.base_token.symbol} ({analysis.global_score}/100)")
        elif analysis.consensus == SignalType.SELL:
            sell_warnings.append(f"{pair.base_token.symbol} ({analysis.global_score}/100)")

    if analyzed == 0:
        return portfolio_failed(lang), {"items": 0}

    avg_score = total_score / analyzed
    lines = portfolio_header(lang, analyzed, avg_score)
    if buy_signals:
        lines.append(portfolio_buy_signals(lang, ", ".join(buy_signals)))
    if sell_warnings:
        lines.append(portfolio_sell_warnings(lang, ", ".join(sell_warnings)))
    if not buy_signals and not sell_warnings:
        lines.append(portfolio_neutral(lang))

    for r in results:
        lines.append(f"- {r['symbol']}: {r['score']}/100 ({r['consensus']})")

    summary = "\n".join(lines)
    append_memory("portfolio", summary)
    return summary, {"items": analyzed, "avg_score": avg_score, "results": results}


def register_aria_host_integrations() -> None:
    from pathlib import Path

    from aria_core import bootstrap
    from app.auth.access_code import DB_PATH, init_auth_db
    from app.auth.rate_limit import check_rate_limit
    from app.config import settings
    from app.games.scores import get_score
    from app.paths import data_dir

    bootstrap.configure(data_dir=data_dir(), settings=settings)
    bootstrap.register_host_integrations(
        get_watchlist=get_watchlist,
        get_game_score=get_score,
        init_auth_db=init_auth_db,
        auth_db_path=Path(DB_PATH),
        check_rate_limit=check_rate_limit,
        run_portfolio_analysis_fn=run_portfolio_analysis,
    )