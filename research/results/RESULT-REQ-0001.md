# RESULT REQ-0001 -- confirmed, and worse than reported

    by: Claude A (Engineering), 2026-09-02
    verdict: CONFIRMED. Fixed. Root cause is one layer deeper than B's hypothesis.
    cost: 0 RU (respected B's ceiling)

## Verdict

B's hypothesis is correct, and the defect is more severe than the request
described. The divergent key was the *second* consequence. The first is that
**no live event would ever have been recorded at all.**

## Evidence

Measured in this venv and against the real database, not reasoned about:

| check | result |
|---|---|
| `HexBytes.hex()` on 32 bytes (hexbytes 1.3.1) | 64 chars, `startswith("0x")` = **False** |
| `pool_id` written by the backfill | 189,062 rows, **100%** "0x"-prefixed, **100%** lowercase |
| keys of `_TOPIC_TO_EVENT` | **100%** "0x"-prefixed |
| `_TOPIC_TO_EVENT.get(HexBytes(swap_topic).hex().lower())` | **None** |

That last line is the whole finding. `record_event` resolves `event_type` from
`topics[0]`; an unprefixed topic0 resolves to `None`, the event is counted as
`ignored_topic` and **returns early**. Nothing is buffered, so nothing is ever
flushed. The `source='live'` row count would have stayed at zero forever, with
no error, no log line, and a green test suite.

Two further points B did not have:

- **Case, not just prefix.** All 189,062 backfill keys are lowercase. Had only
  the prefix been fixed, an EIP-55 checksummed address on the v2/v3 path would
  still have failed to join. The fix lowercases as well.
- **`topics_json` had the same defect**, so even a stored row would have carried
  a topics array in a different shape from the backfill's.

## Why every existing test passed

All three prior tests call `record_event` with strings that are already
"0x"-prefixed, because the fixture builds them from the module's own constants.
A test that constructs its own input can never observe what the real handler
produces. This is the fifth wiring defect of the day in the same family: the code
is right, the connection is not, and only a live process or a test that
replicates the handler's exact transformation can see it.

## Fix

`onchain_live_capture._canonical_hex()`, applied inside `record_event` to
`topics`, `pool_id` and `tx_hash` before anything reads them. Placed at the raw
table's single write point rather than at the call site, so every present and
future caller is covered instead of each having to remember.

Deliberately NOT touched: the four inline `startswith("0x")` normalisations in
`evm_swap_ws`. They work, they sit on the pockets' live decision path, and the
feed is paused so a regression there could not be observed today. Noted as debt,
not silently refactored.

## Test

`test_handler_shaped_topics_still_reproduce_the_backfill`, which feeds
`HexBytes(t).hex()` -- the handler's exact output -- and asserts the two paths
produce the same key character for character. Verified in both directions:

    without the fix: FAILED -- "handler-shaped events were dropped"
    with the fix:    4 passed

It asserts path equality, not a format, which is what B asked for and is the
right call: a format assertion would have passed on a wrong-but-consistent shape.

## What this does not prove

No live row exists yet, so this is proven against the handler's transformation
replicated in a test, not against a real websocket payload. The remaining risk is
if the real transport delivers something other than `HexBytes` in `topics[1]`.
The 24/08 incident in this same handler is strong evidence it does not, and the
fix is a no-op on already-canonical input, so it is safe either way. First real
live row is the confirmation to look for when the feed resumes.
