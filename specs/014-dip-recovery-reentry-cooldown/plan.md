# Implementation Plan: dip_recovery_v2_reentry_cooldown

**Branch**: `014-dip-recovery-reentry-cooldown` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/014-dip-recovery-reentry-cooldown/spec.md`

## Summary

Add a reentry cooldown to `dip_recovery_v2_shadow.py`: after a take-profit close on a (contract,
chain), refuse a new position on the SAME pair for `REENTRY_COOLDOWN_MINUTES = 60` minutes. Real
incident: position id=15 (contract via pool `0x49a11a3515755a730b20ae1d6c3ef5a997e20f728ad46d8859654c4d4eaad95a`,
chain=robinhood, symbol EARTHCOIN) closed `take_profit_25pct` at 21:06:17 UTC (+40.01% PnL);
position id=16 opened on the SAME pair 15 minutes later at 21:21:16 UTC, at a price essentially
identical to id=15's exit (-0.7% difference) — DexPaprika's `var_24h_pct` swung ~8 points between
the two entries despite the real price barely moving, the same metric-instability class already
documented in specs/013's research.md. Timeout closes (`timeout_max_hold`) are explicitly
EXCLUDED from this cooldown — 7 days already elapsed is a different, already-cooled-down
situation this feature's own risk does not apply to.

## Technical Context

**Language/Version**: Python 3.11 (aria-core), asyncio, aiosqlite

**Primary Dependencies**: None new — queries the pocket's own existing `dip_recovery_v2_shadow`
table (`close_reason`, `closed_at` columns, already persisted).

**Storage**: SQLite (`aria_db_path()`), existing table, no schema change.

**Testing**: pytest, extending `test_dip_recovery_v2_shadow.py`.

**Target Platform**: Same `heartbeat.py` in-process `HeartbeatTask` (`dip_recovery_v2_shadow_cycle`).

**Project Type**: single project (library, in-process heartbeat task)

**Performance Goals**: N/A — zero additional network calls (SC-004), one extra local SQLite query
per candidate that clears the open-position dedup.

**Constraints**: Must not change `_has_open_position` (specs/012) or the entry-sanity guard
(specs/013) — purely additive, independent check.

**Scale/Scope**: Single module, one new guard function, one new constant, no new gate.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Governance/gates**: Strategy/entry-filter parameter change → full spec-kit cycle (specs/014),
  per CLAUDE.md's routeur table. ✅ PASS.
- **Guardrail files**: Zero changes. ✅ PASS.
- **Real capital**: Pure shadow/simulation, unchanged. ✅ PASS.
- **Sobriety**: Zero new network calls — one extra local SQLite query, already-open connection
  reused. ✅ PASS.
- **Testability**: Every new behavior gets a dedicated regression test. ✅ PASS.
- **Doctrine d'Ingestion**: The data needed (`close_reason`/`closed_at`) is already persisted —
  no instrumentation gap to fill. ✅ PASS.
- **Independence from specs/012/013**: `_has_open_position` and `ENTRY_SANITY_MIN_CONFLICT_PCT`
  are untouched; this is a third, independent check in the same funnel. ✅ PASS.

No violations. Complexity Tracking table not needed.

## Project Structure

### Documentation (this feature)

```text
specs/014-dip-recovery-reentry-cooldown/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── tasks.md
```

No `contracts/` directory — internal shadow-pocket decision logic.

### Source Code (repository root)

```text
packages/aria-core/
├── src/aria_core/
│   └── dip_recovery_v2_shadow.py     # _maybe_open_position gets the new cooldown check
└── tests/
    └── test_dip_recovery_v2_shadow.py # new test-only helper to seed a closed row, new tests
```

**Structure Decision**: Single project, additive changes inside the existing module and test
file — same shape as specs/012/013.

## Complexity Tracking

Not applicable — no constitution violations to justify.
