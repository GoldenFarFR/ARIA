# Phase 1 Data Model: RPC Security Shadow

Three new SQLite tables in `DATA_DIR`/`aria.db`, following the append-only-log pattern already used by `dex_score_log.py` / `momentum_signal_observation.py`. No existing table is modified.

## Entity: `rpc_security_evaluation`

One row per (token, block, simulation size). Append-only — a verdict is always relative to its block, so a later run never overwrites an earlier one.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `contract` | TEXT NOT NULL | lower-cased |
| `chain` | TEXT NOT NULL | `base` / `robinhood` only (solana cap = 0, out of scope) |
| `evaluated_at` | TEXT NOT NULL | ISO-8601 UTC, wall-clock of the run |
| **status** | TEXT NOT NULL | `safe` / `risky` / `unknown` / `simulation_error` — the four are distinct, never collapsed (FR-002, operator #1) |
| `buy_success` | INTEGER | 1/0/NULL |
| `buy_amount_out` | TEXT | raw integer as string (never a float — precision loss on 18-decimal amounts) |
| `sell_success` | INTEGER | 1/0/NULL |
| `sell_amount_out` | TEXT | raw integer as string |
| `sell_tax_pct` | REAL | estimated, NULL when not computable |
| `failure_cause` | TEXT | one of the closed vocabulary (see below), NULL when no failure |
| `failure_contract` | TEXT | address where the revert originated |
| `failure_function` | TEXT | selector/name from the trace |
| `failure_reason` | TEXT | raw revert string when present |
| **`simulation_block`** | INTEGER NOT NULL | the block the simulation ran against |
| **`rpc_provider`** | TEXT NOT NULL | resolved from the env var actually used — never inferred from `chain` (FR-004) |
| **`rpc_endpoint_role`** | TEXT NOT NULL | role only, never a credentialed URL (FR-005) |
| `chain_id` | INTEGER NOT NULL | |
| `router` | TEXT NOT NULL | address simulated against |
| `dex_family` | TEXT | `v2` / `v3` / `v4`, from the existing `_DEX_FAMILY` mapping |
| `amount_in` | TEXT NOT NULL | raw integer as string |
| `amount_tier` | TEXT NOT NULL | `small` / `mid` / `liquidity_relative` (operator #3) |
| **`state_override_hash`** | TEXT NOT NULL | fingerprint of the override actually applied — two different shapes legitimately give two different verdicts (research.md §5) |
| `trace_available` | INTEGER NOT NULL | 0 when `eth_call` worked but tracing did not — a valid verdict with no diagnosis, not an error |
| `ru_cost` | INTEGER NOT NULL | RU actually charged for this evaluation |
| `latency_ms` | INTEGER NOT NULL | |

Index: `(contract, chain, simulation_block)` for replay lookups; `(evaluated_at)` for the benchmark aggregation.

### Closed failure vocabulary (FR-002, operator #4)

`SELL_REVERT` · `INSUFFICIENT_LIQUIDITY` · `TRANSFER_RESTRICTED` · `MAX_TX` · `MAX_WALLET` · `TRADING_DISABLED` · `HIGH_SELL_TAX` · `ROUTER_FAILURE` · `RPC_FAILURE` · `UNKNOWN`

Only `SELL_REVERT`, `TRANSFER_RESTRICTED`, `MAX_TX`, `MAX_WALLET` and `TRADING_DISABLED` are evidence about the **token**. `HIGH_SELL_TAX` is a *successful* sell. `INSUFFICIENT_LIQUIDITY`, `ROUTER_FAILURE` and `RPC_FAILURE` say nothing about the token's honesty. A trace that matches nothing stays `UNKNOWN` — never forced into the nearest label.

## Entity: `rpc_security_comparison`

One row per (token, chain, evaluation) pairing the two sources.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `evaluation_id` | INTEGER NOT NULL | FK to `rpc_security_evaluation` |
| `contract` / `chain` | TEXT NOT NULL | |
| `rpc_status` | TEXT NOT NULL | copied from the evaluation |
| `existing_source_status` | TEXT | read from the cached watchlist verdict; NULL when none cached |
| `existing_source_cached_at` | TEXT | freshness of that cached verdict |
| `outcome` | TEXT NOT NULL | `agreement` / `disagreement` / `unknown` |
| **`ground_truth`** | TEXT | `safe` / `risky` — **NULL by default**, filled only by adjudication (operator #6) |
| **`ground_truth_source`** | TEXT | `manual` / `onchain_check` — provenance is mandatory when `ground_truth` is set |
| `ground_truth_at` | TEXT | |
| `adjudication_note` | TEXT | why that verdict was reached |

**The rule this schema enforces**: `ground_truth` is never auto-derived from either engine. Neither source is the reference. Without this field, a high "agreement" rate would only prove the two sources behave alike — not that either is right. A disagreement with no adjudication stays an open question, visibly.

## Entity: `rpc_security_benchmark_run`

One row per benchmark execution, per token, per amount tier — the raw material the report aggregates.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `run_id` | TEXT NOT NULL | groups one full benchmark execution |
| `category` | TEXT NOT NULL | `safe` / `honeypot` / `high_tax` / `low_liquidity` / `atypical` |
| `evaluation_id` | INTEGER NOT NULL | FK |
| `ru_buy` / `ru_sell` / `ru_trace` | INTEGER | per-operation cost (operator #2) |
| `ru_total` | INTEGER NOT NULL | per complete token evaluation |
| `latency_ms` | INTEGER NOT NULL | |
| `cause_identified` | INTEGER NOT NULL | 1 when a failure occurred AND a structured cause was determined |

**Report requirement (never stored, always computed)**: per category and overall, each cost and latency metric must be reported as **p50 / p95 / p99 / max**, never a mean alone. A mean-only figure is explicitly not an acceptable deliverable — 2,000 tokens at an affordable average can still be unusable if p99 is catastrophic at peak.

## Validation rules

- A `simulation_error` never carries a `failure_cause` describing the token (FR-014, operator #7) — infrastructure failures are recorded as `RPC_FAILURE` and the status stays `simulation_error`.
- `unknown` and `simulation_error` are counted separately end-to-end, including in the benchmark report (operator #1) — one measures the engine's ability to conclude, the other the infrastructure's reliability.
- Every row carries a non-null `rpc_provider`, `rpc_endpoint_role`, `simulation_block` and `state_override_hash` — an evaluation whose conditions of production are unknown must not exist (SC-001).
- No row may contain a credentialed URL (SC-007).
- Amounts are stored as strings, never floats.
- `ground_truth` NULL is the normal state; setting it requires a `ground_truth_source`.
