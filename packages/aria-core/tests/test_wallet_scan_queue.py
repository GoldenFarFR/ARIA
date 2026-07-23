"""File d'attente de scan wallet en arrière-plan (#157 suite, 15/07) --
`/walletqueue` injecte, `wallet_scan_queue_cycle` fait avancer tout seul jusqu'à
couverture complète, notifie la progression tous les 50 tokens puis le rapport
final. Suivi PERMANENT (#157 suite 2, 15/07) : un wallet qui atteint 100% n'est
JAMAIS retiré -- il bascule en surveillance hebdomadaire, retiré seulement après
`INACTIVITY_CUTOFF_DAYS` (3 mois) sans aucune activité on-chain réelle. Vérifie :
gating (double gate), FIFO/due-scheduling, dédoublonnage, notification de
progression/complétion/surveillance, respect du kill-switch, comptage
rattrapage vs surveillance."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiosqlite
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


async def _force_monitoring_state(wallet: str, *, next_check_at: datetime, monitoring_since: datetime | None = None) -> None:
    """Bascule directement une ligne existante en mode surveillance -- il n'y a
    pas d'API publique pour enfiler un wallet déjà en surveillance (on y arrive
    toujours via une première couverture complète), donc les tests qui portent
    sur le comportement POST-100% manipulent la ligne directement."""
    since = monitoring_since or next_check_at
    async with aiosqlite.connect(wsq.DB_PATH) as db:
        await db.execute(
            "UPDATE wallet_scan_queue SET monitoring_since=?, next_check_at=? WHERE wallet=?",
            (since.isoformat(), next_check_at.isoformat(), wallet.lower()),
        )
        await db.commit()


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
async def test_enqueue_new_wallet_is_immediately_due_and_not_monitoring():
    await wsq.enqueue_wallets([A])
    pending = await wsq.list_pending()
    assert pending[0].is_monitoring is False
    assert pending[0].monitoring_since is None


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
    now = datetime.now(timezone.utc)
    await wsq.mark_attempt(A, next_check_at=now, last_notified_milestone=50)
    pending = await wsq.list_pending()
    assert pending[0].last_notified_milestone == 50
    assert pending[0].last_attempt_at is not None


@pytest.mark.asyncio
async def test_mark_attempt_next_check_at_controls_due_scheduling():
    await wsq.enqueue_wallets([A])
    future = datetime.now(timezone.utc) + timedelta(days=5)
    await wsq.mark_attempt(A, next_check_at=future)
    assert await wsq.list_pending(limit=10) == []


@pytest.mark.asyncio
async def test_queue_counts_distinguishes_catching_up_from_monitoring():
    await wsq.enqueue_wallets([A, B])
    await _force_monitoring_state(A, next_check_at=datetime.now(timezone.utc) + timedelta(days=7))
    counts = await wsq.queue_counts()
    assert counts == {"catching_up": 1, "monitoring": 1}


@pytest.mark.asyncio
async def test_list_pending_prioritizes_catching_up_over_monitoring(monkeypatch):
    """21/07 -- capacité du classement relevée à 600, la surveillance
    hebdomadaire d'une grosse population ne doit jamais affamer la
    découverte de nouveaux candidats. B (surveillance) est DUE DEPUIS PLUS
    LONGTEMPS que A (rattrapage, un nouveau candidat jamais scoré), mais A
    doit quand même sortir en premier."""
    monkeypatch.setattr(wsq, "MAX_WALLETS_PER_CYCLE", 1)
    now = datetime.now(timezone.utc)
    await wsq.enqueue_wallets([A, B])
    await _force_monitoring_state(B, next_check_at=now - timedelta(days=5))  # dû depuis longtemps
    await wsq.mark_attempt(A, next_check_at=now - timedelta(minutes=1))  # rattrapage, dû aussi

    pending = await wsq.list_pending(limit=1)
    assert [q.wallet for q in pending] == [A]


@pytest.mark.asyncio
async def test_list_pending_fifo_within_the_same_group():
    """Au sein du même groupe (deux wallets en rattrapage, ou deux wallets en
    surveillance), le plus anciennement dû sort toujours en premier."""
    now = datetime.now(timezone.utc)
    await wsq.enqueue_wallets([A, B])
    await wsq.mark_attempt(A, next_check_at=now - timedelta(minutes=1))
    await wsq.mark_attempt(B, next_check_at=now - timedelta(minutes=10))  # plus ancien

    pending = await wsq.list_pending(limit=10)
    assert [q.wallet for q in pending] == [B, A]


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
    # Suivi permanent (#157 suite 2, 15/07) : dernière activité on-chain réelle
    # observée -- utilisée par le cycle pour trancher l'inactivité de 3 mois.
    last_activity_at: datetime | None = None
    # Détenteur croisé (21/07, token_holder_intel.py) -- cf. smart_money.py.
    cross_token_holdings: list = field(default_factory=list)
    cross_token_holder_count: int = 0
    # Classement comparatif (21/07, smart_money_leaderboard.py).
    composite_percentile: float | None = None


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
    await wsq.mark_attempt(A, next_check_at=datetime.now(timezone.utc), last_notified_milestone=50)

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
async def test_cycle_first_completion_transitions_to_monitoring_never_removed(monkeypatch):
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
    assert result["completed_first_time"] == [A]
    assert result["dropped_inactive"] == []
    assert len(notified) == 1
    assert "terminé" in notified[0]

    # Jamais retiré de la file -- bascule en surveillance permanente.
    assert await wsq.queue_size() == 2
    counts = await wsq.queue_counts()
    assert counts == {"catching_up": 1, "monitoring": 1}

    # Plus dû immédiatement (reprogrammé +7j) -- absent du prochain `list_pending`.
    pending = await wsq.list_pending(limit=10)
    assert [q.wallet for q in pending] == [B]


@pytest.mark.asyncio
async def test_cycle_first_completion_updates_leaderboard(monkeypatch):
    """21/07 -- la couverture complète déclenche la mise à jour du classement
    smart-money (jamais sur un score partiel, cf. smart_money_leaderboard.py)."""
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])

    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200,
        full_coverage=True, composite_percentile=77.0,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    calls = []

    async def _fake_update_leaderboard(wallet, percentile):
        calls.append((wallet, percentile))
        return "added"

    monkeypatch.setattr(
        "aria_core.services.smart_money_leaderboard.update_leaderboard", _fake_update_leaderboard,
    )

    await wsq.run_wallet_scan_queue_cycle()
    assert calls == [(A, 77.0)]


@pytest.mark.asyncio
async def test_cycle_monitoring_refresh_updates_leaderboard(monkeypatch):
    """21/07 -- une passe de surveillance hebdomadaire (déjà à 100%) recalcule
    aussi le classement, pas seulement la toute première complétion (un wallet
    peut monter ou descendre dans le temps)."""
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])
    past = datetime.now(timezone.utc) - timedelta(days=10)
    await _force_monitoring_state(A, next_check_at=past)

    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200,
        full_coverage=True, tokens_analyzed=0, composite_percentile=45.0,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    calls = []

    async def _fake_update_leaderboard(wallet, percentile):
        calls.append((wallet, percentile))
        return "updated"

    monkeypatch.setattr(
        "aria_core.services.smart_money_leaderboard.update_leaderboard", _fake_update_leaderboard,
    )

    await wsq.run_wallet_scan_queue_cycle()
    assert calls == [(A, 45.0)]


@pytest.mark.asyncio
async def test_cycle_leaderboard_update_failure_never_crashes_the_cycle(monkeypatch):
    """Best-effort (21/07) : une panne d'écriture du classement ne doit jamais
    casser le cycle de scan lui-même."""
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])

    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200,
        full_coverage=True, composite_percentile=55.0,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    async def _raise(*a, **k):
        raise RuntimeError("panne d'écriture")

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)
    monkeypatch.setattr("aria_core.services.smart_money_leaderboard.update_leaderboard", _raise)

    result = await wsq.run_wallet_scan_queue_cycle()
    assert result["completed_first_time"] == [A]  # le cycle a bien terminé normalement


@pytest.mark.asyncio
async def test_cycle_first_completion_underperformer_removed_entirely_not_monitored(monkeypatch):
    """21/07, demande opérateur explicite : un wallet confirmé mauvais dès sa
    1ère couverture complète n'entre JAMAIS en surveillance permanente -- il
    est retiré de la file entièrement et rejeté pour toujours."""
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])

    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200,
        full_coverage=True, composite_percentile=12.0,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    reject_calls = []

    async def _fake_mark_rejected(wallet, percentile, reason):
        reject_calls.append((wallet, percentile, reason))

    async def _fake_remove_and_archive(wallet, reason):
        return "removed"

    monkeypatch.setattr(
        "aria_core.services.smart_money_leaderboard.mark_rejected", _fake_mark_rejected,
    )
    monkeypatch.setattr(
        "aria_core.services.smart_money_leaderboard.remove_and_archive", _fake_remove_and_archive,
    )

    notified = []

    async def _notifier(text):
        notified.append(text)

    result = await wsq.run_wallet_scan_queue_cycle(notifier=_notifier)
    assert result["completed_first_time"] == []  # jamais entré en surveillance
    assert result["rejected_wallets"] == [A]
    assert await wsq.queue_size() == 0  # retiré ENTIÈREMENT, pas juste du classement
    assert len(reject_calls) == 1
    assert reject_calls[0][0] == A
    assert reject_calls[0][1] == 12.0
    assert "sous-performant" in notified[0]


@pytest.mark.asyncio
async def test_cycle_monitoring_refresh_underperformer_removed_entirely(monkeypatch):
    """21/07 -- un wallet déjà en surveillance qui se dégrade sous le seuil
    est lui aussi retiré ENTIÈREMENT de la file, pas seulement du classement."""
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])
    past = datetime.now(timezone.utc) - timedelta(days=10)
    await _force_monitoring_state(A, next_check_at=past)

    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200,
        full_coverage=True, tokens_analyzed=0, composite_percentile=18.0,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    reject_calls = []

    async def _fake_mark_rejected(wallet, percentile, reason):
        reject_calls.append((wallet, percentile, reason))

    monkeypatch.setattr(
        "aria_core.services.smart_money_leaderboard.mark_rejected", _fake_mark_rejected,
    )
    monkeypatch.setattr(
        "aria_core.services.smart_money_leaderboard.remove_and_archive",
        lambda wallet, reason: _immediate("removed"),
    )

    result = await wsq.run_wallet_scan_queue_cycle()
    assert result["rejected_wallets"] == [A]
    assert await wsq.queue_size() == 0
    assert len(reject_calls) == 1


async def _immediate(value):
    return value


@pytest.mark.asyncio
async def test_cycle_underperformer_with_none_percentile_stays_in_monitoring(monkeypatch):
    """Jamais rejeté sur un percentile inconnu (``None``) -- seulement un
    percentile réellement MESURÉ et confirmé mauvais."""
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])

    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200,
        full_coverage=True, composite_percentile=None,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    result = await wsq.run_wallet_scan_queue_cycle()
    assert result["rejected_wallets"] == []
    assert result["completed_first_time"] == [A]
    assert await wsq.queue_size() == 1  # toujours en file, bascule en surveillance normale


@pytest.mark.asyncio
async def test_cycle_monitoring_no_new_activity_is_silent_but_rescheduled(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])
    past = datetime.now(timezone.utc) - timedelta(days=10)
    await _force_monitoring_state(A, next_check_at=past)

    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200, full_coverage=True, tokens_analyzed=0,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    notified = []

    async def _notifier(text):
        notified.append(text)

    result = await wsq.run_wallet_scan_queue_cycle(notifier=_notifier)
    assert result["outcome"] == "ok"
    assert notified == []
    assert await wsq.queue_size() == 1

    # Reprogrammé +7j -- plus dû immédiatement.
    assert await wsq.list_pending(limit=10) == []


@pytest.mark.asyncio
async def test_cycle_monitoring_new_activity_notifies_and_reschedules(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])
    past = datetime.now(timezone.utc) - timedelta(days=10)
    await _force_monitoring_state(A, next_check_at=past)

    card = _FakeCard(
        address=A, tokens_scanned_cumulative=205, tokens_found=205, full_coverage=True, tokens_analyzed=5,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    notified = []

    async def _notifier(text):
        notified.append(text)

    result = await wsq.run_wallet_scan_queue_cycle(notifier=_notifier)
    assert result["outcome"] == "ok"
    assert len(notified) == 1
    assert "activité" in notified[0]
    assert await wsq.queue_size() == 1


@pytest.mark.asyncio
async def test_cycle_monitoring_inactive_over_90_days_is_dropped(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])
    past = datetime.now(timezone.utc) - timedelta(days=10)
    await _force_monitoring_state(A, next_check_at=past)

    stale_activity = datetime.now(timezone.utc) - timedelta(days=95)
    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200, full_coverage=True,
        tokens_analyzed=0, last_activity_at=stale_activity,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    notified = []

    async def _notifier(text):
        notified.append(text)

    result = await wsq.run_wallet_scan_queue_cycle(notifier=_notifier)
    assert result["dropped_inactive"] == [A]
    assert len(notified) == 1
    assert "inactif" in notified[0]
    assert await wsq.queue_size() == 0


@pytest.mark.asyncio
async def test_cycle_dropping_inactive_wallet_also_removes_it_from_leaderboard(monkeypatch):
    """21/07 -- trou corrigé : un wallet retiré pour inactivité ne doit jamais
    garder sa dernière note figée indéfiniment dans le classement smart-money."""
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])
    past = datetime.now(timezone.utc) - timedelta(days=10)
    await _force_monitoring_state(A, next_check_at=past)

    stale_activity = datetime.now(timezone.utc) - timedelta(days=95)
    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200, full_coverage=True,
        tokens_analyzed=0, last_activity_at=stale_activity,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    calls = []

    async def _fake_remove(wallet, reason):
        calls.append((wallet, reason))
        return "removed"

    monkeypatch.setattr(
        "aria_core.services.smart_money_leaderboard.remove_and_archive", _fake_remove,
    )

    await wsq.run_wallet_scan_queue_cycle()
    assert len(calls) == 1
    assert calls[0][0] == A
    assert "inactif" in calls[0][1]


@pytest.mark.asyncio
async def test_cycle_leaderboard_removal_failure_never_crashes_the_cycle(monkeypatch):
    """Best-effort (21/07), même doctrine que la mise à jour du classement."""
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])
    past = datetime.now(timezone.utc) - timedelta(days=10)
    await _force_monitoring_state(A, next_check_at=past)

    stale_activity = datetime.now(timezone.utc) - timedelta(days=95)
    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200, full_coverage=True,
        tokens_analyzed=0, last_activity_at=stale_activity,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    async def _raise(*a, **k):
        raise RuntimeError("panne d'écriture")

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)
    monkeypatch.setattr("aria_core.services.smart_money_leaderboard.remove_and_archive", _raise)

    result = await wsq.run_wallet_scan_queue_cycle()
    assert result["dropped_inactive"] == [A]  # le cycle a bien terminé normalement


@pytest.mark.asyncio
async def test_cycle_monitoring_recent_activity_within_cutoff_is_not_dropped(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")
    await wsq.enqueue_wallets([A])
    past = datetime.now(timezone.utc) - timedelta(days=10)
    await _force_monitoring_state(A, next_check_at=past)

    recent_activity = datetime.now(timezone.utc) - timedelta(days=5)
    card = _FakeCard(
        address=A, tokens_scanned_cumulative=200, tokens_found=200, full_coverage=True,
        tokens_analyzed=0, last_activity_at=recent_activity,
    )

    async def _fake_score_wallets(addresses, **kwargs):
        return _FakeReport(wallets=[card])

    monkeypatch.setattr("aria_core.services.smart_money.score_wallets", _fake_score_wallets)

    result = await wsq.run_wallet_scan_queue_cycle()
    assert result["dropped_inactive"] == []
    assert await wsq.queue_size() == 1


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
    assert len(calls) == wsq.MAX_WALLETS_PER_CYCLE == 1
    assert calls == [A]


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


@pytest.mark.asyncio
async def test_queue_status_summary_empty_queue():
    status = await wsq.queue_status_summary()
    assert status == {
        "total": 0,
        "never_attempted": 0,
        "in_progress": 0,
        "monitoring": 0,
        "oldest_never_attempted_wallet": None,
        "oldest_never_attempted_days": None,
        "last_scored_wallet": None,
        "last_scored_at": None,
    }


@pytest.mark.asyncio
async def test_queue_status_summary_distinguishes_never_attempted_vs_in_progress_vs_monitoring(monkeypatch):
    """The exact distinction the operator asked for (23/07, #29 follow-up):
    "added to the queue" must never be confused with "already scanned"."""
    await wsq.enqueue_wallets([A, B, C])

    # A: never attempted at all.
    # B: attempted once, still catching up (not yet full coverage).
    await wsq.mark_attempt(B, next_check_at=datetime.now(timezone.utc))
    # C: reached full coverage, now in weekly monitoring (mark_attempt sets
    # last_attempt_at AND monitoring_since together, unlike the raw
    # _force_monitoring_state test helper used elsewhere in this file, which
    # bypasses last_attempt_at entirely -- not representative of the real
    # code path here, where a monitoring wallet always has an attempt behind it).
    await wsq.mark_attempt(C, next_check_at=datetime.now(timezone.utc) + timedelta(days=7), monitoring_since=datetime.now(timezone.utc))

    status = await wsq.queue_status_summary()
    assert status["total"] == 3
    assert status["never_attempted"] == 1
    assert status["in_progress"] == 1
    assert status["monitoring"] == 1
    assert status["oldest_never_attempted_wallet"] == A
    assert status["last_scored_wallet"] == C  # C was marked fractionally after B


@pytest.mark.asyncio
async def test_queue_status_summary_oldest_never_attempted_age_in_days(monkeypatch):
    old_added = datetime.now(timezone.utc) - timedelta(days=5)
    await wsq.enqueue_wallets([A])
    async with aiosqlite.connect(wsq.DB_PATH) as db:
        await db.execute(
            "UPDATE wallet_scan_queue SET added_at=?, next_check_at=? WHERE wallet=?",
            (old_added.isoformat(), old_added.isoformat(), A),
        )
        await db.commit()

    status = await wsq.queue_status_summary()
    assert status["oldest_never_attempted_wallet"] == A
    assert status["oldest_never_attempted_days"] == pytest.approx(5.0, abs=0.01)
