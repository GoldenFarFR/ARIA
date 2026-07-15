"""Sourcing automatique de wallets candidats depuis l'historique propre d'ARIA
(15/07, réponse à « qui va trouver les wallets ? »). Vérifie : gating (triple
gate), sélection du bon token (clos, seuil de gain, jamais déjà sourcé),
exclusion du plus gros détenteur/adresses mortes, enfilement dans la file,
respect du kill-switch, aucun double sourcing du même token."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from aria_core.skills import wallet_candidate_sourcing as wcs

CONTRACT_A = "0x" + "a" * 40
CONTRACT_B = "0x" + "b" * 40
W1 = "0x" + "1" * 40
W2 = "0x" + "2" * 40
W3 = "0x" + "3" * 40
POOL = "0x" + "9" * 40


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(wcs, "DB_PATH", str(tmp_path / "wallet_candidate_sourcing_test.db"))
    monkeypatch.setattr(
        "aria_core.services.wallet_scan_queue.DB_PATH", str(tmp_path / "wallet_scan_queue_test.db")
    )
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    yield


def _enable_all(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_CANDIDATE_SOURCING_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")


def test_disabled_by_default():
    assert wcs.wallet_candidate_sourcing_enabled() is False


def test_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_CANDIDATE_SOURCING_ENABLED", "1")
    assert wcs.wallet_candidate_sourcing_enabled() is True


async def _empty_closed_positions():
    return []


async def _empty_predictions():
    return []


@pytest.mark.asyncio
async def test_list_strong_performers_filters_closed_and_threshold(monkeypatch):
    async def _fake_list_all_predictions():
        return [
            {"contract": CONTRACT_A, "status": "closed", "outcome_pct": 150.0, "network": "base"},
            {"contract": CONTRACT_B, "status": "closed", "outcome_pct": 40.0, "network": "base"},
            {"contract": "0x" + "c" * 40, "status": "open", "outcome_pct": None, "network": "base"},
        ]

    monkeypatch.setattr("aria_core.vc_predictions.list_all_predictions", _fake_list_all_predictions)
    monkeypatch.setattr("aria_core.paper_trader.get_closed_positions", _empty_closed_positions)
    performers = await wcs.list_strong_performers()
    assert [p["contract"] for p in performers] == [CONTRACT_A]


@pytest.mark.asyncio
async def test_list_strong_performers_includes_paper_trading_source(monkeypatch):
    """Constat opérateur (15/07) : vc_predictions seule resterait vide des
    semaines (0 pronostic clôturé, horizon 30j) -- paper_trader (stop
    suiveur/prise de profit, résolution rapide) doit compenser."""
    monkeypatch.setattr("aria_core.vc_predictions.list_all_predictions", _empty_predictions)

    async def _fake_closed_positions():
        return [
            {"contract": CONTRACT_A, "pnl_pct": 120.0, "status": "closed"},
            {"contract": CONTRACT_B, "pnl_pct": 30.0, "status": "closed"},
        ]

    monkeypatch.setattr("aria_core.paper_trader.get_closed_positions", _fake_closed_positions)
    performers = await wcs.list_strong_performers()
    assert [p["contract"] for p in performers] == [CONTRACT_A]
    assert performers[0]["network"] == "base"


@pytest.mark.asyncio
async def test_list_strong_performers_dedupes_same_contract_both_sources(monkeypatch):
    async def _fake_list_all_predictions():
        return [{"contract": CONTRACT_A, "status": "closed", "outcome_pct": 150.0, "network": "base"}]

    async def _fake_closed_positions():
        return [{"contract": CONTRACT_A, "pnl_pct": 200.0, "status": "closed"}]

    monkeypatch.setattr("aria_core.vc_predictions.list_all_predictions", _fake_list_all_predictions)
    monkeypatch.setattr("aria_core.paper_trader.get_closed_positions", _fake_closed_positions)
    performers = await wcs.list_strong_performers()
    assert len(performers) == 1
    assert performers[0]["contract"] == CONTRACT_A


@pytest.mark.asyncio
async def test_already_sourced_tracking():
    assert await wcs._already_sourced(CONTRACT_A) is False
    await wcs._mark_sourced(CONTRACT_A)
    assert await wcs._already_sourced(CONTRACT_A) is True


@pytest.mark.asyncio
async def test_cycle_skipped_when_gate_off():
    result = await wcs.run_wallet_candidate_sourcing_cycle()
    assert result == {"outcome": "skipped", "reason": "gate_off"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_downstream_disabled(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_CANDIDATE_SOURCING_ENABLED", "1")
    result = await wcs.run_wallet_candidate_sourcing_cycle()
    assert result == {"outcome": "skipped", "reason": "downstream_disabled"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_paused(monkeypatch):
    _enable_all(monkeypatch)
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    result = await wcs.run_wallet_candidate_sourcing_cycle()
    assert result == {"outcome": "skipped", "reason": "paused"}


@pytest.mark.asyncio
async def test_cycle_no_new_performer(monkeypatch):
    _enable_all(monkeypatch)

    async def _fake_list_all_predictions():
        return []

    monkeypatch.setattr("aria_core.vc_predictions.list_all_predictions", _fake_list_all_predictions)
    result = await wcs.run_wallet_candidate_sourcing_cycle()
    assert result == {"outcome": "no_new_performer"}


@dataclass
class _FakeHolder:
    address: str
    balance: float | None = None
    percentage: float | None = None


@dataclass
class _FakeHoldersResult:
    holders: list = field(default_factory=list)
    total_supply: float | None = None
    available: bool = True
    error: str | None = None


@pytest.mark.asyncio
async def test_cycle_enqueues_holders_excluding_top_and_dead(monkeypatch):
    _enable_all(monkeypatch)

    async def _fake_list_all_predictions():
        return [{"contract": CONTRACT_A, "status": "closed", "outcome_pct": 150.0, "network": "base"}]

    monkeypatch.setattr("aria_core.vc_predictions.list_all_predictions", _fake_list_all_predictions)

    class _FakeClient:
        async def get_token_holders(self, token_address):
            return _FakeHoldersResult(
                holders=[
                    _FakeHolder(address=POOL, balance=1_000_000, percentage=80.0),
                    _FakeHolder(address=W1, balance=1000, percentage=1.0),
                    _FakeHolder(
                        address="0x000000000000000000000000000000000000dEaD", balance=500, percentage=0.5
                    ),
                    _FakeHolder(address=W2, balance=300, percentage=0.3),
                ]
            )

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeClient()
    )

    notified = []

    async def _notifier(text):
        notified.append(text)

    result = await wcs.run_wallet_candidate_sourcing_cycle(notifier=_notifier)
    assert result["outcome"] == "ok"
    assert result["contract"] == CONTRACT_A
    assert result["sourced"] == 2  # W1, W2 -- pas POOL (top holder), pas l'adresse morte

    from aria_core.services.wallet_scan_queue import list_pending

    pending = await list_pending(limit=10)
    wallets = {p.wallet for p in pending}
    assert wallets == {W1.lower(), W2.lower()}
    assert POOL.lower() not in wallets

    assert len(notified) == 1
    assert "2 wallet(s)" in notified[0]
    assert CONTRACT_A[:10] in notified[0]

    assert await wcs._already_sourced(CONTRACT_A) is True


@pytest.mark.asyncio
async def test_cycle_never_resources_same_token_twice(monkeypatch):
    _enable_all(monkeypatch)
    await wcs._mark_sourced(CONTRACT_A)

    async def _fake_list_all_predictions():
        return [{"contract": CONTRACT_A, "status": "closed", "outcome_pct": 150.0, "network": "base"}]

    monkeypatch.setattr("aria_core.vc_predictions.list_all_predictions", _fake_list_all_predictions)

    result = await wcs.run_wallet_candidate_sourcing_cycle()
    assert result == {"outcome": "no_new_performer"}


@pytest.mark.asyncio
async def test_cycle_zero_holders_still_marks_sourced(monkeypatch):
    _enable_all(monkeypatch)

    async def _fake_list_all_predictions():
        return [{"contract": CONTRACT_A, "status": "closed", "outcome_pct": 150.0, "network": "base"}]

    monkeypatch.setattr("aria_core.vc_predictions.list_all_predictions", _fake_list_all_predictions)

    class _FakeClient:
        async def get_token_holders(self, token_address):
            return _FakeHoldersResult(available=False, error="panne")

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeClient()
    )

    result = await wcs.run_wallet_candidate_sourcing_cycle()
    assert result["outcome"] == "ok"
    assert result["contract"] == CONTRACT_A
    assert result["sourced"] == 0
    assert result["total_sourced"] == 0
    assert await wcs._already_sourced(CONTRACT_A) is True


@pytest.mark.asyncio
async def test_cycle_processes_all_new_performers_in_one_pass(monkeypatch):
    """15/07, constat opérateur (« il faudrait au moins 5 tokens/semaine ») --
    aucun plafond artificiel d'un seul token par cycle : si plusieurs gagnants
    sont déjà en attente, tous sont traités dans la même passe."""
    _enable_all(monkeypatch)

    async def _fake_list_all_predictions():
        return [
            {"contract": CONTRACT_A, "status": "closed", "outcome_pct": 150.0, "network": "base"},
            {"contract": CONTRACT_B, "status": "closed", "outcome_pct": 200.0, "network": "base"},
        ]

    monkeypatch.setattr("aria_core.vc_predictions.list_all_predictions", _fake_list_all_predictions)

    class _FakeClient:
        async def get_token_holders(self, token_address):
            # Détenteurs DISTINCTS par token -- un vrai token gagnant différent
            # attire des acheteurs différents, jamais le même wallet partout.
            wallet = W1 if token_address.lower() == CONTRACT_A.lower() else W2
            return _FakeHoldersResult(holders=[_FakeHolder(address=POOL), _FakeHolder(address=wallet)])

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeClient()
    )

    notified = []

    async def _notifier(text):
        notified.append(text)

    result = await wcs.run_wallet_candidate_sourcing_cycle(notifier=_notifier)
    assert result["outcome"] == "ok"
    assert len(result["tokens_processed"]) == 2
    assert result["total_sourced"] == 2  # W1 (token A) + W2 (token B)
    assert await wcs._already_sourced(CONTRACT_A) is True
    assert await wcs._already_sourced(CONTRACT_B) is True
    assert len(notified) == 1  # une seule notification agrégée, pas une par token
