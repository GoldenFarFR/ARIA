import json

import pytest

from src.core.exceptions import ValidationError
from src.ledger.ledger import TruthLedger


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    data_dir = tmp_path / "data" / "ledger"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr("src.ledger.ledger.LEDGER_PATH", data_dir)
    return TruthLedger()


@pytest.mark.unit
def test_add_entry_persists(ledger):
    entry = ledger.add_entry({"action": "test"}, source="pytest")
    assert entry["entry_id"] == 1
    assert ledger.verify_integrity()["status"] == "OK"

    reloaded = TruthLedger()
    assert len(reloaded.get_all()) == 1


@pytest.mark.unit
def test_rejects_non_dict(ledger):
    with pytest.raises(ValidationError):
        ledger.add_entry("not-a-dict")  # type: ignore[arg-type]


@pytest.mark.unit
def test_corrupted_entry_filtered(tmp_path, monkeypatch):
    data_dir = tmp_path / "data" / "ledger"
    data_dir.mkdir(parents=True)
    ledger_file = data_dir / "truth_ledger.json"
    ledger_file.write_text(
        json.dumps([{"entry_id": 1, "hash": "invalid"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr("src.ledger.ledger.LEDGER_PATH", data_dir)

    ledger = TruthLedger()
    assert ledger.get_all() == []
