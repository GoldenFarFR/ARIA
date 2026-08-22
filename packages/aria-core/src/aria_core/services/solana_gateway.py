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
    _last_error: str = field(default="", repr=False)

    def healthy(self) -> bool:
        return time.monotonic() >= self.benched_until

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
        """Next healthy endpoint, round-robin so load SPREADS.

        Paid endpoints are preferred while healthy; public ones are the floor
        that keeps the pocket running rather than stopping it.
        """
        self._ensure()
        if not self._endpoints:
            return None
        healthy = [e for e in self._endpoints if e.healthy()]
        if not healthy:
            return None
        paid = [e for e in healthy if e.paid]
        pool = paid or healthy
        # Serve THEN advance. Advancing first skipped the first endpoint
        # entirely, so a two-provider pool only ever used the second one.
        chosen = pool[self._cursor % len(pool)]
        self._cursor = (self._cursor + 1) % len(pool)
        return chosen

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
                endpoint = self._pick()
                if endpoint is None:
                    break
                if not await endpoint.budget.acquire(priority):
                    # Only LOW gives up; it retries later by design.
                    return None
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
                try:
                    return resp.json()
                except Exception:  # noqa: BLE001 -- a malformed body is not a result
                    return None
            logger.warning("solana_gateway: no endpoint could serve %s", method)
            return None
        finally:
            if owns:
                await client.aclose()

    def stats(self) -> dict:
        self._ensure()
        return {
            "endpoints": [
                {
                    "name": e.name,
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
