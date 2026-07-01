"""Host integration — aria-vanguard calls configure() before importing brain modules."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Awaitable

from aria_core import runtime
from aria_core.paths import configure_data_dir
from aria_core.integrations import host_hooks


def configure(*, data_dir: Path, settings: Any) -> None:
    configure_data_dir(data_dir)
    runtime.configure(settings)


def register_host_integrations(
    *,
    get_watchlist: Callable[[], Awaitable[list]] | None = None,
    get_game_score: Callable[..., Awaitable[int | None]] | None = None,
    init_auth_db: Callable[[], Awaitable[None]] | None = None,
    auth_db_path: Path | None = None,
    check_rate_limit: Callable[..., bool] | None = None,
    run_portfolio_analysis_fn: Callable[[str], Awaitable[tuple[str, dict]]] | None = None,
) -> None:
    host_hooks.register(
        get_watchlist=get_watchlist,
        get_game_score=get_game_score,
        init_auth_db=init_auth_db,
        auth_db_path=auth_db_path,
        check_rate_limit=check_rate_limit,
        run_portfolio_analysis_fn=run_portfolio_analysis_fn,
    )
