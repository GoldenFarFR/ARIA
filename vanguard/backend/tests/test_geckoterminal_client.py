"""Tests du client GeckoTerminal du backend vanguard (lecture seule, aucun
appel réseau réel).

Réel bug trouvé le 01/08 (audit Workflow déclenché par un taux de 429
GeckoTerminal resté élevé malgré 3 correctifs de coordination déjà appliqués
le même jour côté aria-core) : ce client respectait bien le throttle
partagé (``wait_for_shared_rate_limit``, calibré pour du trafic authentifié,
~21 req/min) mais n'envoyait jamais la clé ``COINGECKO_DEMO_API_KEY`` --
tirait donc en réalité au tarif keyless (~10 req/min) sur ce même lock.
Même patron déjà corrigé le même jour côté aria_core.services.ohlcv.py.
"""

import httpx
import pytest

from app.services.geckoterminal import GeckoTerminalClient


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


class FakeClient:
    def __init__(self, response, *, captured_headers: list | None = None):
        self._response = response
        self._captured_headers = captured_headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None, headers=None):
        if self._captured_headers is not None:
            self._captured_headers.append(headers)
        return self._response


def _payload(rows: list) -> dict:
    return {"data": {"attributes": {"ohlcv_list": rows}}}


def _row(ts: int = 1_000) -> list:
    return [ts, 100.0, 102.0, 98.0, 101.0, 10.0]


@pytest.fixture(autouse=True)
def _no_shared_throttle(monkeypatch):
    """Ce client délègue au lock partagé aria-core -- neutralisé ici, testé
    en isolation (déjà couvert par les tests aria-core de ce même lock)."""

    async def _no_wait():
        return None

    monkeypatch.setattr("app.services.geckoterminal.wait_for_shared_rate_limit", _no_wait)


@pytest.mark.asyncio
async def test_api_key_sent_when_configured(monkeypatch):
    monkeypatch.setenv("COINGECKO_DEMO_API_KEY", "demo-key-123")
    captured: list = []
    monkeypatch.setattr(
        "app.services.geckoterminal.httpx.AsyncClient",
        lambda **kw: FakeClient(FakeResponse(200, _payload([_row()])), captured_headers=captured),
    )

    client = GeckoTerminalClient()
    await client._fetch_raw("base", "0xPOOL", "day", 1, 10)

    assert captured
    assert captured[0]["x-cg-demo-api-key"] == "demo-key-123"


@pytest.mark.asyncio
async def test_no_api_key_header_when_not_configured(monkeypatch):
    monkeypatch.delenv("COINGECKO_DEMO_API_KEY", raising=False)
    captured: list = []
    monkeypatch.setattr(
        "app.services.geckoterminal.httpx.AsyncClient",
        lambda **kw: FakeClient(FakeResponse(200, _payload([_row()])), captured_headers=captured),
    )

    client = GeckoTerminalClient()
    await client._fetch_raw("base", "0xPOOL", "day", 1, 10)

    assert captured
    assert "x-cg-demo-api-key" not in captured[0]


@pytest.mark.asyncio
async def test_api_key_header_stripped_of_whitespace(monkeypatch):
    """Même garde que services/ohlcv.py et services/geckoterminal.py côté
    aria-core -- une variable d'env mal renseignée avec des espaces ne doit
    jamais partir telle quelle dans le header."""
    monkeypatch.setenv("COINGECKO_DEMO_API_KEY", "  demo-key-123  ")
    captured: list = []
    monkeypatch.setattr(
        "app.services.geckoterminal.httpx.AsyncClient",
        lambda **kw: FakeClient(FakeResponse(200, _payload([_row()])), captured_headers=captured),
    )

    client = GeckoTerminalClient()
    await client._fetch_raw("base", "0xPOOL", "day", 1, 10)

    assert captured[0]["x-cg-demo-api-key"] == "demo-key-123"


@pytest.mark.asyncio
async def test_accept_header_still_sent_alongside_api_key(monkeypatch):
    """Le header Accept déjà présent avant ce correctif ne doit jamais
    disparaître -- seul l'ajout conditionnel de la clé change."""
    monkeypatch.setenv("COINGECKO_DEMO_API_KEY", "demo-key-123")
    captured: list = []
    monkeypatch.setattr(
        "app.services.geckoterminal.httpx.AsyncClient",
        lambda **kw: FakeClient(FakeResponse(200, _payload([_row()])), captured_headers=captured),
    )

    client = GeckoTerminalClient()
    await client._fetch_raw("base", "0xPOOL", "day", 1, 10)

    assert captured[0]["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_fetch_raw_still_parses_candles_correctly(monkeypatch):
    """Le correctif ne touche que les headers -- le parsing/tri des bougies
    doit rester byte-for-byte identique."""
    monkeypatch.delenv("COINGECKO_DEMO_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.services.geckoterminal.httpx.AsyncClient",
        lambda **kw: FakeClient(FakeResponse(200, _payload([_row(2_000), _row(1_000)]))),
    )

    client = GeckoTerminalClient()
    candles = await client._fetch_raw("base", "0xPOOL", "day", 1, 10)

    assert [c.timestamp for c in candles] == [1_000, 2_000]
