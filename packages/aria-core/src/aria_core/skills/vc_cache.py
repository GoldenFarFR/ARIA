"""In-memory TTL cache for expensive VC analyses (scan + LLM).

Context: the VPS is over-provisioned (CPU ~0%, RAM ~12%). The real cost of
a `/vc` analysis is the LLM call (~30s) + tokens. The same contract
requested again within the TTL window returns the memoized result —
**near-instant, zero tokens**. This is the only real speed lever (cf.
perf review).

Discipline:
- **Disabled by default** (TTL absent/0). Enabled in prod via `ARIA_VC_CACHE_TTL`
  (the Dockerfile sets it to 300s). Offline tests are therefore not polluted.
- **Facts-only compatible**: on-chain facts barely move over a few minutes,
  and the human ALWAYS validates the order — a result up to TTL old stays safe.
- Key = (normalized contract, language): two languages = two distinct entries.
- Bounded (LRU + expired purge): no memory leak.
- Injectable clock (`_now`) for deterministic tests without `sleep`.
"""
from __future__ import annotations

import time as _time
from collections import OrderedDict

_CAP = 256
_now = _time.monotonic  # monkeypatchable in tests

# key -> (expiry timestamp, value)
_store: "OrderedDict[tuple, tuple[float, object]]" = OrderedDict()


def get(key):
    """Memoized value if present AND not expired, otherwise ``None``."""
    entry = _store.get(key)
    if entry is None:
        return None
    expiry, value = entry
    if _now() >= expiry:
        _store.pop(key, None)
        return None
    _store.move_to_end(key)  # LRU: refreshes recency
    return value


def put(key, value, ttl: float) -> None:
    """Memoizes ``value`` for ``ttl`` seconds. ``ttl<=0`` = no-op (cache off)."""
    if ttl <= 0:
        return
    _purge_expired()
    _store[key] = (_now() + ttl, value)
    _store.move_to_end(key)
    while len(_store) > _CAP:
        _store.popitem(last=False)  # evicts the oldest entry


def _purge_expired() -> None:
    now = _now()
    for k in [k for k, (exp, _) in _store.items() if now >= exp]:
        _store.pop(k, None)


def clear() -> None:
    """Clears the cache (tests, or manual invalidation)."""
    _store.clear()


def size() -> int:
    return len(_store)
