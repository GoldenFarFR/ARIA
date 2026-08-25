# Feature Specification: Shadow pockets tuning -- Solana

**Feature Branch**: `003-shadow-solana`

**Created**: 2026-08-25

**Status**: In progress (closes when no further bug/improvement is found)

**Input**: Same operator mandate as `002-shadow-base` -- "+25% en moyenne sur 1000 trade sur chacune des blockchains", carte blanche recherche/tests, condition reelle (rug/frais/FOMO).

## Scope

`solana_late_bonding_shadow.py` -- the only active Solana sourcing pocket (FAST discovery retired 21/08). Runs OUTSIDE Docker, standalone process (`shadow_persistent.py`, `/opt/aria-data/shadow.db` -- NOT `aria.db`), owned by a separate concurrent Claude Code session as of 25/08 (confirmed stopped later same day -- re-verify before assuming this boundary still applies).

## User Scenarios & Testing

### User Story 1 - Confirm the real, current PnL against the +25%/1000 bar (Priority: P1)

**Findings (25/08, real data from `shadow.db`, never assumed)**: this pocket already carries a very mature, actively-tuned calibration (liquidity floor 5500$ validated on 1609 closures across 4 robustness checks -- outlier test, temporal stability, monotonicity; bonding-progress band 70%-98.5%; hard_stop slippage already measured and documented in-code: fills average -33% to -39% vs -20% nominal on thin pools). Largest single-generation sample (`floor3000_20260822`, 1606 closures): **+7.0% raw, +3.29% without top5, 43.5% winrate** -- positive and outlier-resistant, but far below the +25% bar. The generation active TODAY (`reset_20260825`, only 135 closures so far) reads -14.67%/-17.09% without top5 -- too small a sample to conclude anything yet (doctrine: never conclude below a representative sample).

### User Story 2 - Do not duplicate an already-well-calibrated mechanism (Priority: P1)

Given the depth of existing calibration (see User Story 1), this spec's job is NOT to re-invent stop-loss/liquidity-floor tuning already done -- it is to (a) verify the CURRENT generation's health honestly as it accumulates, (b) look for genuinely NEW angles the existing calibration hasn't covered yet (timing filters, cross-checks against an external price source per the project's "a system can never validate its own prices" doctrine), (c) flag a real regression if one is found, never silently retune a parameter someone else is actively mid-experiment on.

### Edge Cases

- What happens when this pocket's owning session (the concurrent one) resumes work mid-spec? -- re-read the real state (`shadow.db`, git log, `ps aux`) before touching any of its constants, never assume the 25/08 "no parallel VPS" confirmation stays true indefinitely.

## Success Criteria

- **SC-001**: PnL average >= +25% on >= 1000 closed positions (outlier-resistant figure), on the CURRENT actively-running generation, not a stale archived one.
- **SC-002**: Any proposed change is verified against a real, sufficiently large sample (never a single day, never under 100 closures for a stop/floor-level claim) and cross-checked against at least one external price source when a price/PnL figure itself is in question.

## Assumptions

- The 25/08 "confirmed no parallel session" state may not hold in a future session -- re-check `ps aux` before resuming work here.
- Real-capital Solana trading pilot (`ARIA_SOLANA_TRADE_PILOT_ENABLED`) stays entirely out of this spec's scope -- shadow/paper only.
