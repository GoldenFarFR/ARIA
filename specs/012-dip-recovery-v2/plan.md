# Implementation Plan: Dip-recovery shadow pocket, v2 -- Base/Robinhood market-cap-bounded dip entry

**Branch**: `012-dip-recovery-v2` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/012-dip-recovery-v2/spec.md`

## Summary

A new, fully independent shadow pocket (`dip_recovery_v2_shadow.py`) that buys a simulated
position when a Base or Robinhood Chain token drops at least 30% in 24h while sitting inside a
market-cap band (50k-1M USD, "post-bonding" per the operator's own framing), clears a 25k USD
liquidity floor, and — new this session — clears a 14-day minimum pair/pool age. Exit is a fixed
+25% take-profit with no stop-loss (operator's explicit choice) and a 7-day timeout safety net.
A working draft already exists (uncommitted) and surfaced one real, confirmed bug during its own
test-writing: the dedup mechanism copied from `dip_recovery_shadow.py` (v1) cannot function here
because v2's discovery feed (DexPaprika's own worst-24h-performers query) never re-surfaces a
token once it recovers, so a recovery-triggered episode flag can latch permanently. Fix: dedupe
directly on "is there already an open row for (contract, chain)" instead (see research.md
Decision 1). This plan also resolves the four FR-010 items the spec required a made, not
guessed, decision on.

## Technical Context

**Language/Version**: Python 3.11 (aria-core), asyncio, aiosqlite

**Primary Dependencies**: `aria_core.services.dexpaprika` (discovery: `get_trending_pools`,
already reused from `geckoterminal`'s `TrendingPool`/`TrendingPoolsResult` dataclasses),
`aria_core.services.dexscreener` (`fetch_token_pairs`/`PairSnapshot` for market cap/liquidity/
price resolution), `aria_core.risk_guard` (`DEX_SWAP_FEE_PCT`, realistic fill/exit pricing —
same pattern as every sibling pocket)

**Storage**: SQLite (`aria_db_path()`) — new table `dip_recovery_v2_shadow` (one row per
simulated position). No episode-state table (rejected, see research.md Decision 1) — dedup
reads `dip_recovery_v2_shadow` directly.

**Testing**: pytest (`.venv/bin/python -m pytest`), existing file
`test_dip_recovery_v2_shadow.py` (14 tests already written this session, 13 green, 1 red —
`test_discover_rearms_after_recovery_above_threshold`, the exact regression guard for the
Decision 1 fix), plus `test_coherence.py` (`_KNOWN_ENABLED_GATES`,
`test_pocket_parameter_registry_matches_the_code`)

**Target Platform**: Linux VPS — lands entirely inside the git-tracked `packages/aria-core`
library, deployed via the normal blue-green Docker cycle (`aria-api` container, heartbeat loop
in-process). No out-of-repo process involved (unlike the Solana pocket) — this is a `heartbeat.py`
`HeartbeatTask`, not a standalone systemd service.

**Project Type**: single project (library, consumed by the existing in-process heartbeat loop)

**Performance Goals**: N/A — shadow/simulation only, bounded by `DISCOVERY_LIMIT=20` candidates
per chain per pass (funnel doctrine: cheap DexPaprika filter first, one bounded paid DexScreener
call only on survivors)

**Constraints**: shadow/simulation only, zero real capital, kill-switch/guardrail files
untouched; must not share table/module/episode-state with `dip_recovery_shadow` (v1); must not
regress the 13 already-green tests while fixing the 1 red one; gate activation in prod `.env`
requires the operator's own edit (direct `.env` writes are structurally blocked this session)

**Scale/Scope**: 1 new module, 1 new table, 1 heartbeat task (draft already wired,
`enabled=False` by default pending operator activation), 2 chains (Base, Robinhood)
simultaneously per FR-001

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate (from constitution) | Status | Note |
|---|---|---|
| Fast-Track vs spec-kit router | PASS | Correctly routed to full spec-kit: this introduces new strategy parameters (entry threshold, market-cap band, take-profit level) — explicitly out of Fast-Track scope. |
| Doctrine d'Ingestion (never abandon for lack of data; instrument collection, conservative provisional hypothesis) | PASS | All entry/exit parameters are the operator's own stated provisional test values ("un truc simpliste pour tester"), logged in full on every position for future recalibration (spec.md SC-004/SC-005, same n≥100/n≥1000 protocol as specs/010 and specs/011). |
| "A system's own data can never validate its own prices" (22/08 doctrine) | PASS, with an explicit new guard | Both entry and exit prices are read from DexScreener, the same provider whose corrupted-ratio price caused today's real `base_momentum_shadow.py` incident (id=202, +707006.8% nominal). Research Decision 2 adds an equivalent price-sanity guard here rather than assuming this pocket is exempt because it's a different module. |
| Pense-Système / funnel doctrine (never a linear/unbounded resource pattern) | PASS | Unchanged from the draft: one cheap server-side-filterable DexPaprika call, one bounded paid DexScreener call only per already-qualified survivor, `DISCOVERY_LIMIT=20` per chain per pass. |
| Cohérence architecturale (reuse existing patterns, never duplicate) | PASS | Reuses `risk_guard.DEX_SWAP_FEE_PCT`, the existing `TrendingPool`/`PairSnapshot` dataclasses (never a duplicated schema), the self-healing `_ensure_table` cache-revalidation pattern fixed dome-wide earlier the same day. |
| vN variant precedent (own module/table, never a bolted-on parameter) | PASS | `dip_recovery_v2_shadow.py` / `dip_recovery_v2_shadow` table, fully separate from v1, same precedent as `robinhood_pump_v2_shadow.py`. |
| Real-capital guardrails (`permission_mode`/`wallet_guard`/kill-switch) | PASS, untouched | Shadow/simulation only; gate defaults `enabled=False`; activation requires the operator's own `.env` edit. |

No violation requiring justification.

## Project Structure

### Documentation (this feature)

```text
specs/012-dip-recovery-v2/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks, not this command)
```

No `contracts/` directory: this feature exposes no external interface — it is an internal
heartbeat-driven shadow pocket consumed only by its own gate/dispatch in `heartbeat.py`.

### Source Code (repository root)

```text
packages/aria-core/src/aria_core/dip_recovery_v2_shadow.py        # module: discovery, dedup fix
                                                                     # (Decision 1), 14-day age
                                                                     # filter (Decision 3), exit
                                                                     # price-sanity guard
                                                                     # (Decision 2)
packages/aria-core/src/aria_core/heartbeat.py                     # HeartbeatTask + gate-check +
                                                                     # dispatch (already drafted,
                                                                     # enabled=False)
packages/aria-core/src/aria_core/pocket_parameters.py              # registry entry (already added)
packages/aria-core/tests/test_dip_recovery_v2_shadow.py           # existing 14 tests, updated for
                                                                     # the dedup fix + new age-filter
                                                                     # and price-sanity tests
packages/aria-core/tests/test_coherence.py                        # _KNOWN_ENABLED_GATES entry
                                                                     # (already added)
```

**Structure Decision**: single project, no new source directories. Unlike specs/010/011 (Solana,
which needs a `systemctl restart` on an out-of-repo process), this pocket runs entirely inside
the existing `aria-api` Docker container's heartbeat loop — the normal blue-green deploy alone is
sufficient to pick up the change, no second deployment surface.

## Complexity Tracking

*No constitution violations — table intentionally omitted.*
