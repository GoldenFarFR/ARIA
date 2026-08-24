"""One shared, prioritised throughput coordinator for Solana RPC (22/08).

**Why this exists.** Chainstack answered `429` while its monthly quota sat at
49% used. The limit that bites is 25 requests per SECOND, and six callers --
curve tracker, price sweep, discovery, buys, sells, reconciliation -- each
honoured its own throttle while knowing nothing about the others. Each was
reasonable alone; together they burst past the ceiling.

Measured cost of that: sell quotes refused for positions ALREADY OPEN, two
real closures at -81.0% and -79.7% against a stop set at -5%, and discovery
down for over three hours.

This is the dome's standing rule, broken twice in one night (Jupiter first,
then Chainstack): several clients on one external provider share ONE
coordination point, never independent throttles that silently add up.

**Three mechanisms, and the third is the one that matters.**

1. A token bucket rather than a fixed minimum interval. A fixed delay throws
   away credit accrued while idle, then gets refused on the next burst anyway.
   ARIA's load is bursty by nature: several loops wake together when a position
   closes. A bucket absorbs that at the same average rate.

2. A single point every call goes through. Per-module throttles are meant to be
   DELETED once a caller is wired here -- two throttles on one provider is the
   bug being fixed, not a belt-and-braces.

3. Priorities where LOW gives up instead of waiting. This is the part that
   keeps things moving when work is queued: a low-priority task that WAITS
   keeps its slot and delays everyone behind it, while one that GIVES UP frees
   the budget instantly. A price refresh missed now is re-taken a second later
   at zero cost; a sell that could not be sent cost 80% of a position.

**Deliberately knows nothing about the network.** It hands out permission and
nothing else, so it is fully testable against a fake clock, and a caller cannot
accidentally route a request through the wrong provider by using it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import IntEnum

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Lower value = served first.

    HIGH is reserved for exiting a position. Everything else can be late; an
    exit cannot, which is the whole lesson of 22/08.
    """

    HIGH = 0      # selling an open position
    NORMAL = 1    # buying, discovery, curve tracking
    LOW = 2       # price refresh, reconciliation


# 24/08 CORRECTION -- 25 rps is Chainstack's GENERAL per-plan dashboard figure,
# not the real ceiling. Solana Mainnet has its own, much lower cap: 5 req/s
# (docs.chainstack.com/docs/limits, confirmed live 2026.08.24, see
# pumpfun_curve_tracker.py's CHAINSTACK_MAX_RPS and docs/HANDOFF_CHAINSTACK.md
# section 3.B). This shared budget ran at 22.5 (90% of the wrong 25) for days
# while already wired into REAL-CAPITAL callers (solana_agent_wallet.py,
# jupiter_swap_signer.py, jupiter_swap_simulation.py, solana_rent_recovery.py)
# -- found live only because a separate module's late-bonding shadow pocket,
# newly reconnected the same day, started drawing 429s from Chainstack the
# moment its own polling resumed. Calibrated to 90% of the real, VERIFIED
# limit per the dome rule -- never a guessed number, and sourced here next to
# the constant so a future reader can re-check it.
DEFAULT_RATE_PER_SECOND = 4.5

# Burst allowance. One second's worth: enough to absorb the simultaneous wake-up
# of every loop, not enough to spend a quiet minute in one shot -- which would
# be refused by the provider regardless of what this module thinks.
DEFAULT_BURST = 4.5

# How long a LOW-priority caller waits before giving up. Deliberately short: it
# exists to skip a momentary contention, not to queue.
LOW_PRIORITY_PATIENCE_SECONDS = 0.25

# How long anything else waits before surfacing a failure rather than hanging.
# A caller blocked this long has a real problem the caller must handle.
MAX_WAIT_SECONDS = 30.0


class BudgetTimeout(RuntimeError):
    """Raised when permission could not be obtained within the deadline.

    Never raised for LOW priority -- that path returns False instead, because
    giving up IS its correct behaviour.
    """


class SolanaRpcBudget:
    """Token bucket with priority. One instance per provider.

    `clock` is injectable so the whole thing is testable without sleeping.
    """

    def __init__(
        self,
        *,
        rate_per_second: float = DEFAULT_RATE_PER_SECOND,
        burst: float = DEFAULT_BURST,
        clock=None,
        sleep=None,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self.rate = float(rate_per_second)
        self.burst = float(max(burst, 1.0))
        # time.monotonic, not the event loop's clock: this object is built at
        # import time, before any loop exists, and a monotonic source cannot
        # jump backwards on an NTP correction either.
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._tokens = self.burst
        self._last_refill = self._clock()
        self._lock: asyncio.Lock | None = None
        # Serves HIGH before NORMAL when both are waiting, without a full
        # priority queue: each level owns a counter, and a lower level yields
        # while a higher one is pending.
        self._waiting = {p: 0 for p in Priority}
        self.granted = {p: 0 for p in Priority}
        self.skipped = {p: 0 for p in Priority}

    # -- internals ---------------------------------------------------------

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def _higher_pending(self, priority: Priority) -> bool:
        return any(self._waiting[p] for p in Priority if p < priority)

    def available(self) -> float:
        """Tokens available right now. Exposed for diagnostics only."""
        self._refill()
        return self._tokens

    # -- public API --------------------------------------------------------

    async def acquire(self, priority: Priority = Priority.NORMAL) -> bool:
        """Wait for permission to make ONE call.

        Returns True when permission is granted. Returns False only for LOW
        priority that chose to skip its turn -- the caller must then do nothing
        and try again later, never proceed anyway.

        Raises `BudgetTimeout` for HIGH/NORMAL held up past `MAX_WAIT_SECONDS`:
        silently hanging a sell would be worse than surfacing the problem.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()

        deadline = self._clock() + (
            LOW_PRIORITY_PATIENCE_SECONDS if priority is Priority.LOW else MAX_WAIT_SECONDS
        )
        self._waiting[priority] += 1
        try:
            while True:
                async with self._lock:
                    self._refill()
                    # A lower level stands aside while a higher one waits, so a
                    # burst of price refreshes cannot starve a sell.
                    if self._tokens >= 1.0 and not self._higher_pending(priority):
                        self._tokens -= 1.0
                        self.granted[priority] += 1
                        return True
                    missing = max(0.0, 1.0 - self._tokens)
                    wait = missing / self.rate if missing else 0.005

                if self._clock() + wait > deadline:
                    if priority is Priority.LOW:
                        self.skipped[priority] += 1
                        return False
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise BudgetTimeout(
                            f"no Solana RPC budget for {priority.name} after "
                            f"{MAX_WAIT_SECONDS:.0f}s"
                        )
                    wait = remaining
                await self._sleep(max(wait, 0.001))
        finally:
            self._waiting[priority] -= 1

    def stats(self) -> dict:
        """What was served and what stood aside. For the diagnostics endpoint."""
        return {
            "tokens_available": round(self.available(), 2),
            "rate_per_second": self.rate,
            "granted": {p.name: self.granted[p] for p in Priority},
            "skipped": {p.name: self.skipped[p] for p in Priority},
            "waiting": {p.name: self._waiting[p] for p in Priority},
        }


# The single shared instance. Callers import THIS, never build their own --
# a second instance is the same bug as a second throttle.
budget = SolanaRpcBudget()


async def acquire(priority: Priority = Priority.NORMAL) -> bool:
    """Convenience wrapper over the shared instance."""
    return await budget.acquire(priority)
