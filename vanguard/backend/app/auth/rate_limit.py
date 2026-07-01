"""Simple in-memory rate limiting for auth endpoints."""

from __future__ import annotations

import time
from collections import defaultdict

_attempts: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(
    key: str,
    *,
    max_attempts: int = 5,
    window_seconds: int = 900,
) -> bool:
    """Return True if request is allowed, False if rate limited."""
    now = time.time()
    window_start = now - window_seconds
    _attempts[key] = [t for t in _attempts[key] if t >= window_start]
    if len(_attempts[key]) >= max_attempts:
        return False
    _attempts[key].append(now)
    return True


def reset_rate_limit(key: str) -> None:
    _attempts.pop(key, None)