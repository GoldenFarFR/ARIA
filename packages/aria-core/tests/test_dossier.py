"""Dossier par token — fusion chronologique pure + extraction d'adresse + rendu.

Tout est hors-ligne : la fusion (`build_events`) est pure, et `build_dossier`
est testé en injectant des lignes canned dans chaque store (aucune DB, aucun réseau).
"""
from __future__ import annotations

import pytest

from aria_core import dossier

A = "0x" + "a" * 40
B = "0x" + "b" * 40


# ── Extraction d'adresse depuis un message Telegram ───────────────────────────

def test_extract_bare_contract():
    assert dossier.extract_contract(A) == A


def test_extract_duplicated_paste():
    # Le copier-coller a doublé l'adresse (cas réel opérateur) → une seule adresse.
    assert dossier.extract_contract(A + A) == A


def test_extract_with_small_label():
    assert dossier.extract_contract(f"CA: {A}") == A


def test_extract_sentence_is_ignored():
    # Une vraie phrase qui cite une adresse ne doit PAS déclencher le dossier.
    assert dossier.extract_contract(f"peux-tu analyser {A} pour moi stp ?") is None


def test_extract_two_distinct_addresses_ignored():
    assert dossier.extract_contract(f"{A} vs {B}") is None


def test_extract_no_address():
    assert dossier.extract_contract("salut, ça donne quoi le marché ?") is None


def test_is_contract():
    assert dossier.is_contract(A)
    assert not dossier.is_contract("0xdead")
    assert not dossier.is_contract(A + "ff")


# ── Fusion pure (build_events) ────────────────────────────────────────────────

def test_build_events_merges_and_sorts_recent_first():
    events = dossier.build_events(
        predictions=[{
            "id": 1, "recommandation": "BUY", "potentiel": 7, "status": "closed",
            "created_at": "2026-07-01T10:00:00+00:00", "closed_at": "2026-07-05T10:00:00+00:00",
            "outcome_pct": 12.0,
        }],
        entries=[{"decision": "WATCH", "symbol": "AAA",
                  "created_at": "2026-07-02T09:00:00+00:00", "facts": []}],
        checkpoints=[{"verdict": "on_track", "price_vs_entry_pct": 5.0,
                      "created_at": "2026-07-03T09:00:00+00:00"}],
        paper=[{"symbol": "AAA", "status": "closed", "pnl_pct": -3.0,
                "opened_at": "2026-07-02T12:00:00+00:00",
                "closed_at": "2026-07-04T12:00:00+00:00", "close_reason": "invalidation"}],
    )
    # 1 analyse + 1 résultat + 1 carnet + 1 suivi + 1 achat paper + 1 vente paper = 6
    assert len(events) == 6
    kinds = [e["kind"] for e in events]
    assert kinds.count("analyse") == 1 and kinds.count("analyse_resultat") == 1
    assert "paper_achat" in kinds and "paper_vente" in kinds
    # Tri décroissant : le premier événement est le plus récent (résultat, 07-05).
    times = [e["at"] for e in events]
    assert times == sorted(times, reverse=True)
    assert events[0]["at"] == "2026-07-05T10:00:00+00:00"


def test_build_events_open_prediction_has_no_result_event():
    events = dossier.build_events(predictions=[{
        "id": 2, "recommandation": "AVOID", "status": "open",
        "created_at": "2026-07-01T10:00:00+00:00",
    }])
    assert [e["kind"] for e in events] == ["analyse"]


def test_build_events_undated_sorted_last():
    events = dossier.build_events(
        checkpoints=[{"verdict": "x", "created_at": None},
                     {"verdict": "y", "created_at": "2026-07-01T00:00:00+00:00"}],
    )
    assert events[-1]["data"]["verdict"] == "x"  # sans date → en dernier


# ── build_dossier (async, stores injectés) ────────────────────────────────────

@pytest.mark.asyncio
async def test_build_dossier_invalid_contract():
    d = await dossier.build_dossier("0xnope")
    assert d["valid"] is False and "error" in d


@pytest.mark.asyncio
async def test_build_dossier_fans_out_and_counts(monkeypatch):
    from aria_core import investment_memory, paper_trader, screened_pool, thesis_journal, vc_predictions

    async def _preds(c, limit=50):
        return [{"id": 1, "recommandation": "BUY", "potentiel": 8, "status": "open",
                 "created_at": "2026-07-01T10:00:00+00:00"}]

    async def _entries(c, limit=50):
        return [{"decision": "BUY", "symbol": "ZHC", "created_at": "2026-07-01T11:00:00+00:00",
                 "facts": ["liquidité 120k"]}]

    async def _checks(c, limit=50):
        return []

    async def _theses(c, limit=10):
        return []

    async def _paper(c, limit=100):
        return []

    async def _status(c):
        return "active"

    monkeypatch.setattr(vc_predictions, "list_predictions_for_contract", _preds)
    monkeypatch.setattr(thesis_journal, "list_entries", _entries)
    monkeypatch.setattr(thesis_journal, "list_checkpoints", _checks)
    monkeypatch.setattr(investment_memory, "list_theses_for_token", _theses)
    monkeypatch.setattr(paper_trader, "list_positions_for_contract", _paper)
    monkeypatch.setattr(screened_pool, "get_status", _status)

    d = await dossier.build_dossier(A)
    assert d["valid"] is True
    assert d["symbol"] == "ZHC"
    assert d["screened_status"] == "active"
    assert d["counts"]["analyses"] == 1 and d["counts"]["carnet"] == 1
    assert d["counts"]["evenements"] == 2


# ── Rendu Telegram ────────────────────────────────────────────────────────────

def test_format_empty_dossier_suggests_next_steps():
    out = dossier.format_dossier_telegram(
        {"valid": True, "contract": A, "symbol": None, "screened_status": None,
         "counts": {}, "events": []}
    )
    assert "Aucune analyse" in out
    assert f"/vc {A}" in out and f"/scan {A}" in out


def test_format_populated_dossier_shows_chronology():
    d = {"valid": True, "contract": A, "symbol": "ZHC", "screened_status": "active",
         "counts": {"analyses": 1, "suivis": 0, "paper": 0},
         "events": [{"at": "2026-07-05T10:00:00+00:00", "kind": "analyse",
                     "summary": "Analyse VC : BUY · potentiel 8/10"}]}
    out = dossier.format_dossier_telegram(d)
    assert "Dossier ZHC" in out
    assert "2026-07-05 10:00" in out
    assert "Analyse VC : BUY" in out


def test_format_invalid_dossier():
    out = dossier.format_dossier_telegram({"valid": False, "error": "Adresse invalide."})
    assert out == "Adresse invalide."


# ── Intégration : casse insensible bout-en-bout (stores réels, DB temporaire) ──

@pytest.mark.asyncio
async def test_dossier_matches_across_case(tmp_path, monkeypatch):
    """Un token enregistré en casse mixte est retrouvé par un CA en minuscules.

    Verrouille la correction : `vc_prediction`, `journal_entry`, `thesis_checkpoint`
    et `investment_thesis` comparent désormais l'adresse sans tenir compte de la casse.
    """
    from aria_core import investment_memory, thesis_journal, vc_predictions
    from aria_core.thesis_journal import JournalEntry

    db = str(tmp_path / "aria.db")
    for mod in (vc_predictions, thesis_journal, investment_memory):
        monkeypatch.setattr(mod, "DB_PATH", db)

    mixed = "0x" + "Ab" * 20            # casse mixte (checksum-like)
    lower = mixed.lower()

    await vc_predictions.record_prediction(
        contract=mixed, recommandation="BUY", potentiel=8, risque="modéré",
        taille_pct=2.0, security_score=80, llm_used=True,
    )
    await thesis_journal.record_entry(JournalEntry(
        contract=mixed, symbol="ZHC", decision="BUY", thesis="builder réel", reasoning="",
        facts=["liquidité 120k"],
    ))
    await thesis_journal.record_checkpoint(
        mixed, price=1.2, price_vs_entry_pct=5.0, activity_status="shipping",
        verdict="on_track",
    )

    d = await dossier.build_dossier(lower)          # requête en MINUSCULES
    assert d["valid"] is True
    assert d["counts"]["analyses"] == 1
    assert d["counts"]["carnet"] == 1
    assert d["counts"]["suivis"] == 1
    assert d["symbol"] == "ZHC"
