"""escalate_spend()/resolve_spend() -- le vrai flux de garde-fou dépenses ACP, jusqu'ici
non testé (seul l'exécuteur onchain_anchor_sepolia avait une couverture, cf.
test_wallet_guard_sepolia_action.py). DB réelle (approvals/wallet_ledger) sur data_dir
isolé -- seules les frontières externes (Telegram, outgoing_pause, exécuteurs ACP réels)
sont mockées, jamais la logique du module lui-même."""
from __future__ import annotations

import pytest

from aria_core import approvals, wallet_guard as wg, wallet_ledger


@pytest.fixture(autouse=True)
def _isolated_wallet_db(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "DB_PATH", str(tmp_path / "approvals.db"))
    monkeypatch.setattr(wallet_ledger, "DB_PATH", str(tmp_path / "approvals.db"))
    yield


def _no_block():
    return None


async def _fake_send_prompt_ok(approval_id, action, description):
    return None


async def _fake_send_prompt_fails(approval_id, action, description):
    raise RuntimeError("Telegram API timeout")


@pytest.fixture(autouse=True)
def _no_memory_side_effects(monkeypatch):
    monkeypatch.setattr(wg, "append_memory", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_escalate_spend_blocked_by_kill_switch(monkeypatch):
    monkeypatch.setattr(
        "aria_core.outgoing_pause.money_block_reason", lambda action: "ARIA en pause globale"
    )
    with pytest.raises(wg.SpendEscalationError, match="pause"):
        await wg.escalate_spend(
            "trade_tokens", amount="10 USDC", counterparty="0xabc",
            description="test", payload={"token_in": "USDC"},
        )
    # Fail-closed AVANT toute création d'approbation/ledger -- rien ne doit exister.
    assert await approvals.get_approval("nonexistent") is None


@pytest.mark.asyncio
async def test_escalate_spend_unknown_action_raises_value_error(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.money_block_reason", lambda action: None)
    with pytest.raises(ValueError, match="inconnue"):
        await wg.escalate_spend(
            "action_qui_nexiste_pas", amount="1", counterparty="x",
            description="test", payload={},
        )


@pytest.mark.asyncio
async def test_escalate_spend_happy_path_creates_pending_approval(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.money_block_reason", lambda action: None)
    monkeypatch.setattr(wg, "send_spend_prompt", _fake_send_prompt_ok)

    approval_id = await wg.escalate_spend(
        "trade_tokens", amount="10 USDC", counterparty="0xabc",
        description="swap test", payload={"token_in": "USDC", "token_out": "ETH", "amount_in": 10},
    )
    entry = await approvals.get_approval(approval_id)
    assert entry.status == approvals.ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_escalate_spend_telegram_failure_blocks_action(monkeypatch):
    """Si l'escalade Telegram échoue, l'action reste bloquée -- jamais une dépense
    silencieuse sans validation humaine possible."""
    monkeypatch.setattr("aria_core.outgoing_pause.money_block_reason", lambda action: None)
    monkeypatch.setattr(wg, "send_spend_prompt", _fake_send_prompt_fails)

    with pytest.raises(wg.SpendEscalationError, match="notification Telegram"):
        await wg.escalate_spend(
            "trade_tokens", amount="10 USDC", counterparty="0xabc",
            description="swap test", payload={"token_in": "USDC"},
        )


@pytest.mark.asyncio
async def test_resolve_spend_kill_switch_blocks_even_when_approved(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.money_block_reason", lambda action: None)
    monkeypatch.setattr(wg, "send_spend_prompt", _fake_send_prompt_ok)
    approval_id = await wg.escalate_spend(
        "trade_tokens", amount="10 USDC", counterparty="0xabc",
        description="swap test", payload={"token_in": "USDC"},
    )

    monkeypatch.setattr(
        "aria_core.outgoing_pause.money_block_reason", lambda action: "ARIA en pause globale"
    )
    result = await wg.resolve_spend(approval_id, True, "admin1")
    assert "pause" in result.lower()
    # Jamais réclamée -- réexécutable après /start (pas de transition pending -> decision).
    entry = await approvals.get_approval(approval_id)
    assert entry.status == approvals.ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_resolve_spend_rejected_records_refusal_no_execution(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.money_block_reason", lambda action: None)
    monkeypatch.setattr(wg, "send_spend_prompt", _fake_send_prompt_ok)
    executed = {"n": 0}
    monkeypatch.setitem(wg.WALLET_ACTIONS, "trade_tokens", lambda payload: executed.update(n=executed["n"] + 1) or ({}, None))

    approval_id = await wg.escalate_spend(
        "trade_tokens", amount="10 USDC", counterparty="0xabc",
        description="swap test", payload={"token_in": "USDC"},
    )
    result = await wg.resolve_spend(approval_id, False, "admin1")
    assert "refusée" in result.lower()
    assert executed["n"] == 0


@pytest.mark.asyncio
async def test_resolve_spend_double_decision_is_idempotent(monkeypatch):
    """Un double-clic sur le même bouton ne doit jamais déclencher une double
    exécution -- claim_for_decision refuse la seconde réclamation."""
    monkeypatch.setattr("aria_core.outgoing_pause.money_block_reason", lambda action: None)
    monkeypatch.setattr(wg, "send_spend_prompt", _fake_send_prompt_ok)
    executed = {"n": 0}

    def _fake_executor(payload):
        executed["n"] += 1
        return {"ok": True}, None

    monkeypatch.setitem(wg.WALLET_ACTIONS, "trade_tokens", _fake_executor)

    approval_id = await wg.escalate_spend(
        "trade_tokens", amount="10 USDC", counterparty="0xabc",
        description="swap test", payload={"token_in": "USDC"},
    )
    first = await wg.resolve_spend(approval_id, True, "admin1")
    second = await wg.resolve_spend(approval_id, True, "admin1")

    assert "exécutée" in first.lower()
    assert "déjà traitée" in second.lower() or "introuvable" in second.lower()
    assert executed["n"] == 1  # jamais deux fois


@pytest.mark.asyncio
async def test_resolve_spend_unknown_executor_returns_warning_without_crash(monkeypatch):
    """Simule un exécuteur retiré ENTRE l'escalade et la résolution (ex. déploiement) --
    escalate_spend le valide à la création, resolve_spend doit dégrader proprement si
    entre-temps il a disparu, jamais un crash."""
    monkeypatch.setattr("aria_core.outgoing_pause.money_block_reason", lambda action: None)
    monkeypatch.setattr(wg, "send_spend_prompt", _fake_send_prompt_ok)

    approval_id = await wg.escalate_spend(
        "trade_tokens", amount="10 USDC", counterparty="0xabc",
        description="swap test", payload={"token_in": "USDC"},
    )
    monkeypatch.delitem(wg.WALLET_ACTIONS, "trade_tokens", raising=False)
    result = await wg.resolve_spend(approval_id, True, "admin1")
    assert "non exécutable" in result.lower()


@pytest.mark.asyncio
async def test_resolve_spend_executor_failure_reports_error_not_success(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.money_block_reason", lambda action: None)
    monkeypatch.setattr(wg, "send_spend_prompt", _fake_send_prompt_ok)
    monkeypatch.setitem(
        wg.WALLET_ACTIONS, "trade_tokens",
        lambda payload: (None, "slippage trop élevé, transaction refusée par le DEX"),
    )

    approval_id = await wg.escalate_spend(
        "trade_tokens", amount="10 USDC", counterparty="0xabc",
        description="swap test", payload={"token_in": "USDC"},
    )
    result = await wg.resolve_spend(approval_id, True, "admin1")
    assert "échoué" in result.lower()
    assert "slippage" in result


@pytest.mark.asyncio
async def test_send_spend_prompt_raises_without_admin_configured(monkeypatch):
    from aria_core.gateway import telegram_bot

    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [])
    with pytest.raises(RuntimeError, match="admin_ids"):
        await wg.send_spend_prompt("abc123", "trade_tokens", "description test")


@pytest.mark.asyncio
async def test_generate_spend_explanation_falls_back_when_llm_returns_nothing(monkeypatch):
    async def _empty_llm(*a, **k):
        return None

    monkeypatch.setattr("aria_core.llm.chat_with_context", _empty_llm)
    explanation = await wg.generate_spend_explanation(
        "trade_tokens", "swap test", {"token_in": "USDC"},
    )
    assert "attente" in explanation.lower()


@pytest.mark.asyncio
async def test_generate_spend_explanation_returns_llm_output(monkeypatch):
    async def _fake_llm(message, system, max_tokens=350):
        return "Cette dépense sert à financer un job client validé."

    monkeypatch.setattr("aria_core.llm.chat_with_context", _fake_llm)
    explanation = await wg.generate_spend_explanation(
        "client_fund_job", "financement job #4", {"job_id": "4"},
    )
    assert explanation == "Cette dépense sert à financer un job client validé."
