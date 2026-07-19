"""x402_executor.fetch_paid_resource -- cascade fail-closed complete avant toute
signature (#202, 16/07). Aucun appel reseau reel : http_fetch_fn/balance_fn/pay_fn
toujours des fakes injectes, meme patron que test_agent_wallet_pilot.py."""
from __future__ import annotations

import base64
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


@pytest.mark.asyncio
async def test_contract_and_token_symbol_recorded_on_success_and_when_blocked():
    """19/07, #143 -- transmis tel quel jusqu'au journal, sur le succès ET sur un
    blocage (ex. plafond hebdo dépassé) -- un paiement REFUSÉ pour un token doit
    rester tout aussi traçable qu'un paiement réussi."""
    async def fake_fetch(url, *, method="GET", headers=None):
        if headers and executor.X_PAYMENT_HEADER in headers:
            return HttpResult(status_code=200, body=b"paid content")
        return HttpResult(status_code=402, body=_payment_required_body(amount="10000"))

    async def sufficient_balance():
        return 5.0

    async def working_pay(requirement):
        return "base64-payment-header"

    await executor.fetch_paid_resource(
        "https://example.com/data", resource="premium-data", provider="acme",
        balance_fn=sufficient_balance, pay_fn=working_pay, http_fetch_fn=fake_fetch,
        contract="0x" + "b" * 40, token_symbol="GIZA",
    )
    spends = await budget.list_spends()
    assert spends[0]["contract"] == "0x" + "b" * 40
    assert spends[0]["token_symbol"] == "GIZA"

    async def unaffordable_balance():
        return 0.0

    await executor.fetch_paid_resource(
        "https://example.com/data2", resource="premium-data", provider="acme",
        balance_fn=unaffordable_balance, pay_fn=working_pay, http_fetch_fn=fake_fetch,
        contract="0x" + "c" * 40, token_symbol="MCADE",
    )
    spends = await budget.list_spends()
    assert spends[0]["status"] == "blocked"
    assert spends[0]["contract"] == "0x" + "c" * 40
    assert spends[0]["token_symbol"] == "MCADE"


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


def _payment_required_header(*, amount="50000", pay_to="0xheaderpayto") -> str:
    """Corps x402 encodé en base64, forme réelle capturée le 17/07 contre
    macro.lonestaroracle.xyz et x402.ottoai.services (deux fournisseurs réels
    du catalogue x402 Bazaar) -- le corps JSON lui-même est vide/custom, l'offre
    complète vit dans ce header ``payment-required``."""
    payload = json.dumps({
        "x402Version": 2,
        "accepts": [{
            "scheme": "exact", "network": "eip155:8453",
            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "amount": amount, "payTo": pay_to, "maxTimeoutSeconds": 300,
        }],
    }).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


@pytest.mark.asyncio
async def test_payment_offer_read_from_header_when_body_empty(monkeypatch):
    """Bug réel corrigé le 17/07 (trouvé en testant 3 fournisseurs réels du
    catalogue x402 Bazaar) : plusieurs fournisseurs ne mettent PAS l'offre dans
    le corps JSON (souvent ``{}`` ou un format custom) mais dans le header de
    réponse ``payment-required``, en base64 -- sans ce correctif, chaque appel
    réel contre ces fournisseurs échouait avec "corps 402 illisible/mal formé"."""
    async def fake_fetch(url, *, method="GET", headers=None):
        # 19/07 -- l'offre vient du header -> x402Version=2 -> la requête payée est
        # envoyée sous PAYMENT-SIGNATURE, jamais X-PAYMENT (v1 legacy uniquement).
        if headers and executor.PAYMENT_SIGNATURE_HEADER in headers:
            return HttpResult(status_code=200, body=b'{"macro": "data"}')
        return HttpResult(
            status_code=402, body=b"{}",
            headers={"payment-required": _payment_required_header(amount="50000", pay_to="0xmacro")},
        )

    async def sufficient_balance():
        return 1.0

    async def working_pay(requirement):
        assert requirement["payTo"] == "0xmacro"
        return "base64-payment-header"

    result = await executor.fetch_paid_resource(
        "https://macro.lonestaroracle.xyz/macro", resource="macro-us", provider="lonestaroracle",
        balance_fn=sufficient_balance, pay_fn=working_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "ok"
    assert result.amount_usd == 0.05
    assert result.body == b'{"macro": "data"}'


@pytest.mark.asyncio
async def test_requirement_normalized_with_max_amount_required_and_resource(monkeypatch):
    """Bug réel corrigé le 17/07 : le SDK x402 officiel (PaymentRequiredV1) exige
    maxAmountRequired ET resource DANS chaque objet accepts[0] pour signer --
    le fil x402 v2 réel (macro.lonestaroracle.xyz, vérifié en direct) n'envoie
    que "amount", sans "resource" à ce niveau. pay_fn doit recevoir les deux."""
    async def fake_fetch(url, *, method="GET", headers=None):
        if headers and executor.X_PAYMENT_HEADER in headers:
            return HttpResult(status_code=200, body=b"ok")
        return HttpResult(
            status_code=402, body=b"{}",
            headers={"payment-required": _payment_required_header(amount="50000", pay_to="0xmacro")},
        )

    async def sufficient_balance():
        return 1.0

    captured = {}

    async def working_pay(requirement):
        captured.update(requirement)
        return "base64-payment-header"

    await executor.fetch_paid_resource(
        "https://macro.lonestaroracle.xyz/macro", resource="macro-us", provider="lonestaroracle",
        balance_fn=sufficient_balance, pay_fn=working_pay, http_fetch_fn=fake_fetch,
    )

    assert captured["maxAmountRequired"] == "50000"
    assert captured["resource"] == "https://macro.lonestaroracle.xyz/macro"
    # 17/07 -- bug réel : x402Version vit à la racine de l'enveloppe (jamais dans
    # accepts[0]) -- sans le réinjecter, x402_cdp_signer.py retombait sur son
    # défaut (1) et routait la signature vers le mauvais schéma du SDK (V1, qui
    # ne connaît pas le format réseau CAIP-2 "eip155:8453" des offres v2 réelles).
    assert captured["x402Version"] == 2
    # 19/07 -- bug réel trouvé en testant 2 fournisseurs v2 réels (lionx402,
    # sociavault) : le SDK exige le header BRUT pour décoder une offre v2 (son
    # repli "corps synthétique" n'accepte que x402Version==1) -- le header original
    # doit être transporté jusqu'à pay_fn, jamais perdu après le parsing.
    assert captured["_raw_v2_header"] == _payment_required_header(amount="50000", pay_to="0xmacro")


@pytest.mark.asyncio
async def test_raw_v2_header_absent_when_offer_comes_from_body():
    """Chemin V1 (corps JSON, ex. Cybercentry) -- jamais de _raw_v2_header, pour ne
    jamais faire dévier x402_cdp_signer.py vers le chemin v2 sur un fournisseur v1."""
    body_offer = _payment_required_body(amount="10000", asset="USDC")

    async def fake_fetch(url, *, method="GET", headers=None):
        if headers and executor.X_PAYMENT_HEADER in headers:
            return HttpResult(status_code=200, body=b"ok")
        return HttpResult(status_code=402, body=body_offer)

    async def sufficient_balance():
        return 1.0

    captured = {}

    async def working_pay(requirement):
        captured.update(requirement)
        return "base64-payment-header"

    await executor.fetch_paid_resource(
        "https://cybercentry.example/verify", resource="wallet-verification", provider="cybercentry",
        balance_fn=sufficient_balance, pay_fn=working_pay, http_fetch_fn=fake_fetch,
    )
    assert "_raw_v2_header" not in captured


@pytest.mark.asyncio
async def test_v2_offer_sends_payment_under_payment_signature_header_not_x_payment():
    """19/07 -- 2e bug réel trouvé sur le MÊME appel réel (lionx402) juste après le
    précédent : le protocole v2 attend le paiement réglé sous "PAYMENT-SIGNATURE"
    (confirmé dans x402/http/constants.py du SDK officiel installé, "X-PAYMENT" y est
    explicitement commenté "V1 legacy") -- envoyer toujours X-PAYMENT faisait échouer
    la requête payée sur tout fournisseur v2, même après une signature réussie."""
    async def fake_fetch(url, *, method="GET", headers=None):
        if headers and executor.PAYMENT_SIGNATURE_HEADER in headers:
            assert executor.X_PAYMENT_HEADER not in headers
            return HttpResult(status_code=200, body=b"ok")
        return HttpResult(
            status_code=402, body=b"{}",
            headers={"payment-required": _payment_required_header(amount="50000", pay_to="0xv2")},
        )

    async def sufficient_balance():
        return 1.0

    async def working_pay(requirement):
        return "signed-payload"

    result = await executor.fetch_paid_resource(
        "https://v2provider.example/data", resource="v2-test", provider="v2provider",
        balance_fn=sufficient_balance, pay_fn=working_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_v1_offer_still_sends_payment_under_x_payment_header():
    """Comportement HISTORIQUE inchangé pour un fournisseur v1 (ex. Cybercentry) --
    jamais de régression sur le header déjà validé en conditions réelles."""
    body_offer = _payment_required_body(amount="10000", asset="USDC")

    async def fake_fetch(url, *, method="GET", headers=None):
        if headers and executor.X_PAYMENT_HEADER in headers:
            assert executor.PAYMENT_SIGNATURE_HEADER not in headers
            return HttpResult(status_code=200, body=b"ok")
        return HttpResult(status_code=402, body=body_offer)

    async def sufficient_balance():
        return 1.0

    async def working_pay(requirement):
        return "signed-payload"

    result = await executor.fetch_paid_resource(
        "https://cybercentry.example/verify", resource="wallet-verification", provider="cybercentry",
        balance_fn=sufficient_balance, pay_fn=working_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_body_accepts_still_preferred_over_header_when_both_present():
    """Le corps reste tenté en premier -- ne jamais régresser un fournisseur
    (ex. Cybercentry) qui utilise déjà correctement le corps."""
    body_offer = _payment_required_body(amount="10000", asset="USDC")

    async def fake_fetch(url, *, method="GET", headers=None):
        if headers and executor.X_PAYMENT_HEADER in headers:
            return HttpResult(status_code=200, body=b"ok")
        return HttpResult(
            status_code=402, body=body_offer,
            headers={"payment-required": _payment_required_header(amount="999999", pay_to="0xshouldnotbeused")},
        )

    async def sufficient_balance():
        return 5.0

    captured = {}

    async def working_pay(requirement):
        captured["payTo"] = requirement.get("payTo")
        return "base64-payment-header"

    result = await executor.fetch_paid_resource(
        "https://example.com/data", resource="r", provider="p",
        balance_fn=sufficient_balance, pay_fn=working_pay, http_fetch_fn=fake_fetch,
    )
    assert result.status == "ok"
    assert result.amount_usd == 0.01  # celui du corps, pas 0.999999 du header
    assert captured["payTo"] == "0xrecipient"  # celui du corps


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
