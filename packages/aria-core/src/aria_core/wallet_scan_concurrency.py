"""Adaptive concurrency for wallet_scan_queue.py's MAX_WALLETS_PER_CYCLE --
built 10/08 after finding this constant had been hand-recalibrated 3 times
following real production incidents: 1->2 (a single wallet monopolizing
the queue for ~10 days each, starving every other queued wallet), then
->25->4 (a confirmed 6-day live-lock: 304 wallets stuck, ZERO progress,
because too many concurrent wallets saturated the shared GeckoTerminal
rate-limit lock -- see wallet_scan_queue.py's own module comment for the
full incident writeup). Same asymmetric tighten-fast/ease-slow doctrine as
services/geckoterminal.py's adaptive throttle -- extends that pattern one
layer up (the number of CONCURRENT consumers of the shared throttle,
rather than the throttle's own rate).

Deliberately scoped to cycles that complete normally: a cycle killed by
heartbeat.py's own 300s per-task ceiling (the exact live-lock signature)
never reaches this module at all, since the whole coroutine is cancelled
from outside before it can report anything -- that failure mode stays
visible via wallet_scan_queue.queue_status_summary()'s
oldest_never_attempted_days, already the established way a session
catches it. Building a second, speculative detection path for an
externally-cancelled cycle was considered and deliberately left out
(real complexity for a failure mode already surfaced elsewhere)."""
from __future__ import annotations

import logging

from aria_core.paths import aria_db_path
from aria_core.single_row_state import SingleRowStore

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
_TABLE = "wallet_scan_concurrency_state"
_COLUMNS = [
    ("current_max_wallets", "INTEGER NOT NULL DEFAULT 4", 4),
    ("consecutive_healthy_cycles", "INTEGER NOT NULL DEFAULT 0", 0),
]

# Never lower than this -- N=1 documented (26/07, wallet_scan_queue.py) to
# let a single wallet monopolize the queue for ~10 days each, a different
# failure mode than the live-lock below but equally bad.
_FLOOR = 2

# Never higher than this -- N=25 is CONFIRMED bad (29/07 live-lock, 304
# wallets stuck 6 days) via the shared GeckoTerminal lock. 8 leaves real
# headroom above the known-safe N=4 baseline without ever approaching the
# documented failure zone.
_CEILING = 8

# A completed cycle "overran" past this -- derived from wallet_scan_queue.
# CATCHUP_CYCLE_SOFT_DEADLINE_SECONDS (240s) plus a small margin: past
# this, the batch is crowding heartbeat's own 300s hard ceiling regardless
# of which single wallet caused it.
HEALTHY_DURATION_CEILING_SECONDS = 260

_EASE_AFTER_CONSECUTIVE_HEALTHY_CYCLES = 10


def _store() -> SingleRowStore:
    return SingleRowStore(DB_PATH, _TABLE, _COLUMNS)


async def current_max_wallets() -> int:
    row = await _store().read("current_max_wallets")
    return row[0] if row else 4


async def record_cycle_duration(duration_seconds: float) -> int:
    """Called once a batch (asyncio.gather over up to current_max_wallets
    concurrent wallets) actually completes. Tightens immediately on an
    overrun (mirrors geckoterminal.py's tighten-fast), eases by 1 only
    after _EASE_AFTER_CONSECUTIVE_HEALTHY_CYCLES consecutive comfortable
    cycles (ease-slow) -- never outside [_FLOOR, _CEILING]. Returns the
    (possibly updated) concurrency value."""
    store = _store()
    row = await store.read("current_max_wallets", "consecutive_healthy_cycles")
    current, healthy_streak = row or (4, 0)

    if duration_seconds >= HEALTHY_DURATION_CEILING_SECONDS:
        new_max = max(_FLOOR, current - 1)
        if new_max < current:
            logger.warning(
                "wallet_scan_queue: cycle took %.0fs (>= %ss ceiling) -- "
                "tightening MAX_WALLETS_PER_CYCLE %s -> %s.",
                duration_seconds, HEALTHY_DURATION_CEILING_SECONDS, current, new_max,
            )
        await store.write({"current_max_wallets": new_max, "consecutive_healthy_cycles": 0})
        return new_max

    healthy_streak += 1
    if healthy_streak >= _EASE_AFTER_CONSECUTIVE_HEALTHY_CYCLES and current < _CEILING:
        new_max = current + 1
        logger.info(
            "wallet_scan_queue: %s consecutive healthy cycles (<%ss) -- "
            "easing MAX_WALLETS_PER_CYCLE %s -> %s.",
            healthy_streak, HEALTHY_DURATION_CEILING_SECONDS, current, new_max,
        )
        await store.write({"current_max_wallets": new_max, "consecutive_healthy_cycles": 0})
        return new_max

    await store.write({"consecutive_healthy_cycles": healthy_streak})
    return current
