"""The single door to Solana. Every RPC call goes through here (22/08).

**Why a door rather than one more throttle.** The night of 21-22/08 produced
seven defects, and the pattern was identical every time: some module talked to
Solana its own way -- picking its endpoint, handling its rate or not, its
errors or not, its failover or not. When Helius' quota ran out, each module
fell over on its own while a healthy provider sat unused, and the pocket went
three hours without a trade.

The decisive evidence that convention is not enough: the liquidation script
written THREE HOURS AFTER documenting the rule reproduced the bug -- it picked
an endpoint while the wallet module resolved a different one, so all 32 sales
failed on "balance unreadable". If the author of the rule breaks it the same
day, only a mechanical guard holds.

**What this owns, so no caller has to:**

  * every endpoint, paid then public, in priority order;
  * SPREADING load across the healthy ones -- capacity ADDS UP, two providers
    at 22 rps give 44, which is what makes the worst case survivable;
  * a per-endpoint token bucket, because a shared global budget would waste
    exactly the capacity that spreading buys;
  * failover when one dies, and automatic return when it recovers;
  * priorities, so a sell is never queued behind a price refresh.

**Sizing, measured not assumed.** Steady state with 59 open positions --
tracking, discovery and curve polling all at once -- costs 0.83 call/s, i.e. 4%
of one provider. The case that hurts is a market break where every position
exits at once: 59 sales, 4 calls each. On one provider that takes 11s for the
last position; on two it halves, and `URGENT` trims a call per sale on top.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

from aria_core.services import solana_rpc_budget as shared_rpc_budget
from aria_core.services.solana_rpc_budget import Priority, SolanaRpcBudget

logger = logging.getLogger(__name__)

# Per-endpoint default, 90% of Chainstack's documented 25 rps free tier.
# https://chainstack.com/best-solana-rpc-providers-in-2026/
DEFAULT_RATE_PER_SECOND = 22.5

# How long an endpoint stays benched after refusing. Long enough that a quota
# problem is not hammered, short enough that a transient blip self-heals.
COOLDOWN_SECONDS = 120.0

# A quota exhaustion is not a blip: the provider will refuse for a long time,
# so it is benched much longer rather than retried every two minutes.
QUOTA_COOLDOWN_SECONDS = 900.0

# Public fallbacks, verified answering on 22/08 while BOTH paid providers were
# down. Slower and rate-limited, but they are the difference between degraded
# and stopped -- and the whole pocket stopping is what this module prevents.
# Overflow threshold (operator's design, 22/08). An endpoint is used until its
# INSTANTANEOUS utilisation reaches this, then traffic spills to the next one.
#
# Better than round-robin, which sent as much traffic to the backup as to the
# primary even when the primary had headroom. Cascading keeps the best provider
# in front, only leans on the next when needed, and leaves the others fresh for
# a burst -- which is exactly when they matter.
#
# 80% rather than 100%: reaching the ceiling IS the refusal, so the spill has
# to happen before it, not at it.
OVERFLOW_AT = 0.80

# Pressure levels (operator's design, 22/08). Individual throttles only ever
# knew their own state, so nobody saw a chain failure coming: each endpoint
# looked locally fine right up to the moment the whole pool refused.
#
# Pressure is the utilisation of the WHOLE pool, so callers can ease off BEFORE
# anything is refused rather than discovering it through errors.
PRESSURE_TENSE = 0.60      # ease off: low-priority work should stand down
PRESSURE_CRITICAL = 0.85   # only exits should still be asking

# Self-regulation on the MONTHLY budget, not just the per-second rate
# (operator's design, 22/08). An endpoint can be perfectly fluid second to
# second and still burn a month's quota in three days -- which is exactly what
# happened to Helius. Instantaneous throttling cannot see that coming.
#
# Each endpoint compares what it has SPENT against what it should have spent by
# now if consumption were even. Above this ratio it is running ahead of its
# budget and voluntarily takes less traffic, so the pool rebalances itself
# before anyone is refused.
BURN_RATE_WARNING = 1.20   # 20% ahead of schedule: take less
BURN_RATE_CRITICAL = 1.60  # far ahead: only exits

# Warn this far ahead of a predicted exhaustion. Knowing a provider dies in six
# hours is actionable; discovering it at 3am through a three-hour outage is
# not -- which is exactly how 22/08 went.
EXHAUSTION_WARNING_SECONDS = 6 * 3600.0

# Where the pool state survives a restart. Without it every restart forgets
# which provider is exhausted and hammers it again -- and this service was
# restarted a dozen times in one night.
STATE_PATH = "/opt/aria-data/solana_gateway_state.json"

PUBLIC_ENDPOINTS = (
    "https://public.rpc.solanavibestation.com",
    "https://api.mainnet-beta.solana.com",
)


@dataclass
class _Endpoint:
    url: str
    name: str
    paid: bool
    budget: SolanaRpcBudget
    benched_until: float = 0.0
    calls: int = 0
    refusals: int = 0
    # Monthly budget, when the provider publishes one. None means "unmetered
    # as far as we know" -- never a guessed figure, per the dome rule.
    quota_total: int | None = None
    quota_spent: int = 0
    quota_period_seconds: float = 30 * 86400.0
    quota_started_at: float = field(default_factory=time.monotonic)
    _last_error: str = field(default="", repr=False)

    def healthy(self) -> bool:
        return time.monotonic() >= self.benched_until

    def burn_rate(self) -> float | None:
        """Spent-so-far divided by should-have-spent-by-now. None if unmetered.

        1.0 means exactly on schedule to last the period. Above 1.0 the
        endpoint is running ahead of its budget and will run out early.
        """
        if not self.quota_total or self.quota_total <= 0:
            return None
        elapsed = max(1.0, time.monotonic() - self.quota_started_at)
        fraction_of_period = min(1.0, elapsed / self.quota_period_seconds)
        expected = self.quota_total * fraction_of_period
        if expected <= 0:
            return None
        return self.quota_spent / expected

    def exhausts_in_seconds(self) -> float | None:
        """How long before this endpoint runs out at the CURRENT pace.

        This is the "I will not be able to take requests until ..." figure --
        knowing it in advance is the whole point of measuring burn rate.
        """
        if not self.quota_total:
            return None
        remaining = self.quota_total - self.quota_spent
        if remaining <= 0:
            return 0.0
        elapsed = max(1.0, time.monotonic() - self.quota_started_at)
        per_second = self.quota_spent / elapsed
        if per_second <= 0:
            return None
        return remaining / per_second

    def utilisation(self) -> float:
        """0.0 = idle, 1.0 = no budget left right now."""
        if self.budget.burst <= 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - self.budget.available() / self.budget.burst))

    def bench(self, *, quota: bool, error: str = "") -> None:
        seconds = QUOTA_COOLDOWN_SECONDS if quota else COOLDOWN_SECONDS
        self.benched_until = time.monotonic() + seconds
        self._last_error = error[:120]
        self.refusals += 1
        logger.warning(
            "solana_gateway: %s benched %.0fs (%s)",
            self.name, seconds, "quota" if quota else "error",
        )


def _host(url: str) -> str:
    try:
        return url.split("//", 1)[1].split("/", 1)[0]
    except IndexError:
        return url[:24]


class SolanaGateway:
    """Owns every Solana RPC call. Build once, import everywhere."""

    def __init__(self, *, rate_per_second: float = DEFAULT_RATE_PER_SECOND) -> None:
        self.rate = rate_per_second
        self._endpoints: list[_Endpoint] = []
        self._cursor = 0
        self._configured = False
        self._level = "calm"

    # -- configuration ------------------------------------------------------

    def configure(self, *, urls: list[tuple[str, bool]] | None = None) -> None:
        """Builds the endpoint pool. Paid first, public last, duplicates dropped.

        Reads the environment when no explicit list is given, so a caller never
        has to know which variable holds what.
        """
        if urls is None:
            urls = []
            for var, paid in (
                ("ARIA_SOLANA_RPC_HTTP", True),
                ("ARIA_SOLANA_RPC_HTTP_POLLING", True),
            ):
                value = (os.environ.get(var, "") or "").strip()
                if value:
                    urls.append((value, paid))
            urls.extend((u, False) for u in PUBLIC_ENDPOINTS)

        seen: set[str] = set()
        self._endpoints = []
        for url, paid in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            self._endpoints.append(
                _Endpoint(
                    url=url,
                    name=_host(url),
                    paid=paid,
                    # Per endpoint, deliberately: a single shared bucket would
                    # cap the pool at one provider's rate and throw away the
                    # capacity that having several is meant to buy.
                    # Burst tracks the rate -- one second's worth. Left at its
                    # default it stayed at 22.5 tokens whatever rate was
                    # configured, so a deliberately slow endpoint still served
                    # a 22-call burst.
                    budget=SolanaRpcBudget(
                        rate_per_second=self.rate, burst=self.rate,
                    ),
                )
            )
        self._configured = True
        logger.info(
            "solana_gateway: %d endpoint(s) -- %s",
            len(self._endpoints), ", ".join(e.name for e in self._endpoints),
        )

    def _ensure(self) -> None:
        if not self._configured:
            self.configure()

    # -- selection ----------------------------------------------------------

    def _pick(self) -> _Endpoint | None:
        """First healthy endpoint with headroom -- CASCADE, not round-robin.

        Each endpoint is used until its instantaneous utilisation reaches
        `OVERFLOW_AT`, then traffic spills to the next. Paid endpoints come
        first; public ones are the floor that keeps the pocket running rather
        than stopping it.

        When everything is saturated the LAST endpoint is returned rather than
        None: the budget paces the call anyway, and refusing here would turn a
        slow moment into a failure.
        """
        self._ensure()
        healthy = [e for e in self._endpoints if e.healthy()]
        if not healthy:
            return None
        # An endpoint burning ahead of its budget takes less traffic: it is
        # skipped while another has room, so the pool rebalances ITSELF rather
        # than waiting for a quota to run out.
        for endpoint in healthy:
            burn = endpoint.burn_rate()
            if burn is not None and burn >= BURN_RATE_WARNING:
                continue
            if endpoint.utilisation() < OVERFLOW_AT:
                return endpoint
        # Nobody is both within budget and free: fall back to whoever has
        # instantaneous room, overspending being better than not trading.
        for endpoint in healthy:
            if endpoint.utilisation() < OVERFLOW_AT:
                return endpoint
        return healthy[-1]

    # -- the only public entry point ---------------------------------------

    async def call(
        self,
        method: str,
        params: list | None = None,
        *,
        priority: Priority = Priority.NORMAL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
    ) -> dict | None:
        """One JSON-RPC call. Returns the parsed payload, or None.

        None means "could not be done right now" and must never be read as an
        empty result -- the callers that treat unknown as zero are exactly how
        this dome loses money.
        """
        self._ensure()
        owns = client is None
        client = client or httpx.AsyncClient(timeout=timeout)
        try:
            # Two passes: the second one reaches the public endpoints once the
            # paid ones have been benched by the first.
            for _ in range(2):
                if self.should_stand_down(priority):
                    # Anticipation, not reaction: the pool is under pressure and
                    # this caller is not the one that must get through.
                    return None
                endpoint = self._pick()
                if endpoint is None:
                    break
                if not await endpoint.budget.acquire(priority):
                    # Only LOW gives up; it retries later by design.
                    return None
                if endpoint.paid:
                    # 26/08 -- counted right before the attempt, not after a
                    # successful response: Chainstack bills a request the
                    # moment it reaches their server, so a timeout or a 5xx
                    # here still spent the RU. This closed a real gap where
                    # this gateway's own callers (jupiter_swap_signer.py,
                    # solana_rent_recovery.py, solana_agent_wallet.py,
                    # pumpfun_curve_price.py) sent real Chainstack traffic
                    # that chainstack_ru_budget never saw -- measured 26/08:
                    # the dashboard's real Solana RU (419,197 on 25/08) was
                    # 7.3x this dome's own internal count (57,796) for the
                    # same day. Public endpoints (paid=False) never touch
                    # this budget -- they cost no RU.
                    from aria_core.services import chainstack_ru_budget

                    chainstack_ru_budget.record_usage_fast("solana", 1)
                try:
                    resp = await client.post(
                        endpoint.url,
                        json={"jsonrpc": "2.0", "id": 1, "method": method,
                              "params": params or []},
                    )
                except Exception as exc:  # noqa: BLE001 -- try the next one
                    endpoint.bench(quota=False, error=repr(exc))
                    continue

                if resp.status_code == 429:
                    endpoint.bench(quota=True, error="429")
                    continue
                if resp.status_code in (401, 402, 403):
                    # Forbidden/payment-required is a quota or key problem, not
                    # a blip -- Chainstack answered 403 on 22/08 with the quota
                    # spent. Benching it briefly would just burn the retry.
                    endpoint.bench(quota=True, error=f"HTTP {resp.status_code}")
                    continue
                if resp.status_code >= 500:
                    endpoint.bench(quota=False, error=f"HTTP {resp.status_code}")
                    continue

                endpoint.calls += 1
                endpoint.quota_spent += 1
                try:
                    return resp.json()
                except Exception:  # noqa: BLE001 -- a malformed body is not a result
                    return None
            logger.warning("solana_gateway: no endpoint could serve %s", method)
            return None
        finally:
            if owns:
                await client.aclose()

    # -- feedback loop ------------------------------------------------------

    def pressure(self) -> float:
        """Utilisation of the whole pool, 0.0 to 1.0.

        Averaged over HEALTHY endpoints only: a benched one contributes no
        capacity, and counting it as idle would report calm while the survivors
        drown -- the precise blindness that let the chain failure build.
        """
        healthy = [e for e in self._endpoints if e.healthy()]
        if not healthy:
            return 1.0
        return sum(e.utilisation() for e in healthy) / len(healthy)

    def level(self) -> str:
        """`calm` / `tense` / `critical`, and logs each transition once.

        Logging the TRANSITION rather than the state is deliberate: a level
        printed every pass is noise nobody reads, which is how the 847-error
        storm went unnoticed for minutes.
        """
        value = self.pressure()
        if value >= PRESSURE_CRITICAL:
            level = "critical"
        elif value >= PRESSURE_TENSE:
            level = "tense"
        else:
            level = "calm"
        if level != self._level:
            healthy = sum(1 for e in self._endpoints if e.healthy())
            # 27/08, backlog #364 step 1 (observe-only) -- this gateway's own
            # per-endpoint buckets and solana_rpc_budget's shared singleton
            # are two SEPARATE regulators for the same real providers (see
            # that module's own comment on `calls_by_caller`). Logging the
            # direct-caller totals at every pressure transition is a cheap,
            # zero-behaviour-change way to see whether direct traffic was
            # already adding to a real ceiling the moment this gateway alone
            # got tight -- a week of these lines is the "collision log" the
            # backlog item asked for, before any structural fix is attempted.
            direct_calls = shared_rpc_budget.budget.calls_by_caller
            logger.warning(
                "solana_gateway: pressure %s -> %s (%.0f%%, %d/%d endpoints healthy, "
                "direct shared-budget calls so far: %s)",
                self._level, level, value * 100, healthy, len(self._endpoints),
                direct_calls or "none",
            )
            self._level = level
        return level

    def should_stand_down(self, priority: Priority) -> bool:
        """Should this caller skip its turn BEFORE even trying?

        The anticipation half of the feedback loop. Asking permission and being
        refused still costs a round trip through the budget; standing down
        costs nothing and leaves the capacity to whoever needs it.
        """
        level = self.level()
        if level == "critical":
            return priority is not Priority.HIGH
        if level == "tense":
            return priority is Priority.LOW
        return False

    # -- memory across restarts --------------------------------------------

    def save_state(self, path: str = STATE_PATH) -> None:
        """Persists what a restart must not forget: who is benched and until
        when, and how much of each quota is already spent."""
        import json
        import tempfile

        now = time.monotonic()
        payload = {
            "saved_at": time.time(),
            "endpoints": {
                e.name: {
                    "benched_for": max(0.0, e.benched_until - now),
                    "quota_spent": e.quota_spent,
                    "quota_total": e.quota_total,
                    "quota_elapsed": now - e.quota_started_at,
                }
                for e in self._endpoints
            },
        }
        try:
            directory = os.path.dirname(path) or "."
            with tempfile.NamedTemporaryFile(
                "w", dir=directory, delete=False, encoding="utf-8"
            ) as handle:
                json.dump(payload, handle)
                temporary = handle.name
            os.replace(temporary, path)      # atomic: never a half-written file
        except Exception as exc:  # noqa: BLE001 -- persistence is never fatal
            logger.info("solana_gateway: could not save state (%s)", exc)

    def load_state(self, path: str = STATE_PATH) -> None:
        """Restores it. A missing or unreadable file is simply a fresh start."""
        import json

        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:  # noqa: BLE001
            return
        offline = max(0.0, time.time() - float(payload.get("saved_at") or 0))
        now = time.monotonic()
        for endpoint in self._endpoints:
            saved = (payload.get("endpoints") or {}).get(endpoint.name)
            if not saved:
                continue
            # Time spent offline counts against the bench: a provider does not
            # stay angry while we are down.
            remaining = float(saved.get("benched_for") or 0) - offline
            if remaining > 0:
                endpoint.benched_until = now + remaining
                logger.info(
                    "solana_gateway: %s still benched %.0fs after restart",
                    endpoint.name, remaining,
                )
            endpoint.quota_spent = int(saved.get("quota_spent") or 0)
            if saved.get("quota_total"):
                endpoint.quota_total = int(saved["quota_total"])
            endpoint.quota_started_at = now - float(saved.get("quota_elapsed") or 0) - offline

    def warnings(self) -> list[str]:
        """Trouble worth surfacing BEFORE it becomes an outage.

        Answers the operator's question -- "I will not be able to take requests
        until ..." -- while there is still time to act on it.
        """
        out: list[str] = []
        for endpoint in self._endpoints:
            left = endpoint.exhausts_in_seconds()
            if left is not None and left < EXHAUSTION_WARNING_SECONDS:
                out.append(
                    f"{endpoint.name}: quota epuise dans ~{left/3600:.1f} h "
                    f"au rythme actuel ({endpoint.quota_spent}/{endpoint.quota_total})"
                )
            burn = endpoint.burn_rate()
            if burn is not None and burn >= BURN_RATE_CRITICAL:
                out.append(
                    f"{endpoint.name}: consomme {burn:.1f}x plus vite que son budget"
                )
        healthy = [e for e in self._endpoints if e.healthy()]
        if len(healthy) <= 1 and len(self._endpoints) > 1:
            out.append(
                f"un seul endpoint sain sur {len(self._endpoints)} -- "
                f"plus aucune marge de bascule"
            )
        return out

    def stats(self) -> dict:
        self._ensure()
        return {
            "pressure": round(self.pressure(), 3),
            "level": self.level(),
            "warnings": self.warnings(),
            "endpoints": [
                {
                    "name": e.name,
                    "burn_rate": (round(e.burn_rate(), 2)
                                  if e.burn_rate() is not None else None),
                    "exhausts_in_hours": (round(e.exhausts_in_seconds() / 3600, 1)
                                          if e.exhausts_in_seconds() else None),
                    "paid": e.paid,
                    "healthy": e.healthy(),
                    "calls": e.calls,
                    "refusals": e.refusals,
                    "benched_for": max(0.0, round(e.benched_until - time.monotonic(), 1)),
                    "last_error": e._last_error,
                }
                for e in self._endpoints
            ],
            "total_rate_per_second": round(
                self.rate * sum(1 for e in self._endpoints if e.healthy()), 1
            ),
            # backlog #364 step 1 -- see the comment in level() above.
            "shared_budget_direct_calls": shared_rpc_budget.budget.calls_by_caller,
        }


# The single instance. Import THIS -- building another one recreates the very
# bug this module exists to remove.
gateway = SolanaGateway()


async def call(
    method: str,
    params: list | None = None,
    *,
    priority: Priority = Priority.NORMAL,
    client: httpx.AsyncClient | None = None,
) -> dict | None:
    """Convenience wrapper over the shared gateway."""
    return await gateway.call(method, params, priority=priority, client=client)
