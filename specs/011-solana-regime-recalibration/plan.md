# Implementation Plan: Recalibrate the Solana late-bonding shadow pocket's regime gate

**Branch**: `011-solana-regime-recalibration` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/011-solana-regime-recalibration/spec.md`

## Summary

`solana_late_bonding_shadow.py`'s regime gate (`REGIME_MIN_MEDIAN_PEAK_PCT`, raised 25→40 on
2026-08-24) has been closed ~94-96% of the time since, producing zero new closures for 15h+
despite the curve tracker actively finding in-band candidates. Research re-measured the real
open-time trade-off on the current population (4631 candidates, 2026-08-25T14:39 onward,
`solana_regime_candidates_log`): 20%→37.6%, 25%→19.8%, 30%→11.5%, 35%→6.4%, 40%→4.0% open-time.
The 20% bar is explicitly excluded (it is the exact value whose capture-gap — mean real peak
+16.23% vs. mean net captured -11.74%, measured on 44 closures, 23/08 — motivated every
tightening since). The capture-gap itself is a structural property of the exit mechanics
(`TRAILING_STOP_PCT`, liquidity-collapse exit), not a function of the regime threshold, and
cannot be re-measured per-threshold today (only 12 real closures currently exist, far below
even the provisional n≥100 bar) — so the chosen value is a reasoned compromise (open-time gain
vs. safety margin from the known-dangerous 20% bar), not a re-proven capture-gap figure.
Fix: lower `REGIME_MIN_MEDIAN_PEAK_PCT` from 40.0 to **30.0** (see research.md Decision 1 for
the full rationale over 25.0), unchanged mechanism, recalibration protocol documented for when
real closures accumulate under the new value.

## Technical Context

**Language/Version**: Python 3.11 (aria-core), asyncio

**Primary Dependencies**: `aria_core.solana_late_bonding_shadow` (the pocket itself —
`regime_state()`, `regime_median_peak()`), the out-of-repo standalone process
`/opt/aria-data/solana-robinhood-shadow/shadow_persistent.py` (systemd service
`aria-shadow-persistent.service`) that actually runs this pocket's discovery/exit loops

**Storage**: SQLite (`/opt/aria-data/shadow.db`) — `solana_regime_candidates_log` (regime
sensor's own input, read-only for this feature), `solana_late_bonding_shadow_log` (the
pocket's position log, archived to `_archive_reset_<date>` at epoch boundaries — existing
schema/pattern, no migration needed)

**Testing**: pytest (`.venv/bin/python -m pytest`), existing file
`test_solana_late_bonding_shadow.py`, plus `test_coherence.py` for project-wide invariants
(including `test_pocket_parameter_registry_matches_the_code`)

**Target Platform**: Linux VPS — the constant change lands in the git-tracked
`packages/aria-core` library (deployed via the normal blue-green Docker cycle), but the
process that actually evaluates `regime_state()` for this pocket is the out-of-repo
`shadow_persistent.py` (requires `systemctl restart aria-shadow-persistent.service` to pick up
the new value, same two-halves deployment shape as specs/010)

**Project Type**: single project (library + one external always-on process consuming it)

**Performance Goals**: N/A — this is a threshold/parameter fix, not a throughput change

**Constraints**: must not touch `TRAILING_STOP_PCT`, the liquidity-collapse exit, or any other
exit-mechanics parameter (the actual source of the capture-gap, out of scope here); must not
select 20.0 or anything at/below it (explicitly excluded by spec.md FR-002); must not
retroactively re-evaluate already-open positions under the new threshold (FR-006); shadow/
simulation only, kill-switch (`/stop`) stays armed throughout, untouched

**Scale/Scope**: 1 constant (`REGIME_MIN_MEDIAN_PEAK_PCT`), 1 pocket module, 1 out-of-repo
call site (the process reads the constant directly via import, no separate wiring needed),
1 epoch-boundary archive (same pattern as specs/010's T014b)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate (from constitution) | Status | Note |
|---|---|---|
| Doctrine d'Ingestion (never abandon for lack of data; conservative provisional hypothesis + recalibration plan) | PASS | 30.0 is explicitly provisional (research.md Decision 1); recalibration protocol documented (spec.md User Story 3 / FR-007), gated on n≥100 then n≥1000. |
| Statistical guardrail (never trust a segment without outlier removal / day-count check / re-testing without top-2/top-5) | PASS | The open-time trade-off table is a distribution-wide median calculation (already outlier-robust by construction — it is itself a rolling median, not a mean); the recalibration protocol requires the project's standard outlier/day-count checks once real closures exist. |
| "A system's own data can never validate its own prices/verdicts" | PASS (with an honest limit) | The capture-gap figure this recalibration respects (-11.74%/+16.23%) was measured on 44 REAL closures (23/08, before the current epoch), not invented; it cannot be re-measured at the new threshold today (only 12 real closures exist post-reset) — this limit is stated explicitly in research.md rather than glossed over. |
| Lire toutes les lignes avant conclusion (aggregate, never sample) | PASS | The open-time table was recomputed in this session directly from all 4631 rows in `solana_regime_candidates_log` (not resampled from the earlier same-day HANDOFF figures), with the day-count and row-count stated. |
| Sobriety / architectural coherence (reuse existing patterns, no duplicated logic) | PASS | No new mechanism — same `regime_state()`/`regime_median_peak()` function, same table, only the threshold constant changes. |
| Real-capital guardrails (`permission_mode`/`wallet_guard`/kill-switch) | PASS, untouched | Shadow/simulation only; kill-switch stays armed exactly as-is per the operator's standing instruction this session. |
| Fast-Track vs spec-kit router | PASS | Correctly routed to full spec-kit: this changes a strategy parameter (regime threshold), explicitly out of Fast-Track scope. |

No violation requiring justification.

## Project Structure

### Documentation (this feature)

```text
specs/011-solana-regime-recalibration/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks, not this command)
```

No `contracts/` directory: this feature exposes no external interface — it is an internal
strategy-parameter fix consumed only by the pocket's own regime gate.

### Source Code (repository root)

```text
packages/aria-core/src/aria_core/solana_late_bonding_shadow.py   # REGIME_MIN_MEDIAN_PEAK_PCT:
                                                                    # 40.0 -> 30.0, comment updated
                                                                    # with this recalibration's
                                                                    # rationale and history
/opt/aria-data/solana-robinhood-shadow/shadow_persistent.py       # OUT OF REPO -- no code change
                                                                    # needed (imports the constant
                                                                    # directly), but the service
                                                                    # MUST be restarted to load the
                                                                    # new value
packages/aria-core/tests/test_solana_late_bonding_shadow.py       # updated/new tests asserting the
                                                                    # new threshold value and the
                                                                    # regime-state behavior around it
```

**Structure Decision**: single project, no new source directories. Same two-halves deployment
shape as specs/010: the constant change lands in the git-tracked monorepo (Docker blue-green),
but the out-of-repo `shadow_persistent.py` process (which imports and evaluates this constant
via `regime_state()`) needs a `systemctl restart` to actually pick it up — the library change
alone has zero effect on the running pocket until that restart happens.

## Complexity Tracking

*No constitution violations — table intentionally omitted.*
