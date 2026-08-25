"""Tests des grains de marché DefiLlama (25/08, chain/protocol) -- aucun appel
réseau réel, même patron de mock que test_defillama_client.py (``_get_json``
est module-level, partagé avec ``fetch_chain_tvl_ranking``)."""

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


@pytest.fixture(autouse=True)
def _reset_protocol_cache():
    """The address->slug index is a module-level cache -- each test starts clean."""
    defillama._protocol_index_cache = None
    yield
    defillama._protocol_index_cache = None


@pytest.mark.asyncio
async def test_chain_tvl_history_is_the_regime_grain(monkeypatch):
    _patch_client(monkeypatch, [FakeResponse(200, [
        {"date": 1000, "tvl": 5_000_000.0},
        {"date": 2000, "tvl": 5_100_000.0},
    ])])

    got = await defillama.get_chain_tvl_history("Base")

    assert got.available is True
    assert got.points == [(1000, 5_000_000.0), (2000, 5_100_000.0)]


@pytest.mark.asyncio
async def test_chain_dex_volume_lowercases_the_slug(monkeypatch):
    """25/08 finding: this endpoint is lowercase-only, distinct casing from
    historicalChainTvl -- a caller passing "Robinhood" must still resolve."""
    _patch_client(monkeypatch, [FakeResponse(200, {
        "totalDataChart": [[1000, 644_000_000.0]],
        "total24h": 644_000_000.0, "total7d": 3_000_000_000.0,
    })])

    got = await defillama.get_chain_dex_volume("Robinhood")

    assert got.available is True
    assert got.total_24h == 644_000_000.0
    assert got.points == [(1000, 644_000_000.0)]


@pytest.mark.asyncio
async def test_an_unresolved_address_is_the_expected_outcome_not_a_failure(monkeypatch):
    """Most early VC candidates will not be in DefiLlama's curated catalogue
    -- None here must read as "not listed", never surface like an error."""
    _patch_client(monkeypatch, [FakeResponse(200, [
        {"slug": "aerodrome-slipstream", "address": "base:0xAAA"},
    ])])

    got = await defillama.resolve_protocol_slug("base", "0xdeadbeef")

    assert got is None


@pytest.mark.asyncio
async def test_a_listed_address_resolves_to_its_slug(monkeypatch):
    _patch_client(monkeypatch, [FakeResponse(200, [
        {"slug": "aerodrome-slipstream", "address": "base:0xAAA"},
    ])])

    got = await defillama.resolve_protocol_slug("Base", "0xAAA")

    assert got == "aerodrome-slipstream"


@pytest.mark.asyncio
async def test_the_protocol_index_is_fetched_once_and_reused(monkeypatch):
    """8100+ entries, ~8.6MB -- re-fetching per lookup would be exactly the
    kind of linear, unbounded cost the resource-budget doctrine forbids."""
    calls = []
    orig_get_json = defillama._get_json

    async def _counting_get_json(path):
        calls.append(path)
        return await orig_get_json(path)

    _patch_client(monkeypatch, [
        FakeResponse(200, [{"slug": "aerodrome-slipstream", "address": "base:0xAAA"}]),
    ])
    monkeypatch.setattr(defillama, "_get_json", _counting_get_json)

    await defillama.resolve_protocol_slug("base", "0xAAA")
    await defillama.resolve_protocol_slug("base", "0xAAA")

    assert calls == ["/protocols"]


@pytest.mark.asyncio
async def test_protocol_growth_degrades_gracefully_without_volume_or_fees(monkeypatch):
    """Not every protocol has a DEX/fee adapter -- missing volume/fees must
    not fail the whole result when TVL itself is real."""
    _patch_client(monkeypatch, [
        FakeResponse(200, {
            "tvl": [{"date": 1000, "totalLiquidityUSD": 10_000.0}],
            "chains": ["Base"],
        }),
        FakeResponse(404, None),
        FakeResponse(404, None),
    ])

    got = await defillama.get_protocol_growth("some-slug")

    assert got.available is True
    assert got.tvl_points == [(1000, 10_000.0)]
    assert got.volume_points == []
    assert got.fee_points == []
    assert got.chains == ["Base"]


@pytest.mark.asyncio
async def test_protocol_growth_unavailable_when_the_protocol_itself_is_unlisted(monkeypatch):
    _patch_client(monkeypatch, [FakeResponse(400, "Protocol not found")])

    got = await defillama.get_protocol_growth("unknown-slug")

    assert got.available is False


@pytest.mark.asyncio
async def test_a_429_is_retried_reactively_not_blocked_on_a_fixed_throttle(monkeypatch):
    """No documented free-tier rate limit exists (see module docstring) --
    the only defense is reactive backoff on a real 429, never a fabricated
    proactive delay."""
    monkeypatch.setattr(defillama.asyncio, "sleep", _no_sleep)
    _patch_client(monkeypatch, [
        FakeResponse(429, None),
        FakeResponse(200, [{"date": 1000, "tvl": 1.0}]),
    ])

    got = await defillama.get_chain_tvl_history("Base")

    assert got.available is True
