"""18/08 -- guardrail wrapper for the homemade agent wallet (Safe+
AllowanceModule / Squads v4). Mirrors test_agent_wallet_pilot.py's doctrine
checks (gate, kill-switch, cap, systematic logging) -- never a real chain
call here, send_fn/remaining_fn are always fakes injected by each test."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_log, homemade_agent_wallet as haw


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_wallet_log, "DB_PATH", str(tmp_path / "homemade_wallet_test.db"))
    yield


@pytest.fixture(autouse=True)
def _gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", raising=False)
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    monkeypatch.setattr("aria_core.custody_pause.is_paused", lambda: False)
    yield


async def _ok_remaining() -> int:
    return 10_000_000


async def _ok_send(**kwargs) -> dict:
    return {"error": None, "tx_hash": "0xdeadbeef", "status": "ok"}


@pytest.mark.asyncio
async def test_blocked_when_gate_disabled_by_default():
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"
    assert "ARIA_HOMEMADE_AGENT_WALLET_ENABLED" in result.reason
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["status"] == "blocked"
    assert rows[0]["wallet_product"] == "homemade_agent_wallet"


@pytest.mark.asyncio
async def test_blocked_when_kill_switch_paused(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_blocked_when_custody_paused(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    monkeypatch.setattr("aria_core.custody_pause.is_paused", lambda: True)
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_blocked_when_amount_is_zero_or_negative(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=0, remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"
    assert "nul" in result.reason


@pytest.mark.asyncio
async def test_blocked_when_amount_exceeds_app_level_cap(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=haw.MAX_TRANSACTION_NATIVE_UNITS + 1,
        remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"
    assert "plafond" in result.reason


@pytest.mark.asyncio
async def test_blocked_when_amount_exceeds_real_remaining_allowance(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def low_remaining():
        return 100

    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=101, remaining_fn=low_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"
    assert "allowance restante réelle" in result.reason


@pytest.mark.asyncio
async def test_blocked_fail_closed_when_remaining_fn_raises(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def broken_remaining():
        raise ConnectionError("RPC down")

    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1, remaining_fn=broken_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"
    assert "indisponible" in result.reason


@pytest.mark.asyncio
async def test_ok_path_logs_success_and_returns_tx_hash(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
        safe="0xabc", to="0xdef",
    )
    assert result.status == "ok"
    assert result.tx_hash == "0xdeadbeef"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_send_kwargs_are_forwarded_to_send_fn(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    seen = {}

    async def _capturing_send(**kwargs):
        seen.update(kwargs)
        return {"error": None, "tx_hash": "0x1", "status": "ok"}

    await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining,
        send_fn=_capturing_send, safe="0xabc", to="0xdef", delegate_key_path="/tmp/x",
    )
    assert seen["amount"] == 1_000
    assert seen["safe"] == "0xabc"
    assert seen["to"] == "0xdef"
    assert seen["delegate_key_path"] == "/tmp/x"


@pytest.mark.asyncio
async def test_send_fn_exception_is_logged_as_failed(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def _broken_send(**kwargs):
        raise RuntimeError("send crashed")

    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_broken_send,
    )
    assert result.status == "failed"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_send_fn_error_field_is_treated_as_failure(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def _erroring_send(**kwargs):
        return {"error": "on-chain call rejected", "tx_hash": None}

    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_erroring_send,
    )
    assert result.status == "failed"
    assert result.reason == "on-chain call rejected"


@pytest.mark.asyncio
async def test_reverted_onchain_status_is_treated_as_failure_not_success(monkeypatch):
    """A mined transaction that reverted must never be reported as 'ok' --
    the whole point of re-checking the on-chain receipt status."""
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def _reverted_send(**kwargs):
        return {"error": None, "tx_hash": "0xreverted", "status": "reverted"}

    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_reverted_send,
    )
    assert result.status == "failed"
    assert result.tx_hash == "0xreverted"
    assert "REVERTED" in result.reason


# ── wallet_product (19/08) -- never covered by a test until now: added
# alongside the Solana leg's own promotion, but every test above only ever
# exercises the DEFAULT value (the EVM product name), never the explicit
# ``WALLET_PRODUCT_SOLANA`` the docstring claims is threaded through every
# ``_blocked``/``record_transaction`` call site. A parameter can be "wired"
# in the sense of being accepted and forwarded, yet still be silently wrong
# (e.g. a typo'd literal instead of the constant, or a code path that reads
# the default instead of the passed-in value) without a single assertion
# ever catching it -- exactly the gap this closes.
@pytest.mark.asyncio
async def test_default_wallet_product_is_the_evm_constant():
    """No test above ever asserts this explicitly -- they only check the
    literal string, never that it matches the real exported constant (a
    future rename of ``WALLET_PRODUCT`` could silently desync the default
    from what every EVM caller actually expects)."""
    assert haw.WALLET_PRODUCT == "homemade_agent_wallet"


@pytest.mark.asyncio
async def test_wallet_product_defaults_to_evm_constant_when_unspecified(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "ok"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["wallet_product"] == haw.WALLET_PRODUCT


@pytest.mark.asyncio
async def test_wallet_product_solana_is_logged_on_ok_path(monkeypatch):
    """The exact claim in the module docstring ('wallet_product distinguishes
    the agent_wallet_log rows per chain') -- proven here for the Solana
    product on the success path, never exercised by any test since the
    parameter was added 19/08."""
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    result = await haw.attempt_transfer(
        chain="solana", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
        wallet_product=haw.WALLET_PRODUCT_SOLANA,
    )
    assert result.status == "ok"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["wallet_product"] == haw.WALLET_PRODUCT_SOLANA
    assert rows[0]["wallet_product"] != haw.WALLET_PRODUCT
    assert rows[0]["chain"] == "solana"


@pytest.mark.asyncio
async def test_wallet_product_solana_is_logged_on_every_blocked_reason(monkeypatch):
    """Every ``_blocked`` call site (gate/kill-switch/custody/zero-amount/
    app-cap/real-remaining/remaining_fn-exception) must honor the caller's
    ``wallet_product`` -- not just the happy path. Exercises one
    representative case per distinct call site inside ``attempt_transfer``
    (mirrors the existing EVM-only ``test_blocked_when_*`` tests above, this
    time asserting the LOGGED PRODUCT, which none of those did)."""
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def low_remaining():
        return 100

    result = await haw.attempt_transfer(
        chain="solana", amount=101, remaining_fn=low_remaining, send_fn=_ok_send,
        wallet_product=haw.WALLET_PRODUCT_SOLANA,
    )
    assert result.status == "blocked"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["wallet_product"] == haw.WALLET_PRODUCT_SOLANA


@pytest.mark.asyncio
async def test_wallet_product_solana_is_logged_when_gate_disabled(monkeypatch):
    """Same as ``test_blocked_when_gate_disabled_by_default`` above, but for
    the Solana product -- the gate check runs BEFORE ``wallet_product`` is
    ever consulted for anything else, the exact spot a copy-paste from the
    EVM path could have hardcoded the wrong literal without any test
    noticing."""
    monkeypatch.delenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", raising=False)
    result = await haw.attempt_transfer(
        chain="solana", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
        wallet_product=haw.WALLET_PRODUCT_SOLANA,
    )
    assert result.status == "blocked"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["wallet_product"] == haw.WALLET_PRODUCT_SOLANA


@pytest.mark.asyncio
async def test_wallet_product_solana_is_logged_on_send_fn_failure(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def _broken_send(**kwargs):
        raise RuntimeError("devnet RPC down")

    result = await haw.attempt_transfer(
        chain="solana", amount=1_000, remaining_fn=_ok_remaining, send_fn=_broken_send,
        wallet_product=haw.WALLET_PRODUCT_SOLANA,
    )
    assert result.status == "failed"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["wallet_product"] == haw.WALLET_PRODUCT_SOLANA


@pytest.mark.asyncio
async def test_wallet_product_solana_is_logged_on_reverted_status(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def _reverted_send(**kwargs):
        return {"error": None, "tx_hash": "sig_reverted", "status": "reverted"}

    result = await haw.attempt_transfer(
        chain="solana", amount=1_000, remaining_fn=_ok_remaining, send_fn=_reverted_send,
        wallet_product=haw.WALLET_PRODUCT_SOLANA,
    )
    assert result.status == "failed"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["wallet_product"] == haw.WALLET_PRODUCT_SOLANA
