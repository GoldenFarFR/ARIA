"""Tests du client GeckoTerminal (lecture seule, #157) — aucun appel réseau
réel, tout est mocké."""

import pytest

from aria_core.services.geckoterminal import GeckoTerminalClient, _pool_is_plausible, price_at


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


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.geckoterminal.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.geckoterminal.asyncio.sleep", _fake_sleep)


@pytest.mark.asyncio
async def test_get_pool_created_at_parses_timestamp(monkeypatch):
    client = GeckoTerminalClient()
    url = f"{client.base_url}/networks/base/pools/0xpool"
    _patch_client(
        monkeypatch,
        {url: FakeResponse(200, {"data": {"attributes": {"pool_created_at": "2026-07-02T13:07:59Z"}}})},
    )

    result = await client.get_pool_created_at("0xpool")

    assert result.available is True
    assert result.created_at.year == 2026
    assert result.created_at.month == 7


@pytest.mark.asyncio
async def test_get_pool_created_at_missing_date_unavailable(monkeypatch):
    client = GeckoTerminalClient()
    url = f"{client.base_url}/networks/base/pools/0xpool"
    _patch_client(monkeypatch, {url: FakeResponse(200, {"data": {"attributes": {}}})})

    result = await client.get_pool_created_at("0xpool")

    assert result.available is False
    assert "indisponible" in result.error


@pytest.mark.asyncio
async def test_get_ohlcv_delegates_to_wide_ohlcv_client(monkeypatch):
    # #157, correction 14/07 : get_ohlcv ne fait plus sa propre requête HTTP
    # (fenêtre fixe ~8 jours, trop étroite -- root cause confirmée des jambes
    # "sans prix" persistant après le fix retry/429) -- délègue désormais à
    # services.ohlcv.ohlcv_client (échelle jour->4h->1h, déjà éprouvée en prod).
    from aria_core.services import ohlcv as ohlcv_module
    from aria_core.skills.ta_levels import Candle

    async def _fake_wide_get_ohlcv(pool_address, *, network="base"):
        assert pool_address == "0xpool"
        return ohlcv_module.OHLCVResult(
            pool_address=pool_address,
            network=network,
            candles=[
                Candle(ts=100, open=1.0, high=1.5, low=0.9, close=1.2, volume=1000.0),
                Candle(ts=200, open=2.0, high=2.5, low=1.9, close=2.2, volume=500.0),
            ],
            timeframe="1D",
            available=True,
        )

    monkeypatch.setattr(ohlcv_module.ohlcv_client, "get_ohlcv", _fake_wide_get_ohlcv)

    client = GeckoTerminalClient()
    result = await client.get_ohlcv("0xpool")

    assert result.available is True
    assert [c.ts for c in result.candles] == [100, 200]  # trié


@pytest.mark.asyncio
async def test_get_ohlcv_unavailable_when_wide_client_has_nothing(monkeypatch):
    from aria_core.services import ohlcv as ohlcv_module

    async def _fake_wide_get_ohlcv(pool_address, *, network="base"):
        return ohlcv_module.OHLCVResult(pool_address=pool_address, network=network, error="pool introuvable sur GeckoTerminal")

    monkeypatch.setattr(ohlcv_module.ohlcv_client, "get_ohlcv", _fake_wide_get_ohlcv)

    client = GeckoTerminalClient()
    result = await client.get_ohlcv("0xpool")

    assert result.available is False
    assert result.candles == []


class TestResolvePrimaryPool:
    """#157, correction 14/07 : `get_pool_created_at`/`get_ohlcv` attendent une
    adresse de POOL, pas un contrat de TOKEN -- `resolve_primary_pool` corrige un
    bug latent où le code appelant passait directement l'adresse du token."""

    @pytest.mark.asyncio
    async def test_picks_pool_with_highest_liquidity(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_client(
            monkeypatch,
            {
                url: FakeResponse(
                    200,
                    {
                        "data": [
                            {
                                "attributes": {
                                    "address": "0xpool_low",
                                    "reserve_in_usd": "10.0",
                                    "pool_created_at": "2026-01-01T00:00:00Z",
                                }
                            },
                            {
                                "attributes": {
                                    "address": "0xpool_high",
                                    "reserve_in_usd": "50000.0",
                                    "pool_created_at": "2026-02-01T00:00:00Z",
                                }
                            },
                        ]
                    },
                )
            },
        )

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is True
        assert result.pool_address == "0xpool_high"
        assert result.created_at.month == 2

    @pytest.mark.asyncio
    async def test_no_pools_found_unavailable_never_guesses_token_as_pool(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_client(monkeypatch, {url: FakeResponse(200, {"data": []})})

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is False
        assert result.pool_address == "0xtoken"  # jamais présenté comme un vrai pool résolu (available=False)

    @pytest.mark.asyncio
    async def test_malformed_reserve_treated_as_zero_not_a_crash(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_client(
            monkeypatch,
            {
                url: FakeResponse(
                    200,
                    {
                        "data": [
                            {"attributes": {"address": "0xpool_a", "reserve_in_usd": "not-a-number"}},
                            {"attributes": {"address": "0xpool_b", "reserve_in_usd": "1.0"}},
                        ]
                    },
                )
            },
        )

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is True
        assert result.pool_address == "0xpool_b"

    @pytest.mark.asyncio
    async def test_error_response_propagates_unavailable(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_no_sleep(monkeypatch)
        _patch_client(monkeypatch, {url: FakeResponse(429)})

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is False

    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self, monkeypatch):
        """#157, correction 14/07 -- un 429 isolé ne doit plus abandonner net."""
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_no_sleep(monkeypatch)
        _patch_client(
            monkeypatch,
            {
                url: [
                    FakeResponse(429),
                    FakeResponse(
                        200,
                        {"data": [{"attributes": {"address": "0xpool_a", "reserve_in_usd": "10"}}]},
                    ),
                ]
            },
        )

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is True
        assert result.pool_address == "0xpool_a"

    @pytest.mark.asyncio
    async def test_429_gives_up_after_max_retries(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_no_sleep(monkeypatch)
        _patch_client(monkeypatch, {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is False
        assert "rate limit" in result.error

    @pytest.mark.asyncio
    async def test_network_param_threaded_into_url(self, monkeypatch):
        # #157, multi-chaînes 14/07 : resolve_primary_pool doit interroger le
        # réseau demandé (ex. "eth"), jamais "base" en dur.
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/eth/tokens/0xtoken/pools"
        _patch_client(
            monkeypatch,
            {url: FakeResponse(200, {"data": [{"attributes": {"address": "0xpool_eth", "reserve_in_usd": "100"}}]})},
        )

        result = await client.resolve_primary_pool("0xtoken", network="eth")

        assert result.available is True
        assert result.pool_address == "0xpool_eth"

    @pytest.mark.asyncio
    async def test_single_pool_never_filtered_even_if_ratio_looks_implausible(self, monkeypatch):
        """Correctif 14/07 (relecture pré-merge, sélection de pool) : un token à
        POOL UNIQUE (immense majorité des cas hors wallet-scoring, ex. /vc) doit
        toujours obtenir ce pool, MÊME si son ratio réserve/volume ressemblerait
        au cas corrompu réel qui a motivé ce correctif -- rien à départager avec
        un seul candidat, le filtre de plausibilité ne s'applique jamais ici."""
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_client(
            monkeypatch,
            {
                url: FakeResponse(
                    200,
                    {
                        "data": [
                            {
                                "attributes": {
                                    "address": "0xonly_pool",
                                    "reserve_in_usd": "7622967873.4775",
                                    "volume_usd": {"h24": "37309.9666477679"},
                                }
                            }
                        ]
                    },
                )
            },
        )

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is True
        assert result.pool_address == "0xonly_pool"

    @pytest.mark.asyncio
    async def test_all_pools_implausible_fails_honestly_instead_of_picking_worst(self, monkeypatch):
        """Quand PLUSIEURS pools existent et qu'AUCUN ne passe le filtre de
        plausibilité, l'échec doit être honnête (`available=False`) plutôt que
        de retomber sur le moins pire d'un lot déjà jugé incohérent."""
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_client(
            monkeypatch,
            {
                url: FakeResponse(
                    200,
                    {
                        "data": [
                            # réserve géante, volume quasi nul -- signal de donnée corrompue.
                            {"attributes": {"address": "0xinflated", "reserve_in_usd": "5000000000", "volume_usd": {"h24": "10"}}},
                            # volume géant, réserve quasi nulle -- signal de wash-trading.
                            {"attributes": {"address": "0xwashtraded", "reserve_in_usd": "5", "volume_usd": {"h24": "5000000"}}},
                        ]
                    },
                )
            },
        )

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is False
        assert "plausible" in result.error

    @pytest.mark.asyncio
    async def test_picks_highest_volume_among_plausible_pools_reserve_as_tiebreak(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_client(
            monkeypatch,
            {
                url: FakeResponse(
                    200,
                    {
                        "data": [
                            {"attributes": {"address": "0xlow_volume", "reserve_in_usd": "1000000", "volume_usd": {"h24": "50000"}}},
                            {"attributes": {"address": "0xhigh_volume", "reserve_in_usd": "500000", "volume_usd": {"h24": "800000"}}},
                        ]
                    },
                )
            },
        )

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is True
        assert result.pool_address == "0xhigh_volume"


class TestPoolIsPlausible:
    """Filtre isolé (correctif 14/07) -- garde-fou symétrique : réserve gonflée
    sans volume réel (donnée corrompue) OU volume gonflé sans réserve réelle
    (wash-trading) sont tous deux jugés implausibles."""

    def test_normal_ratio_is_plausible(self):
        # Ratio réel observé sur le pool WETH/USDC légitime (14/07) : ~1.41x.
        assert _pool_is_plausible(106196962.2335, 75267968.567069) is True

    def test_inflated_reserve_tiny_volume_is_implausible(self):
        # Cas réel confirmé (14/07) : ratio ~204 000x -- pool WETH corrompu qui
        # avait été silencieusement sélectionné avant ce correctif.
        assert _pool_is_plausible(7622967873.4775, 37309.9666477679) is False

    def test_inflated_volume_tiny_reserve_is_implausible(self):
        # Symétrique : wash-trading (volume déclaré sans réserve réelle).
        assert _pool_is_plausible(5.0, 5_000_000.0) is False

    def test_zero_or_negative_reserve_always_implausible(self):
        assert _pool_is_plausible(0.0, 1000.0) is False
        assert _pool_is_plausible(-5.0, 1000.0) is False

    def test_zero_volume_alone_not_disqualifying(self):
        # Un token légitime peut simplement n'avoir eu aucun trade dans les
        # dernières 24h -- ne pas le pénaliser pour ça seul.
        assert _pool_is_plausible(10000.0, 0.0) is True


class TestWethPoolSelectionRegression:
    """Régression 14/07 (relecture pré-merge, prototype prix-depuis-swap) :
    données réelles capturées via un appel direct à l'API GeckoTerminal pour
    WETH sur Base (20 pools), incluant le pool corrompu (`Bnb / WETH 0.3%`,
    réserve déclarée 7,6 milliards de dollars pour 37 000 dollars de volume
    24h) qui était sélectionné par l'ancien critère ("plus fort
    reserve_in_usd" seul) à la place du vrai pool WETH/USDC utilisé dans la
    transaction réelle 0x9ef4f224b8b347d0aa21ccb258d5c63bd4767e400329001fc963e8e5596c4923
    (wallet 0xbae88c80d8b362bbd09a39b60dd5eb15b3ed36b0), causant un écart de
    prix de ~8x jamais signalé comme erreur."""

    _REAL_WETH_POOLS = [
        ("0x6c561b446416e1a00e8e93e221854d6ea4171372", "106196962.2335", "75267968.567069"),
        ("0x42d4a22cad0f5a49681a5715ce994af73a43b76b", "5416629.0015", "71458798.8795574"),
        ("0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59", "8971818.7064", "34106932.4495687"),
        ("0x523c7fe4f1bc157e5af4339d5530c20947b2fddf", "7622967873.4775", "37309.9666477679"),  # corrompu
        ("0x3fe04a59ebd38cf06080a6f60a98d124eb59392a", "4291703.3585", "51767380.8896011"),
        ("0xd0b53d9277642d899df5c87a3966a349a798f224", "10344627.6515", "16668250.3797106"),
        ("0xc211e1f853a898bd1302385ccde55f33a8c4b3f3", "3822218.1909", "38754387.9942906"),
        ("0x72ab388e2e2f6facef59e3c3fa2c4e29011c2d38", "3510337.0018", "33752204.9975264"),
        ("0x70acdf2ad0bf2402c957154f944c19ef4e1cbae1", "16071169.9228", "5842714.18365213"),
        ("0x74f72788f4814d7ff3c49b44684aa98eee140c0e", "26085123.8159", "2256538.21087803"),
        ("0x8c7080564b5a792a33ef2fd473fba6364d5495e5", "10756736.6722", "3523388.5926364"),
        ("0x7aea2e8a3843516afa07293a10ac8e49906dabd1", "3988446.4377", "8036035.17845976"),
        ("0x47ca96ea59c13f72745928887f84c9f52c3d7348", "2683704.6198", "11064036.1577872"),
        ("0xb1383dc47d9971fc999c3a9088f79e744b376e97", "2442818.2894", "5654330.80007406"),
        ("0xb775272e537cc670c65dc852908ad47015244eaf", "1813749.3127", "3864319.61222483"),
        ("0x01784ef301d79e4b2df3a21ad9a536d4cf09a5ce", "8865190.9828", "635360.242030753"),
        ("0x3f9b863ef4b295d6ba370215bcca3785fcc44f44", "1780920.5585", "1822018.89479215"),
        ("0x4e506648d493c8870f55e870480f92f2f33ece51", "1809376.9931", "1718381.22563409"),
        ("0x7ec6c9d993d9832aa654593f2dbc21303650bc6c", "2078996.2213", "1169607.24519356"),
        ("0x7c7420dd105e2779316423ba3e973f434315efa9", "281037.5904", "8583450.9886989"),
    ]
    _CORRUPTED_POOL = "0x523c7fe4f1bc157e5af4339d5530c20947b2fddf"
    _REAL_POOL = "0x6c561b446416e1a00e8e93e221854d6ea4171372"

    @staticmethod
    def _old_pick_max_reserve_only(pools):
        """Reproduction fidèle de l'ANCIEN critère ("plus fort reserve_in_usd"
        seul, sans filtre) -- utilisée ici uniquement pour PROUVER que le bug
        se reproduit avec l'ancienne logique sur ces données réelles, en
        contraste direct avec le comportement du client corrigé ci-dessous."""
        return max(pools, key=lambda p: float(p[1]))[0]

    def test_old_criterion_would_have_picked_corrupted_pool(self):
        # Reproduction du bug AVANT correctif, sur les données réelles capturées.
        assert self._old_pick_max_reserve_only(self._REAL_WETH_POOLS) == self._CORRUPTED_POOL

    @pytest.mark.asyncio
    async def test_fixed_client_picks_real_pool_not_corrupted_one(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xweth/pools"
        _patch_client(
            monkeypatch,
            {
                url: FakeResponse(
                    200,
                    {
                        "data": [
                            {"attributes": {"address": addr, "reserve_in_usd": reserve, "volume_usd": {"h24": volume}}}
                            for addr, reserve, volume in self._REAL_WETH_POOLS
                        ]
                    },
                )
            },
        )

        result = await client.resolve_primary_pool("0xweth")

        assert result.available is True
        assert result.pool_address == self._REAL_POOL
        assert result.pool_address != self._CORRUPTED_POOL

    @pytest.mark.asyncio
    async def test_resolved_pool_price_matches_real_swap_not_the_8x_divergence(self, monkeypatch):
        """Suite de `test_fixed_client_picks_real_pool_not_corrupted_one` --
        vérifie que le correctif change bien le PRIX final utilisé, pas
        seulement l'adresse de pool sélectionnée. Prix implicite réel du swap
        0x9ef4f224... (WETH->cbBTC->USDC, 1.00682... WETH pour 2037.830289
        USDC) : ~2024.35 USDC/WETH -- c'est le prix que le pool corrigé
        (`_REAL_POOL`) doit renvoyer. Le pool corrompu (`_CORRUPTED_POOL`)
        renvoyait ~244.66 (valeur mesurée en direct le 14/07 via
        `price_at` sur ses vraies bougies GeckoTerminal) -- ~8x d'écart,
        reproduit ici par construction pour prouver qu'il disparaît."""
        from aria_core.services import ohlcv as ohlcv_module
        from aria_core.skills.ta_levels import Candle

        tx_ts = 1770638279  # ts réel de la tx 0x9ef4f224... (2026-02-09T11:57:59Z)
        prices_by_pool = {
            self._REAL_POOL: 2024.35,  # prix implicite réel du swap (ratio in/out)
            self._CORRUPTED_POOL: 244.664600282265,  # valeur mesurée en direct le 14/07, pool corrompu
        }

        async def _fake_wide_get_ohlcv(pool_address, *, network="base"):
            close = prices_by_pool[pool_address]
            return ohlcv_module.OHLCVResult(
                pool_address=pool_address,
                network=network,
                candles=[Candle(ts=tx_ts, open=close, high=close, low=close, close=close, volume=1.0)],
                timeframe="1D",
                available=True,
            )

        monkeypatch.setattr(ohlcv_module.ohlcv_client, "get_ohlcv", _fake_wide_get_ohlcv)

        gecko_pools_url = f"{GeckoTerminalClient().base_url}/networks/base/tokens/0xweth/pools"
        client = GeckoTerminalClient()
        _patch_client(
            monkeypatch,
            {
                gecko_pools_url: FakeResponse(
                    200,
                    {
                        "data": [
                            {"attributes": {"address": addr, "reserve_in_usd": reserve, "volume_usd": {"h24": volume}}}
                            for addr, reserve, volume in self._REAL_WETH_POOLS
                        ]
                    },
                )
            },
        )

        pool_meta = await client.resolve_primary_pool("0xweth")
        assert pool_meta.pool_address == self._REAL_POOL

        ohlcv = await client.get_ohlcv(pool_meta.pool_address)
        price = price_at(ohlcv, tx_ts)

        # Le prix résolu doit correspondre au vrai prix du swap, pas à la
        # valeur ~8x plus basse que le pool corrompu aurait renvoyée.
        assert price == pytest.approx(2024.35, rel=1e-6)
        assert price != pytest.approx(244.664600282265, rel=0.1)


class TestGetOhlcvNetworkParam:
    @pytest.mark.asyncio
    async def test_get_ohlcv_forwards_network_to_wide_client(self, monkeypatch):
        # #157, multi-chaînes 14/07 : le network demandé doit atteindre
        # services.ohlcv.ohlcv_client, jamais rester bloqué sur "base".
        from aria_core.services import ohlcv as ohlcv_module

        captured = {}

        async def _fake_wide_get_ohlcv(pool_address, *, network="base"):
            captured["network"] = network
            return ohlcv_module.OHLCVResult(pool_address=pool_address, network=network, candles=[], available=False, error="vide")

        monkeypatch.setattr(ohlcv_module.ohlcv_client, "get_ohlcv", _fake_wide_get_ohlcv)

        client = GeckoTerminalClient()
        await client.get_ohlcv("0xpool", network="bsc")

        assert captured["network"] == "bsc"
