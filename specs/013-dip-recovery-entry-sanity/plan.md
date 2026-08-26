# Implementation Plan: dip_recovery_v2_entry_sanity_guard

**Branch**: `013-dip-recovery-entry-sanity` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-dip-recovery-entry-sanity/spec.md`

## Summary

Add a cross-provider sanity guard to `dip_recovery_v2_shadow.py`'s entry path: before opening a
position on DexPaprika's `var_24h_pct <= -30.0` dip signal, cross-check it against
`dexscreener.PairSnapshot.price_change_24h`, already resolved by the same call
`_maybe_open_position` makes for market cap/liquidity (zero extra network cost). Reject the entry
ONLY when the two readings flatly disagree on direction with both readings large in magnitude
(DexPaprika strongly negative, DexScreener strongly positive) — the exact shape of the real
incident that motivated this feature (position id=13, `dip_recovery_v2_shadow`, contract
`0x23acfab04106a21af0ae1643b74cfec3c9aac181`, chain=robinhood: DexPaprika read -31.9487% at
entry, DexScreener/DexPaprika's own live lookup both read ~+29% minutes later for the same
token). Ordinary same-direction disagreement (a few points off) or a missing/zero DexScreener
reading must never block an entry — this is a plausibility guard, not a second liquidity filter.

## Technical Context

**Language/Version**: Python 3.11 (aria-core), asyncio, aiosqlite

**Primary Dependencies**: `aria_core.services.dexscreener` (`PairSnapshot.price_change_24h`,
already fetched by the existing `_resolve_market_cap_and_price` call) — no new dependency.

**Storage**: SQLite (`aria_db_path()`), existing `dip_recovery_v2_shadow` table — no schema
change (see data-model.md: this is a decision-time filter, not a persisted field).

**Testing**: pytest (`.venv/bin/python -m pytest`), extending the existing
`test_dip_recovery_v2_shadow.py` (`_snapshot()` helper gets a new `price_change_24h` parameter).

**Target Platform**: Linux VPS — same `heartbeat.py` in-process `HeartbeatTask` this pocket
already runs under (`dip_recovery_v2_shadow_cycle`), no new process/service.

**Project Type**: single project (library, in-process heartbeat task)

**Performance Goals**: N/A — shadow/simulation only, zero additional network calls (SC-004).

**Constraints**: Must not change this pocket's existing market-cap/liquidity/pool-age filters or
the exit-side `EXIT_PRICE_SANITY_MULTIPLE` guard — purely additive to the entry path.

**Scale/Scope**: Single module (`dip_recovery_v2_shadow.py`), one new guard function, no new
table, no new gate — the existing `ARIA_DIP_RECOVERY_V2_SHADOW_ENABLED` gate covers this pocket
as a whole already.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Governance/gates**: This is a strategy/entry-filter parameter change → full spec-kit cycle
  required (not Fast-Track), per CLAUDE.md's routeur table. ✅ Followed (specs/013, this cycle).
- **Guardrail files**: Zero changes to `permission_mode`/`wallet_guard`/`regles-uniques`/
  `config.toml`. ✅ PASS.
- **Real capital**: `dip_recovery_v2_shadow` is pure shadow/simulation, never wired to real
  capital or the kill-switch. ✅ PASS.
- **Sobriety (perf & cost)**: Zero new network calls — reuses data already fetched (SC-004). ✅
  PASS.
- **Testability**: Every new behavior gets a dedicated regression test (see research.md/
  quickstart.md). ✅ PASS.
- **"A system's own data can never validate that system's own prices"**: This guard is a
  genuine cross-PROVIDER check (DexPaprika vs DexScreener, distinct sources/computations), not
  the same self-referential trap the 22/08 doctrine warns against. ✅ PASS.
- **Doctrine d'Ingestion (never abandon for missing data)**: The needed field
  (`price_change_24h`) is already fetched today, zero instrumentation needed — direct
  application of "check if the data is already available before building anything new". ✅
  PASS.

No violations. Complexity Tracking table not needed.

## Project Structure

### Documentation (this feature)

```text
specs/013-dip-recovery-entry-sanity/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
└── tasks.md              # Phase 2 output (/speckit-tasks, not this command)
```

No `contracts/` directory — internal shadow-pocket decision logic, no external interface.

### Source Code (repository root)

```text
packages/aria-core/
├── src/aria_core/
│   └── dip_recovery_v2_shadow.py     # _maybe_open_position gets the new guard
└── tests/
    └── test_dip_recovery_v2_shadow.py # _snapshot() helper extended, new guard tests
```

**Structure Decision**: Single project, additive changes inside the existing
`dip_recovery_v2_shadow.py` module and its existing test file — same shape as specs/012, no new
files needed beyond documentation.

## Complexity Tracking

Not applicable — no constitution violations to justify.
