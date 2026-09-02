# Contract: `aria_core.rpc_security_engine` (+ shadow comparator, benchmark)

Internal Python module contract. No HTTP surface.

## Gate

`rpc_security_shadow_enabled() -> bool` — reads `ARIA_RPC_SECURITY_SHADOW_ENABLED`, OFF by default. No network call happens until this is on.

## Provider resolution (FR-004, FR-005)

```python
def resolve_endpoint(chain: str) -> tuple[str, str, str] | None
    # -> (https_url, provider_name, endpoint_role) or None if unsupported
```

Base and Robinhood only (`chainstack_ru_budget.cap_for(chain) > 0`). The HTTPS URL is derived from the chain's WSS variable (this repo's standing wss→https rule), and `provider_name` is resolved from **which variable was actually used**, never from the chain name — on Base, `ARIA_BASE_RPC_URL` is Alchemy while `ARIA_BASE_RPC_WS` is Chainstack. Only the role is ever returned to callers for persistence; the credentialed URL never leaves this function.

## Core evaluation

```python
async def evaluate_token(
    contract: str, chain: str, *,
    pair_address: str, dex_id: str,
    amount_tier: str = "small",          # small | mid | liquidity_relative
    block: int | None = None,            # None = latest; an int = historical replay
) -> RpcSecurityEvaluation
```

Sequence:
1. Resolve endpoint + provider. Unsupported chain → `simulation_error` / `ROUTER_FAILURE`, no call made.
2. Resolve `dex_family` via `evm_swap_ws._DEX_FAMILY` (imported, never restated). Unmapped, or an Aerodrome **stable** pool → `unknown` / `ROUTER_FAILURE`.
3. Check `chainstack_ru_budget.can_spend(chain)`. Exhausted → `simulation_error` with an explicit budget reason (FR-011), no call made.
4. Simulate the buy leg (`eth_call` + stateOverride on native balance).
5. Simulate the sell leg (override shape per research.md §5 — the one open implementation question).
6. On sell failure, `debug_traceCall` to derive the structured cause. Trace unavailable → keep the verdict, set `trace_available=0` (a valid verdict with no diagnosis, never an error).
7. Record RU via `chainstack_ru_budget.record_usage(...)` — the existing mechanism, never a second budget.
8. Return the normalized result with its complete reproduction context.

**MUST NOT**: call anything in `momentum_entry`, `paper_trader`, `limit_orders`, `goplus_watchlist` (beyond the read-only `get_fresh` used by the comparator), or write to any table a trading decision reads. **MUST NOT** ever return `risky` from an infrastructure failure.

## Status derivation (the rule the whole feature protects)

```
buy ok + sell ok + tax below threshold        -> safe
buy ok + sell ok + tax above threshold        -> risky   (HIGH_SELL_TAX)
buy ok + sell reverts in the TOKEN contract   -> risky   (SELL_REVERT / TRANSFER_RESTRICTED / MAX_TX / MAX_WALLET / TRADING_DISABLED)
buy ok + sell reverts in the POOL/ROUTER      -> unknown (INSUFFICIENT_LIQUIDITY / ROUTER_FAILURE)
buy fails                                      -> unknown (never risky — an unusable simulation is an absence of information)
any RPC/decoding/timeout failure               -> simulation_error (RPC_FAILURE)
```

The tax threshold is an **explicitly uncalibrated starting value**, labelled as such in code and never treated as validated because the engine shipped.

## Shadow comparator

```python
async def compare_with_existing_source(evaluation) -> ComparisonRow
```

Reads the existing verdict from `goplus_watchlist.get_fresh(contract, chain)` — the cached entry, **never a fresh paid call** (spending the quota under measurement would distort the measurement). No cached verdict → `outcome="unknown"` on that side.

`ground_truth` is left NULL. It is filled only by:

```python
async def adjudicate(comparison_id: int, verdict: str, source: str, note: str) -> None
```

with `source` in `{manual, onchain_check}`. Never auto-derived from either engine (operator #6).

## Benchmark harness

```python
async def run_benchmark(run_id: str, tokens_by_category: dict[str, list[dict]]) -> dict
async def benchmark_report(run_id: str) -> dict
```

Per token × 3 amount tiers: buy, sell, trace. The report returns, **per category and overall**: `ru_buy` / `ru_sell` / `ru_trace` / `ru_total`, and `latency_ms`, each as **p50 / p95 / p99 / max**; plus error rate, unknown rate (separately), agreement rate, cause-identification rate; plus the projection `budget_free / p95_ru_total` = evaluations affordable per day (FR-012, SC-004).

## Concurrency probe (operator #8)

```python
async def probe_concurrency(chain: str, levels: tuple[int, ...] = (1, 5, 10, 25)) -> dict
```

Runs N simultaneous evaluations at each level, reporting RU, latency, error and rate-limit rates. The instantaneous ceiling may bind long before the daily quota — the existing source's 10-per-minute limit shows this is a real phenomenon in this domain.

## Failure-injection tests (operator #7)

Timeout, rate limit, endpoint unavailable, trace unavailable, and the asymmetric case (`eth_call` works, tracing does not). Required outcome in every case: `unknown` or `simulation_error`, **never** `risky`, and never a rejection.

## Mechanical shadow proof (invariant 1)

Two tests, same bar as specs/017:
1. **AST-level**: the module's identifiers never include `run_paper_cycle`, `open_position`, `_default_momentum_analyzer`, `process_active_orders`, `evaluate_hard_gates`, `_check_honeypot`, `send_trading_notification`.
2. **Zero-row-delta**: row counts in `paper_position`, `pending_limit_order` and `momentum_signal_observation` are unchanged across a full evaluation + comparison + benchmark run.
