"""Tests for the Robinhood Chain Stock Token registry client (#309, 16/08) --
no real network call, everything mocked at the httpx.AsyncClient level (same
pattern as test_defillama_client.py)."""

from __future__ import annotations

import pytest

from aria_core.services import robinhood_stock_tokens as rst


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


class FakeClient:
    """``httpx.AsyncClient(...)`` is re-instantiated on EVERY attempt inside
    ``_fetch_assets_json`` -- ``_responses`` must be SHARED across every
    instance a single ``_patch_client`` call creates (cf. test_defillama_client.py)."""

    def __init__(self, responses: list):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers=None):
        return self._responses.pop(0)


def _patch_client(monkeypatch, responses):
    shared = list(responses)
    monkeypatch.setattr(
        "aria_core.services.robinhood_stock_tokens.httpx.AsyncClient", lambda **kw: FakeClient(shared),
    )


async def _no_sleep(_seconds):
    return None


def _reset_cache(monkeypatch):
    monkeypatch.setattr(rst, "_cache_addresses", None)
    monkeypatch.setattr(rst, "_cache_fetched_at", 0.0)


_SAMPLE_PAYLOAD = {
    "assets": [
        {
            "tokenSymbol": "NVDA",
            "deployments": [{"contractAddress": "0xAbCdEf0000000000000000000000000000AaAa", "chainId": 4663}],
        },
        {
            "tokenSymbol": "AAPL",
            "deployments": [{"contractAddress": "0x1111111111111111111111111111111111Bbbb", "chainId": 4663}],
        },
        {
            # a malformed/foreign-chain deployment must never leak in
            "tokenSymbol": "SOMETHING_ELSE",
            "deployments": [{"contractAddress": "0x2222222222222222222222222222222222Cccc", "chainId": 1}],
        },
    ]
}


class TestFetchStockTokenAddresses:
    @pytest.mark.asyncio
    async def test_extracts_only_robinhood_chain_id_addresses_lowercased(self, monkeypatch):
        _patch_client(monkeypatch, [FakeResponse(200, _SAMPLE_PAYLOAD)])

        addresses = await rst.fetch_stock_token_addresses()

        assert addresses == frozenset({
            "0xabcdef0000000000000000000000000000aaaa",
            "0x1111111111111111111111111111111111bbbb",
        })

    @pytest.mark.asyncio
    async def test_unexpected_shape_returns_none_never_raises(self, monkeypatch):
        _patch_client(monkeypatch, [FakeResponse(200, {"unexpected": "shape"})])

        assert await rst.fetch_stock_token_addresses() is None

    @pytest.mark.asyncio
    async def test_malformed_rows_skipped_not_a_crash(self, monkeypatch):
        payload = {"assets": ["not-a-dict", {"tokenSymbol": "X", "deployments": "not-a-list"}]}
        _patch_client(monkeypatch, [FakeResponse(200, payload)])

        assert await rst.fetch_stock_token_addresses() == frozenset()

    @pytest.mark.asyncio
    async def test_429_exhausted_returns_none(self, monkeypatch):
        monkeypatch.setattr(rst.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(429), FakeResponse(429), FakeResponse(429)])

        assert await rst.fetch_stock_token_addresses() is None

    @pytest.mark.asyncio
    async def test_timeout_retries_once_then_fails(self, monkeypatch):
        import httpx

        monkeypatch.setattr(rst.asyncio, "sleep", _no_sleep)

        class TimeoutClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, *a, **kw):
                raise httpx.TransportError("boom")

        monkeypatch.setattr(
            "aria_core.services.robinhood_stock_tokens.httpx.AsyncClient", lambda **kw: TimeoutClient(),
        )

        assert await rst.fetch_stock_token_addresses() is None


class TestGetStockTokenAddressesCache:
    @pytest.mark.asyncio
    async def test_cold_start_failure_returns_empty_fail_open(self, monkeypatch):
        _reset_cache(monkeypatch)
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])
        monkeypatch.setattr(rst.asyncio, "sleep", _no_sleep)

        result = await rst.get_stock_token_addresses()

        assert result == frozenset()

    @pytest.mark.asyncio
    async def test_successful_fetch_populates_cache(self, monkeypatch):
        _reset_cache(monkeypatch)
        _patch_client(monkeypatch, [FakeResponse(200, _SAMPLE_PAYLOAD)])

        result = await rst.get_stock_token_addresses()

        assert "0xabcdef0000000000000000000000000000aaaa" in result

    @pytest.mark.asyncio
    async def test_fresh_cache_never_triggers_a_second_network_call(self, monkeypatch):
        _reset_cache(monkeypatch)
        _patch_client(monkeypatch, [FakeResponse(200, _SAMPLE_PAYLOAD)])
        first = await rst.get_stock_token_addresses()

        # a second call with NO queued response left would raise IndexError
        # (list.pop(0) on empty) if it wrongly hit the network again
        second = await rst.get_stock_token_addresses()

        assert first == second

    @pytest.mark.asyncio
    async def test_failed_refresh_falls_back_to_stale_cache_not_empty(self, monkeypatch):
        _reset_cache(monkeypatch)
        _patch_client(monkeypatch, [FakeResponse(200, _SAMPLE_PAYLOAD)])
        warm = await rst.get_stock_token_addresses()
        assert warm  # sanity: cache actually warmed

        # force the TTL to have expired, then make the refresh fail entirely
        monkeypatch.setattr(rst, "_cache_fetched_at", 0.0)
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])
        monkeypatch.setattr(rst.asyncio, "sleep", _no_sleep)

        stale_served = await rst.get_stock_token_addresses()

        assert stale_served == warm  # never silently forgotten on a transient outage


class TestIsStockToken:
    @pytest.mark.asyncio
    async def test_non_robinhood_chain_never_touches_the_network(self, monkeypatch):
        _reset_cache(monkeypatch)

        async def _never_called():
            raise AssertionError("a non-robinhood chain must never trigger the registry fetch")

        monkeypatch.setattr(rst, "get_stock_token_addresses", _never_called)

        assert await rst.is_stock_token("0xabcdef0000000000000000000000000000aaaa", "base") is False

    @pytest.mark.asyncio
    async def test_robinhood_chain_confirmed_stock_token_returns_true(self, monkeypatch):
        _reset_cache(monkeypatch)
        _patch_client(monkeypatch, [FakeResponse(200, _SAMPLE_PAYLOAD)])

        assert await rst.is_stock_token("0xAbCdEf0000000000000000000000000000AaAa", "robinhood") is True

    @pytest.mark.asyncio
    async def test_robinhood_chain_ordinary_token_returns_false(self, monkeypatch):
        _reset_cache(monkeypatch)
        _patch_client(monkeypatch, [FakeResponse(200, _SAMPLE_PAYLOAD)])

        assert await rst.is_stock_token("0x" + "9" * 40, "robinhood") is False

    @pytest.mark.asyncio
    async def test_empty_contract_returns_false_without_a_cache_read(self, monkeypatch):
        _reset_cache(monkeypatch)

        async def _never_called():
            raise AssertionError("an empty contract must never trigger the registry fetch")

        monkeypatch.setattr(rst, "get_stock_token_addresses", _never_called)

        assert await rst.is_stock_token("", "robinhood") is False
