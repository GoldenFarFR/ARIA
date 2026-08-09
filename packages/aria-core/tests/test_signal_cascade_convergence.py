"""Multi-source signal cascade -- stages 3 (convergence) + 4 (persistent
triage queue). Never a trigger, never blocks a source column's own cycle."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import signal_cascade_convergence as scc

CONTRACT = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_cascade_convergence.db")
    monkeypatch.setattr(scc, "DB_PATH", db_path)
    monkeypatch.setattr(scc, "_table_ready", False)

    async def _no_price(contract, chain):
        return None

    monkeypatch.setattr(scc, "_current_price_usd", _no_price)
    yield


def _mock_price(monkeypatch, price: float | None) -> None:
    async def _fake(contract, chain):
        return price

    monkeypatch.setattr(scc, "_current_price_usd", _fake)


async def _decide_at_price(monkeypatch, contract, chain, decision, reasoning, price):
    _mock_price(monkeypatch, price)
    return await scc.record_triage_decision(contract, chain, decision, reasoning)


async def _age_decision(db_path, contract, chain, days_ago: float) -> None:
    import aiosqlite

    old = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE signal_cascade_triage_queue SET decided_at = ? WHERE contract = ? AND chain = ?",
            (old, contract, chain),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_positive_signal_queues_for_triage():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x", symbol="TP")
    pending = await scc.list_pending_triage()
    assert len(pending) == 1
    assert pending[0]["convergence_count"] == 1
    assert pending[0]["symbol"] == "TP"


@pytest.mark.asyncio
async def test_weak_signal_never_queues():
    await scc.record_source_signal(CONTRACT, "base", "github", "weak", detail="repo x")
    assert await scc.list_pending_triage() == []


@pytest.mark.asyncio
async def test_two_sources_agreeing_raises_convergence_count():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    await scc.record_source_signal(CONTRACT, "base", "farcaster", "positive", detail="cast y")
    pending = await scc.list_pending_triage()
    assert len(pending) == 1
    assert pending[0]["convergence_count"] == 2
    sources = {s["source"] for s in pending[0]["sources"]}
    assert sources == {"github", "farcaster"}


@pytest.mark.asyncio
async def test_already_queued_token_never_duplicated():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x updated")
    pending = await scc.list_pending_triage()
    assert len(pending) == 1  # one row, not two


@pytest.mark.asyncio
async def test_pending_sorted_by_convergence_count_then_oldest_first():
    await scc.record_source_signal("0x" + "1" * 40, "base", "github", "positive", detail="a")
    await scc.record_source_signal("0x" + "2" * 40, "base", "github", "positive", detail="b")
    await scc.record_source_signal("0x" + "2" * 40, "base", "farcaster", "positive", detail="c")
    pending = await scc.list_pending_triage()
    assert pending[0]["contract"] == "0x" + "2" * 40  # 2 sources beats 1
    assert pending[0]["convergence_count"] == 2


@pytest.mark.asyncio
async def test_record_triage_decision_requires_a_reasoning():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    ok = await scc.record_triage_decision(CONTRACT, "base", "validated", "")
    assert ok is False
    pending = await scc.list_pending_triage()
    assert len(pending) == 1  # still pending -- empty reasoning rejected


@pytest.mark.asyncio
async def test_record_triage_decision_rejects_invalid_decision_value():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    ok = await scc.record_triage_decision(CONTRACT, "base", "maybe", "un vrai raisonnement")
    assert ok is False


@pytest.mark.asyncio
async def test_record_triage_decision_removes_from_pending():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    ok = await scc.record_triage_decision(
        CONTRACT, "base", "validated", "substance réelle confirmée, commits techniques réguliers",
    )
    assert ok is True
    assert await scc.list_pending_triage() == []


@pytest.mark.asyncio
async def test_decided_item_never_reopened_by_a_later_source():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    await scc.record_triage_decision(CONTRACT, "base", "rejected", "substance faible, pas convaincant")
    assert await scc.list_pending_triage() == []

    await scc.record_source_signal(CONTRACT, "base", "farcaster", "positive", detail="cast y")
    assert await scc.list_pending_triage() == []  # still not reopened despite a 2nd source agreeing


@pytest.mark.asyncio
async def test_record_triage_decision_on_unknown_contract_returns_false():
    ok = await scc.record_triage_decision(CONTRACT, "base", "rejected", "raisonnement")
    assert ok is False


@pytest.mark.asyncio
async def test_signal_never_raises_even_on_broken_db(monkeypatch):
    monkeypatch.setattr(scc, "DB_PATH", "/nonexistent/dir/shadow.db")
    monkeypatch.setattr(scc, "_table_ready", False)
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="x")  # does not raise


# ---- falsifiability test (validated vs rejected forward returns) ------

@pytest.mark.asyncio
async def test_decision_captures_price_at_decision(monkeypatch):
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="x")
    await _decide_at_price(monkeypatch, CONTRACT, "base", "validated", "raisonnement", 2.0)
    async with aiosqlite.connect(scc.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT price_at_decision FROM signal_cascade_triage_queue WHERE contract = ?", (CONTRACT,)
        )
        (price,) = await cursor.fetchone()
    assert price == 2.0


@pytest.mark.asyncio
async def test_price_lookup_failure_never_blocks_recording_the_decision(monkeypatch):
    _mock_price(monkeypatch, None)
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="x")
    ok = await scc.record_triage_decision(CONTRACT, "base", "validated", "raisonnement")
    assert ok is True  # decision recorded even though price_at_decision stays NULL


@pytest.mark.asyncio
async def test_refresh_forward_prices_fills_24h_only_once_elapsed(monkeypatch):
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="x")
    await _decide_at_price(monkeypatch, CONTRACT, "base", "validated", "raisonnement", 1.0)

    _mock_price(monkeypatch, 1.5)
    updated = await scc.refresh_forward_prices()
    assert updated == 0  # too recent, 24h not elapsed yet

    await _age_decision(scc.DB_PATH, CONTRACT, "base", days_ago=1.1)
    updated = await scc.refresh_forward_prices()
    assert updated == 1


@pytest.mark.asyncio
async def test_refresh_forward_prices_never_touches_an_already_filled_row(monkeypatch):
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="x")
    await _decide_at_price(monkeypatch, CONTRACT, "base", "validated", "raisonnement", 1.0)
    await _age_decision(scc.DB_PATH, CONTRACT, "base", days_ago=1.1)

    _mock_price(monkeypatch, 1.5)
    await scc.refresh_forward_prices()

    _mock_price(monkeypatch, 999.0)  # would corrupt the already-captured value if re-applied
    await scc.refresh_forward_prices()

    async with aiosqlite.connect(scc.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT price_after_24h FROM signal_cascade_triage_queue WHERE contract = ?", (CONTRACT,)
        )
        (price,) = await cursor.fetchone()
    assert price == 1.5


@pytest.mark.asyncio
async def test_falsifiability_report_honest_below_minimum_samples(monkeypatch):
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="x")
    await _decide_at_price(monkeypatch, CONTRACT, "base", "validated", "raisonnement", 1.0)
    await _age_decision(scc.DB_PATH, CONTRACT, "base", days_ago=1.1)
    _mock_price(monkeypatch, 1.5)

    report = await scc.falsifiability_report()
    assert report["window_24h"]["enough_data"] is False
    assert "pas assez de données" in report["window_24h"]["verdict"]


@pytest.mark.asyncio
async def test_falsifiability_report_detects_validated_outperforming(monkeypatch):
    validated_contracts = [f"0x{i:040x}" for i in range(scc._MIN_SAMPLES_PER_SIDE)]
    rejected_contracts = [f"0x{i + 100:040x}" for i in range(scc._MIN_SAMPLES_PER_SIDE)]

    for contract in validated_contracts:
        await scc.record_source_signal(contract, "base", "github", "positive", detail="x")
        await _decide_at_price(monkeypatch, contract, "base", "validated", "raisonnement", 1.0)
        await _age_decision(scc.DB_PATH, contract, "base", days_ago=1.1)

    for contract in rejected_contracts:
        await scc.record_source_signal(contract, "base", "github", "positive", detail="x")
        await _decide_at_price(monkeypatch, contract, "base", "rejected", "raisonnement", 1.0)
        await _age_decision(scc.DB_PATH, contract, "base", days_ago=1.1)

    # Validated tokens doubled, rejected tokens went to zero.
    async def _fake_price(contract, chain):
        return 2.0 if contract in validated_contracts else 0.1

    monkeypatch.setattr(scc, "_current_price_usd", _fake_price)
    report = await scc.falsifiability_report()
    bucket = report["window_24h"]
    assert bucket["enough_data"] is True
    assert bucket["avg_return_validated_pct"] > bucket["avg_return_rejected_pct"]
    assert "critère utile" in bucket["verdict"]


# ---- falsifiability watch cycle (heartbeat wrapper) --------------------

async def _seed_enough_samples_for_24h(monkeypatch) -> None:
    validated = [f"0x{i:040x}" for i in range(scc._MIN_SAMPLES_PER_SIDE)]
    rejected = [f"0x{i + 100:040x}" for i in range(scc._MIN_SAMPLES_PER_SIDE)]
    for contract in validated:
        await scc.record_source_signal(contract, "base", "github", "positive", detail="x")
        await _decide_at_price(monkeypatch, contract, "base", "validated", "raisonnement", 1.0)
        await _age_decision(scc.DB_PATH, contract, "base", days_ago=1.1)
    for contract in rejected:
        await scc.record_source_signal(contract, "base", "github", "positive", detail="x")
        await _decide_at_price(monkeypatch, contract, "base", "rejected", "raisonnement", 1.0)
        await _age_decision(scc.DB_PATH, contract, "base", days_ago=1.1)

    async def _fake_price(contract, chain):
        return 2.0 if contract in validated else 0.1

    monkeypatch.setattr(scc, "_current_price_usd", _fake_price)


@pytest.mark.asyncio
async def test_watch_cycle_logs_once_when_window_crosses_threshold(monkeypatch, caplog):
    await _seed_enough_samples_for_24h(monkeypatch)

    with caplog.at_level("WARNING"):
        await scc.run_falsifiability_watch_cycle()
    assert any("falsifiability [24h]" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_watch_cycle_never_repeats_for_an_already_notified_window(monkeypatch, caplog):
    await _seed_enough_samples_for_24h(monkeypatch)
    await scc.run_falsifiability_watch_cycle()

    caplog.clear()
    with caplog.at_level("WARNING"):
        await scc.run_falsifiability_watch_cycle()
    assert not any("falsifiability [24h]" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_watch_cycle_silent_below_minimum_samples(caplog):
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="x")
    with caplog.at_level("WARNING"):
        await scc.run_falsifiability_watch_cycle()
    assert not any("falsifiability" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_watch_cycle_never_raises_even_on_broken_db(monkeypatch):
    monkeypatch.setattr(scc, "DB_PATH", "/nonexistent/dir/shadow.db")
    monkeypatch.setattr(scc, "_table_ready", False)
    report = await scc.run_falsifiability_watch_cycle()  # does not raise
    assert report["window_24h"]["enough_data"] is False


# ---- impersonation gate (contract_confirmed_on_site, 09/08) ------------

@pytest.mark.asyncio
async def test_pending_item_exposes_confirmed_none_when_no_web_source():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    pending = await scc.list_pending_triage()
    assert pending[0]["contract_confirmed_on_site"] is None


@pytest.mark.asyncio
async def test_pending_item_exposes_confirmed_true_from_web_source():
    await scc.record_source_signal(
        CONTRACT, "base", "web", "positive", detail="site x", contract_confirmed_on_site=True,
    )
    pending = await scc.list_pending_triage()
    assert pending[0]["contract_confirmed_on_site"] is True


@pytest.mark.asyncio
async def test_pending_item_exposes_confirmed_false_from_web_source():
    await scc.record_source_signal(
        CONTRACT, "base", "web", "positive", detail="site x", contract_confirmed_on_site=False,
    )
    pending = await scc.list_pending_triage()
    assert pending[0]["contract_confirmed_on_site"] is False


@pytest.mark.asyncio
async def test_validated_refused_when_contract_not_confirmed():
    await scc.record_source_signal(
        CONTRACT, "base", "web", "positive", detail="site x", contract_confirmed_on_site=False,
    )
    ok = await scc.record_triage_decision(CONTRACT, "base", "validated", "site vérifié mais contrat absent")
    assert ok is False
    pending = await scc.list_pending_triage()
    assert len(pending) == 1  # toujours pending, jamais silencieusement validé


@pytest.mark.asyncio
async def test_validated_allowed_with_explicit_override():
    await scc.record_source_signal(
        CONTRACT, "base", "web", "positive", detail="site x", contract_confirmed_on_site=False,
    )
    ok = await scc.record_triage_decision(
        CONTRACT, "base", "validated", "confirmé manuellement via sous-domaine docs.*",
        override_unconfirmed_contract=True,
    )
    assert ok is True
    assert await scc.list_pending_triage() == []


@pytest.mark.asyncio
async def test_validated_never_blocked_when_no_web_source_at_all():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    await scc.record_source_signal(CONTRACT, "base", "farcaster", "positive", detail="cast y")
    ok = await scc.record_triage_decision(CONTRACT, "base", "validated", "2 sources concordantes, pas de web")
    assert ok is True


@pytest.mark.asyncio
async def test_validated_never_blocked_when_contract_confirmed_true():
    await scc.record_source_signal(
        CONTRACT, "base", "web", "positive", detail="site x", contract_confirmed_on_site=True,
    )
    ok = await scc.record_triage_decision(CONTRACT, "base", "validated", "contrat confirmé sur le site")
    assert ok is True


@pytest.mark.asyncio
async def test_rejected_decision_never_gated_by_confirmation():
    """Le gate ne s'applique qu'à 'validated' -- rejeter un token reste
    toujours possible, peu importe l'état de confirmation."""
    await scc.record_source_signal(
        CONTRACT, "base", "web", "positive", detail="site x", contract_confirmed_on_site=False,
    )
    ok = await scc.record_triage_decision(CONTRACT, "base", "rejected", "substance faible de toute façon")
    assert ok is True
