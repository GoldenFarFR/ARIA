"""Carnet d'auto-amélioration — mémoire des upgrades candidats (DB isolée)."""
from __future__ import annotations

import pytest

from aria_core import improvement_ledger as il


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(il, "DB_PATH", str(tmp_path / "ledger_test.db"))
    yield


@pytest.mark.asyncio
async def test_record_and_get():
    cid = await il.record_candidate(
        title="Radar X", description="écoute sociale filtrée on-chain",
        category="tool", source="idée opérateur", benefit="meilleur sourcing",
        seam="services/x_social.py -> include_social",
    )
    row = await il.get_candidate(cid)
    assert row["title"] == "Radar X"
    assert row["status"] == "proposed"
    assert row["category"] == "tool"
    assert row["seam"].startswith("services/x_social")


@pytest.mark.asyncio
async def test_unknown_category_defaults_to_idea():
    cid = await il.record_candidate(title="truc", category="n'importe quoi")
    assert (await il.get_candidate(cid))["category"] == "idea"


@pytest.mark.asyncio
async def test_dedup_same_title_not_reinserted():
    a = await il.record_candidate(title="Fact-check ArAIstotle")
    b = await il.record_candidate(title="fact-check araistotle")  # casse différente
    assert a == b  # même candidat, pas de doublon


@pytest.mark.asyncio
async def test_lifecycle_proposed_to_grafted_requires_evidence():
    cid = await il.record_candidate(title="OHLCV CoinAPI")
    # testing OK
    assert (await il.update_candidate(cid, status="testing"))["status"] == "testing"
    # grafted SANS preuve => refusé
    assert await il.update_candidate(cid, status="grafted") is None
    # grafted AVEC preuve => accepté
    row = await il.update_candidate(
        cid, status="grafted", evidence="calibration +6 pts sur 40 verdicts"
    )
    assert row["status"] == "grafted"
    assert "calibration" in row["evidence"]


@pytest.mark.asyncio
async def test_invalid_status_rejected():
    cid = await il.record_candidate(title="x")
    assert await il.update_candidate(cid, status="magique") is None


@pytest.mark.asyncio
async def test_rejected_title_can_be_reproposed():
    a = await il.record_candidate(title="idée moyenne")
    await il.update_candidate(a, status="rejected")
    b = await il.record_candidate(title="idée moyenne")  # rejetée => ré-insérable
    assert b != a


@pytest.mark.asyncio
async def test_ingest_seeds_is_idempotent():
    n1 = await il.ingest_seeds()  # charge le vrai fichier de graines
    assert n1 > 0
    proposed = await il.list_candidates(status="proposed", limit=100)
    assert len(proposed) == n1
    # 2e appel : dédoublonnage par titre -> aucun doublon créé.
    await il.ingest_seeds()
    again = await il.list_candidates(status="proposed", limit=100)
    assert len(again) == n1


@pytest.mark.asyncio
async def test_ingest_missing_file_is_graceful(tmp_path):
    assert await il.ingest_seeds(tmp_path / "nope.yaml") == 0


@pytest.mark.asyncio
async def test_list_and_count_by_status():
    c1 = await il.record_candidate(title="A")
    c2 = await il.record_candidate(title="B")
    await il.update_candidate(c2, status="testing")
    proposed = await il.list_candidates(status="proposed")
    assert {r["id"] for r in proposed} == {c1}
    counts = await il.count_by_status()
    assert counts["proposed"] == 1 and counts["testing"] == 1
    assert counts["grafted"] == 0 and counts["rejected"] == 0
