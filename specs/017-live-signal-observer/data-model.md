# Phase 1 Data Model: Live Signal Observer

This feature stores almost nothing new. The observation itself is specs/016's `momentum_signal_observation` + `momentum_signal_forward_performance`, written automatically by the `evaluate_momentum_entry` wrapper. This feature reads that row back to build the message.

## New table: `live_signal_notification`

Enforces the per-token notification cooldown across restarts (a blue-green redeploy restarts the process; in-memory state would re-notify every token).

| Column | Type | Notes |
|---|---|---|
| `contract` | TEXT NOT NULL | lower-cased |
| `chain` | TEXT NOT NULL | |
| `observation_id` | INTEGER NOT NULL | the specs/016 row the message was built from (SC-007 traceability) |
| `status` | TEXT NOT NULL | the status that was sent |
| `notified_at` | TEXT NOT NULL | ISO-8601 UTC |

Primary key `(contract, chain)` — upserted on each send; only the latest notification per token matters for the cooldown.

## Derived (never stored) presentation model

Computed at message-build time from the observation row's `onchain_json`/`chart_json`/`social_json`:

```
FamilyPresentation
  family:        "onchain" | "chart" | "social"
  total:         int      # sub-signals defined for the family
  fresh:         int      # available AND not STALE
  stale:         int      # available but older than the source's freshness threshold
  favorable:     int      # among fresh, reading favorable (research.md §8)
  quality:       "HIGH" | "MEDIUM" | "LOW"
  figure:        int | None   # 0-100; None when quality is LOW (FR-008)

SignalStatus = "CONVERGENCE" | "MIXED" | "DIVERGENCE" | "DATA_INCOMPLETE"
```

STALE is derived from the stored `data_timestamp` against a per-source threshold — never a new stored value, so specs/016's schema stays the single model (FR-007).

## Validation rules

- A family at quality LOW never carries a `figure` (FR-008, SC-005).
- `DATA_INCOMPLETE` takes precedence whenever any family is LOW (FR-009).
- No stored or derived value ever combines the three families into one number (FR-009).
- A message is built only from a persisted observation row; if capture failed, no message (spec.md Edge Cases).
