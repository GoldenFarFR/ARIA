"""Tests du client CoinMarketCap DEX (#157, 14/07) -- aucun appel réseau réel,
tout est mocké au niveau httpx.AsyncClient (même patron que
test_geckoterminal_client.py)."""

import pytest

from aria_core.services import coinmarketcap as cmc


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
    ``_get_json`` (``async with httpx.AsyncClient(...)``) -- ``_responses``/
    ``calls`` doivent donc être PARTAGÉS entre toutes les instances créées par
    une même ``_patch_client``, pas recopiés à chaque instanciation, sinon une
    séquence de retry (429 puis 200) revoit la même première réponse en boucle."""

    def __init__(self, responses: list, calls: list):
        self._responses = responses
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        return self._responses.pop(0)


def _patch_client(monkeypatch, responses):
    shared_responses = list(responses)
    shared_calls = []
    holder = {"calls": shared_calls}

    def factory(**kw):
        return FakeClient(shared_responses, shared_calls)

    monkeypatch.setattr("aria_core.services.coinmarketcap.httpx.AsyncClient", factory)
    return holder


def _envelope(data):
    return {"data": data, "status": {"error_code": "0", "error_message": ""}}


class TestKeySelection:
    @pytest.mark.asyncio
    async def test_keyless_when_no_env_var(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        holder = _patch_client(monkeypatch, [FakeResponse(200, _envelope([]))])

        await cmc._get_json("/v1/dex/token/pools", params={})

        url, _, headers = holder["calls"][0]
        assert url.startswith(cmc.BASE_URL_KEYLESS)
        assert "X-CMC_PRO_API_KEY" not in headers

    @pytest.mark.asyncio
    async def test_keyed_when_env_var_present(self, monkeypatch):
        monkeypatch.setenv("COINMARKETCAP_API_KEY", "test-key-123")
        holder = _patch_client(monkeypatch, [FakeResponse(200, _envelope([]))])

        await cmc._get_json("/v1/dex/token/pools", params={})

        url, _, headers = holder["calls"][0]
        assert url.startswith(cmc.BASE_URL_KEYED)
        assert "/public-api" not in url
        assert headers["X-CMC_PRO_API_KEY"] == "test-key-123"


class TestErrorEnvelope:
    @pytest.mark.asyncio
    async def test_http_200_with_nonzero_error_code_is_unavailable(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        _patch_client(
            monkeypatch,
            [FakeResponse(200, {"data": None, "status": {"error_code": "1002", "error_message": "API key missing."}})],
        )

        data, error = await cmc._get_json("/v1/dex/token/pools", params={})

        assert data is None
        assert error is not None
        assert "API key missing" in error


class TestDomeRetry:
    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        monkeypatch.setattr(cmc.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(429), FakeResponse(200, _envelope([]))])

        data, error = await cmc._get_json("/v1/dex/token/pools", params={})

        assert error is None
        assert data["data"] == []

    @pytest.mark.asyncio
    async def test_429_exhausted_after_three_attempts(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        monkeypatch.setattr(cmc.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(429), FakeResponse(429), FakeResponse(429)])

        data, error = await cmc._get_json("/v1/dex/token/pools", params={})

        assert data is None
        assert "rate limit" in error

    @pytest.mark.asyncio
    async def test_5xx_retries_once_then_fails(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        monkeypatch.setattr(cmc.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])

        data, error = await cmc._get_json("/v1/dex/token/pools", params={})

        assert data is None
        assert "erreur serveur" in error

    @pytest.mark.asyncio
    async def test_timeout_retries_once_then_fails(self, monkeypatch):
        import httpx

        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        monkeypatch.setattr(cmc.asyncio, "sleep", _no_sleep)

        class TimeoutClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, *a, **kw):
                raise httpx.TransportError("boom")

        monkeypatch.setattr("aria_core.services.coinmarketcap.httpx.AsyncClient", lambda **kw: TimeoutClient())

        data, error = await cmc._get_json("/v1/dex/token/pools", params={})

        assert data is None
        assert "timeout" in error


async def _no_sleep(_seconds):
    return None


class TestResolvePrimaryPool:
    @pytest.mark.asyncio
    async def test_picks_pool_with_highest_liquidity(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(
                    200,
                    _envelope(
                        [
                            {"pool_address": "0xlow", "liquidity": 10.0},
                            {"pool_address": "0xhigh", "liquidity": 50000.0, "pool_created_at": "2026-02-01T00:00:00Z"},
                        ]
                    ),
                )
            ],
        )

        result = await cmc.resolve_primary_pool("0xtoken")

        assert result.available is True
        assert result.pool_address == "0xhigh"
        assert result.created_at.month == 2

    @pytest.mark.asyncio
    async def test_no_pools_found_unavailable_never_guesses_token_as_pool(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        _patch_client(monkeypatch, [FakeResponse(200, _envelope([]))])

        result = await cmc.resolve_primary_pool("0xtoken")

        assert result.available is False
        assert result.pool_address == "0xtoken"

    @pytest.mark.asyncio
    async def test_malformed_liquidity_treated_as_zero_not_a_crash(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(
                    200,
                    _envelope(
                        [
                            {"pool_address": "0xa", "liquidity": "not-a-number"},
                            {"pool_address": "0xb", "liquidity": 1.0},
                        ]
                    ),
                )
            ],
        )

        result = await cmc.resolve_primary_pool("0xtoken")

        assert result.available is True
        assert result.pool_address == "0xb"

    @pytest.mark.asyncio
    async def test_500_keyless_unavailable_matches_live_test_14_07(self, monkeypatch):
        """Reproduit le comportement observé en direct ce soir : /v1/dex/token/pools
        renvoie 500 en keyless -- dégradation douce attendue, jamais un crash."""
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        monkeypatch.setattr(cmc.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])

        result = await cmc.resolve_primary_pool("0xtoken")

        assert result.available is False


class TestGetOhlcv:
    @pytest.mark.asyncio
    async def test_parses_candles_and_sorts_by_timestamp(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(
                    200,
                    _envelope(
                        [
                            {"timestamp": 200, "open": 2.0, "high": 2.5, "low": 1.9, "close": 2.2, "volume": 500.0},
                            {"timestamp": 100, "open": 1.0, "high": 1.5, "low": 0.9, "close": 1.2, "volume": 1000.0},
                        ]
                    ),
                )
            ],
        )

        result = await cmc.get_ohlcv("0xpool")

        assert result.available is True
        assert [c.ts for c in result.candles] == [100, 200]

    @pytest.mark.asyncio
    async def test_milliseconds_timestamp_converted_to_seconds(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(
                    200,
                    _envelope(
                        [{"timestamp": 1_700_000_000_000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
                    ),
                )
            ],
        )

        result = await cmc.get_ohlcv("0xpool")

        assert result.available is True
        assert result.candles[0].ts == 1_700_000_000

    @pytest.mark.asyncio
    async def test_empty_candles_list_unavailable(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        _patch_client(monkeypatch, [FakeResponse(200, _envelope([]))])

        result = await cmc.get_ohlcv("0xpool")

        assert result.available is False
        assert result.candles == []

    @pytest.mark.asyncio
    async def test_malformed_rows_skipped_not_a_crash(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(
                    200,
                    _envelope(
                        [
                            {"timestamp": "not-a-number", "open": 1, "high": 1, "low": 1, "close": 1},
                            {"timestamp": 100, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 10.0},
                        ]
                    ),
                )
            ],
        )

        result = await cmc.get_ohlcv("0xpool")

        assert result.available is True
        assert len(result.candles) == 1
        assert result.candles[0].ts == 100

    @pytest.mark.asyncio
    async def test_unexpected_shape_unavailable_never_raises(self, monkeypatch):
        monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
        _patch_client(monkeypatch, [FakeResponse(200, _envelope({"unexpected": "shape"}))])

        result = await cmc.get_ohlcv("0xpool")

        assert result.available is False
        assert result.candles == []
