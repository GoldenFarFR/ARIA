# Implementation Plan: Momentum Signal Observation Layer

**Branch**: `016-momentum-signal-observation-layer` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/016-momentum-signal-observation-layer/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Capture, for every candidate the momentum pipeline evaluates (bought or rejected), the three already-existing signal families (on-chain, chart, social) as separate blocks, the pipeline's real decision, and forward price performance at five fixed horizons — a strictly additive, read-after-decide observation layer with zero effect on the existing decision logic. Technical approach: wrap `evaluate_momentum_entry()` at its single call boundary (never duplicate capture at its ~16 internal early-return gates), persist to two new SQLite tables following this repo's established append-only-log pattern (`dex_score_log.py`/`signal_cascade_convergence.py`), and resolve forward prices via a new lightweight heartbeat cycle reusing the already-throttled `dexscreener.fetch_token_pairs()` client.

## Technical Context

**Language/Version**: Python 3.11 (existing `aria_core` package)

**Primary Dependencies**: `aiosqlite` (existing pattern, see `dex_score_log.py`), `aria_core.services.dexscreener` (existing, already-throttled REST client), `aria_core.heartbeat` (existing cycle scheduler)

**Storage**: SQLite via `aria_core.paths.aria_db_path()` (`DATA_DIR`/`aria.db`), same convention as every other pocket/shadow/log table in this repo — two new tables, no new storage technology

**Testing**: pytest, following this repo's existing test file conventions (`tests/test_momentum_entry.py`, `tests/test_dex_score_log.py`-style module tests)

**Target Platform**: Linux server (VPS), inside the existing `aria-api` Docker container / `heartbeat.py` loop

**Project Type**: Single backend package (`packages/aria-core`) — no frontend/API surface change

**Performance Goals**: Zero added latency on the momentum decision path (FR-008) — capture must be async/best-effort and never block the caller; forward-price cycle runs independently on its own short cadence (~60s), not on the decision path

**Constraints**: Must not modify any existing threshold/gate/decision logic (FR-009); must never fabricate a price (repo-wide doctrine); must reuse existing throttled clients rather than open new ones (Sobriety doctrine); observation capture must be non-blocking best-effort (same resilience posture as `narrative_signal_shadow.record_evaluation`, already used at the exact same call site)

**Scale/Scope**: One observation row per momentum candidate evaluation (both `paper_trader.py`'s 1M$ test and momentum-driven shadow pockets funnel through the same `evaluate_momentum_entry()`); five forward-performance rows per observation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Guardrail files untouched** (`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`): PASS — feature touches none of them.
- **Real capital**: PASS — shadow/observation only, zero capital, zero execution change (FR-008/FR-009).
- **Destructive git operations**: PASS — none planned.
- **Zero-Permission Policy** (test-validated code deploys autonomously): applies once tests are green; no operator confirmation needed to proceed through planning/implementation.
- **Architectural coherence / no duplication** (constitution §1bis): the plan reuses `dexscreener.fetch_token_pairs()` (already throttled) rather than a new price client, and follows the exact append-only-log table pattern already established by `dex_score_log.py`/`signal_cascade_convergence.py` rather than inventing a new persistence style.
- **No brute-force / staged funnel** (Resource-Engineering doctrine): the forward-price cycle filters via a cheap SQL `WHERE` (horizon due, not yet resolved) before any network call, and deduplicates by token per cycle rather than firing one call per pending horizon row.
- **Testability**: every new capability ships with a test wired into CI (`## 5.` implementation phase), per Permanent Norms.

No violations requiring Complexity Tracking justification.

## Project Structure

### Documentation (this feature)

```text
specs/016-momentum-signal-observation-layer/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/
│   └── momentum_signal_observation.md
└── tasks.md              # Phase 2 output (/speckit-tasks, not this command)
```

### Source Code (repository root)

Single existing backend package, no new project/service. Real paths:

```text
packages/aria-core/src/aria_core/
├── momentum_entry.py                    # MODIFIED: evaluate_momentum_entry renamed
│                                         #   to _evaluate_momentum_entry_core, thin
│                                         #   wrapper added under the same public name
├── momentum_signal_observation.py       # NEW: capture_observation(),
│                                         #   resolve_due_forward_prices(), table DDL,
│                                         #   read helpers — follows dex_score_log.py's
│                                         #   module shape
├── heartbeat.py                          # MODIFIED: one new cycle entry calling
│                                         #   momentum_signal_observation.resolve_due_forward_prices()
└── services/dexscreener.py               # UNCHANGED: reused as-is (fetch_token_pairs)

packages/aria-core/tests/
├── test_momentum_entry.py                # UNCHANGED assertions (regression gate, SC-004)
│                                         #   — new tests only ADD coverage for the wrapper
└── test_momentum_signal_observation.py   # NEW: capture shape, availability semantics,
                                         #   forward-resolution funnel, dedup-by-token
```

**Structure Decision**: single new module (`momentum_signal_observation.py`) inside the existing `aria_core` package, mirroring `dex_score_log.py`'s file shape (module-level DDL guard + async functions, `aiosqlite`, `aria_db_path()`). No new package, no new service, no frontend/API surface. The only edits to a file this feature does not own are `momentum_entry.py` (the rename + wrapper described in research.md §1 / contracts/momentum_signal_observation.md) and `heartbeat.py` (one new cycle registration, same pattern as every other heartbeat entry in that file).

## Complexity Tracking

No Constitution Check violations — nothing to justify here.
