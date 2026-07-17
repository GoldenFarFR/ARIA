"""x402_executor.fetch_paid_resource -- cascade fail-closed complete avant toute
signature (#202, 16/07). Aucun appel reseau reel : http_fetch_fn/balance_fn/pay_fn
toujours des fakes injectes, meme patron que test_agent_wallet_pilot.py."""
from __future__ import annotations

import json

import pytest

from aria_core import x402_budget as budget
from aria_core import x402_executor as executor
from aria_core.x402_executor import HttpResult


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "DB_PATH", str(tmp_path / "x402_budget_test.db"))
    yield


@pytest.fixture(autouse=True)
def _not_paused(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    yield


def _payment_required_body(*, amount="10000", asset="USDC", network="base") -> bytes:
    return json.dumps({
        "x402Version": 1,
        "accepts": [{"asset": asset, "amount": amount, "network": network, "payTo": "0xrecipient"}],
    }).encode("utf-8")


async def _never_called_balance():
    raise AssertionError("balance_fn ne doit pas etre appele")


async def _never_called_pay(requirement):
    raise AssertionError("pay_fn ne doit pas etre appele")


@pytest.mark.asyncio
async def test_non_402_response_passes_through_without_logging_spend():
    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=200, body=b"ok")

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=_never_called_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "ok"
    assert result.http_status == 200
    assert result.body == b"ok"
    assert (await budget.list_spends()) == []


@pytest.mark.asyncio
async def test_initial_request_failure_returns_failed_without_logging():
    async def fake_fetch(url, *, method="GET", headers=None):
        raise RuntimeError("dns down")

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=_never_called_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "failed"
    assert "requête initiale" in result.reason
    assert (await budget.list_spends()) == []


@pytest.mark.asyncio
async def test_killswitch_blocks_before_reading_payment_requirement(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)

    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=402, body=b"not even valid json -- never parsed")

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=_never_called_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "blocked"
    spends = await budget.list_spends()
    assert len(spends) == 1
    assert spends[0]["status"] == "blocked"


@pytest.mark.asyncio
async def test_malformed_402_body_blocked():
    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=402, body=b"not json")

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=_never_called_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "blocked"
    assert "illisible" in result.reason


@pytest.mark.asyncio
async def test_non_usdc_asset_blocked():
    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=402, body=_payment_required_body(asset="DAI"))

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=_never_called_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "blocked"
    assert "actif non supporté" in result.reason


@pytest.mark.asyncio
async def test_disallowed_network_blocked():
    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=402, body=_payment_required_body(network="ethereum"))

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=_never_called_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "blocked"
    assert "réseau non autorisé" in result.reason


@pytest.mark.asyncio
async def test_over_weekly_cap_blocked():
    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=402, body=_payment_required_body(amount="6000000"))  # 6$

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=_never_called_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "blocked"
    assert "plafond hebdomadaire" in result.reason


@pytest.mark.asyncio
async def test_balance_fn_exception_blocked_fail_closed():
    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=402, body=_payment_required_body())

    async def raising_balance():
        raise RuntimeError("cdp down")

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=raising_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "blocked"
    assert "solde réel indisponible" in result.reason


@pytest.mark.asyncio
async def test_balance_fn_none_blocked_fail_closed():
    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=402, body=_payment_required_body())

    async def none_balance():
        return None

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=none_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "blocked"
    assert "balance_fn a renvoyé None" in result.reason


@pytest.mark.asyncio
async def test_insufficient_balance_blocked():
    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=402, body=_payment_required_body(amount="10000"))  # 0.01$

    async def low_balance():
        return 0.001

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=low_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "blocked"
    assert "solde réel" in result.reason


@pytest.mark.asyncio
async def test_pay_fn_failure_recorded_as_failed():
    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=402, body=_payment_required_body())

    async def sufficient_balance():
        return 5.0

    async def failing_pay(requirement):
        raise RuntimeError("signature refusée")

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=sufficient_balance,
        pay_fn=failing_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "failed"
    spends = await budget.list_spends()
    assert spends[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_still_402_after_payment_recorded_as_failed():
    calls = []

    async def fake_fetch(url, *, method="GET", headers=None):
        calls.append(headers)
        return HttpResult(status_code=402, body=_payment_required_body())

    async def sufficient_balance():
        return 5.0

    async def working_pay(requirement):
        return "base64-payment-header"

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=sufficient_balance,
        pay_fn=working_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "failed"
    assert "toujours 402" in result.reason
    assert len(calls) == 2  # requete initiale + retentative payee
    assert calls[1] == {executor.X_PAYMENT_HEADER: "base64-payment-header"}
    spends = await budget.list_spends()
    assert spends[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_successful_payment_returns_ok_and_records_spend():
    async def fake_fetch(url, *, method="GET", headers=None):
        if headers and executor.X_PAYMENT_HEADER in headers:
            return HttpResult(status_code=200, body=b"paid content")
        return HttpResult(status_code=402, body=_payment_required_body(amount="10000"))

    async def sufficient_balance():
        return 5.0

    async def working_pay(requirement):
        return "base64-payment-header"

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="premium-data", provider="acme",
        balance_fn=sufficient_balance, pay_fn=working_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "ok"
    assert result.amount_usd == 0.01
    assert result.body == b"paid content"
    spends = await budget.list_spends()
    assert len(spends) == 1
    assert spends[0]["status"] == "ok"
    assert spends[0]["resource"] == "premium-data"
    assert spends[0]["provider"] == "acme"
    assert spends[0]["amount_usd"] == 0.01
    # 17/07 -- pay_to journalisé (corrélation agent_wallet_monitor.py)
    assert spends[0]["pay_to"] == "0xrecipient"


def _real_cybercentry_402_body() -> bytes:
    """Corps 402 RÉEL capturé le 17/07 contre https://x402-cybercentry-wallet-
    verification.up.railway.app/verify (facilitator Coinbase CDP officiel) --
    non un exemple inventé. ``maxAmountRequired`` (pas ``amount``), ``asset`` =
    adresse de contrat USDC sur Base (pas la chaîne "USDC")."""
    return json.dumps({
        "error": "X-PAYMENT header is required",
        "accepts": [{
            "scheme": "exact", "network": "base", "maxAmountRequired": "20000",
            "resource": "https://x402-cybercentry-wallet-verification.up.railway.app/verify",
            "description": "Verify wallet addresses for sanctions, fraud, and risk using "
                            "Cybercentry Wallet Verification (CWV) API",
            "mimeType": "application/json",
            "payTo": "0xfEE13309251B632317ea2d475d6ABa7E7E0219e6",
            "maxTimeoutSeconds": 300,
            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "outputSchema": {"input": {"type": "http", "method": "GET"}, "output": {"type": "object"}},
            "extra": {"name": "USD Coin", "version": "2"},
        }],
        "x402Version": 1,
    }).encode("utf-8")


@pytest.mark.asyncio
async def test_real_cybercentry_402_schema_parsed_correctly(monkeypatch):
    """Bug réel corrigé le 17/07 : le schéma x402 v1 RÉEL (maxAmountRequired,
    asset = adresse de contrat) faisait échouer CHAQUE appel réel avec l'ancien
    parsing (fail-closed, donc sans risque, mais jamais fonctionnel). Verrouille
    le vrai corps capturé contre le vrai facilitator, pas un exemple inventé."""
    async def fake_fetch(url, *, method="GET", headers=None):
        if headers and executor.X_PAYMENT_HEADER in headers:
            return HttpResult(status_code=200, body=b'{"risk": "low"}')
        return HttpResult(status_code=402, body=_real_cybercentry_402_body())

    async def sufficient_balance():
        return 1.0

    async def working_pay(requirement):
        return "base64-payment-header"

    result = await executor.fetch_paid_resource(
        "https://x402-cybercentry-wallet-verification.up.railway.app/verify",
        resource="wallet-verification", provider="cybercentry",
        balance_fn=sufficient_balance, pay_fn=working_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "ok"
    assert result.amount_usd == 0.02
    assert result.body == b'{"risk": "low"}'


@pytest.mark.asyncio
async def test_pay_fn_never_called_when_balance_insufficient():
    """Verifie explicitement l'ordre : balance_fn avant pay_fn -- jamais de
    signature tentee sur un solde qu'on sait deja insuffisant."""
    async def fake_fetch(url, *, method="GET", headers=None):
        return HttpResult(status_code=402, body=_payment_required_body(amount="10000000"))  # 10$

    async def low_balance():
        return 1.0

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=low_balance,
        pay_fn=_never_called_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_eip155_network_form_accepted():
    async def fake_fetch(url, *, method="GET", headers=None):
        if headers and executor.X_PAYMENT_HEADER in headers:
            return HttpResult(status_code=200, body=b"paid")
        return HttpResult(status_code=402, body=_payment_required_body(network="eip155:8453"))

    async def sufficient_balance():
        return 5.0

    async def working_pay(requirement):
        return "hdr"

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="test", balance_fn=sufficient_balance,
        pay_fn=working_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "ok"
