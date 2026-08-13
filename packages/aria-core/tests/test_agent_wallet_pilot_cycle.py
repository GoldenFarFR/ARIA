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


@pytest.fixture(autouse=True)
def _no_real_purchase_journal_lookup(monkeypatch):
    """13/08 -- ``list_aria_bought_tokens_still_held`` touche une vraie DB ;
    vide par défaut ici (jamais de token réputé acheté par ARIA) -- les tests
    qui veulent une position réelle le redéfinissent explicitement."""
    async def fake_held():
        return set()

    monkeypatch.setattr("aria_core.agent_wallet_log.list_aria_bought_tokens_still_held", fake_held)
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
async def test_position_open_when_held_token_is_in_aria_purchase_journal(monkeypatch):
    """13/08 -- le blocage ne se déclenche QUE pour un token qu'ARIA a
    réellement acheté (journal), jamais sur le seul solde brut du wallet."""
    async def fake_summary():
        return _summary(other_tokens=[{"symbol": "SOMECOIN", "address": "0xReal", "amount": 100.0}])

    async def fake_held():
        return {"0xreal"}

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_log.list_aria_bought_tokens_still_held", fake_held)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "position_open", "held": ["SOMECOIN"]}


@pytest.mark.asyncio
async def test_dust_attack_token_never_blocks_the_pilot(monkeypatch):
    """13/08 -- gap réel trouvé en prod (~1 mois de blocage silencieux) :
    un token reçu sans avoir jamais été acheté par ARIA (dust attack, ex.
    ``www.bairdrop.co``) ne doit JAMAIS compter comme une position ouverte,
    même avec une valeur affichée non nulle -- sinon un attaquant peut geler
    le pilote en envoyant un dust token à forte valeur affichée (DoS)."""
    async def fake_summary():
        return _summary(usdc_usd=100.0, other_tokens=[
            {"symbol": "www.bairdrop.co ✅", "address": "0xDust", "amount": 1.0, "value_usd": 50_000.0},
        ])

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        return []

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    result = await cycle.run_agent_wallet_pilot_cycle()
    # jamais "position_open" -- le cycle continue normalement (ici jusqu'à no_candidate)
    assert result == {"outcome": "no_candidate", "checked": 0}


@pytest.mark.asyncio
async def test_dust_attack_token_logs_a_non_blocking_alert(monkeypatch, caplog):
    """La visibilité (alerte log) reste utile même si ça ne bloque jamais."""
    import logging

    async def fake_summary():
        return _summary(usdc_usd=100.0, other_tokens=[
            {"symbol": "www.bairdrop.co ✅", "address": "0xDust", "amount": 1.0, "value_usd": 50_000.0},
        ])

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        return []

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    with caplog.at_level(logging.WARNING, logger="aria_core.agent_wallet_pilot_cycle"):
        await cycle.run_agent_wallet_pilot_cycle()
    assert any("0xDust" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_low_value_unrecognized_token_does_not_alert(monkeypatch, caplog):
    """Un dust token de valeur négligeable ne mérite même pas le log --
    évite le bruit sur les dizaines de spams sans valeur réelle."""
    import logging

    async def fake_summary():
        return _summary(usdc_usd=100.0, other_tokens=[
            {"symbol": "spam", "address": "0xTiny", "amount": 1.0, "value_usd": 0.01},
        ])

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        return []

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    with caplog.at_level(logging.WARNING, logger="aria_core.agent_wallet_pilot_cycle"):
        result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "no_candidate", "checked": 0}
    assert not any("0xTiny" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_mixed_real_position_and_dust_only_blocks_on_the_real_one(monkeypatch):
    """Un vrai token acheté ET un dust token en même temps : seul le premier
    déclenche position_open, le second n'apparaît jamais dans ``held``."""
    async def fake_summary():
        return _summary(other_tokens=[
            {"symbol": "REAL", "address": "0xReal", "amount": 10.0, "value_usd": 500.0},
            {"symbol": "dust", "address": "0xDust", "amount": 1.0, "value_usd": 50_000.0},
        ])

    async def fake_held():
        return {"0xreal"}

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_log.list_aria_bought_tokens_still_held", fake_held)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "position_open", "held": ["REAL"]}


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
async def test_security_unverifiable_returned_and_stops_the_cycle(monkeypatch):
    """03/08 -- distinct from a normal HOLD: real capital, surfaced
    immediately (return, not continue to the next candidate) rather than
    silently moving on."""
    async def fake_summary():
        return _summary()

    async def fake_size(*, balance_fn):
        return 0.03

    async def fake_discover(*, chains):
        return [{"contract": "0xa", "chain": "base"}, {"contract": "0xb", "chain": "base"}]

    async def fake_evaluate(contract, chain):
        return {
            "action": "HOLD", "chain": "base", "symbol": "TOK",
            "hold_reason": "holder_concentration_unverifiable",
        }

    async def fake_cooldown(contract, *, within_minutes, structural_within_minutes=None):
        return False

    monkeypatch.setattr(cycle, "get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_sizing.size_trade_usd", fake_size)
    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", fake_discover)
    monkeypatch.setattr("aria_core.momentum_entry.evaluate_momentum_entry", fake_evaluate)
    monkeypatch.setattr("aria_core.agent_wallet_log.recent_failed_swap", fake_cooldown)
    result = await cycle.run_agent_wallet_pilot_cycle()
    assert result == {"outcome": "security_unverifiable", "contract": "0xa", "symbol": "TOK"}


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


def test_alert_security_unverifiable_marked_real_money_and_mentions_symbol():
    text = cycle.format_agent_wallet_swap_alert(
        {"outcome": "security_unverifiable", "symbol": "TOK", "contract": "0xa"}
    )
    assert "ARGENT RÉEL" in text
    assert "TOK" in text
    assert "invérifiable" in text
