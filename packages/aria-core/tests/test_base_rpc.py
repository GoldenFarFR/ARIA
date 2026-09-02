"""Single resolution point for Base's HTTPS RPC endpoint (02/09).

Born from a real incident: the operator retired the Alchemy key, and because
five modules each carried `os.environ.get(...) or _DEFAULT_RPC_URL` inline,
every one of them kept dialling a dead endpoint -- the fallback only fires on
an EMPTY variable, never on a set-but-dead one."""
from __future__ import annotations

import pytest

from aria_core.services import base_rpc

CHAINSTACK_WS = "wss://base-mainnet.core.chainstack.com/deadbeef"
CHAINSTACK_HTTPS = "https://base-mainnet.core.chainstack.com/deadbeef"
ALCHEMY = "https://base-mainnet.g.alchemy.com/v2/somekey"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ARIA_BASE_RPC_WS", raising=False)
    monkeypatch.delenv("ARIA_BASE_RPC_URL", raising=False)
    yield


def test_chainstack_wins_when_the_ws_variable_is_set(monkeypatch):
    monkeypatch.setenv("ARIA_BASE_RPC_WS", CHAINSTACK_WS)
    monkeypatch.setenv("ARIA_BASE_RPC_URL", ALCHEMY)
    assert base_rpc.base_rpc_url() == CHAINSTACK_HTTPS
    assert base_rpc.base_rpc_provider() == base_rpc.PROVIDER_CHAINSTACK


def test_env_url_is_used_when_no_chainstack_ws(monkeypatch):
    """A fresh key in ARIA_BASE_RPC_URL restores the old behaviour with no
    code change -- the migration does not burn that bridge."""
    monkeypatch.setenv("ARIA_BASE_RPC_URL", ALCHEMY)
    assert base_rpc.base_rpc_url() == ALCHEMY
    assert base_rpc.base_rpc_provider() == base_rpc.PROVIDER_ENV


def test_public_endpoint_is_the_last_resort_never_empty():
    assert base_rpc.base_rpc_url() == base_rpc.PUBLIC_BASE_RPC_URL
    assert base_rpc.base_rpc_provider() == base_rpc.PROVIDER_PUBLIC


def test_ws_scheme_conversion(monkeypatch):
    monkeypatch.setenv("ARIA_BASE_RPC_WS", "ws://host/key")
    assert base_rpc.base_rpc_url() == "http://host/key"
    monkeypatch.setenv("ARIA_BASE_RPC_WS", "https://already-http/key")  # not a ws scheme
    assert base_rpc.base_rpc_url() == base_rpc.PUBLIC_BASE_RPC_URL      # ignored, not mangled


def test_blank_variables_are_treated_as_absent(monkeypatch):
    monkeypatch.setenv("ARIA_BASE_RPC_WS", "   ")
    monkeypatch.setenv("ARIA_BASE_RPC_URL", "  ")
    assert base_rpc.base_rpc_url() == base_rpc.PUBLIC_BASE_RPC_URL


def test_the_five_modules_no_longer_restate_the_resolution(monkeypatch):
    """The §1bis guard: the incident happened because one line lived in five
    places. This fails if anyone copies it back."""
    import pathlib
    from aria_core.services import base_rpc as mod

    root = pathlib.Path(mod.__file__).parent.parent
    offenders = []
    for rel in ("early_legitimacy_shadow.py", "services/b20.py", "services/doppler.py",
                "services/basenames.py", "services/base_onchain.py"):
        src = (root / rel).read_text(encoding="utf-8")
        if 'os.environ.get("ARIA_BASE_RPC_URL"' in src:
            offenders.append(rel)
    assert not offenders, f"resolution RPC Base re-dupliquee dans : {offenders}"


def test_every_migrated_module_resolves_through_the_shared_point(monkeypatch):
    monkeypatch.setenv("ARIA_BASE_RPC_WS", CHAINSTACK_WS)
    from aria_core import early_legitimacy_shadow
    from aria_core.services import b20, base_onchain, basenames, doppler

    for mod in (early_legitimacy_shadow, b20, doppler, basenames, base_onchain):
        assert mod._rpc_url() == CHAINSTACK_HTTPS, f"{mod.__name__} ne passe pas par base_rpc"
