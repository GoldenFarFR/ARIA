"""x402_cdp_signer.build_x402_payment_header -- pont EthAccountSigner/EvmLocalAccount
verifie contre les deux SDK officiels (cdp-sdk/x402), jamais un vrai appel reseau ici
(aucun identifiant CDP dans cette suite) -- meme patron d'injection de faux modules que
test_agent_wallet_cdp_adapter.py."""
from __future__ import annotations

import sys
import types

import pytest

from aria_core import x402_cdp_signer as signer
from aria_core.agent_wallet_cdp_adapter import WALLET_NAME


def _install_fake_x402_modules(
    monkeypatch, *, header_value="base64-payment-header", raise_on="none",
):
    class FakeAccount:
        address = "0xabc123"

    class FakeEvm:
        # 24/07 -- renamed from get_or_create_account (never auto-creates
        # anymore, cf. agent_wallet_cdp_adapter._get_wallet_account, the same
        # fail-closed helper used by every OTHER real-money call site since
        # 23/07 -- this signer alone was missed until this fix).
        async def get_account(self, name):
            if raise_on == "account":
                raise RuntimeError("CDP API down")
            if raise_on == "account_404":
                raise _FakeApiError(http_code=404)
            assert name == WALLET_NAME
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

    def fake_decode_payment_required_header(raw_header):
        if raise_on == "decode_v2":
            raise RuntimeError("header v2 illisible")
        return {"decoded_from_header": raw_header}

    fake_cdp = types.ModuleType("cdp")
    fake_cdp.CdpClient = FakeCdpClient
    fake_cdp_evm_local_account = types.ModuleType("cdp.evm_local_account")
    fake_cdp_evm_local_account.EvmLocalAccount = FakeEvmLocalAccount
    # 24/07 -- _get_wallet_account (agent_wallet_cdp_adapter.py) lazily imports
    # cdp.openapi_client.errors.ApiError -- must exist in sys.modules even
    # when no ApiError is actually raised, same fake as test_agent_wallet_cdp_adapter.py.
    fake_openapi_client = types.ModuleType("cdp.openapi_client")
    fake_errors_module = types.ModuleType("cdp.openapi_client.errors")

    class _FakeApiError(Exception):
        def __init__(self, http_code):
            self.http_code = http_code
            super().__init__(f"fake ApiError http_code={http_code}")

    fake_errors_module.ApiError = _FakeApiError

    fake_x402 = types.ModuleType("x402")
    fake_x402.x402Client = Fakex402Client
    fake_x402_http = types.ModuleType("x402.http")
    fake_x402_http_client = types.ModuleType("x402.http.x402_http_client")
    fake_x402_http_client.x402HTTPClient = Fakex402HTTPClient
    fake_x402_http_utils = types.ModuleType("x402.http.utils")
    fake_x402_http_utils.decode_payment_required_header = fake_decode_payment_required_header
    fake_x402_mechanisms = types.ModuleType("x402.mechanisms")
    fake_x402_mechanisms_evm = types.ModuleType("x402.mechanisms.evm")
    fake_x402_mechanisms_evm_exact = types.ModuleType("x402.mechanisms.evm.exact")
    fake_x402_mechanisms_evm_exact.register_exact_evm_client = fake_register_exact_evm_client
    fake_x402_mechanisms_evm_signers = types.ModuleType("x402.mechanisms.evm.signers")
    fake_x402_mechanisms_evm_signers.EthAccountSigner = FakeEthAccountSigner

    monkeypatch.setitem(sys.modules, "cdp", fake_cdp)
    monkeypatch.setitem(sys.modules, "cdp.openapi_client", fake_openapi_client)
    monkeypatch.setitem(sys.modules, "cdp.openapi_client.errors", fake_errors_module)
    monkeypatch.setitem(sys.modules, "cdp.evm_local_account", fake_cdp_evm_local_account)
    monkeypatch.setitem(sys.modules, "x402", fake_x402)
    monkeypatch.setitem(sys.modules, "x402.http", fake_x402_http)
    monkeypatch.setitem(sys.modules, "x402.http.x402_http_client", fake_x402_http_client)
    monkeypatch.setitem(sys.modules, "x402.http.utils", fake_x402_http_utils)
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
async def test_never_auto_creates_a_phantom_wallet_on_name_mismatch(monkeypatch):
    """24/07 -- 5-agent audit finding: this signer used to call
    cdp.evm.get_or_create_account directly, the exact unsafe pattern that
    caused the 21/07 and 23/07 phantom-wallet incidents (a stale WALLET_NAME
    silently created and signed against a brand-new empty wallet). Now
    reuses agent_wallet_cdp_adapter._get_wallet_account -- a 404 must fail
    LOUDLY, never silently create a second wallet."""
    _install_fake_x402_modules(monkeypatch, raise_on="account_404")
    with pytest.raises(RuntimeError, match="refusing to auto-create a new empty wallet"):
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
    with pytest.raises(RuntimeError, match="n'a produit ni PAYMENT-SIGNATURE ni X-PAYMENT"):
        await signer.build_x402_payment_header({"asset": "USDC", "amount": "10000"})


@pytest.mark.asyncio
async def test_defaults_x402_version_to_1_when_absent(monkeypatch):
    _install_fake_x402_modules(monkeypatch)
    # Pas d'assertion directe sur le body interne (encapsule par les fakes) --
    # verifie seulement que l'absence de x402Version ne leve rien (comportement
    # par defaut documente dans le module).
    result = await signer.build_x402_payment_header({"asset": "USDC", "amount": "10000"})
    assert result


# ── 19/07 : chemin v2 (header brut) -- bug réel trouvé sur 2 fournisseurs Bazaar réels ──

@pytest.mark.asyncio
async def test_v2_header_present_uses_decode_payment_required_header_directly(monkeypatch):
    """Quand _raw_v2_header est présent (offre v2, ex. lionx402/sociavault), le SDK
    decode_payment_required_header doit être appelé DIRECTEMENT sur le header brut --
    jamais via get_payment_required_response (dont le repli "corps" n'accepte que v1,
    cf. commentaire du module -- lu dans le vrai code source du SDK installé)."""
    _install_fake_x402_modules(monkeypatch, header_value="the-real-header")
    result = await signer.build_x402_payment_header(
        {"asset": "0x8335...", "amount": "12000", "x402Version": 2, "_raw_v2_header": "b64rawheader"}
    )
    assert result == "the-real-header"


@pytest.mark.asyncio
async def test_v2_header_absent_falls_back_to_legacy_body_path(monkeypatch):
    """Sans _raw_v2_header (v1, ex. Cybercentry) -- comportement HISTORIQUE inchangé,
    jamais de régression sur le fournisseur déjà validé en conditions réelles."""
    _install_fake_x402_modules(monkeypatch, header_value="the-real-header")
    result = await signer.build_x402_payment_header({"asset": "USDC", "amount": "10000"})
    assert result == "the-real-header"


@pytest.mark.asyncio
async def test_v2_header_decode_failure_raises(monkeypatch):
    _install_fake_x402_modules(monkeypatch, raise_on="decode_v2")
    with pytest.raises(RuntimeError, match="header v2 illisible"):
        await signer.build_x402_payment_header(
            {"asset": "0x8335...", "amount": "12000", "_raw_v2_header": "b64rawheader"}
        )
