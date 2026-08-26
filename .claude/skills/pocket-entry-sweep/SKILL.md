---
name: "pocket-entry-sweep"
description: "Run the mandatory pocket_entry_sweep tool before any pocket analysis -- sweeps every _at_entry metric at every decile, reports only candidates surviving outlier/day-stability/monotonicity checks."
argument-hint: "<pocket_name> (e.g. solana_late_bonding, solana_support_bounce_v1)"
metadata:
  author: "aria-ops"
user-invocable: true
disable-model-invocation: false
---

## User Input

```text
$ARGUMENTS
```

## Why this exists

CLAUDE.md (Permanent norms) mandates this tool as the FIRST step of any pocket
analysis -- never eyeball a raw table. Full doctrine and founding incident live
in CLAUDE.md, not duplicated here: reread it there if the rationale is needed.
This skill only makes the mandatory command a one-shot, reliable trigger.

## Steps

1. If `$ARGUMENTS` is empty, ask which pocket to sweep (list candidates by
   grepping `_at_entry`-suffixed columns across the shadow/paper modules if
   the user is unsure).
2. Run:
   ```bash
   python -m aria_core.pocket_entry_sweep $ARGUMENTS
   ```
   from the repo root, with `DATA_DIR` already pointed at the real prod
   database (never a scratch/dev DB -- see `configure_data_dir` norm).
3. Report ONLY the metrics that survive all three checks the tool applies
   (outlier test, day-by-day stability, monotonicity) -- never a raw decile
   dump. Include the sample size (n) and number of distinct days covered.
4. If a candidate metric survives, name it explicitly as a candidate filter
   and ask whether to open a `specs/` chantier for it (per the spec-kit
   router in CLAUDE.md) -- never propose wiring it as a live gate directly.

## Done When

- [ ] `pocket_entry_sweep` actually executed against the real prod DB
- [ ] Result reported with n, distinct-day count, and surviving metrics only
- [ ] No raw table eyeballed or cited instead of the tool's output
