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
_reset_operator_failed_attempts: Callable[[str], Awaitable[bool]] | None = None
_generate_operator_invite_code: Callable[[], Awaitable[str]] | None = None

run_portfolio_analysis = None  # set by register()


def register(
    *,
    get_watchlist: Callable[[], Awaitable[list[Any]]] | None = None,
    get_game_score: Callable[..., Awaitable[int | None]] | None = None,
    init_auth_db: Callable[[], Awaitable[None]] | None = None,
    auth_db_path: Path | None = None,
    check_rate_limit: Callable[..., bool] | None = None,
    run_portfolio_analysis_fn: Callable[[str], Awaitable[tuple[str, dict]]] | None = None,
    reset_operator_failed_attempts_fn: Callable[[str], Awaitable[bool]] | None = None,
    generate_operator_invite_code_fn: Callable[[], Awaitable[str]] | None = None,
) -> None:
    global _get_watchlist, _get_game_score, _init_auth_db, _auth_db_path, _check_rate_limit
    global _run_portfolio_analysis, run_portfolio_analysis, _reset_operator_failed_attempts
    global _generate_operator_invite_code
    _get_watchlist = get_watchlist
    _get_game_score = get_game_score
    _init_auth_db = init_auth_db
    _auth_db_path = auth_db_path
    _check_rate_limit = check_rate_limit
    _run_portfolio_analysis = run_portfolio_analysis_fn
    run_portfolio_analysis = run_portfolio_analysis_fn
    _reset_operator_failed_attempts = reset_operator_failed_attempts_fn
    _generate_operator_invite_code = generate_operator_invite_code_fn


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


async def reset_operator_failed_attempts(username: str) -> bool:
    """Item #201 -- second, SSH-independent unlock path for the operator mobile
    account's progressive login slowdown (Telegram /unlockmobile, owner-only).
    False (never raises) if the host never registered this hook -- e.g. a
    non-vanguard host, or the mobile account feature not wired up yet."""
    if _reset_operator_failed_attempts is None:
        return False
    return await _reset_operator_failed_attempts(username)


async def generate_operator_invite_code() -> str | None:
    """08/07 -- Privy auth redesign: the /mobileinvite Telegram command's real
    implementation, same host-hook bridge pattern as reset_operator_failed_
    attempts above. None (never raises) if the host never registered this
    hook -- e.g. a non-vanguard host, or the feature not wired up yet."""
    if _generate_operator_invite_code is None:
        return None
    return await _generate_operator_invite_code()
