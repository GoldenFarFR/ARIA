# Implementation Plan: RPC Security Shadow

**Branch**: `018-rpc-security-shadow` | **Date**: 2026-09-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/018-rpc-security-shadow/spec.md`

## Summary

Build the measuring instrument, not the replacement. A new isolated engine simulates a buy then a sell against the chain (`eth_call` + stateOverride, `debug_traceCall` for the diagnosis), emits a normalized verdict bound to its block and full simulation context, and a comparator records agreement/disagreement/unknown against the existing source's *cached* verdict plus the real RU cost. A curated 50-token benchmark across 3 amount tiers produces cost distributions (p50/p95/p99/max), separate unknown vs simulation-error rates, and the daily-capacity projection. Nothing gates, rejects or sizes anything; whether the existing security source can ever be replaced is a later decision this experiment exists to inform.

## Technical Context

**Language/Version**: Python 3.12 (existing `aria_core`)

**Primary Dependencies**: `httpx` (existing), `aiosqlite` (existing); imports from `services/evm_swap_ws.py` (`_DEX_FAMILY`, price/decimals conventions), `services/chainstack_ru_budget.py` (the sole budget mechanism), `services/goplus_watchlist.py` (`get_fresh`, read-only)

**Storage**: three new SQLite tables in `DATA_DIR`/`aria.db`, append-only pattern (`dex_score_log.py` shape)

**Testing**: pytest + pytest-asyncio; new `tests/test_rpc_security_engine.py`; regression set = `test_momentum_entry.py`, `test_paper_trader.py`, `test_goplus_watchlist.py`, `test_momentum_signal_observation.py` unchanged

**Target Platform**: `aria-api` container; invoked by the benchmark harness and (later) a shadow cycle

**Project Type**: single backend package, no host wiring change in this phase

**Performance Goals**: measured, not assumed. Projection from the official RU table: 4 RU/token with trace, 12 RU across 3 tiers -> ~6,371 tokens/day on Base's idle budget alone (3x the ~2,000/day needed). The benchmark must confirm this empirically and report p95, not the mean.

**Constraints**: shadow-only (FR-007, mechanically tested); never `risky` from an infrastructure failure (FR-014); `unknown` and `simulation_error` counted separately end-to-end; provider recorded explicitly, never inferred from the chain (FR-004); no credentialed URL persisted (FR-005); Base + Robinhood only (solana cap = 0); no modification to `evaluate_hard_gates` / `_check_honeypot` / `goplus_watchlist`

**Scale/Scope**: benchmark 50 tokens x 5 categories x 3 tiers; live shadow later bounded by the same hourly cap the execution path honours

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Guardrail files untouched**: PASS.
- **Real capital**: PASS -- simulation only, no transaction is ever broadcast; `eth_call` cannot move funds by construction.
- **Destructive git operations**: PASS.
- **Architectural coherence (§1bis)**: PASS -- DEX families imported from `evm_swap_ws._DEX_FAMILY` (whose docstring forbids guessing a second mapping), budget through the existing `chainstack_ru_budget`, comparison verdict from the existing cached watchlist. No constant restated, no second budget.
- **Resource-Engineering funnel**: PASS -- budget checked before any call; comparison read from cache rather than spending the quota under measurement; benchmark on 50 curated tokens before any volume.
- **Fail-safe / never fabricate**: PASS -- an unusable simulation is `unknown`, never `risky`; a failed sell is never called "honeypot" without trace evidence in the token contract; the tax threshold is labelled uncalibrated.
- **"Verify before asserting"**: PASS -- method availability, archive depth (~926 d on Base), Robinhood parity and the RU table were all measured or read from the provider's own docs before this plan, not assumed.
- **Testability**: PASS -- shadow proven by AST + zero-row-delta tests, not by comment.

Post-design re-check: no violations.

## Project Structure

### Documentation (this feature)

```text
specs/018-rpc-security-shadow/
|- plan.md
|- research.md
|- data-model.md
|- quickstart.md
|- contracts/rpc_security_engine.md
`- tasks.md   (/speckit-tasks)
```

### Source Code (repository root)

```text
packages/aria-core/src/aria_core/
|- rpc_security_engine.py        # NEW: endpoint/provider resolution, buy+sell simulation,
|                                #      trace-based cause derivation, comparator, benchmark,
|                                #      concurrency probe, 3 tables
`- services/goplus.py            # MODIFIED: correct the stale "no monthly/daily cap
                                 #      confirmed" comment (150k/mo, 30k/day, 150/min are public)

packages/aria-core/tests/
`- test_rpc_security_engine.py   # NEW
```

**Structure Decision**: one self-contained module. No host wiring, no heartbeat cycle in this phase -- the engine is driven by the benchmark harness first, exactly as the operator sequenced it (curated set before volume). Wiring it to the live candidate stream is a later, separate step once the cost figures exist.

## Complexity Tracking


None -- no Constitution Check violations.
