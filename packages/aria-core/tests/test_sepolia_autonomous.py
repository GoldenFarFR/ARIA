"""Rehearsal Sepolia autonome — sizing Kelly, kill-switch, coupe-circuit, télémétrie
(hors-ligne, tout injecté). Ne doit JAMAIS appeler wallet_guard.escalate_spend/resolve_spend."""
from __future__ import annotations

import pytest

from aria_core.onchain import sepolia_autonomous as sa

LEDGER_ADDRESS = "0x000000000000000000000000000000000000dEaD"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "DB_PATH", str(tmp_path / "sepolia_auto_test.db"))
    from aria_core import vc_predictions

    monkeypatch.setattr(vc_predictions, "DB_PATH", str(tmp_path / "vc_pred_test.db"))
    yield


@pytest.fixture(autouse=True)
def _gates_open(monkeypatch):
    monkeypatch.setenv("ARIA_SEPOLIA_WALLET_ENABLED", "1")
    monkeypatch.setenv("ARIA_SEPOLIA_AUTONOMOUS_ENABLED", "1")
    monkeypatch.setenv("ARIA_ONCHAIN_ANCHOR_ENABLED", "1")
    monkeypatch.setenv("ARIA_LEDGER_ADDRESS", LEDGER_ADDRESS)
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: False)
    yield


def _buy_analyzer(these="Thèse factice, setup net."):
    async def analyzer(contract):
        return {
            "action": "BUY", "symbol": "TEST", "price": 1.0,
            "target": 1.5, "invalidation": 0.8, "these": these,
        }
    return analyzer


async def _hold_analyzer(contract):
    return {"action": "HOLD", "symbol": "TEST", "price": 1.0, "target": None, "invalidation": None}


# ── kelly_fraction (pure) ─────────────────────────────────────────────────────────────

def test_kelly_fraction_positive_edge():
    f = sa.kelly_fraction(win_rate=0.6, avg_win_pct=40.0, avg_loss_pct=-20.0)
    assert 0.0 < f <= sa.KELLY_CAP


def test_kelly_fraction_negative_edge_clamped_to_zero():
    # Faible win-rate + perte moyenne large -> Kelly brut négatif -> 0, jamais négatif.
    f = sa.kelly_fraction(win_rate=0.2, avg_win_pct=5.0, avg_loss_pct=-30.0)
    assert f == 0.0


def test_kelly_fraction_missing_data_falls_back():
    assert sa.kelly_fraction(None, None, None) == sa.KELLY_FALLBACK_FRACTION


def test_kelly_fraction_degenerate_falls_back():
    assert sa.kelly_fraction(0.6, avg_win_pct=0.0, avg_loss_pct=-10.0) == sa.KELLY_FALLBACK_FRACTION
    assert sa.kelly_fraction(0.6, avg_win_pct=10.0, avg_loss_pct=0.0) == sa.KELLY_FALLBACK_FRACTION


def test_kelly_fraction_hard_capped():
    # Edge extrême (win-rate très haut, ratio gain/perte énorme) -> plafonné, jamais du plein-Kelly brut.
    f = sa.kelly_fraction(win_rate=0.95, avg_win_pct=200.0, avg_loss_pct=-1.0)
    assert f == sa.KELLY_CAP


# ── gating ─────────────────────────────────────────────────────────────────────────────

def test_autonomous_disabled_without_wallet_flag(monkeypatch):
    monkeypatch.delenv("ARIA_SEPOLIA_WALLET_ENABLED", raising=False)
    assert sa.sepolia_autonomous_enabled() is False


def test_autonomous_disabled_without_autonomous_flag(monkeypatch):
    monkeypatch.delenv("ARIA_SEPOLIA_AUTONOMOUS_ENABLED", raising=False)
    assert sa.sepolia_autonomous_enabled() is False


def test_autonomous_enabled_when_both_flags_set():
    assert sa.sepolia_autonomous_enabled() is True


# ── run_autonomous_cycle : gates fail-closed ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skips_when_paused(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: True)
    result = await sa.run_autonomous_cycle(candidates=["0xAAA"])
    assert result["outcome"] == "skipped_paused"


@pytest.mark.asyncio
async def test_skips_when_autonomous_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_SEPOLIA_AUTONOMOUS_ENABLED", raising=False)
    result = await sa.run_autonomous_cycle(candidates=["0xAAA"])
    assert result["outcome"] == "skipped_disabled"


@pytest.mark.asyncio
async def test_skips_when_no_ledger_configured(monkeypatch):
    monkeypatch.delenv("ARIA_LEDGER_ADDRESS", raising=False)
    result = await sa.run_autonomous_cycle(candidates=["0xAAA"])
    assert result["outcome"] == "skipped_no_ledger"


@pytest.mark.asyncio
async def test_never_touches_wallet_guard_escalation(monkeypatch):
    """Le chemin autonome ne doit JAMAIS passer par escalate_spend — sinon ce ne serait
    plus autonome, et le garde-fou Telegram partagé serait contourné en silence."""
    called = {"n": 0}

    async def fake_escalate(*args, **kwargs):
        called["n"] += 1
        return "should-not-be-called"

    monkeypatch.setattr("aria_core.wallet_guard.escalate_spend", fake_escalate)

    result = await sa.run_autonomous_cycle(
        candidates=["0xAAA"], analyzer=_buy_analyzer(), anchor_sender=lambda record: "0xtxhash",
    )
    assert result["outcome"] == "ok"
    assert called["n"] == 0


# ── run_autonomous_cycle : HOLD / BUY / erreurs ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_hold_logs_without_sending_tx():
    sent = {"n": 0}

    def anchor_sender(record):
        sent["n"] += 1
        return "0xtxhash"

    result = await sa.run_autonomous_cycle(
        candidates=["0xAAA"], analyzer=_hold_analyzer, anchor_sender=anchor_sender,
    )
    assert result["outcome"] == "hold"
    assert sent["n"] == 0

    status = await sa.autonomous_status()
    assert status["cycles_total"] == 1
    assert status["tx_count"] == 0


@pytest.mark.asyncio
async def test_buy_sends_autonomous_tx_and_logs_kelly_size():
    captured = {}

    def anchor_sender(record):
        captured["record"] = record
        return "0xdeadbeef"

    result = await sa.run_autonomous_cycle(
        candidates=["0xAAA"], analyzer=_buy_analyzer(), anchor_sender=anchor_sender,
    )
    assert result["outcome"] == "ok"
    assert result["tx_hash"] == "0xdeadbeef"
    assert 0.0 <= result["kelly_fraction"] <= sa.KELLY_CAP
    assert result["kelly_size_usd"] == pytest.approx(sa.REHEARSAL_NOTIONAL_USD * result["kelly_fraction"])
    assert captured["record"]["contract"] == "0xAAA"

    status = await sa.autonomous_status()
    assert status["tx_count"] == 1
    assert status["last"]["decision"] == "BUY"
    assert status["last"]["tx_hash"] == "0xdeadbeef"


@pytest.mark.asyncio
async def test_swap_not_attempted_when_gate_off():
    result = await sa.run_autonomous_cycle(
        candidates=["0xAAA"], analyzer=_buy_analyzer(), anchor_sender=lambda r: "0xdeadbeef",
    )
    assert result["swap_tx"] is None
    assert result["swap_error"] is None


@pytest.mark.asyncio
async def test_swap_attempted_independently_on_buy_when_enabled(monkeypatch):
    monkeypatch.setenv("ARIA_SEPOLIA_SWAP_ENABLED", "1")
    called = {}

    def swap_sender():
        called["hit"] = True
        return {"deposit_tx": "0xd1", "approve_tx": "0xa1", "swap_tx": "0xs1"}

    result = await sa.run_autonomous_cycle(
        candidates=["0xAAA"], analyzer=_buy_analyzer(),
        anchor_sender=lambda r: "0xdeadbeef", swap_sender=swap_sender,
    )
    assert called.get("hit") is True
    assert result["swap_tx"] == "0xs1"
    assert result["swap_error"] is None
    assert result["tx_hash"] == "0xdeadbeef"  # ancrage indépendant, toujours présent

    status = await sa.autonomous_status()
    assert status["swap_tx_count"] == 1
    assert status["swap_error_count"] == 0


@pytest.mark.asyncio
async def test_swap_failure_does_not_erase_anchor_success(monkeypatch):
    monkeypatch.setenv("ARIA_SEPOLIA_SWAP_ENABLED", "1")

    def failing_swap_sender():
        raise RuntimeError("liquidité insuffisante sur la paire de test")

    result = await sa.run_autonomous_cycle(
        candidates=["0xAAA"], analyzer=_buy_analyzer(),
        anchor_sender=lambda r: "0xdeadbeef", swap_sender=failing_swap_sender,
    )
    assert result["outcome"] == "ok"
    assert result["tx_hash"] == "0xdeadbeef"
    assert result["swap_tx"] is None
    assert "liquidité insuffisante" in result["swap_error"]

    status = await sa.autonomous_status()
    assert status["swap_error_count"] == 1


@pytest.mark.asyncio
async def test_analyzer_exception_is_logged_not_raised():
    async def broken_analyzer(contract):
        raise RuntimeError("scan indisponible")

    result = await sa.run_autonomous_cycle(candidates=["0xAAA"], analyzer=broken_analyzer)
    assert result["outcome"] == "error"
    assert "scan indisponible" in result["error"]

    status = await sa.autonomous_status()
    assert status["error_count"] == 1


@pytest.mark.asyncio
async def test_anchor_failure_is_logged_not_raised():
    def failing_sender(record):
        raise RuntimeError("RPC indisponible")

    result = await sa.run_autonomous_cycle(
        candidates=["0xAAA"], analyzer=_buy_analyzer(), anchor_sender=failing_sender,
    )
    assert result["outcome"] == "error"
    assert result["tx_hash"] is None


# ── coupe-circuit local ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_circuit_breaker_trips_after_consecutive_errors():
    async def broken_analyzer(contract):
        raise RuntimeError("panne réseau")

    # Contrats distincts par cycle (le cooldown de 6h exclut un contrat déjà décidé,
    # même en erreur) — ce sont bien CONSECUTIVE_ERROR_CIRCUIT_BREAKER échecs consécutifs
    # dans le journal, peu importe le contrat, qui doivent déclencher le coupe-circuit.
    for i in range(sa.CONSECUTIVE_ERROR_CIRCUIT_BREAKER):
        result = await sa.run_autonomous_cycle(candidates=[f"0x{i:040x}"], analyzer=broken_analyzer)
        assert result["outcome"] == "error"

    tripped = await sa.run_autonomous_cycle(candidates=["0xNEW"], analyzer=broken_analyzer)
    assert tripped["outcome"] == "circuit_breaker_open"


@pytest.mark.asyncio
async def test_circuit_breaker_self_heals_next_cycle():
    async def broken_analyzer(contract):
        raise RuntimeError("panne réseau")

    for i in range(sa.CONSECUTIVE_ERROR_CIRCUIT_BREAKER):
        await sa.run_autonomous_cycle(candidates=[f"0x{i:040x}"], analyzer=broken_analyzer)
    tripped = await sa.run_autonomous_cycle(candidates=["0xNEW"], analyzer=broken_analyzer)
    assert tripped["outcome"] == "circuit_breaker_open"

    # Le cycle suivant repart propre (le SKIP journalisé n'est pas une "erreur" consécutive).
    recovered = await sa.run_autonomous_cycle(
        candidates=["0xRECOVERED"], analyzer=_buy_analyzer(), anchor_sender=lambda r: "0xok",
    )
    assert recovered["outcome"] == "ok"


# ── plafonds de bon sens (pas de risque financier — testnet) ────────────────────────────

@pytest.mark.asyncio
async def test_rate_cap_blocks_further_tx_same_day():
    for i in range(sa.MAX_AUTONOMOUS_TX_PER_DAY):
        result = await sa.run_autonomous_cycle(
            candidates=[f"0x{i:040x}"], analyzer=_buy_analyzer(), anchor_sender=lambda r: f"0xtx{i}",
        )
        assert result["outcome"] == "ok"

    capped = await sa.run_autonomous_cycle(
        candidates=["0xNEW"], analyzer=_buy_analyzer(), anchor_sender=lambda r: "0xshouldnotsend",
    )
    assert capped["outcome"] == "skipped_rate_cap"


@pytest.mark.asyncio
async def test_recently_decided_contract_is_skipped():
    await sa.run_autonomous_cycle(
        candidates=["0xAAA"], analyzer=_hold_analyzer, anchor_sender=lambda r: "0xtx",
    )
    # Même contrat proposé à nouveau immédiatement après -> exclu (cooldown), aucun autre candidat.
    result = await sa.run_autonomous_cycle(candidates=["0xAAA"], analyzer=_hold_analyzer)
    assert result["outcome"] == "skipped_no_candidate"


# ── statut public agrégé ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_autonomous_status_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_SEPOLIA_AUTONOMOUS_ENABLED", raising=False)
    status = await sa.autonomous_status()
    assert status["enabled"] is False
    assert status["cycles_total"] == 0
    assert status["last"] is None
