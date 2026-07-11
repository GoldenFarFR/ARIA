"""Lecture on-chain Base mainnet pour graduation_progress -- lecture seule (hors-ligne, injecté)."""
from __future__ import annotations

import pytest

from aria_core.services import base_onchain as bo

PAIR = "0xPairAddress00000000000000000000000000001"
TOKEN = "0xTokenAddress0000000000000000000000000002"


class _FakeCall:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value


class _FakeFunctions:
    def __init__(self, reserve0=None, threshold=None):
        self._reserve0 = reserve0
        self._threshold = threshold

    def getReserves(self):  # noqa: N802 -- nom ABI
        return _FakeCall((int(self._reserve0), 1))

    def tokenGradThreshold(self, address):  # noqa: N802 -- nom ABI
        return _FakeCall(int(self._threshold))


class _FakeContract:
    def __init__(self, address, functions):
        self.address = address
        self.functions = functions


class _FakeEth:
    def __init__(self, reserve0=None, threshold=None):
        self._reserve0 = reserve0
        self._threshold = threshold

    def contract(self, address, abi):
        return _FakeContract(address, _FakeFunctions(self._reserve0, self._threshold))


class _FakeW3:
    def __init__(self, reserve0=None, threshold=None):
        self.eth = _FakeEth(reserve0, threshold)

    def to_checksum_address(self, addr):
        return addr


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("ARIA_ONCHAIN_GRADUATION_ENABLED", "1")
    yield


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_ONCHAIN_GRADUATION_ENABLED", raising=False)
    w3 = _FakeW3(reserve0=163_037_404 * 10**18, threshold=168_316_831 * 10**18)
    assert bo.onchain_graduation_progress(pair_address=PAIR, token_address=TOKEN, w3=w3) is None


def test_fresh_token_low_progress():
    # reserve0 proche de la réserve initiale -> progression proche de 0.
    w3 = _FakeW3(reserve0=999_500_000 * 10**18, threshold=168_316_831 * 10**18)
    progress = bo.onchain_graduation_progress(pair_address=PAIR, token_address=TOKEN, w3=w3)
    assert progress is not None
    assert 0.0 <= progress < 0.01


def test_graduated_token_matches_real_confirmed_case():
    # Chiffres réels vérifiés le 11/07 sur un vrai token gradué on-chain.
    w3 = _FakeW3(reserve0=163_037_404.9556 * 10**18, threshold=168_316_831.6831683 * 10**18)
    progress = bo.onchain_graduation_progress(pair_address=PAIR, token_address=TOKEN, w3=w3)
    assert progress is not None
    assert progress == 1.0  # clampé -- reserve0 déjà sous le seuil au moment de la lecture


def test_progress_clamped_to_one():
    w3 = _FakeW3(reserve0=0, threshold=168_316_831 * 10**18)
    assert bo.onchain_graduation_progress(pair_address=PAIR, token_address=TOKEN, w3=w3) == 1.0


def test_threshold_zero_means_not_registered_on_this_instance():
    w3 = _FakeW3(reserve0=500_000_000 * 10**18, threshold=0)
    assert bo.onchain_graduation_progress(pair_address=PAIR, token_address=TOKEN, w3=w3) is None


def test_missing_pair_address_returns_none():
    w3 = _FakeW3(reserve0=500_000_000 * 10**18, threshold=168_316_831 * 10**18)
    assert bo.onchain_graduation_progress(pair_address=None, token_address=TOKEN, w3=w3) is None


def test_missing_token_address_returns_none():
    w3 = _FakeW3(reserve0=500_000_000 * 10**18, threshold=168_316_831 * 10**18)
    assert bo.onchain_graduation_progress(pair_address=PAIR, token_address=None, w3=w3) is None


def test_rpc_error_degrades_to_none():
    class _BrokenEth:
        def contract(self, address, abi):
            raise ConnectionError("RPC down")

    class _BrokenW3:
        eth = _BrokenEth()

        def to_checksum_address(self, addr):
            return addr

    result = bo.onchain_graduation_progress(pair_address=PAIR, token_address=TOKEN, w3=_BrokenW3())
    assert result is None


def test_fetch_pair_reserve0_direct():
    w3 = _FakeW3(reserve0=250_000_000 * 10**18)
    assert bo.fetch_pair_reserve0(PAIR, w3=w3) == 250_000_000.0


def test_fetch_token_grad_threshold_direct():
    w3 = _FakeW3(threshold=168_316_831 * 10**18)
    assert bo.fetch_token_grad_threshold(TOKEN, w3=w3) == 168_316_831.0


def test_rpc_url_default(monkeypatch):
    monkeypatch.delenv("ARIA_BASE_RPC_URL", raising=False)
    assert bo._rpc_url() == "https://mainnet.base.org"


def test_rpc_url_override(monkeypatch):
    monkeypatch.setenv("ARIA_BASE_RPC_URL", "https://custom.example/rpc")
    assert bo._rpc_url() == "https://custom.example/rpc"
