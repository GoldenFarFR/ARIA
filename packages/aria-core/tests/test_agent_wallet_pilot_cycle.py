"""Boucle de décision autonome du pilote agent-wallet réel (18/07, "option 2" --
ARIA décide ET exécute SEULE). Teste l'ORCHESTRATION uniquement (gate, position
en cours, sizing, sourcing, cooldown) -- attempt_swap est mocké en bloc, ses
propres garde-fous (plafond/kill-switch/slippage) sont déjà couverts par
test_agent_wallet_pilot.py, jamais retesté ici en double."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_pilot_cycle as cycle


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    """Gate activé par défaut dans ce fichier -- les tests qui veulent le
    comportement OFF le redéfinissent explicitement."""
    monkeypatch.setattr("aria_core.agent_wallet_pilot.agent_wallet_pilot_enabled", lambda: True)
    yield


def _summary(*, other_tokens=(), usdc_usd=1.0, wallet_address="0xAgent"):
    return {
        "wallet_address": wallet_address, "chain": "base",
        "usdc_usd": usdc_usd, "eth": 0.001, "other_tokens": list(other_tokens),
    }


def _hold(reason="no_entry_signal"):
    return {"action": "HOLD", "chain": "base", "hold_reason": reason}


def _buy(contract="0xcand", symbol="CAND"):
    return {
        "action": "BUY", "chain": "base", "symbol": symbol, "price": 1.0,
        "target": 2.0, "invalidation": 0.5, "rr": 2.0, "align_score": 2,
    }


@pytest.mark.asyncio
async def test_disabled_when_gate_off(monkeypatch):
    monkeypatch.setattr("aria_core.agent_wallet_pilot.agent_wallet_pilot_enabled", lambda: False)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "disabled"}


@pytest.mark.asyncio
async def test_position_open_when_other_tokens_held(monkeypatch):
    async def fake_summary():
        return _summary(other_tokens=[{"symbol": "SOMECOIN", "amount": 100.0}])

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "position_open", "held": ["SOMECOIN"]}


@pytest.mark.asyncio
async def test_balance_unavailable_when_other_tokens_none(monkeypatch):
    async def fake_summary():
        return _summary() | {"other_tokens": None}

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "balance_unavailable"}


@pytest.mark.asyncio
async def test_balance_summary_exception_is_fail_closed(monkeypatch):
    async def fake_summary():
        raise RuntimeError("CDP indisponible")

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result["outcome"] == "balance_unavailable"
    assert "CDP indisponible" in result["reason"]


@pytest.mark.asyncio
async def test_no_balance_when_sizing_returns_none(monkeypatch):
    async def fake_summary():
        return _summary(usdc_usd=0.0)

    async def fake_size(*, balance_fn):
        return None

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "no_balance"}


@pytest.mark.asyncio
async def test_no_candidate_when_sourcing_empty(monkeypatch):
    async def fake_summary():
        return _summary()

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        assert chains == ("base",)
        return []

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "no_candidate", "checked": 0}


@pytest.mark.asyncio
async def test_sourcing_exception_is_handled(monkeypatch):
    async def fake_summary():
        return _summary()

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        raise RuntimeError("GeckoTerminal indisponible")

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result["outcome"] == "sourcing_failed"


@pytest.mark.asyncio
async def test_no_candidate_when_all_hold(monkeypatch):
    async def fake_summary():
        return _summary()

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        return [{"contract": "0xa", "chain": "base"}, {"contract": "0xb", "chain": "base"}]

    async def fake_evaluate(contract, chain):
        return _hold()

    async def fake_cooldown(contract, *, within_minutes, structural_within_minutes=None):
        return False

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    monkeypatch.setattr("aria_core.momentum_entry.evaluate_momentum_entry", fake_evaluate)
    monkeypatch.setattr("aria_core.agent_wallet_log.recent_failed_swap", fake_cooldown)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "no_candidate", "checked": 2}


@pytest.mark.asyncio
async def test_evaluate_exception_on_one_candidate_does_not_stop_cycle(monkeypatch):
    async def fake_summary():
        return _summary()

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        return [{"contract": "0xbroken", "chain": "base"}, {"contract": "0xgood", "chain": "base"}]

    async def fake_evaluate(contract, chain):
        if contract == "0xbroken":
            raise RuntimeError("scan cassé")
        return _buy(contract=contract)

    async def fake_cooldown(contract, *, within_minutes, structural_within_minutes=None):
        return False

    captured = {}

    async def fake_attempt_swap(**kwargs):
        captured.update(kwargs)
        from aria_core.agent_wallet_pilot import SwapAttemptResult
        return SwapAttemptResult(status="ok", tx_hash="0xreal", amount_out=1.0)

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    monkeypatch.setattr("aria_core.momentum_entry.evaluate_momentum_entry", fake_evaluate)
    monkeypatch.setattr("aria_core.agent_wallet_log.recent_failed_swap", fake_cooldown)
    monkeypatch.setattr("aria_core.agent_wallet_pilot.attempt_swap", fake_attempt_swap)

    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result["outcome"] == "ok"
    assert captured["token_out"] == "0xgood"


@pytest.mark.asyncio
async def test_swap_attempted_on_buy_signal_with_correct_args(monkeypatch):
    async def fake_summary():
        return _summary(wallet_address="0xAgentReal")

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        return [{"contract": "0xCAND", "chain": "base"}]

    async def fake_evaluate(contract, chain):
        assert chain == "base"
        return _buy(contract=contract)

    async def fake_cooldown(contract, *, within_minutes, structural_within_minutes=None):
        return False

    captured = {}

    async def fake_attempt_swap(**kwargs):
        captured.update(kwargs)
        from aria_core.agent_wallet_pilot import SwapAttemptResult
        return SwapAttemptResult(status="ok", tx_hash="0xreal", amount_out=1.0)

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    monkeypatch.setattr("aria_core.momentum_entry.evaluate_momentum_entry", fake_evaluate)
    monkeypatch.setattr("aria_core.agent_wallet_log.recent_failed_swap", fake_cooldown)
    monkeypatch.setattr("aria_core.agent_wallet_pilot.attempt_swap", fake_attempt_swap)

    result = await cycle.run_agent_wallet_pilot_cycle()

    assert result["outcome"] == "ok"
    assert result["contract"] == "0xcand"
    assert result["amount_usd"] == 0.03
    from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS
    assert captured["chain"] == "base"
    assert captured["token_in"] == USDC_BASE_ADDRESS
    assert captured["token_out"] == "0xcand"
    assert captured["amount_in_usd"] == 0.03
    assert captured["wallet_address"] == "0xAgentReal"


@pytest.mark.asyncio
async def test_cooldown_skips_recently_failed_candidate(monkeypatch):
    async def fake_summary():
        return _summary()

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        return [{"contract": "0xoncooldown", "chain": "base"}]

    evaluate_called = False

    async def fake_evaluate(contract, chain):
        nonlocal evaluate_called
        evaluate_called = True
        return _buy(contract=contract)

    async def fake_cooldown(contract, *, within_minutes, structural_within_minutes=None):
        assert contract == "0xoncooldown"
        assert within_minutes == cycle.SWAP_FAILURE_COOLDOWN_MINUTES
        # 19/07 -- le cooldown structurel étendu (incident URANUS) doit être transmis
        # à chaque appel, pas seulement documenté en constante inutilisée.
        assert structural_within_minutes == cycle.STRUCTURAL_SWAP_FAILURE_COOLDOWN_MINUTES
        return True

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    monkeypatch.setattr("aria_core.momentum_entry.evaluate_momentum_entry", fake_evaluate)
    monkeypatch.setattr("aria_core.agent_wallet_log.recent_failed_swap", fake_cooldown)

    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "no_candidate", "checked": 1}
    assert evaluate_called is False, "un candidat en cooldown ne doit jamais être évalué"


@pytest.mark.asyncio
async def test_respects_max_candidates_per_cycle(monkeypatch):
    async def fake_summary():
        return _summary()

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        return [{"contract": f"0x{i}", "chain": "base"} for i in range(20)]

    checked_contracts = []

    async def fake_evaluate(contract, chain):
        checked_contracts.append(contract)
        return _hold()

    async def fake_cooldown(contract, *, within_minutes, structural_within_minutes=None):
        return False

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    monkeypatch.setattr("aria_core.momentum_entry.evaluate_momentum_entry", fake_evaluate)
    monkeypatch.setattr("aria_core.agent_wallet_log.recent_failed_swap", fake_cooldown)

    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result["checked"] == cycle.MAX_CANDIDATES_PER_CYCLE
    assert len(checked_contracts) == cycle.MAX_CANDIDATES_PER_CYCLE


# ── format_agent_wallet_swap_alert ───────────────────────────────────────────

def test_alert_ok_marked_real_money_never_simulation():
    text = cycle.format_agent_wallet_swap_alert(
        {"outcome": "ok", "symbol": "CAND", "contract": "0xcand", "amount_usd": 0.03, "tx_hash": "0xreal"}
    )
    assert "ARGENT RÉEL" in text
    assert "SIMULATION" not in text
    assert "0xreal" in text


def test_alert_failed_includes_reason():
    text = cycle.format_agent_wallet_swap_alert(
        {"outcome": "failed", "symbol": "CAND", "contract": "0xcand", "reason": "slippage dépassé"}
    )
    assert "ÉCHOUÉ" in text
    assert "slippage dépassé" in text


def test_alert_blocked_includes_reason():
    text = cycle.format_agent_wallet_swap_alert({"outcome": "blocked", "reason": "kill-switch actif"})
    assert "bloqué" in text
    assert "kill-switch actif" in text


@pytest.mark.parametrize("outcome", ["disabled", "no_candidate", "position_open"])
def test_alert_empty_for_non_notable_outcomes(outcome):
    assert cycle.format_agent_wallet_swap_alert({"outcome": outcome}) == ""
