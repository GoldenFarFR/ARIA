# Phase 1 Data Model: Momentum Signal Observation Layer

Two new SQLite tables, `DATA_DIR`/`aria.db` (via `aria_core.paths.aria_db_path()`), following the append-only-log pattern already established by `dex_score_log.py`/`signal_cascade_convergence.py`. No existing table is modified.

## Entity: `momentum_signal_observation`

One row per candidate evaluation by the momentum pipeline (User Story 1). Append-only — never updated after insert (the decision it records already happened and cannot change).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `contract` | TEXT NOT NULL | lower-cased, same normalization as `dex_score_log`/`momentum_entry.py`'s own `normalize_contract_case` |
| `chain` | TEXT NOT NULL | e.g. `base`, `robinhood`, `solana` |
| `symbol` | TEXT | best-effort, may be null if never resolved before rejection |
| `decision_ts` | TEXT NOT NULL | ISO-8601 UTC, the moment `evaluate_momentum_entry()`'s wrapper captured the return value — the T0 reference instant for every forward-performance delta |
| `decision_action` | TEXT NOT NULL | verbatim from the pipeline's own return value: `BUY` / `HOLD` / a `hold_reason`-style rejection code — never inferred or recomputed by this feature (FR-003) |
| `decision_reason` | TEXT | verbatim `reasons`/`hold_reason` text already produced by the existing decision logic |
| `reference_price_usd` | REAL | price at decision time, if the core pipeline resolved one (`best.price_usd`) — null if the candidate was rejected before any price snapshot existed (see spec.md Edge Cases) |
| `signal_version` | TEXT NOT NULL | identifies the state of the pipeline's signal-computation logic at capture time (FR-004, FR-011) — see "signal_version scheme" below |
| `onchain_json` | TEXT NOT NULL | JSON blob, on-chain signal family block (schema below) |
| `chart_json` | TEXT NOT NULL | JSON blob, chart signal family block (schema below) |
| `social_json` | TEXT NOT NULL | JSON blob, social signal family block (schema below) |

Index: `(contract, chain, decision_ts)` for time-ordered per-token queries; `(signal_version)` for SC-005's cross-version interpretability checks.

### Signal family block schema (shared shape across `onchain_json`/`chart_json`/`social_json`)

Each family is a JSON object whose keys are the family's named sub-signals. Every sub-signal is one of two shapes — this is the mechanism that satisfies FR-005 (never confuse "not available" with "available and neutral"):

```json
{
  "sub_signal_name": {
    "available": true,
    "value": <raw value, family-appropriate type>,
    "data_timestamp": "2026-09-01T12:03:44Z"
  },
  "other_sub_signal_name": {
    "available": false,
    "reason": "not_evaluated_this_gate"
  }
}
```

`reason` (when `available: false`) is one of a fixed vocabulary, not free text, so later analysis can group reliably:
- `not_evaluated_this_gate` — the pipeline's decision returned before this signal was ever computed (e.g. rejected on honeypot before chart signals ran)
- `source_disabled` — the underlying module's own `ARIA_*_ENABLED` gate was off at decision time
- `not_yet_scanned` — an asynchronous source (signal cascade) has no row yet for this contract
- `no_persisted_state` — the source has no passively-readable history at all (currently only `radar_x`, per research.md §4)
- `lookup_failed` — a passive read was attempted and failed (e.g. transient DB error) — best-effort, never blocks the decision

#### `onchain_json` sub-signals

- `composite_score` — value: the `dex_composite_score` 0-100 float, when computed (only when `action == "BUY"` reached that stage, per research.md §2)
- `composite_pillars` — value: the four pillar sub-scores (mint_authority, dev_wallet, smart_money, liquidity_depth) as returned by `compute_dex_composite_score`, when available
- `smart_money_rescue_triggered` — value: boolean, whether the rare parabolic-rescue path (`momentum_entry.py:403-441`) fired for this candidate
- `holder_concentration_top10_pct` — value: float, from the existing hard-gate check, when computed

#### `chart_json` sub-signals

- `golden_pocket_present` — value: boolean, from `entry_signals.detect_entry`
- `rsi_divergence_present` — value: boolean, from the same call
- `risk_reward_ratio` — value: float (`signal.rr`), when the setup was present
- `technical_align_score` — value: int, from `_technical_alignment`
- `rvol_confirmed` — value: boolean + the measured ratio, from `_check_volume_confirmation`
- `market_regime` — value: one of `peur`/`neutre`/`euphorie`, from `market_sentiment.resolve_meta_regime()`

#### `social_json` sub-signals

- `conviction_research_score` — value: float (`potential_score`), when `conviction_research` ran
- `signal_cascade_convergence` — value: array of `{source, signal, accelerating, detail}` from the passive `signal_cascade_convergence` table read (0-4 entries; `available: false, reason: not_yet_scanned` when the query returns zero rows)
- `radar_x_signal` — always `available: false, reason: no_persisted_state` in this feature's baseline (research.md §4) — field present for architectural completeness, not yet populatable

### `signal_version` scheme

A short string identifying the observation-capture logic's own version, e.g. `obs-v1`. This is distinct from any threshold/gate value inside `momentum_entry.py` itself — it only needs to change when the *shape* of what this feature captures changes (a new sub-signal added, a family's schema restructured), not every time an unrelated pipeline threshold is tuned. Bumping it is a deliberate, explicit act by whoever changes the capture code, documented in that change's own commit — never silently incremented.

## Entity: `momentum_signal_forward_performance`

One row per (observation, horizon) pair — five rows created (all `pending`) at observation-insert time, then updated in place as each horizon resolves (this table is the one deliberately-mutable part of the design; see research.md §5 for why this differs from the observation table's append-only posture).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `observation_id` | INTEGER NOT NULL | FK to `momentum_signal_observation.id` |
| `horizon` | TEXT NOT NULL | one of `1m` / `5m` / `15m` / `1h` / `4h` |
| `due_at` | TEXT NOT NULL | `decision_ts + horizon`, precomputed at insert time so the resolver cycle's SQL filter is a cheap indexed comparison |
| `status` | TEXT NOT NULL DEFAULT 'pending' | `pending` / `measured` / `unavailable` |
| `price_usd` | REAL | set only when `status = 'measured'` |
| `pct_change_vs_reference` | REAL | `(price_usd - reference_price_usd) / reference_price_usd * 100`, set only when `status = 'measured'` AND the observation had a non-null `reference_price_usd` |
| `resolved_at` | TEXT | when this row moved out of `pending` |
| `unavailable_reason` | TEXT | set only when `status = 'unavailable'` — e.g. `no_reference_price`, `token_unpriceable`, `dexscreener_lookup_failed` |

Index: `(status, due_at)` — this is the exact index the resolver cycle's funnel-step-1 query (research.md §3) scans first.

## Relationships

```
momentum_signal_observation (1) ──< (5) momentum_signal_forward_performance
```

No relationship to any existing table — this is intentionally a read-only consumer of `momentum_entry.py`/`dex_composite_score.py`/`conviction_research.py`/`signal_cascade_convergence.py`'s already-computed values, never a foreign key into their own tables (those modules' schemas are out of this feature's scope, per FR-009).

## Validation rules (from Functional Requirements)

- `decision_action` MUST be non-null and copied verbatim from the pipeline's real return value (FR-003) — the capture code MUST NOT contain any branch that infers a decision independently.
- Every sub-signal object MUST have either `available: true` with a `value`, or `available: false` with a `reason` from the fixed vocabulary above — no third shape, no implicit default (FR-005).
- `momentum_signal_forward_performance` rows are created eagerly (all 5, all `pending`) at observation-insert time, never created lazily on first resolution attempt — this guarantees SC-003's "resolved or explicitly unavailable, never missing" holds structurally, not by convention.
