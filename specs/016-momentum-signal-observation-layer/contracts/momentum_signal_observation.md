# Contract: `momentum_signal_observation` module

This is an internal Python module contract (no HTTP/external API surface — `aria_core` is a library consumed in-process by `paper_trader.py`/shadow pockets/`heartbeat.py`). The contract below is what `/speckit-tasks` and `/speckit-implement` build against.

## Module: `aria_core.momentum_signal_observation`

### `async def capture_observation(contract: str, chain: str, core_result: dict | str | None, *, extra_context: dict | None = None) -> None`

Called exactly once, from the `evaluate_momentum_entry()` wrapper described in research.md §1, immediately after `_evaluate_momentum_entry_core(...)` returns and before the wrapper's own `return`.

**Preconditions**: `core_result` is whatever `_evaluate_momentum_entry_core` actually returned — a decision dict, a bare hold-reason string (the `hard_gate_hold` early-exit shape), or `None` (the `best is None` edge case). This function MUST NOT be given a chance to alter what the wrapper returns to its own caller.

**Behavior**:
1. Extract `decision_action`/`decision_reason`/`reference_price_usd` from `core_result`'s actual shape (never inferred).
2. Build the three family JSON blocks (`onchain_json`/`chart_json`/`social_json`) per data-model.md's schema, reading only keys `core_result` (or `extra_context`, for values the wrapper has in scope but that never made it into the return dict, e.g. intermediate `align_score` on an early rejection) actually contains — every absent key becomes `available: false` with the matching reason code.
3. Passively read `signal_cascade_convergence` (data-model.md's read path) — a single indexed `SELECT`, no scan triggered.
4. Insert one `momentum_signal_observation` row and five `momentum_signal_forward_performance` rows (all `pending`).

**Postconditions**: Best-effort. Any exception is caught and logged, never propagated — same posture as the existing `narrative_signal_shadow.record_evaluation` call at the same site (research.md §1). Never raises into the caller.

**MUST NOT**: read or write to any table owned by `momentum_entry.py`/`dex_composite_score.py`/`conviction_research.py`/`signal_cascade_*.py`/`radar_x.py` other than the one read-only `SELECT` on `signal_cascade_convergence` named above. MUST NOT call any network client not already invoked by the core decision itself (no new live scans).

### `async def resolve_due_forward_prices() -> int`

Called by a new heartbeat cycle entry (research.md §3), independent cadence (~60s), never from the decision path.

**Behavior**:
1. `SELECT` from `momentum_signal_forward_performance` joined to `momentum_signal_observation` where `status = 'pending' AND due_at <= now`.
2. Deduplicate the result set by `(contract, chain)`.
3. For each unique token, call `dexscreener.fetch_token_pairs(contract, chain)` once (reusing the existing throttled client — no new rate limit to calibrate).
4. For every due row of that token: if a price resolved, `UPDATE ... SET status='measured', price_usd=?, pct_change_vs_reference=?, resolved_at=?`; if not, `UPDATE ... SET status='unavailable', unavailable_reason=?, resolved_at=?` — a row's `reference_price_usd` being null (observation had none) always resolves to `unavailable, reason='no_reference_price'` without even attempting the network call.

**Returns**: count of rows resolved this cycle (for heartbeat logging, same convention as `signal_cascade_convergence.refresh_forward_prices() -> int`).

**MUST NOT**: retry a row already `measured`/`unavailable` (append-once-resolved, per data-model.md). MUST NOT block on a single token's lookup failure — one failure marks that token's due rows `unavailable` and the cycle continues to the next token.

## Wrapper contract: `momentum_entry.evaluate_momentum_entry`

Existing public signature is preserved exactly (no caller in `paper_trader.py`/shadow pockets changes a single line). Internally:

```python
async def _evaluate_momentum_entry_core(...) -> ...:
    # exact current body of today's evaluate_momentum_entry, unchanged

async def evaluate_momentum_entry(*args, **kwargs):
    result = await _evaluate_momentum_entry_core(*args, **kwargs)
    try:
        await momentum_signal_observation.capture_observation(..., core_result=result)
    except Exception:
        pass  # best-effort, never blocks the real decision
    return result
```

This is the only change to `momentum_entry.py` this feature makes — a rename plus a thin wrapper. No line inside the renamed core function changes.
