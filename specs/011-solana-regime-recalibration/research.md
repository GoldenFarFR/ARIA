# Research: Recalibrate the Solana late-bonding shadow pocket's regime gate

## Decision 1: New `REGIME_MIN_MEDIAN_PEAK_PCT` value = 30.0 (down from 40.0)

**Method**: `regime_state()`'s exact formula was reproduced independently in this session
(not copied from the earlier same-day HANDOFF figures) directly against
`solana_regime_candidates_log`: for every candidate in chronological order (`decided_at` ASC),
compute `(peak_price / entry_price - 1) * 100`, then take the rolling median of the last
`REGIME_WINDOW=30` values (matching `regime_median_peak()`'s own sort-and-median logic
exactly), and measure what fraction of those rolling medians would have cleared each
candidate threshold.

**Population**: 4631 rows, all dated `2026-08-25T14:39:29` through `2026-08-26T14:14:08` (the
entire table — it was reset that day, per `solana_regime_candidates_log_archive_reset_20260825`
existing alongside it). 4602 of those rows have a full 30-sample window behind them. Two
distinct days covered (25/08, 26/08), consistent with the project's day-count coverage
doctrine.

**Real, recomputed open-time table** (this session, not the earlier same-day estimate —
the population grew from ~2980 to 4631 rows between the two measurements, so a modest drift
between the two tables is expected and is itself evidence the underlying data keeps
accumulating, not a discrepancy to explain away):

| Threshold | Open-time (this session, n=4602 medians) |
|---|---|
| 10.0% | 83.9% |
| 15.0% | 62.5% |
| 20.0% | 37.6% |
| 25.0% | 19.8% |
| 30.0% | 11.5% |
| 35.0% | 6.4% |
| 40.0% (current) | 4.0% |

Global candidate peak distribution: median 16.5%, mean 32.06% (the median/mean gap confirms a
heavy right tail — a handful of large winners pull the mean up — which is exactly why the
gate's own logic already uses a MEDIAN, not a mean: it is outlier-robust by construction).

**Why NOT re-derive the capture-gap figure per threshold**: the -11.74%/+16.23% figure that
justified every tightening since 23/08 was measured on 44 REAL closures under the pocket's
exit mechanics (`TRAILING_STOP_PCT`, liquidity-collapse exit) — it describes a structural
property of HOW POSITIONS EXIT, not a function of the regime gate's entry threshold. It is
not something a threshold choice can "avoid" by picking a different number; it is a fixed cost
the pocket pays on every position regardless of when it entered. The regime gate's role is to
only let a position open when the SURROUNDING MARKET is hot enough that, even after paying
that fixed capture-gap cost, the average result is still expected to land positive. Attempting
to re-measure this figure specifically "at threshold=25%" or "at threshold=30%" is not
possible today: only 12 real closures exist in the current epoch (`solana_late_bonding_shadow_log`,
post-2026-08-25 reset), far below even the project's own provisional n≥100 bar — any per-threshold
capture figure computed on 12 rows split across multiple candidate thresholds would be noise,
not evidence. This limit is stated explicitly rather than papered over with a false-precision
number (per "a system's own data can never validate its own verdicts" and "read every row before
concluding" — an n this small fails the outlier-removal/day-count checks before the calculation
even starts).

**Rationale for 30.0 over 25.0** (the two candidates spec.md bounds the choice between):

- Safety margin from the known-dangerous 20% bar: 30.0 sits 10 points above it; 25.0 sits only
  5 points above it. Since the per-threshold capture-gap cannot be re-verified today (see
  above), a wider margin from the one value known empirically to produce a negative average
  result is the more conservative choice — consistent with the Doctrine d'Ingestion's own
  framing (a conservative hypothesis over a promising-but-unverified one).
- Open-time gain is already large at 30.0: 11.5% vs. the current 4.0% is a ~2.9x increase in
  gate-open time — enough to meaningfully accelerate closure accumulation (this spec's actual
  goal), without jumping all the way to 25.0's 19.8% (~5x), which would erase most of the
  safety margin gained by every tightening step taken since 23/08 in one move.
- Consistent with this pocket's own recalibration history: every change recorded in the code's
  comments (20→50→30→25→40) has been an incremental step, never a jump straight back to the
  most permissive previously-tried value. 30.0 continues that pattern — a measured step down
  from 40.0, not a full reversal to 25.0 (the value in place immediately before the 24/08 raise)
  or 20.0 (the value that caused the original problem).
- 25.0 remains a legitimate next step if 30.0's real closures (once n≥100 accumulate) show the
  capture-gap has NOT reopened and more volume is still needed — this is exactly what the
  recalibration protocol (Decision 2 below) exists for, rather than trying to guess the final
  answer in one shot.

**Alternatives considered**:
- **25.0**: rejected as the first move (see above) — banked as the natural next step in the
  recalibration protocol if 30.0 proves safe on real data.
- **35.0**: rejected — only 6.4% open-time (a ~1.6x gain over the current 4.0%), unlikely to
  meaningfully accelerate closure accumulation within a reasonable observation window.
- **20.0 or below**: explicitly excluded per spec.md FR-002 — this is the exact value with a
  measured, real, negative capture-gap outcome.
- **Leave at 40.0, address the volume problem some other way (e.g., widen `REGIME_WINDOW`,
  change the candidate pool)**: rejected as out of this spec's scope (spec.md bounds the change
  to the threshold itself); banked as a possible future lead if 30.0 alone proves insufficient.

## Decision 2: Recalibration protocol (mirrors specs/010's Decision 4 / User Story 3)

Once the new threshold (30.0) accumulates closures in `solana_late_bonding_shadow_log`:

- **n≥100 (provisional gate)**: run a `pocket_entry_sweep`-style pass — outlier removal
  (retest without top-2, top-5 best trades), day-count coverage check, and specifically
  re-measure the capture-gap (mean real peak vs. mean net captured) under the new threshold's
  own population. If the gap has reopened to something resembling the pre-24/08 defect, this
  is a signal to tighten back toward 35-40%, not a silent continuation.
- **n≥1000 (validated, spec-closure gate)**: per spec.md SC-004, this spec closes only once the
  average realized return across ≥1000 same-epoch closed trades reaches +25% minimum. Same
  epoch-reset discipline as specs/010: deploying this recalibration starts a new epoch (see
  tasks.md's epoch-archive step), so the 1000-trade count restarts from zero at deployment,
  never blended with pre-recalibration closures.
- **If the sample stalls well below either gate for an extended period**: state so explicitly
  (per the Doctrine d'Ingestion's "never abandon for lack of data, but never fabricate
  precision either") rather than silently drawing a conclusion from too few trades — this
  mirrors specs/010's own Closure section language exactly.

## Decision 3: No change to exit mechanics, candidate pool, or observation window

Confirmed in scope review: `TRAILING_STOP_PCT`, the liquidity-collapse exit, `REGIME_WINDOW`
(30), and the candidate discovery/curve-tracker mechanism are all unaffected by this change —
this recalibration touches exactly one constant. This keeps the change attributable: if closure
volume or quality shifts after deployment, it can only be due to the threshold move, not a
confound from some other simultaneous change (same discipline as the project's "never stack a
second change on the same mechanism being measured" rule).
