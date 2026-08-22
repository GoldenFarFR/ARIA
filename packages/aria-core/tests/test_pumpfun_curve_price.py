"""Curve-derived spot price: decoding, refusals, batching."""
from __future__ import annotations

import base64

import pytest

from aria_core.services import pumpfun_curve_price as cp

MINT = "So11111111111111111111111111111111111111112"
SOL_USD = 95.0


def _curve(*, virtual_tokens: int, virtual_sol: int, complete: bool = False) -> bytes:
    raw = bytearray(64)
    raw[8:16] = int(virtual_tokens).to_bytes(8, "little")
    raw[16:24] = int(virtual_sol).to_bytes(8, "little")
    raw[48] = 1 if complete else 0
    return bytes(raw)


class TestDecoding:
    def test_price_is_sol_reserves_over_token_reserves(self):
        # 30 SOL against 1,000,000 tokens at 95$/SOL -> 0.00285$
        raw = _curve(virtual_tokens=1_000_000 * 10**6, virtual_sol=30 * 10**9)
        price = cp.price_from_curve_data(raw, sol_usd=SOL_USD)
        assert price == pytest.approx(30 / 1_000_000 * SOL_USD)

    def test_a_graduated_curve_prices_nothing(self):
        """`complete` means the curve is frozen -- pumpswap owns the price."""
        raw = _curve(virtual_tokens=1_000_000 * 10**6, virtual_sol=30 * 10**9, complete=True)
        assert cp.price_from_curve_data(raw, sol_usd=SOL_USD) is None

    def test_empty_reserves_price_nothing(self):
        raw = _curve(virtual_tokens=0, virtual_sol=30 * 10**9)
        assert cp.price_from_curve_data(raw, sol_usd=SOL_USD) is None

    def test_a_truncated_account_refuses_rather_than_decoding_garbage(self):
        assert cp.price_from_curve_data(b"\x00" * 20, sol_usd=SOL_USD) is None

    def test_an_unknown_sol_price_refuses(self):
        """Without a SOL rate there is no dollar price, only a ratio."""
        raw = _curve(virtual_tokens=1_000_000 * 10**6, virtual_sol=30 * 10**9)
        assert cp.price_from_curve_data(raw, sol_usd=0) is None


class TestCurveAddress:
    def test_derivation_is_deterministic(self):
        assert cp.curve_address(MINT) == cp.curve_address(MINT)

    def test_different_mints_give_different_curves(self):
        other = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        assert cp.curve_address(MINT) != cp.curve_address(other)


class TestFetch:
    @staticmethod
    def _client(payload, calls=None):
        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        class _Client:
            async def post(self, url, json=None, **k):
                if calls is not None:
                    calls.append(json["params"][0])
                return _Resp()

        return _Client()

    @pytest.mark.asyncio
    async def test_prices_are_keyed_by_mint(self):
        raw = _curve(virtual_tokens=1_000_000 * 10**6, virtual_sol=30 * 10**9)
        payload = {"result": {"value": [{"data": [base64.b64encode(raw).decode(), "base64"]}]}}
        out = await cp.fetch_prices(
            [MINT], sol_usd=SOL_USD, rpc_http_url="http://rpc", client=self._client(payload)
        )
        assert set(out) == {MINT}

    @pytest.mark.asyncio
    async def test_a_missing_account_is_absent_never_zero(self):
        """Absent means 'unknown, fall back' -- a zero would fire every stop."""
        payload = {"result": {"value": [None]}}
        out = await cp.fetch_prices(
            [MINT], sol_usd=SOL_USD, rpc_http_url="http://rpc", client=self._client(payload)
        )
        assert out == {}

    @pytest.mark.asyncio
    async def test_batches_respect_the_hundred_account_limit(self):
        calls: list = []
        payload = {"result": {"value": []}}
        mints = [MINT] * 250
        await cp.fetch_prices(
            mints, sol_usd=SOL_USD, rpc_http_url="http://rpc",
            client=self._client(payload, calls),
        )
        assert [len(c) for c in calls] == [100, 100, 50]

    @pytest.mark.asyncio
    async def test_a_dead_rpc_returns_nothing_rather_than_raising(self):
        class _Client:
            async def post(self, *a, **k):
                raise RuntimeError("rpc down")

        out = await cp.fetch_prices(
            [MINT], sol_usd=SOL_USD, rpc_http_url="http://rpc", client=_Client()
        )
        assert out == {}
