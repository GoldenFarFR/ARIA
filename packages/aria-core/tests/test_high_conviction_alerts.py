"""Alertes proactives haute-conviction — signal de tri, jamais un ordre d'achat, hors-ligne,
tout injecté. Vérifie : gating, seuil de conviction, un contrat alerté une seule fois,
respect du kill-switch."""
from __future__ import annotations

import pytest

from aria_core.skills import high_conviction_alerts as hca
from aria_core.skills.candidate_ranking import RankedCandidate

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40

SAFE_HIGH = RankedCandidate(
    contract=A, symbol="ZHC", rank_score=88.5, security_score=90,
    liquidity_usd=250_000.0, top_holder_pct=6.0, verdict="SAFE",
)
SAFE_LOW = RankedCandidate(
    contract=B, symbol="LOW", rank_score=55.0, security_score=60,
    liquidity_usd=40_000.0, top_holder_pct=18.0, verdict="SAFE",
)
CAUTION_HIGH = RankedCandidate(
    contract=C, symbol="CTN", rank_score=92.0, security_score=70,
    liquidity_usd=300_000.0, top_holder_pct=10.0, verdict="CAUTION",
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(hca, "DB_PATH", str(tmp_path / "alerts_test.db"))
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    yield


def test_disabled_by_default():
    assert hca.high_conviction_alerts_enabled() is False


def test_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("ARIA_HIGH_CONVICTION_ALERTS_ENABLED", "1")
    assert hca.high_conviction_alerts_enabled() is True


def test_is_high_conviction_requires_safe_and_score():
    assert hca._is_high_conviction(SAFE_HIGH) is True
    assert hca._is_high_conviction(SAFE_LOW) is False
    assert hca._is_high_conviction(CAUTION_HIGH) is False  # score haut mais pas SAFE


def test_format_alert_points_to_vc_not_an_order():
    text = hca.format_alert(SAFE_HIGH)
    assert "ZHC" in text and "88" in text and A in text
    assert "/vc" in text
    assert "pas un ordre d'achat" in text


@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled():
    result = await hca.run_high_conviction_alert_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_paused(monkeypatch):
    monkeypatch.setenv("ARIA_HIGH_CONVICTION_ALERTS_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    result = await hca.run_high_conviction_alert_cycle()
    assert result == {"outcome": "skipped_paused"}


@pytest.mark.asyncio
async def test_cycle_nothing_new_when_no_high_conviction(monkeypatch):
    monkeypatch.setenv("ARIA_HIGH_CONVICTION_ALERTS_ENABLED", "1")
    result = await hca.run_high_conviction_alert_cycle(candidates=[SAFE_LOW, CAUTION_HIGH])
    assert result == {"outcome": "nothing_new"}


@pytest.mark.asyncio
async def test_cycle_alerts_once_for_new_high_conviction_candidate(monkeypatch):
    monkeypatch.setenv("ARIA_HIGH_CONVICTION_ALERTS_ENABLED", "1")
    notified = []

    async def notifier(text):
        notified.append(text)

    result = await hca.run_high_conviction_alert_cycle(
        candidates=[SAFE_LOW, SAFE_HIGH], notifier=notifier,
    )

    assert result["outcome"] == "ok"
    assert result["contract"] == A
    assert len(notified) == 1
    assert "ZHC" in notified[0]


@pytest.mark.asyncio
async def test_cycle_never_alerts_same_contract_twice(monkeypatch):
    monkeypatch.setenv("ARIA_HIGH_CONVICTION_ALERTS_ENABLED", "1")
    notified = []

    async def notifier(text):
        notified.append(text)

    first = await hca.run_high_conviction_alert_cycle(candidates=[SAFE_HIGH], notifier=notifier)
    assert first["outcome"] == "ok"

    second = await hca.run_high_conviction_alert_cycle(candidates=[SAFE_HIGH], notifier=notifier)
    assert second == {"outcome": "nothing_new"}
    assert len(notified) == 1


@pytest.mark.asyncio
async def test_cycle_error_on_scan_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv("ARIA_HIGH_CONVICTION_ALERTS_ENABLED", "1")

    async def broken_top_candidates(n):
        raise RuntimeError("scan indisponible")

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", broken_top_candidates,
    )

    result = await hca.run_high_conviction_alert_cycle()
    assert result["outcome"] == "error"
    assert "scan indisponible" in result["error"]
