"""Optional hooks supplied by aria-vanguard host."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

_get_watchlist: Callable[[], Awaitable[list[Any]]] | None = None
_get_game_score: Callable[..., Awaitable[int | None]] | None = None
_init_auth_db: Callable[[], Awaitable[None]] | None = None
_auth_db_path: Path | None = None
_check_rate_limit: Callable[..., bool] | None = None
_run_portfolio_analysis: Callable[[str], Awaitable[tuple[str, dict]]] | None = None

run_portfolio_analysis = None  # set by register()


def register(
    *,
    get_watchlist: Callable[[], Awaitable[list[Any]]] | None = None,
    get_game_score: Callable[..., Awaitable[int | None]] | None = None,
    init_auth_db: Callable[[], Awaitable[None]] | None = None,
    auth_db_path: Path | None = None,
    check_rate_limit: Callable[..., bool] | None = None,
    run_portfolio_analysis_fn: Callable[[str], Awaitable[tuple[str, dict]]] | None = None,
) -> None:
    global _get_watchlist, _get_game_score, _init_auth_db, _auth_db_path, _check_rate_limit
    global _run_portfolio_analysis, run_portfolio_analysis
    _get_watchlist = get_watchlist
    _get_game_score = get_game_score
    _init_auth_db = init_auth_db
    _auth_db_path = auth_db_path
    _check_rate_limit = check_rate_limit
    _run_portfolio_analysis = run_portfolio_analysis_fn
    run_portfolio_analysis = run_portfolio_analysis_fn


async def get_watchlist() -> list[Any]:
    if _get_watchlist is None:
        return []
    return await _get_watchlist()


async def get_game_score(**kwargs: Any) -> int | None:
    if _get_game_score is None:
        return None
    return await _get_game_score(**kwargs)


async def init_auth_db() -> None:
    if _init_auth_db is not None:
        await _init_auth_db()


def auth_db_path() -> Path:
    return _auth_db_path or Path("data/auth.db")


def check_rate_limit(*args: Any, **kwargs: Any) -> bool:
    if _check_rate_limit is None:
        return True
    return _check_rate_limit(*args, **kwargs)
