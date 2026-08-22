# Plan — one shared, prioritised throughput coordinator for Solana RPC

> Written 2026.08.22 after a real 3h+ trading outage. Diagnosis is complete and
> measured; the implementation is deliberately NOT started in the same session,
> because three consecutive hot-fixes that night each introduced a new bug.
> This document exists so a fresh session can build it without re-deriving
> anything.

## The problem, measured not assumed

Chainstack answered `429` while the monthly quota sat at **49% used**. So this
was never a volume problem:

| Consumer | Calls/day (measured) |
|---|---|
| Curve tracker | 25 900 |
| Price sweep | 17 000 |
| Discovery + trading | 6 500 |
| **Total** | **~49 400 / 100 000** |

The limit that actually bites is **25 requests per SECOND**. The curve tracker
honours its own 22 rps throttle, but five other callers — price sweep,
discovery, buys, sells, reconciliation — add to it while knowing nothing about
it. Each is reasonable alone; together they burst past the ceiling.

This is the dome's standing rule, broken twice in one night (Jupiter first,
then Chainstack): **several clients on one external provider share ONE
coordination point, never independent throttles that silently add up.**

What it cost, concretely: sell quotes were refused for positions already
open, so exits could not fire. Two real positions closed at **-81.0%** and
**-79.7%** against a stop set at -5%, and discovery stopped entirely for over
three hours.

## Design

### 1. Token bucket, not a fixed interval

Current throttles impose a minimum delay between calls. That is rigid: credit
accumulated during a quiet period is thrown away, and the next burst is refused
anyway. ARIA's load is bursty by nature — several loops wake together when a
position closes.

A token bucket accrues the right to call during quiet periods, up to a cap, and
absorbs a short burst. Same average rate, far fewer refusals.

Calibrate to **90% of the real, VERIFIED limit** (dome rule), sourced in a
comment next to the constant. Chainstack's documented free tier is 25 rps →
22.5 rps. Never a guessed number.

### 2. A single point every call goes through

The missing piece, and the whole reason the outage happened. Mirror
`services/geckoterminal.wait_for_shared_rate_limit`, which already exists as
the reference pattern in this codebase.

Every Solana RPC call — curve tracker, price sweep, discovery, buys, sells,
reconciliation, balance reads — awaits the same coordinator. Per-module
throttles are then DELETED, not left alongside: two throttles on one provider
is the bug being fixed.

### 3. Priorities, where low priority ABANDONS rather than waits

This is the part that answers "how do we keep things flowing when work is
queued".

| Priority | Work | Behaviour under saturation |
|---|---|---|
| HIGH | **sell** an open position | always goes through; may delay everything else |
| NORMAL | buy, discovery, curve tracking | queues and waits its turn |
| LOW | price refresh, reconciliation | **gives up immediately**, retries later |

The critical distinction: a low-priority task that WAITS keeps its slot and
delays the queue behind it. One that GIVES UP frees the budget instantly. A
price refresh missed now is re-taken a second later at zero cost; a sell that
could not be sent cost 80% of a position on 22/08.

## Implementation steps

1. **`services/solana_rpc_budget.py`** — the coordinator. Token bucket +
   priority. No network code, no provider knowledge: it hands out permission,
   nothing else. Fully unit-testable with a fake clock.
2. **Wire every call site** through it. Grep for `require_solana_rpc_http`,
   `ARIA_SOLANA_RPC_HTTP_POLLING`, and every `client.post` targeting an RPC.
3. **Delete the per-module throttles** it replaces, notably
   `pumpfun_curve_tracker`'s `CHAINSTACK_MAX_RPS`/`HELIUS_MAX_RPS`. Leaving
   them is how the sum silently reappears.
4. **A coherence test** that fails if a new Solana RPC call is added without
   going through the coordinator — the same mechanical guard style as
   `test_external_write_actions_registered_in_allowlist`. Without it this
   regresses the first time someone adds a loop.
5. **Feed the 429 signal back in**: a refusal should shrink the bucket
   temporarily, not just be retried. `pumpswap_ws.note_rpc_http_exhausted`
   already exists for the provider-exhausted case and should plug into this.

## What must NOT be touched

- **The 50-70% curve band stays at 10s.** Its own comment explains why:
  tokens cross to the entry threshold in ~26s measured, and slowing it makes
  every candidate fail on `MIN_DISTINCT_BUYERS`. That exact failure happened
  once already, at the 18:49 switchover. Cheapness stops where correctness
  does.
- **Slippage stays capped at 10%.** A sell failing on `0x1771` is re-quoted,
  never loosened.

## What this does NOT fix

Paying is not the answer and was checked: Chainstack's flat-rate plans start at
$1 199/month and the unlimited-node add-on at $149/month, against a $28 wallet
and a standing rule of no paid tooling below $1 000/month of ARIA revenue. At
49% of a free quota, there is nothing to buy — the problem is coordination.

Helius is a separate matter: its monthly quota is exhausted, which is a
calendar issue, not a design one.
