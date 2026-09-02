# Implementation Plan: Live Signal Observer

**Branch**: `017-live-signal-observer` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/017-live-signal-observer/spec.md`

## Summary

A dedicated background service that discovers momentum candidates with the same DexScreener feed, dedup, cooldown and hourly cap as the execution path (all imported, never restated), evaluates them through `evaluate_momentum_entry()` directly (pure computation; specs/016's wrapper persists the observation for free), then sends a dedicated live-signal Telegram message — three families shown separately with a data-quality label and one status, never a combined score, never trade wording — while paper-trading stays paused. Zero modification to `momentum_websocket.py`, `paper_trader.py`, `momentum_entry.py`'s decision logic, guardrails, or the kill-switch.

## Technical Context

**Language/Version**: Python 3.12 (existing `aria_core`)

**Primary Dependencies**: `websockets` (already a base dep, used by `momentum_websocket.py`), `aiosqlite` (existing pattern), `aria_core.gateway.telegram_bot.send_message` (existing primitive), imports from `momentum_websocket`/`momentum_entry`/`paper_trader`/`skills.market_sentiment`/`momentum_signal_observation`/`risk_guard`

**Storage**: one small SQLite table (`live_signal_notification`) in `DATA_DIR`/`aria.db`; observations themselves live in specs/016's tables

**Testing**: pytest + pytest-asyncio (existing); new `tests/test_live_signal_observer.py`; regression = existing `test_momentum_websocket.py`/`test_paper_trader.py`/`test_momentum_entry.py` unchanged

**Target Platform**: `aria-api` container, started from `vanguard/backend/app/main.py` like `momentum_websocket_listener`

**Project Type**: single backend package + one host wiring change

**Performance Goals**: evaluation load ≤ execution path's `MAX_EVALUATIONS_PER_HOUR` (200/h, imported) — no new load class on GeckoTerminal/GoPlus/Blockscout; Telegram sends ≤ one per token per 4h

**Constraints**: FR-003 (runs while `/offpaper` active), FR-004 (zero execution, row-count-tested), FR-005 (no edit to the named modules), research.md §1 (not gated by `/stop`), never read/write `vanguard/backend/.env`

**Scale/Scope**: Base only today (`DEFAULT_CHAINS`); ~120 lines of loop plumbing + ~150 lines of presentation/format + tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Guardrail files untouched**: PASS.
- **Real capital**: PASS — observation + operator messaging only; FR-004 mechanically tested.
- **Destructive git operations**: PASS — none.
- **Architectural coherence / no restated constants** (§1bis): PASS — every threshold/endpoint/cap is imported from its owning module (research.md §4); `discovery_core` extraction explicitly deferred as a later refactor per operator.
- **Resource-Engineering funnel**: PASS — liquidity prefilter + cooldown + hourly cap before any evaluation; Telegram cooldown + status threshold before any send.
- **Fail-safe / never fabricate**: PASS — LOW-quality family shows no figure; DATA INCOMPLETE precedence; no message without a persisted observation row.
- **Testability**: PASS — pure helpers unit-tested; zero-execution and no-trade-wording asserted by tests.

Post-design re-check: no violations. No Complexity Tracking needed.

## Project Structure

### Documentation (this feature)

```text
specs/017-live-signal-observer/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/live_signal_observer.md
└── tasks.md   (/speckit-tasks)
```

### Source Code (repository root)

```text
packages/aria-core/src/aria_core/
├── live_signal_observer.py          # NEW: LiveSignalObserver, presentation, classify, format, notification table
└── (no other aria_core file modified)

vanguard/backend/app/main.py         # MODIFIED: +2 lines (start()/stop() wiring next to momentum_websocket_listener)

packages/aria-core/tests/
└── test_live_signal_observer.py     # NEW
```

**Structure Decision**: one new module mirroring `momentum_websocket.py`'s service shape (singleton with `start`/`stop`, endpoint loops, drain loop), plus the two-line host wiring that every background service in this dome already uses. Nothing else changes.

## Complexity Tracking

None — no Constitution Check violations.
