# Quickstart: Momentum Signal Observation Layer

Validation guide — proves the feature works end-to-end once implemented. Not a spec, not implementation code; see data-model.md and contracts/ for the authoritative shapes.

## Prerequisites

- `DATA_DIR` configured (`aria_core.bootstrap.configure_data_dir`), same as any other one-off script against the real or a test DB (per this repo's standing "scratch scripts need `configure_data_dir`" lesson).
- `momentum_signal_observation` module implemented per contracts/momentum_signal_observation.md.
- `evaluate_momentum_entry()` wrapped per research.md §1.
- New heartbeat cycle registered for `resolve_due_forward_prices()`.

## Scenario 1 — Every evaluated candidate produces exactly one observation (User Story 1 / SC-001)

```python
import asyncio
from aria_core import bootstrap
bootstrap.configure_data_dir("/path/to/test-data-dir")

from aria_core import momentum_entry, momentum_signal_observation

async def main():
    # A candidate expected to be rejected early (e.g. a known-bad contract)
    await momentum_entry.evaluate_momentum_entry("0x...rejected_candidate", "base")
    # A candidate expected to pass every gate
    await momentum_entry.evaluate_momentum_entry("0x...buy_candidate", "base")

    rows = await momentum_signal_observation.list_recent(limit=2)  # helper for this check only
    assert len(rows) == 2
    assert {r["decision_action"] for r in rows} != set()  # both decisions captured, whatever they were

asyncio.run(main())
```

**Expected outcome**: two rows in `momentum_signal_observation`, one per call, regardless of which one (if either) resulted in BUY — confirms FR-001/SC-001.

## Scenario 2 — Absent signals are explicit, never neutral (User Story 1 / User Story 3 / SC-002)

Evaluate a candidate that fails a very early gate (e.g. honeypot) before any chart/social signal would ever run.

**Expected outcome**: that observation's `chart_json`/`social_json` sub-signals are all `{"available": false, "reason": "not_evaluated_this_gate"}` — never a `0`/`false`/neutral value standing in for "never checked". Confirms FR-005/SC-002.

## Scenario 3 — Forward performance resolves over time, never fabricated (User Story 2 / SC-003)

```python
# immediately after insert
rows = await momentum_signal_observation.forward_performance_for(observation_id)
assert all(r["status"] == "pending" for r in rows)

# simulate time passing past the +1m horizon, then run one resolver cycle
await asyncio.sleep(65)
await momentum_signal_observation.resolve_due_forward_prices()

rows = await momentum_signal_observation.forward_performance_for(observation_id)
one_min = next(r for r in rows if r["horizon"] == "1m")
assert one_min["status"] in ("measured", "unavailable")  # never still "pending", never a guessed value
if one_min["status"] == "measured":
    assert one_min["price_usd"] is not None and one_min["pct_change_vs_reference"] is not None
else:
    assert one_min["unavailable_reason"] is not None
```

**Expected outcome**: no horizon is ever left silently unresolved once due, and no horizon reports a price without a reason it could be trusted (measured) or an explicit reason it couldn't (unavailable). Confirms FR-006/FR-007/SC-003.

## Scenario 4 — Zero behavioral regression on the real decision (SC-004)

Run the existing `momentum_entry.py` test suite (e.g. `tests/test_momentum_entry.py`) unchanged, before and after this feature is wired in. Every existing assertion about BUY/HOLD/reject outcomes MUST still pass byte-for-byte — the wrapper must be provably transparent. This is the acceptance bar for FR-008/FR-009/SC-004, checked as part of `/speckit-implement`'s test phase, not a new test written for this feature specifically (the existing suite already is the regression check).

## Scenario 5 — Observations remain interpretable across a `signal_version` change (SC-005)

After implementation, capture one observation, bump `signal_version` (e.g. add a new sub-signal), capture a second observation, and confirm both remain independently queryable and correctly labeled with their own `signal_version` — no migration required, no silent reinterpretation of the older row under the newer schema's assumptions.
