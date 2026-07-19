"""Export JSONL du Sealed Ledger (19/07, #214) -- garde fail-fast de continuité de
chaîne. Aucun appel git ici -- voir sealed_ledger_export.py."""
from __future__ import annotations

import pytest

from aria_core import sealed_ledger as sl
from aria_core import sealed_ledger_export as sle


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "DB_PATH", str(tmp_path / "sealed_ledger_test.db"))
    yield


def _event_dict(event_id, trade_id, sequence, prev_hash, payload=None):
    ev = {
        "event_id": event_id, "trade_id": trade_id, "event_type": "ENTRY_DECISION",
        "sequence": sequence, "timestamp_utc": "2026-07-19T00:00:00+00:00",
        "prev_hash": prev_hash, "payload": payload or {"x": sequence},
    }
    ev["hash"] = sl._compute_event_hash(
        event_id=ev["event_id"], trade_id=ev["trade_id"], event_type=ev["event_type"],
        sequence=ev["sequence"], timestamp_utc=ev["timestamp_utc"],
        prev_hash=ev["prev_hash"], payload=ev["payload"],
    )
    return ev


def test_export_to_fresh_file(tmp_path):
    jsonl_path = tmp_path / "trades.jsonl"
    ev1 = _event_dict("e1", "t1", 1, sl.GENESIS_HASH)

    result = sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[ev1])

    assert result == {"appended": 1, "tail_hash": ev1["hash"]}
    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_export_appends_to_existing_file_when_chain_continues(tmp_path):
    jsonl_path = tmp_path / "trades.jsonl"
    ev1 = _event_dict("e1", "t1", 1, sl.GENESIS_HASH)
    sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[ev1])

    ev2 = _event_dict("e2", "t1", 2, ev1["hash"])
    result = sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[ev2])

    assert result["appended"] == 1
    assert result["tail_hash"] == ev2["hash"]
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_export_rejects_when_continuity_broken_against_existing_file(tmp_path):
    jsonl_path = tmp_path / "trades.jsonl"
    ev1 = _event_dict("e1", "t1", 1, sl.GENESIS_HASH)
    sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[ev1])

    # ev_bad ne référence pas ev1.hash comme prev_hash -- divergence.
    ev_bad = _event_dict("e2", "t1", 2, "f" * 64)

    with pytest.raises(sle.ExportIntegrityError):
        sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[ev_bad])

    # Rien n'a été écrit -- le fichier garde exactement son contenu d'avant.
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_export_rejects_when_batch_itself_is_internally_broken(tmp_path):
    jsonl_path = tmp_path / "trades.jsonl"
    ev1 = _event_dict("e1", "t1", 1, sl.GENESIS_HASH)
    # ev2 ne chaîne pas correctement sur ev1 (prev_hash faux) -- même lot.
    ev2 = _event_dict("e2", "t1", 2, "bad" * 21 + "x")

    with pytest.raises(sle.ExportIntegrityError):
        sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[ev1, ev2])

    assert not jsonl_path.exists()


def test_export_sorts_events_by_sequence_before_writing(tmp_path):
    jsonl_path = tmp_path / "trades.jsonl"
    ev1 = _event_dict("e1", "t1", 1, sl.GENESIS_HASH)
    ev2 = _event_dict("e2", "t1", 2, ev1["hash"])

    # Passés dans le désordre -- la fonction doit les trier avant d'écrire/vérifier.
    result = sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[ev2, ev1])

    assert result["appended"] == 2
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert '"e1"' in lines[0]
    assert '"e2"' in lines[1]


def test_export_empty_events_is_a_noop(tmp_path):
    jsonl_path = tmp_path / "trades.jsonl"
    result = sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[])
    assert result == {"appended": 0, "tail_hash": None}
    assert not jsonl_path.exists()


def test_read_jsonl_events_missing_file_returns_empty_list(tmp_path):
    assert sle.read_jsonl_events(tmp_path / "does_not_exist.jsonl") == []


def test_read_jsonl_events_roundtrip(tmp_path):
    jsonl_path = tmp_path / "trades.jsonl"
    ev1 = _event_dict("e1", "t1", 1, sl.GENESIS_HASH)
    ev2 = _event_dict("e2", "t1", 2, ev1["hash"])
    sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[ev1, ev2])

    read_back = sle.read_jsonl_events(jsonl_path)
    assert len(read_back) == 2
    assert read_back[0]["event_id"] == "e1"
    assert read_back[1]["event_id"] == "e2"


def test_exported_and_reread_chain_passes_independent_verification(tmp_path):
    """Le test qui compte vraiment : écrire via le vrai module sealed_ledger, exporter,
    puis relire le fichier fraîchement écrit et le faire passer par verify_chain() --
    sans jamais retoucher à la base SQLite. C'est exactement le chemin qu'un tiers
    emprunterait pour re-vérifier le registre depuis GitHub."""
    jsonl_path = tmp_path / "trades.jsonl"
    ev1 = _event_dict("e1", "t1", 1, sl.GENESIS_HASH, {"decision_price_usd": 1.0})
    ev2 = _event_dict("e2", "t1", 2, ev1["hash"], {"execution_price_usd": 1.0})
    sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[ev1, ev2])

    read_back = sle.read_jsonl_events(jsonl_path)
    ok, reason = sl.verify_chain(read_back)
    assert ok is True
    assert reason is None


def test_exported_tampered_file_fails_independent_verification(tmp_path):
    jsonl_path = tmp_path / "trades.jsonl"
    ev1 = _event_dict("e1", "t1", 1, sl.GENESIS_HASH, {"decision_price_usd": 1.0})
    sle.export_snapshot_to_jsonl(jsonl_path=jsonl_path, new_events=[ev1])

    # Simule une altération manuelle du fichier après export.
    tampered = jsonl_path.read_text(encoding="utf-8").replace(
        '"decision_price_usd":1.0', '"decision_price_usd":999.0'
    )
    jsonl_path.write_text(tampered, encoding="utf-8")

    read_back = sle.read_jsonl_events(jsonl_path)
    ok, reason = sl.verify_chain(read_back)
    assert ok is False
