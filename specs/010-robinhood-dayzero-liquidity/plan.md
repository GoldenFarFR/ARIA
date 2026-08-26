# Implementation Plan: Calibrate Robinhood shadow pocket's day-zero liquidity gate

**Branch**: `010-robinhood-dayzero-liquidity` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/010-robinhood-dayzero-liquidity/spec.md`

## Summary

Robinhood shadow pocket's day-zero discovery feed (specs/006, deployed 25/08) has been
silent for ~15h+: the existing `MIN_LIQUIDITY_USD=4000.0` floor, calibrated on the retired
DexPaprika "trending pools" population (200 real trades, 61.8% winrate), blocks virtually
100% of the structurally-different day-zero population (freshly created pools, median
measured liquidity at rejection ≈ $0). Research confirmed the existing 10-minute retry/
maturation window already works correctly — the defect is purely the threshold. Fix:
introduce a new, entry-mode-scoped floor (`MIN_LIQUIDITY_USD_DAY_ZERO = $200`, provisional
per Doctrine d'Ingestion) applied consistently at both duplicated filter sites, in both
pocket variants (v1/v2), leaving the validated DexPaprika-path floor untouched. A
recalibration protocol (n≥100, existing statistical guardrails) is documented for later,
once real day-zero closures accumulate — no final number forced today.

## Technical Context

**Language/Version**: Python 3.11 (aria-core), asyncio

**Primary Dependencies**: `aria_core.services.onchain_pool_discovery` (day-zero WS discovery),
`aria_core.robinhood_pump_shadow` / `robinhood_pump_v2_shadow` (shadow ledgers),
`aria_core.pretrade_rejection_log` (existing rejection audit trail), the out-of-repo
standalone process `/opt/aria-data/solana-robinhood-shadow/shadow_persistent.py` (systemd
service `aria-shadow-persistent.service`) that actually runs `robinhood_discovery_loop`

**Storage**: SQLite (`/opt/aria-data/shadow.db`) — `robinhood_pump_shadow_log`,
`robinhood_pump_v2_shadow_log` (table name TBD, verify during implementation),
`robinhood_pump_regime_candidates_log`, `fresh_launch_pretrade_gate_log` (all existing
schemas, no migration needed)

**Testing**: pytest (`.venv/bin/python -m pytest`), existing test files
`test_robinhood_pump_shadow.py`, `test_robinhood_pump_v2_shadow.py`,
`test_onchain_pool_discovery.py`, plus `test_coherence.py` for project-wide invariants

**Target Platform**: Linux VPS — one change lands in the git-tracked `packages/aria-core`
library, the other lands in the out-of-repo `shadow_persistent.py` call site (requires a
`systemctl restart aria-shadow-persistent.service` to take effect, same as this session's
earlier Solana fix)

**Project Type**: single project (library + one external always-on process consuming it)

**Performance Goals**: N/A — this is a threshold/logic fix, not a throughput change

**Constraints**: must not change `MIN_LIQUIDITY_USD` (4000.0, DexPaprika path) in any way;
must not regress the pre-23/08 defect (positions on near-zero-liquidity pools); shadow/
simulation only, no real-capital path touched, kill-switch stays armed throughout

**Scale/Scope**: 2 pocket modules (v1 + v2, sharing one imported constant), 1 discovery
service call site, 1 new constant, 1 new branch on existing `entry_mode` parameter

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate (from constitution) | Status | Note |
|---|---|---|
| Doctrine d'Ingestion (never abandon for lack of data; conservative provisional hypothesis + recalibration plan) | PASS | Exactly the pattern applied: $200 provisional floor, explicit recalibration protocol gated on n≥100 (research.md Decision 2, 4). |
| Statistical guardrail (never trust a segment without outlier removal / day-count check) | PASS | Baked into the recalibration protocol (Decision 4), not skipped. |
| "A system's own data can never validate its own prices/verdicts" | PASS | The $200 floor is derived from `fresh_launch_pretrade_gate_log` (external-ish, mechanically logged rejections), explicitly flagged as left-censored — not blindly trusted as complete truth. |
| Sobriety / architectural coherence (reuse existing patterns, no duplicated logic) | PASS | Reuses the existing `entry_mode` discriminator already threaded through the call chain, rather than inventing a second flag. |
| Real-capital guardrails (`permission_mode`/`wallet_guard`/kill-switch) | PASS, untouched | This feature is shadow/simulation only; kill-switch stays armed per operator's explicit 26/08 decision, no real-capital code path is touched. |
| Fast-Track vs spec-kit router | PASS | Correctly routed to full spec-kit: this changes a strategy parameter (entry liquidity filter), explicitly out of Fast-Track scope. |

No violation requiring justification.

## Project Structure

### Documentation (this feature)

```text
specs/010-robinhood-dayzero-liquidity/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md         # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks, not this command)
```

No `contracts/` directory: this feature exposes no external interface — it is an internal
strategy-parameter fix consumed only by the pocket's own shadow-trading loop.

### Source Code (repository root)

```text
packages/aria-core/src/aria_core/robinhood_pump_shadow.py       # new MIN_LIQUIDITY_USD_DAY_ZERO
                                                                  # constant + entry_mode branch
                                                                  # in record_signals()
packages/aria-core/src/aria_core/robinhood_pump_v2_shadow.py     # same entry_mode branch, imports
                                                                  # the new constant from v1
/opt/aria-data/solana-robinhood-shadow/shadow_persistent.py      # OUT OF REPO -- robinhood_discovery_loop's
                                                                  # check_candidates() call updated to
                                                                  # pass the entry-mode-aware floor;
                                                                  # requires a systemd restart to deploy
packages/aria-core/tests/test_robinhood_pump_shadow.py           # new tests for the entry_mode branch
packages/aria-core/tests/test_robinhood_pump_v2_shadow.py        # same, v2
```

**Structure Decision**: single project, no new source directories. The one structural
wrinkle: half the change lands in the git-tracked monorepo (deployed via the normal
blue-green Docker cycle), the other half lands in the standalone out-of-repo process
(deployed via `systemctl restart`) — both must ship together for the fix to take effect,
since `shadow_persistent.py` is what actually calls `record_signals()` in production.

## Complexity Tracking

*No constitution violations — table intentionally omitted.*
