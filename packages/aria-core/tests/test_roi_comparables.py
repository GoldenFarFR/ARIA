"""Voûte 3 — projection ROI par comparables historiques (facts-only, déterministe)."""
from __future__ import annotations

from aria_core.skills.roi_comparables import (
    ComparableScenario,
    project_roi,
    resolve_sector,
)


def test_no_market_cap_is_unavailable():
    for bad in (None, 0, -10):
        res = project_roi(bad, ["ai-agents"])
        assert res.available is False
        assert res.scenarios == []
        assert "indisponible" in res.reason


def test_upside_only_scenarios_and_multiples():
    # Petite capitalisation dans un secteur reconnu → placements à la hausse.
    res = project_roi(1_000_000, ["ai-agents"])
    assert res.available is True
    assert res.sector == "ai-agents"
    assert res.sector_recognized is True
    assert len(res.scenarios) >= 1
    # Tous les scénarios sont à la HAUSSE (ref > actuel, multiple > 1) et triés.
    caps = [s.ref_mcap_usd for s in res.scenarios]
    assert caps == sorted(caps)
    for s in res.scenarios:
        assert isinstance(s, ComparableScenario)
        assert s.ref_mcap_usd > res.current_mcap_usd
        assert s.multiple > 1


def test_multiple_arithmetic_is_exact():
    # ref médian ai-agents = 30M ; à 3M actuel → 10x pile.
    res = project_roi(3_000_000, ["ai-agents"])
    median = next(s for s in res.scenarios if s.ref_mcap_usd == 30_000_000)
    assert median.multiple == 10.0


def test_already_large_cap_has_no_upside_scenarios():
    # Au-dessus du plus gros jalon → aucun placement à la hausse → indisponible.
    res = project_roi(5_000_000_000, ["ai-agents"])
    assert res.available is False
    assert "aucun comparable" in res.reason


def test_absurd_multiple_is_filtered():
    # 1000 USD de cap ferait des multiples > 500x sur tous les jalons → filtrés.
    res = project_roi(1_000, ["ai-agents"])
    assert res.available is False


def test_unknown_sector_falls_back_to_generic():
    res = project_roi(1_000_000, ["quantum-basket-weaving"])
    assert res.sector == "generic"
    assert res.sector_recognized is False
    assert res.available is True


def test_no_categories_uses_generic():
    res = project_roi(1_000_000, None)
    assert res.sector == "generic"
    assert res.sector_recognized is False


def test_resolve_sector_by_alias():
    assert resolve_sector(["decentralized-finance"]) == ("defi", True)
    assert resolve_sector(["Layer 2"]) == ("infra", True)
    assert resolve_sector(["Meme"]) == ("memecoin", True)


def test_disclaimer_always_present_and_no_promise_language():
    res = project_roi(1_000_000, ["defi"])
    assert res.disclaimer
    low = res.disclaimer.lower()
    assert "garantie" in low  # dit explicitement "aucune garantie"
    assert "cible" in low or "prevision" in low


def test_deterministic_same_input_same_output():
    a = project_roi(2_000_000, ["gaming"])
    b = project_roi(2_000_000, ["gaming"])
    assert [(s.label, s.multiple) for s in a.scenarios] == [
        (s.label, s.multiple) for s in b.scenarios
    ]


def test_fdv_basis_is_recorded():
    res = project_roi(1_000_000, ["defi"], basis="fdv")
    assert res.basis == "fdv"
