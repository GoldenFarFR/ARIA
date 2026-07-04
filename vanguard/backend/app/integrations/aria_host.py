"""Aria Market host plugins registered into aria-core at boot (yeux marché)."""

from __future__ import annotations

import os

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


def _env_flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


def _apply_operator_llm_profile() -> None:
    """Founder mode + budgets LLM depuis .env / vault (shell opérateur)."""
    from app.config import settings

    founder = _env_flag("ARIA_OPERATOR_FOUNDER_MODE")
    settings.aria_operator_founder_mode = founder

    depth = (os.environ.get("ARIA_LLM_DEPTH_DEFAULT") or "").strip()
    if depth:
        settings.aria_llm_depth_default = depth
    elif founder:
        settings.aria_llm_depth_default = "standard"

    if founder:
        settings.aria_epistemic_web_verify = _env_flag(
            "ARIA_EPISTEMIC_WEB_VERIFY", default=False,
        )
        settings.aria_epistemic_critic = _env_flag(
            "ARIA_EPISTEMIC_CRITIC", default=False,
        )
        settings.aria_llm_cost_footer = _env_flag(
            "ARIA_LLM_COST_FOOTER", default=False,
        )
    elif os.environ.get("ARIA_EPISTEMIC_WEB_VERIFY"):
        settings.aria_epistemic_web_verify = _env_flag("ARIA_EPISTEMIC_WEB_VERIFY")
    if os.environ.get("ARIA_EPISTEMIC_CRITIC"):
        settings.aria_epistemic_critic = _env_flag("ARIA_EPISTEMIC_CRITIC")
    if os.environ.get("ARIA_LLM_COST_FOOTER"):
        settings.aria_llm_cost_footer = _env_flag("ARIA_LLM_COST_FOOTER")

    int_fields = (
        ("ARIA_LLM_MAX_TOKENS_BRIEF", "aria_llm_max_tokens_brief"),
        ("ARIA_LLM_MAX_TOKENS_STANDARD", "aria_llm_max_tokens_standard"),
        ("ARIA_LLM_MAX_TOKENS_DEVELOP", "aria_llm_max_tokens_develop"),
        ("ARIA_LLM_CONTEXT_MAX_BRIEF", "aria_llm_context_max_brief"),
        ("ARIA_LLM_CONTEXT_MAX_STANDARD", "aria_llm_context_max_standard"),
        ("ARIA_LLM_CONTEXT_MAX_DEVELOP", "aria_llm_context_max_develop"),
    )
    for env_key, attr in int_fields:
        raw = (os.environ.get(env_key) or "").strip()
        if raw.isdigit():
            setattr(settings, attr, int(raw))


def register_aria_host_integrations() -> None:
    from pathlib import Path

    from aria_core import bootstrap
    from aria_core.spark_config import apply_spark_to_environ, resolve_spark_runtime
    from app.auth.access_code import DB_PATH, init_auth_db
    from app.auth.rate_limit import check_rate_limit
    from app.config import settings
    from app.games.scores import get_score
    from app.paths import data_dir

    cfg = resolve_spark_runtime(bridge_keys=True)
    apply_spark_to_environ(cfg)
    settings.llm_provider = cfg.provider
    settings.llm_model = cfg.llm_model
    if cfg.virtuals_api_key:
        settings.virtuals_api_key = cfg.virtuals_api_key
    settings.aria_llm_model_standard = cfg.aria_llm_model_standard
    settings.aria_llm_model_develop = cfg.aria_llm_model_develop
    settings.aria_llm_model_brief = cfg.aria_llm_model_brief
    settings.aria_spark_aggressive = cfg.aria_spark_aggressive
    _apply_operator_llm_profile()

    bootstrap.configure(data_dir=data_dir(), settings=settings)
    bootstrap.register_host_integrations(
        get_watchlist=get_watchlist,
        get_game_score=get_score,
        init_auth_db=init_auth_db,
        auth_db_path=Path(DB_PATH),
        check_rate_limit=check_rate_limit,
        run_portfolio_analysis_fn=run_portfolio_analysis,
    )