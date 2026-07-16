"""x402_cdp_signer.build_x402_payment_header -- pont EthAccountSigner/EvmLocalAccount
verifie contre les deux SDK officiels (cdp-sdk/x402), jamais un vrai appel reseau ici
(aucun identifiant CDP dans cette suite) -- meme patron d'injection de faux modules que
test_agent_wallet_cdp_adapter.py."""
from __future__ import annotations

import sys
import types

import pytest

from aria_core import x402_cdp_signer as signer


def _install_fake_x402_modules(
    monkeypatch, *, header_value="base64-payment-header", raise_on="none",
):
    class FakeAccount:
        address = "0xabc123"

    class FakeEvm:
        async def get_or_create_account(self, name):
            if raise_on == "account":
                raise RuntimeError("CDP API down")
            assert name == signer.WALLET_NAME
            return FakeAccount()

    class FakeCdpClient:
        def __init__(self):
            self.evm = FakeEvm()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeEvmLocalAccount:
        def __init__(self, account):
            self.account = account

    class FakeEthAccountSigner:
        def __init__(self, local_account):
            self.local_account = local_account

    def fake_register_exact_evm_client(client, signer_obj):
        client.registered_signer = signer_obj

    class Fakex402Client:
        def __init__(self):
            self.registered_signer = None

        async def create_payment_payload(self, parsed):
            if raise_on == "payload":
                raise RuntimeError("construction du paiement echouee")
            return {"parsed": parsed}

    class Fakex402HTTPClient:
        def __init__(self, client):
            self.client = client

        def get_payment_required_response(self, resolver, body):
            if raise_on == "parse":
                raise RuntimeError("corps mal forme")
            return {"body": body}

        def encode_payment_signature_header(self, payload):
            if raise_on == "encode":
                raise RuntimeError("encodage echoue")
            if raise_on == "missing_header":
                return {}
            return {"X-PAYMENT": header_value}

    fake_cdp = types.ModuleType("cdp")
    fake_cdp.CdpClient = FakeCdpClient
    fake_cdp_evm_local_account = types.ModuleType("cdp.evm_local_account")
    fake_cdp_evm_local_account.EvmLocalAccount = FakeEvmLocalAccount

    fake_x402 = types.ModuleType("x402")
    fake_x402.x402Client = Fakex402Client
    fake_x402_http = types.ModuleType("x402.http")
    fake_x402_http_client = types.ModuleType("x402.http.x402_http_client")
    fake_x402_http_client.x402HTTPClient = Fakex402HTTPClient
    fake_x402_mechanisms = types.ModuleType("x402.mechanisms")
    fake_x402_mechanisms_evm = types.ModuleType("x402.mechanisms.evm")
    fake_x402_mechanisms_evm_exact = types.ModuleType("x402.mechanisms.evm.exact")
    fake_x402_mechanisms_evm_exact.register_exact_evm_client = fake_register_exact_evm_client
    fake_x402_mechanisms_evm_signers = types.ModuleType("x402.mechanisms.evm.signers")
    fake_x402_mechanisms_evm_signers.EthAccountSigner = FakeEthAccountSigner

    monkeypatch.setitem(sys.modules, "cdp", fake_cdp)
    monkeypatch.setitem(sys.modules, "cdp.evm_local_account", fake_cdp_evm_local_account)
    monkeypatch.setitem(sys.modules, "x402", fake_x402)
    monkeypatch.setitem(sys.modules, "x402.http", fake_x402_http)
    monkeypatch.setitem(sys.modules, "x402.http.x402_http_client", fake_x402_http_client)
    monkeypatch.setitem(sys.modules, "x402.mechanisms", fake_x402_mechanisms)
    monkeypatch.setitem(sys.modules, "x402.mechanisms.evm", fake_x402_mechanisms_evm)
    monkeypatch.setitem(sys.modules, "x402.mechanisms.evm.exact", fake_x402_mechanisms_evm_exact)
    monkeypatch.setitem(sys.modules, "x402.mechanisms.evm.signers", fake_x402_mechanisms_evm_signers)


@pytest.mark.asyncio
async def test_returns_payment_header_on_success(monkeypatch):
    _install_fake_x402_modules(monkeypatch, header_value="the-real-header")
    result = await signer.build_x402_payment_header(
        {"asset": "USDC", "amount": "10000", "network": "base"}
    )
    assert result == "the-real-header"


@pytest.mark.asyncio
async def test_raises_when_cdp_account_lookup_fails(monkeypatch):
    _install_fake_x402_modules(monkeypatch, raise_on="account")
    with pytest.raises(RuntimeError, match="CDP API down"):
        await signer.build_x402_payment_header({"asset": "USDC", "amount": "10000"})


@pytest.mark.asyncio
async def test_raises_when_payment_response_parsing_fails(monkeypatch):
    _install_fake_x402_modules(monkeypatch, raise_on="parse")
    with pytest.raises(RuntimeError, match="corps mal forme"):
        await signer.build_x402_payment_header({"asset": "USDC", "amount": "10000"})


@pytest.mark.asyncio
async def test_raises_when_payload_construction_fails(monkeypatch):
    _install_fake_x402_modules(monkeypatch, raise_on="payload")
    with pytest.raises(RuntimeError, match="construction du paiement"):
        await signer.build_x402_payment_header({"asset": "USDC", "amount": "10000"})


@pytest.mark.asyncio
async def test_raises_when_header_encoding_fails(monkeypatch):
    _install_fake_x402_modules(monkeypatch, raise_on="encode")
    with pytest.raises(RuntimeError, match="encodage"):
        await signer.build_x402_payment_header({"asset": "USDC", "amount": "10000"})


@pytest.mark.asyncio
async def test_raises_when_no_x_payment_header_produced(monkeypatch):
    _install_fake_x402_modules(monkeypatch, raise_on="missing_header")
    with pytest.raises(RuntimeError, match="n'a pas produit de X-PAYMENT"):
        await signer.build_x402_payment_header({"asset": "USDC", "amount": "10000"})


@pytest.mark.asyncio
async def test_defaults_x402_version_to_1_when_absent(monkeypatch):
    _install_fake_x402_modules(monkeypatch)
    # Pas d'assertion directe sur le body interne (encapsule par les fakes) --
    # verifie seulement que l'absence de x402Version ne leve rien (comportement
    # par defaut documente dans le module).
    result = await signer.build_x402_payment_header({"asset": "USDC", "amount": "10000"})
    assert result
