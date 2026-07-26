"""Tests du client OHLCV GeckoTerminal (lecture seule) — aucun appel réseau réel.

Vérifie : parsing robuste (lignes malformées ignorées), l'échelle de repli
1D → 4H → 1H, la dégradation gracieuse (aucune bougie inventée), et le tri
chronologique. Motif de mock identique à test_coingecko_client.py (FakeClient).
"""

import httpx
import pytest

from aria_core.services.ohlcv import (
    DEFAULT_NETWORK,
    OHLCVClient,
    _parse_candles,
)

POOL = "0x" + "ab" * 20


def _rows(n: int, *, start_ts: int = 1_000) -> list[list[float]]:
    """n bougies [ts, open, high, low, close, volume] cohérentes et croissantes."""
    out = []
    for i in range(n):
        base = 100.0 + i
        out.append([start_ts + i * 3600, base, base + 2, base - 2, base + 1, 10.0 + i])
    return out


def _payload(rows: list) -> dict:
    return {"data": {"attributes": {"ohlcv_list": rows}}}


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
    def __init__(self, responses: dict):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None, headers=None):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.ohlcv.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )

    async def _no_sleep(_):
        return None

    monkeypatch.setattr("aria_core.services.ohlcv.asyncio.sleep", _no_sleep)


def _url(period: str, base: str = "https://gt.test") -> str:
    return f"{base}/networks/{DEFAULT_NETWORK}/pools/{POOL}/ohlcv/{period}"


# ── parsing pur ───────────────────────────────────────────────────────────────

def test_parse_candles_ignores_malformed():
    payload = _payload([
        [1000, 1, 2, 0.5, 1.5, 10],   # ok
        [1001, "x", 2, 1, 2, 5],       # open non numérique -> ignoré
        [1002, 2, 3],                  # trop court -> ignoré
        "pas une ligne",               # type invalide -> ignoré
        [999, 3, 4, 2, 3, 7],          # ok, plus ancien -> doit être trié devant
    ])
    candles = _parse_candles(payload)
    assert len(candles) == 2
    assert [c.ts for c in candles] == [999, 1000]  # tri chronologique


def test_parse_candles_empty_shapes():
    assert _parse_candles({}) == []
    assert _parse_candles({"data": {"attributes": {}}}) == []
    assert _parse_candles("bogus") == []


# ── échelle de repli & dégradation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_enough_candles_stops_ladder(monkeypatch):
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(monkeypatch, {_url("day"): FakeResponse(200, _payload(_rows(40)))})
    res = await client.get_ohlcv(POOL)
    assert res.available is True
    assert res.timeframe == "1D"
    assert len(res.candles) == 40
    assert res.error is None


@pytest.mark.asyncio
async def test_falls_back_to_hourly_when_daily_thin(monkeypatch):
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    # 1D vide -> on descend ; 4H (period=hour) fournit assez de bougies.
    _patch(
        monkeypatch,
        {
            _url("day"): FakeResponse(200, _payload([])),
            _url("hour"): FakeResponse(200, _payload(_rows(30))),
        },
    )
    res = await client.get_ohlcv(POOL)
    assert res.available is True
    assert res.timeframe == "4H"
    assert len(res.candles) == 30


@pytest.mark.asyncio
async def test_all_unavailable_is_graceful(monkeypatch):
    """26/07 -- day 404 ("pool not found") est une vraie erreur réseau, pas un
    "pas assez de bougies" -- la cascade s'arrête immédiatement, "hour" n'est
    JAMAIS appelé (le pool n'existera pas plus à une autre granularité)."""
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(monkeypatch, {_url("day"): FakeResponse(404)})
    res = await client.get_ohlcv(POOL)
    assert res.available is False
    assert res.candles == []
    assert res.error  # message explicite, jamais une bougie inventée
    # si le code avait quand même appelé "hour", FakeClient lèverait un
    # KeyError (absent du dict responses) -- confirme l'arrêt immédiat.


# ── #26/07 : une vraie erreur réseau (429/timeout/5xx/pool inconnu) arrête la
# cascade au lieu d'escalader -- distinct du cas légitime "pas assez de
# bougies après un succès HTTP", qui doit toujours continuer à escalader ──

@pytest.mark.asyncio
async def test_rate_limit_on_first_rung_stops_the_ladder(monkeypatch):
    """Un vrai 429 (3 tentatives internes épuisées) sur "day" ne doit JAMAIS
    déclencher un essai sur "hour" -- le rate limit s'applique à tout le
    pool/endpoint, pas à une granularité précise (confirmé en prod : le même
    pool a pris day->429 PUIS hour->429 dans la même rafale)."""
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(
        monkeypatch,
        {_url("day"): [FakeResponse(429), FakeResponse(429), FakeResponse(429)]},
    )
    res = await client.get_ohlcv(POOL)
    assert res.available is False
    assert "rate limit" in res.error
    # "hour" absent du dict responses -- un appel dessus lèverait un KeyError.


@pytest.mark.asyncio
async def test_rate_limit_on_second_rung_preserves_partial_result_from_first(monkeypatch):
    """Cas le plus important : "day" réussit mais avec peu de bougies (best
    partiel retenu), PUIS "hour" tape un vrai rate limit -- le résultat
    partiel du premier palier doit être retourné, jamais un échec total qui
    jetterait une donnée déjà obtenue à cause d'un problème réseau survenu
    APRÈS ce succès."""
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(
        monkeypatch,
        {
            _url("day"): FakeResponse(200, _payload(_rows(5))),  # < _MIN_USEFUL_CANDLES
            _url("hour"): [FakeResponse(429), FakeResponse(429), FakeResponse(429)],
        },
    )
    res = await client.get_ohlcv(POOL)
    assert res.available is True
    assert res.timeframe == "1D"
    assert len(res.candles) == 5


@pytest.mark.asyncio
async def test_timeout_on_first_rung_stops_the_ladder(monkeypatch):
    """Même doctrine que le rate limit -- un timeout confirmé (retry interne
    épuisé) est une vraie panne réseau, pas un signal "pas assez de bougies
    à ce grain" -- la cascade s'arrête, jamais un essai sur "hour"."""
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)

    class RaisingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None, headers=None):
            raise httpx.TransportError("boom")

    monkeypatch.setattr("aria_core.services.ohlcv.httpx.AsyncClient", lambda **kw: RaisingClient())

    async def _no_sleep(_):
        return None

    monkeypatch.setattr("aria_core.services.ohlcv.asyncio.sleep", _no_sleep)

    res = await client.get_ohlcv(POOL)
    assert res.available is False
    assert "timeout" in res.error


@pytest.mark.asyncio
async def test_empty_pool_address():
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    res = await client.get_ohlcv("   ")
    assert res.available is False
    assert res.error


# ── min_useful_candles (#182, 15/07, correctif de vitesse wallet-scoring) ──

@pytest.mark.asyncio
async def test_min_useful_candles_one_accepts_first_tier_immediately(monkeypatch):
    """Le wallet-scoring ne consomme qu'une seule bougie via price_at --
    min_useful_candles=1 doit accepter le palier journalier dès qu'il a AU
    MOINS une bougie, sans jamais escalader vers 4H/1H (économie de 2 appels
    GeckoTerminal par token pour un token jeune/microcap)."""
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(monkeypatch, {_url("day"): FakeResponse(200, _payload(_rows(1)))})

    res = await client.get_ohlcv(POOL, min_useful_candles=1)

    assert res.available is True
    assert res.timeframe == "1D"
    assert len(res.candles) == 1


@pytest.mark.asyncio
async def test_min_useful_candles_default_unchanged_for_existing_callers(monkeypatch):
    """Aucune régression pour les appelants existants (/vc, ta_levels) qui
    n'ont jamais passé ce paramètre -- 1 seule bougie journalière doit
    toujours déclencher l'escalade vers 4H, comme avant ce chantier."""
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(
        monkeypatch,
        {
            _url("day"): FakeResponse(200, _payload(_rows(1))),
            _url("hour"): FakeResponse(200, _payload(_rows(30))),
        },
    )

    res = await client.get_ohlcv(POOL)  # pas de min_useful_candles -- défaut

    assert res.available is True
    assert res.timeframe == "4H"  # escalade toujours déclenchée par défaut


@pytest.mark.asyncio
async def test_min_useful_candles_one_still_falls_back_if_first_tier_truly_empty(monkeypatch):
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(
        monkeypatch,
        {
            _url("day"): FakeResponse(200, _payload([])),
            _url("hour"): FakeResponse(200, _payload(_rows(1))),
        },
    )

    res = await client.get_ohlcv(POOL, min_useful_candles=1)

    assert res.available is True
    assert res.timeframe == "4H"


# ── shared GeckoTerminal throttle (07/24, throughput audit -- this client's
# own independent lock never coordinated with geckoterminal.py's) ───────────


@pytest.mark.asyncio
async def test_default_client_keeps_its_own_independent_lock(monkeypatch):
    """Non-régression : un client construit sans use_shared_throttle (les 7
    sites de test existants, min_interval=0.0 compris) n'appelle jamais le
    limiteur partagé."""
    called = False

    async def fake_shared(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        "aria_core.services.geckoterminal.wait_for_shared_rate_limit", fake_shared
    )
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)

    await client._throttle()

    assert called is False


@pytest.mark.asyncio
async def test_shared_throttle_opt_in_calls_the_shared_limiter(monkeypatch):
    called = False

    async def fake_shared(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        "aria_core.services.geckoterminal.wait_for_shared_rate_limit", fake_shared
    )
    client = OHLCVClient(base_url="https://gt.test", use_shared_throttle=True)

    await client._throttle()

    assert called is True


# ── mode="scalping" (Item #101, 26/07 -- dedicated 15min/30min sub-hour ladder) ─

@pytest.mark.asyncio
async def test_scalping_mode_uses_15min_candles_when_available(monkeypatch):
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(monkeypatch, {_url("minute"): FakeResponse(200, _payload(_rows(120)))})

    res = await client.get_ohlcv(POOL, mode="scalping")

    assert res.available is True
    assert res.timeframe == "15M"
    assert len(res.candles) == 120


@pytest.mark.asyncio
async def test_scalping_mode_falls_back_to_30min_when_15min_thin(monkeypatch):
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    # 15min vide -> descend au 30min (même period="minute", aggregate différent).
    _patch(
        monkeypatch,
        {_url("minute"): [FakeResponse(200, _payload([])), FakeResponse(200, _payload(_rows(50)))]},
    )

    res = await client.get_ohlcv(POOL, mode="scalping")

    assert res.available is True
    assert res.timeframe == "30M"
    assert len(res.candles) == 50


@pytest.mark.asyncio
async def test_scalping_mode_never_falls_back_to_standard_ladder(monkeypatch):
    """Si 15min échoue avec une vraie erreur réseau, le mode scalping renvoie
    available=False -- il ne dégrade JAMAIS vers le ladder standard (day/hour),
    qui corromprait silencieusement la lecture RSI/golden-pocket calibrée pour
    du 15-30min avec des bougies day-scale. 26/07 : 30min n'est même plus
    tenté (même vraie erreur réseau que 15min, un SEUL appel "minute")."""
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(monkeypatch, {_url("minute"): [FakeResponse(404)]})

    res = await client.get_ohlcv(POOL, mode="scalping")

    assert res.available is False
    assert res.candles == []
    assert res.error
    # liste à un seul élément -- si le code avait quand même retenté 30min,
    # FakeClient lèverait un IndexError (pop(0) sur liste vide).


@pytest.mark.asyncio
async def test_standard_mode_never_calls_minute_endpoint(monkeypatch):
    """Non-régression : mode="standard" (défaut, tous les appelants existants)
    ne touche jamais au endpoint minute -- comportement 1D/4H/1H inchangé."""
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(monkeypatch, {_url("day"): FakeResponse(200, _payload(_rows(40)))})

    res = await client.get_ohlcv(POOL)  # mode par défaut = "standard"

    assert res.available is True
    assert res.timeframe == "1D"
    # aucune réponse "minute" n'a été enregistrée -- si le code y avait appelé,
    # FakeClient.get lèverait un KeyError (non attrapé), faisant échouer le test.


@pytest.mark.asyncio
async def test_module_singleton_uses_the_shared_throttle():
    """The one real-production instance (ohlcv_client) must opt in -- this is
    exactly the fix for the dual-lock gap the audit found (smart_money.py's
    per-token loop calls both gecko.resolve_primary_pool and gecko.get_ohlcv,
    which delegates here)."""
    from aria_core.services.ohlcv import ohlcv_client

    assert ohlcv_client._use_shared_throttle is True
