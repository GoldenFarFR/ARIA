"""Tests du client DefiLlama (#157, 14/07) -- aucun appel réseau réel, tout
est mocké au niveau httpx.AsyncClient (même patron que test_coinmarketcap_client.py)."""

import pytest

from aria_core.services import defillama


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
    """``httpx.AsyncClient(...)`` est réinstancié à CHAQUE tentative dans
    ``_get_json`` -- ``_responses`` doit être PARTAGÉ entre toutes les
    instances créées par une même ``_patch_client`` (cf. test_coinmarketcap_client.py)."""

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
    monkeypatch.setattr("aria_core.services.defillama.httpx.AsyncClient", lambda **kw: FakeClient(shared))


async def _no_sleep(_seconds):
    return None


def _set_chain_ids(monkeypatch, chain_ids: dict):
    monkeypatch.setattr("aria_core.services.blockscout.CHAIN_IDS", chain_ids)


class TestFetchChainTvlRanking:
    @pytest.mark.asyncio
    async def test_filters_and_sorts_by_tvl_descending(self, monkeypatch):
        _set_chain_ids(monkeypatch, {"base": 8453, "ethereum": 1, "arbitrum": 42161})
        _patch_client(
            monkeypatch,
            [
                FakeResponse(
                    200,
                    [
                        {"name": "Base", "tvl": 100.0, "chainId": 8453},
                        {"name": "Ethereum", "tvl": 900.0, "chainId": 1},
                        {"name": "Arbitrum", "tvl": 500.0, "chainId": 42161},
                        {"name": "Some Other Chain", "tvl": 999999.0, "chainId": 999999},
                    ],
                )
            ],
        )

        ranking = await defillama.fetch_chain_tvl_ranking()

        assert ranking == [("ethereum", 900.0), ("arbitrum", 500.0), ("base", 100.0)]

    @pytest.mark.asyncio
    async def test_unconfirmed_chain_never_included_even_with_highest_tvl(self, monkeypatch):
        _set_chain_ids(monkeypatch, {"base": 8453})
        _patch_client(
            monkeypatch,
            [FakeResponse(200, [{"name": "Base", "tvl": 1.0, "chainId": 8453}, {"name": "Solana", "tvl": 999.0, "chainId": None}])],
        )

        ranking = await defillama.fetch_chain_tvl_ranking()

        assert ranking == [("base", 1.0)]

    @pytest.mark.asyncio
    async def test_malformed_tvl_treated_as_zero_not_a_crash(self, monkeypatch):
        _set_chain_ids(monkeypatch, {"base": 8453})
        _patch_client(monkeypatch, [FakeResponse(200, [{"name": "Base", "tvl": "not-a-number", "chainId": 8453}])])

        ranking = await defillama.fetch_chain_tvl_ranking()

        assert ranking == [("base", 0.0)]

    @pytest.mark.asyncio
    async def test_unexpected_shape_returns_none_never_raises(self, monkeypatch):
        _set_chain_ids(monkeypatch, {"base": 8453})
        _patch_client(monkeypatch, [FakeResponse(200, {"unexpected": "shape"})])

        ranking = await defillama.fetch_chain_tvl_ranking()

        assert ranking is None

    @pytest.mark.asyncio
    async def test_malformed_rows_skipped_not_a_crash(self, monkeypatch):
        _set_chain_ids(monkeypatch, {"base": 8453})
        _patch_client(monkeypatch, [FakeResponse(200, ["not-a-dict", {"name": "Base", "tvl": 5.0, "chainId": 8453}])])

        ranking = await defillama.fetch_chain_tvl_ranking()

        assert ranking == [("base", 5.0)]


class TestDomeRetry:
    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self, monkeypatch):
        _set_chain_ids(monkeypatch, {"base": 8453})
        monkeypatch.setattr(defillama.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(429), FakeResponse(200, [{"name": "Base", "tvl": 1.0, "chainId": 8453}])])

        ranking = await defillama.fetch_chain_tvl_ranking()

        assert ranking == [("base", 1.0)]

    @pytest.mark.asyncio
    async def test_429_exhausted_after_three_attempts_returns_none(self, monkeypatch):
        _set_chain_ids(monkeypatch, {"base": 8453})
        monkeypatch.setattr(defillama.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(429), FakeResponse(429), FakeResponse(429)])

        ranking = await defillama.fetch_chain_tvl_ranking()

        assert ranking is None

    @pytest.mark.asyncio
    async def test_5xx_retries_once_then_fails(self, monkeypatch):
        _set_chain_ids(monkeypatch, {"base": 8453})
        monkeypatch.setattr(defillama.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])

        ranking = await defillama.fetch_chain_tvl_ranking()

        assert ranking is None

    @pytest.mark.asyncio
    async def test_timeout_retries_once_then_fails(self, monkeypatch):
        import httpx

        _set_chain_ids(monkeypatch, {"base": 8453})
        monkeypatch.setattr(defillama.asyncio, "sleep", _no_sleep)

        class TimeoutClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, *a, **kw):
                raise httpx.TransportError("boom")

        monkeypatch.setattr("aria_core.services.defillama.httpx.AsyncClient", lambda **kw: TimeoutClient())

        ranking = await defillama.fetch_chain_tvl_ranking()

        assert ranking is None
