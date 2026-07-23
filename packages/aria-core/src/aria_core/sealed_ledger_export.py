"""Sealed Ledger export to a public JSONL file (19/07, #214) -- see
``sealed_ledger.py`` for the full design. Module kept SEPARATE and
deliberately free of any git/subprocess call: this file ONLY does file I/O +
the fail-fast chain-continuity guard, which makes it testable without
mocking git. The actual commit+push is a thin script
(``scripts/sealed_ledger_seed_demo.py`` for the v0 proof) that calls
``export_snapshot_to_jsonl`` then does the ``git add``/``commit``/``push``
itself.

Fail-fast guard (spec locked in the ARIA/operator conversation of 19/07): if
the JSONL file already exists and contains at least one line, read the hash
of its LAST line and compare it against the ``prev_hash`` of the first event
about to be appended. A mismatch means the remote file has diverged from
what the exporter believed -- write NOTHING, raise loudly. The ledger's
integrity rests on this continuity check on every export, not on GitHub
branch protection (explicitly settled in the design conversation)."""
from __future__ import annotations

import json
from pathlib import Path

from aria_core.sealed_ledger import GENESIS_HASH


class ExportIntegrityError(RuntimeError):
    """The existing JSONL file doesn't chain with the new events -- never
    caught to write anyway, this is exactly the scenario this guard exists
    to block."""


def read_jsonl_events(jsonl_path: Path) -> list[dict]:
    """Re-reads an existing export as-is -- used both by the internal
    fail-fast guard AND by independent third-party re-verification (which
    must be able to re-read an export WITHOUT touching the local database)."""
    if not jsonl_path.exists():
        return []
    events = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def export_snapshot_to_jsonl(*, jsonl_path: Path, new_events: list[dict]) -> dict:
    """Appends ``new_events`` (sorted by ``sequence`` by this function, never
    assumed already sorted) to the end of the JSONL file, one canonical JSON
    line per event. Checks chain continuity before writing anything.

    Returns {"appended": N, "tail_hash": "<hash of the last event written>"}.
    Raises ``ExportIntegrityError`` without writing anything if continuity
    is broken. Makes NO git call -- that's the caller's responsibility."""
    if not new_events:
        return {"appended": 0, "tail_hash": None}

    ordered = sorted(new_events, key=lambda e: e["sequence"])

    existing = read_jsonl_events(jsonl_path)
    expected_prev_hash = existing[-1]["hash"] if existing else GENESIS_HASH

    if ordered[0]["prev_hash"] != expected_prev_hash:
        raise ExportIntegrityError(
            f"Continuity break: the first event to export "
            f"(event_id={ordered[0]['event_id']}) has prev_hash={ordered[0]['prev_hash']!r}, "
            f"but file {jsonl_path} ends on hash={expected_prev_hash!r}. "
            f"Nothing was written."
        )

    # Internal continuity of the batch itself, before writing anything.
    for i in range(1, len(ordered)):
        if ordered[i]["prev_hash"] != ordered[i - 1]["hash"]:
            raise ExportIntegrityError(
                f"Continuity break WITHIN the batch to export, at index {i} "
                f"(event_id={ordered[i]['event_id']}). Nothing was written."
            )

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as fh:
        for ev in ordered:
            fh.write(json.dumps(ev, sort_keys=True, separators=(",", ":")))
            fh.write("\n")

    return {"appended": len(ordered), "tail_hash": ordered[-1]["hash"]}
