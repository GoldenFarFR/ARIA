"""File d'attente de scan wallet en arrière-plan (#157 suite, 15/07) --
`/walletqueue` injecte, `wallet_scan_queue_cycle` fait avancer tout seul jusqu'à
couverture complète, notifie la progression tous les 50 tokens puis le rapport
final. Vérifie : gating (double gate), FIFO, dédoublonnage, notification de
progression/complétion, respect du kill-switch, taille de file affichée."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from aria_core.services import wallet_scan_queue as wsq

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(wsq, "DB_PATH", str(tmp_path / "wallet_scan_queue_test.db"))
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    yield


def test_disabled_by_default():
    assert wsq.wallet_scan_queue_enabled() is False


def test_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    assert wsq.wallet_scan_queue_enabled() is True


@pytest.mark.asyncio
async def test_enqueue_then_size_and_fifo_order():
    added = await wsq.enqueue_wallets([A, B])
    assert added == [A, B]
    assert await wsq.queue_size() == 2
    pending = await wsq.list_pending(limit=10)
    assert [q.wallet for q in pending] == [A, B]


@pytest.mark.asyncio
async def test_enqueue_duplicate_is_ignored():
    await wsq.enqueue_wallets([A])
    added_again = await wsq.enqueue_wallets([A, B])
    assert added_again == [B]
    assert await wsq.queue_size() == 2


@pytest.mark.asyncio
async def test_enqueue_lowercases_address():
    await wsq.enqueue_wallets([A.upper()])
    pending = await wsq.list_pending()
    assert pending[0].wallet == A.lower()


@pytest.mark.asyncio
async def test_remove_from_queue():
    await wsq.enqueue_wallets([A, B])
    await wsq.remove_from_queue(A)
    assert await wsq.queue_size() == 1
    pending = await wsq.list_pending()
    assert pending[0].wallet == B


@pytest.mark.asyncio
async def test_mark_attempt_updates_milestone():
    await wsq.enqueue_wallets([A])
    await wsq.mark_attempt(A, last_notified_milestone=50)
    pending = await wsq.list_pending()
    assert pending[0].last_notified_milestone == 50
    assert pending[0].last_attempt_at is not None


@pytest.mark.asyncio
async def test_cycle_skipped_when_gate_off():
    await wsq.enqueue_wallets([A])
    result = await wsq.run_wallet_scan_queue_cycle()
    assert result == {"outcome": "skipped", "reason": "gate_off"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_wallet_scoring_disabled(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    await wsq.enqueue_wallets([A])
    result = await wsq.run_wallet_scan_queue_cycle()
    assert result == {"outcome": "skipped", "reason": "wallet_scoring_disabled"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_paused(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    await wsq.enqueue_wallets([A])
    result = await wsq.run_wallet_scan_queue_cycle()
    assert result == {"outcome": "skipped", "reason": "paused"}


@pytest.mark.asyncio
async def test_cycle_empty_queue(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    result = await wsq.run_wallet_scan_queue_cycle()
    assert result == {"outcome": "empty_queue"}


@dataclass
class _FakeCard:
    address: str
    available: bool = True
    tokens_scanned_cumulative: int = 0
    tokens_found: int = 100
    full_coverage: bool = False
    chains_scanned: list = field(default_factory=list)
    disqualified: bool = False
    disqualification_reasons: list = field(default_factory=list)
    financing_check_note: str | None = None
    tokens_analyzed: int = 0
    tokens_skipped_capped: bool = False
    unpriced_legs: int = 0
    pool_lookup_errors: int = 0
    gecko_dexscreener_gap_count: int = 0
    win_rate: float | None = None
    realized_pnl_usd: float | None = None
    sortino: float | None = None
    early_entry_recurrence_count: int = 0
    suspect_positive: bool = False
    thesis: str | None = None
    display_name: str | None = None
    error: str | None = None


@dataclass
class _FakeReport:
    available: bool = True
    error: str | None = None
    wallets: list = field(default_factory=list)
    convergence_pairs: list = field(default_factory=list)
    synthesis: str | None = None


@pytest.mark.asyncio
async def test_cycle_sends_progress_notification_on_milestone_crossing(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])

    card = _FakeCard(address=A, tokens_scanned_cumulative=50, tokens_found=200, full_coverage=False)

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    notified = []

    async def _notifier(text):
        notified.append(text)

    result = await wsq.run_wallet_scan_queue_cycle(notifier=_notifier)
    assert result["outcome"] == "ok"
    assert len(notified) == 1
    assert "50/200" in notified[0]
    assert "File d'attente" in notified[0]

    pending = await wsq.list_pending()
    assert pending[0].last_notified_milestone == 50


@pytest.mark.asyncio
async def test_cycle_no_notification_below_next_milestone(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])
    await wsq.mark_attempt(A, last_notified_milestone=50)

    card = _FakeCard(address=A, tokens_scanned_cumulative=60, tokens_found=200, full_coverage=False)

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    notified = []

    async def _notifier(text):
        notified.append(text)

    result = await wsq.run_wallet_scan_queue_cycle(notifier=_notifier)
    assert result["outcome"] == "ok"
    assert notified == []


@pytest.mark.asyncio
async def test_cycle_sends_completion_notification_and_removes_from_queue(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A, B])

    async def _fake_score_wallets(addresses, **kwargs):
        wallet = addresses[0]
        full = wallet == A
        card = _FakeCard(
            address=wallet,
            tokens_scanned_cumulative=200 if full else 10,
            tokens_found=200,
            full_coverage=full,
        )
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    notified = []

    async def _notifier(text):
        notified.append(text)

    result = await wsq.run_wallet_scan_queue_cycle(notifier=_notifier)
    assert result["completed"] == [A]
    assert len(notified) == 1
    assert "terminé" in notified[0]
    assert "1 wallet(s) restant(s)" in notified[0]

    assert await wsq.queue_size() == 1
    pending = await wsq.list_pending()
    assert pending[0].wallet == B


@pytest.mark.asyncio
async def test_cycle_processes_at_most_max_wallets_per_cycle(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A, B, C])

    calls = []

    async def _fake_score_wallets(addresses, **kwargs):
        calls.append(addresses[0])
        return _FakeReport(wallets=[_FakeCard(address=addresses[0], tokens_scanned_cumulative=10, tokens_found=100)])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    result = await wsq.run_wallet_scan_queue_cycle()
    assert result["outcome"] == "ok"
    assert len(calls) == wsq.MAX_WALLETS_PER_CYCLE == 2
    assert calls == [A, B]


@pytest.mark.asyncio
async def test_cycle_unavailable_report_marks_attempt_and_keeps_in_queue(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(available=False, error="panne", wallets=[])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    result = await wsq.run_wallet_scan_queue_cycle()
    assert result["outcome"] == "ok"
    assert await wsq.queue_size() == 1
    pending = await wsq.list_pending()
    assert pending[0].last_attempt_at is not None
