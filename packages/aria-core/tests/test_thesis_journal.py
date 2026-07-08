"""Carnet de bord + suivi de thèse : journal, activité projet, révision."""
from __future__ import annotations

import aria_core.thesis_journal as tj
import pytest
from aria_core.thesis_journal import (
    ActivityVerdict,
    JournalEntry,
    assess_project_activity,
    judge_thesis,
)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "DB_PATH", str(tmp_path / "journal.db"))


# ── Activité du projet (pur) ───────────────────────────────────

def test_recent_commit_is_shipping():
    v = assess_project_activity(github_last_commit_days=3)
    assert v.status == "shipping"


def test_old_everything_is_stagnating():
    v = assess_project_activity(github_last_commit_days=60, social_last_post_days=45)
    assert v.status == "stagnating"


def test_takes_freshest_signal():
    # commit ancien mais post récent -> livre (communique)
    v = assess_project_activity(github_last_commit_days=90, social_last_post_days=2)
    assert v.status == "shipping"


def test_no_signal_is_unknown():
    assert assess_project_activity().status == "unknown"


def test_slowing_between_windows():
    assert assess_project_activity(social_last_post_days=20).status == "slowing"


# ── Verdict de thèse (pur) ─────────────────────────────────────

def test_invalidation_breaks_thesis():
    verdict, _ = judge_thesis(price_vs_entry_pct=-5, invalidation_hit=True,
                              activity=ActivityVerdict("shipping"))
    assert verdict == "invalidated"


def test_stagnation_flags_thesis():
    verdict, _ = judge_thesis(price_vs_entry_pct=10, invalidation_hit=False,
                              activity=ActivityVerdict("stagnating", "rien depuis 40 j"))
    assert verdict == "stagnating"


def test_shipping_and_price_holding_is_delivering():
    verdict, _ = judge_thesis(price_vs_entry_pct=25, invalidation_hit=False,
                              activity=ActivityVerdict("shipping"))
    assert verdict == "delivering"


def test_otherwise_on_track():
    verdict, _ = judge_thesis(price_vs_entry_pct=-10, invalidation_hit=False,
                              activity=ActivityVerdict("slowing"))
    assert verdict == "on_track"


# ── Journal (persistant) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_record_and_list_entry():
    eid = await tj.record_entry(JournalEntry(
        contract="0x" + "a" * 40, symbol="ATLAS", decision="BUY",
        thesis="Vrai builder Base, produit live", facts=["liquidité 120k", "dev aligned"],
        entry_price=0.01, target_price=0.05, invalidation_price=0.008,
    ))
    assert eid > 0
    entries = await tj.list_entries()
    assert entries[0]["symbol"] == "ATLAS"
    assert entries[0]["thesis"].startswith("Vrai builder")
    assert "liquidité 120k" in entries[0]["facts"]


@pytest.mark.asyncio
async def test_checkpoints_track_thesis_over_time():
    c = "0x" + "b" * 40
    await tj.record_entry(JournalEntry(contract=c, symbol="X", decision="BUY", entry_price=1.0))
    await tj.record_checkpoint(c, price=1.2, price_vs_entry_pct=20.0,
                               activity_status="shipping", verdict="delivering", note="commits récents")
    await tj.record_checkpoint(c, price=0.7, price_vs_entry_pct=-30.0,
                               activity_status="stagnating", verdict="stagnating", note="rien depuis 40j")
    checks = await tj.list_checkpoints(c)
    assert len(checks) == 2
    assert checks[0]["verdict"] == "stagnating"  # plus récent d'abord


@pytest.mark.asyncio
async def test_export_txt_is_human_readable():
    c = "0x" + "c" * 40
    await tj.record_entry(JournalEntry(contract=c, symbol="GEM", decision="BUY",
                                       thesis="thèse test", entry_price=2.0))
    await tj.record_checkpoint(c, price=3.0, price_vs_entry_pct=50.0,
                               activity_status="shipping", verdict="delivering", note="actif")
    txt = await tj.export_txt()
    assert "CARNET DE BORD ARIA" in txt
    assert "GEM" in txt and "thèse test" in txt
    assert "delivering" in txt
