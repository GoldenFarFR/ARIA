"""Tests du client Dune Analytics (#157/15-07, Execute SQL API) -- aucun appel
réseau réel, tout est mocké au niveau httpx.AsyncClient (même patron que
test_coinmarketcap_client.py/test_defillama_client.py)."""

import pytest

from aria_core.services import dune


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
    ``_request`` -- ``_responses``/``calls`` doivent être PARTAGÉS entre
    toutes les instances créées par une même ``_patch_client`` (même
    correctif que test_coinmarketcap_client.py, sinon une séquence de retry
    revoit la même première réponse en boucle)."""

    def __init__(self, responses: list, calls: list):
        self._responses = responses
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers=None):
        self.calls.append(("GET", url, None, headers))
        return self._responses.pop(0)

    async def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, json, headers))
        return self._responses.pop(0)


def _patch_client(monkeypatch, responses):
    shared_responses = list(responses)
    shared_calls = []
    holder = {"calls": shared_calls}

    def factory(**kw):
        return FakeClient(shared_responses, shared_calls)

    monkeypatch.setattr("aria_core.services.dune.httpx.AsyncClient", factory)
    return holder


async def _no_sleep(_seconds):
    return None


class TestApiKeyGate:
    @pytest.mark.asyncio
    async def test_no_key_returns_unavailable_without_any_http_call(self, monkeypatch):
        monkeypatch.delenv("DUNE_API_KEY", raising=False)
        holder = _patch_client(monkeypatch, [])

        data, error = await dune._request("GET", "/v1/execution/abc/status")

        assert data is None
        assert "DUNE_API_KEY absente" in error
        assert holder["calls"] == []  # aucun appel HTTP tenté

    def test_is_dune_configured(self, monkeypatch):
        monkeypatch.delenv("DUNE_API_KEY", raising=False)
        assert dune.is_dune_configured() is False
        monkeypatch.setenv("DUNE_API_KEY", "some-key")
        assert dune.is_dune_configured() is True

    @pytest.mark.asyncio
    async def test_key_sent_in_header_when_present(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "test-key-123")
        holder = _patch_client(monkeypatch, [FakeResponse(200, {"ok": True})])

        await dune._request("GET", "/v1/execution/abc/status")

        _, _, _, headers = holder["calls"][0]
        assert headers["X-Dune-Api-Key"] == "test-key-123"


class TestDomeRetry:
    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(429), FakeResponse(200, {"ok": True})])

        data, error = await dune._request("GET", "/v1/execution/abc/status")

        assert error is None
        assert data == {"ok": True}

    @pytest.mark.asyncio
    async def test_429_exhausted_after_three_attempts(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(429), FakeResponse(429), FakeResponse(429)])

        data, error = await dune._request("GET", "/v1/execution/abc/status")

        assert data is None
        assert "rate limit" in error

    @pytest.mark.asyncio
    async def test_5xx_retries_once_then_fails(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])

        data, error = await dune._request("GET", "/v1/execution/abc/status")

        assert data is None
        assert "erreur serveur" in error

    @pytest.mark.asyncio
    async def test_timeout_retries_once_then_fails(self, monkeypatch):
        import httpx

        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)

        class TimeoutClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, *a, **kw):
                raise httpx.TransportError("boom")

        monkeypatch.setattr("aria_core.services.dune.httpx.AsyncClient", lambda **kw: TimeoutClient())

        data, error = await dune._request("GET", "/v1/execution/abc/status")

        assert data is None
        assert "timeout" in error

    @pytest.mark.asyncio
    async def test_401_invalid_key_unavailable_not_a_crash(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "bad-key")
        _patch_client(monkeypatch, [FakeResponse(401)])

        data, error = await dune._request("GET", "/v1/execution/abc/status")

        assert data is None
        assert "clé Dune invalide" in error


class TestExecuteSql:
    @pytest.mark.asyncio
    async def test_success_returns_execution_id(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        holder = _patch_client(monkeypatch, [FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_PENDING"})])

        result = await dune.execute_sql("SELECT 1", performance="medium")

        assert result.available is True
        assert result.execution_id == "exec-1"
        method, url, body, _ = holder["calls"][0]
        assert method == "POST"
        assert url.endswith("/v1/sql/execute")
        assert body == {"sql": "SELECT 1", "performance": "medium"}

    @pytest.mark.asyncio
    async def test_default_performance_tier_is_small(self, monkeypatch):
        """18/07 -- bug réel trouvé en testant en direct avec une vraie clé (compte
        gratuit) : "medium" ET "large" sont rejetés par l'API ("Invalid performance
        tier"), contrairement à ce que suggérait la doc générale -- seul "small"
        fonctionne sur ce compte. Verrouille le défaut corrigé pour ne pas régresser."""
        monkeypatch.setenv("DUNE_API_KEY", "k")
        holder = _patch_client(monkeypatch, [FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_PENDING"})])

        await dune.execute_sql("SELECT 1")

        _, _, body, _ = holder["calls"][0]
        assert body["performance"] == "small"

    @pytest.mark.asyncio
    async def test_missing_execution_id_unavailable(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        _patch_client(monkeypatch, [FakeResponse(200, {"state": "QUERY_STATE_PENDING"})])

        result = await dune.execute_sql("SELECT 1")

        assert result.available is False
        assert "execution_id absent" in result.error

    @pytest.mark.asyncio
    async def test_unexpected_shape_unavailable_never_raises(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        _patch_client(monkeypatch, [FakeResponse(200, ["not", "a", "dict"])])

        result = await dune.execute_sql("SELECT 1")

        assert result.available is False


class TestGetExecutionStatus:
    @pytest.mark.asyncio
    async def test_success_parses_fields(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        _patch_client(
            monkeypatch,
            [FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_COMPLETED", "is_execution_finished": True})],
        )

        status = await dune.get_execution_status("exec-1")

        assert status.available is True
        assert status.state == "QUERY_STATE_COMPLETED"
        assert status.is_execution_finished is True

    @pytest.mark.asyncio
    async def test_malformed_response_unavailable(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        _patch_client(monkeypatch, [FakeResponse(200, "bogus")])

        status = await dune.get_execution_status("exec-1")

        assert status.available is False


class TestGetExecutionResult:
    @pytest.mark.asyncio
    async def test_success_parses_rows(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        _patch_client(
            monkeypatch,
            [
                FakeResponse(
                    200,
                    {
                        "execution_id": "exec-1",
                        "result": {
                            "rows": [{"wallet_address": "0xabc", "peak_multiple": 12.5}, {"wallet_address": "0xdef", "peak_multiple": 7.1}],
                            "metadata": {"row_count": 2},
                        },
                    },
                )
            ],
        )

        result = await dune.get_execution_result("exec-1")

        assert result.available is True
        assert len(result.rows) == 2
        assert result.row_count == 2
        assert result.rows[0]["wallet_address"] == "0xabc"

    @pytest.mark.asyncio
    async def test_missing_result_unavailable(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        _patch_client(monkeypatch, [FakeResponse(200, {"execution_id": "exec-1"})])

        result = await dune.get_execution_result("exec-1")

        assert result.available is False
        assert "result absent" in result.error

    @pytest.mark.asyncio
    async def test_missing_rows_unavailable(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        _patch_client(monkeypatch, [FakeResponse(200, {"execution_id": "exec-1", "result": {"metadata": {}}})])

        result = await dune.get_execution_result("exec-1")

        assert result.available is False
        assert "rows absent" in result.error

    @pytest.mark.asyncio
    async def test_malformed_rows_skipped_not_a_crash(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        _patch_client(
            monkeypatch,
            [FakeResponse(200, {"execution_id": "exec-1", "result": {"rows": ["not-a-dict", {"wallet_address": "0xabc"}]}})],
        )

        result = await dune.get_execution_result("exec-1")

        assert result.available is True
        assert len(result.rows) == 1


class TestRunSqlAndWait:
    @pytest.mark.asyncio
    async def test_happy_path_polls_then_fetches_result(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_PENDING"}),  # execute_sql
                FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_EXECUTING", "is_execution_finished": False}),  # 1er poll
                FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_COMPLETED", "is_execution_finished": True}),  # 2e poll
                FakeResponse(200, {"execution_id": "exec-1", "result": {"rows": [{"wallet_address": "0xabc"}]}}),  # résultat
            ],
        )

        result = await dune.run_sql_and_wait("SELECT 1", poll_interval=0.0, max_wait=10.0)

        assert result.available is True
        assert result.rows == [{"wallet_address": "0xabc"}]

    @pytest.mark.asyncio
    async def test_execute_sql_failure_propagates_immediately(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)

        result = await dune.run_sql_and_wait("SELECT 1")

        assert result.available is False

    @pytest.mark.asyncio
    async def test_terminal_failure_state_unavailable_never_crashes(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_PENDING"}),
                FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_FAILED", "is_execution_finished": True}),
            ],
        )

        result = await dune.run_sql_and_wait("SELECT 1", poll_interval=0.0)

        assert result.available is False
        assert "QUERY_STATE_FAILED" in result.error

    @pytest.mark.asyncio
    async def test_max_wait_exceeded_bounded_never_infinite(self, monkeypatch):
        """Jamais une attente non bornée -- même si Dune ne termine jamais
        l'exécution, `run_sql_and_wait` abandonne proprement après `max_wait`."""
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)

        pending_status = FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_EXECUTING", "is_execution_finished": False})
        responses = [FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_PENDING"})]
        responses += [pending_status] * 20  # largement assez pour dépasser max_wait avec poll_interval=1.0
        _patch_client(monkeypatch, responses)

        result = await dune.run_sql_and_wait("SELECT 1", poll_interval=1.0, max_wait=3.0)

        assert result.available is False
        assert "délai d'exécution dépassé" in result.error


class TestBuildEarlyBuyerMultipleQuery:
    def test_substitutes_parameters(self):
        sql = dune.build_early_buyer_multiple_query(min_multiple=5.0, lookback_days=30)
        assert "5.0" in sql
        assert "30" in sql
        assert "dex.trades" in sql
        assert "blockchain = 'base'" in sql

    def test_token_launch_filters_via_having_not_where(self):
        """Correctif de revue (15/07, avant merge) : un token ÉTABLI dont le
        premier trade DANS la fenêtre lookback_days tombe par hasard il y a
        `lookback_days` jours ne doit JAMAIS être classé comme "vient de
        naître" -- l'agrégat MIN(block_time) doit porter sur l'historique
        complet (aucun filtre de date dans le WHERE de token_launch), et
        seul le résultat agrégé est filtré via HAVING."""
        sql = dune.build_early_buyer_multiple_query(min_multiple=5.0, lookback_days=30)
        token_launch_cte = sql.split("token_launch AS (")[1].split("early_buyers AS (")[0]
        assert "HAVING MIN(block_time) >= NOW() - INTERVAL '30' day" in token_launch_cte
        assert "WHERE blockchain = 'base'\n    GROUP BY" in token_launch_cte

    def test_rejects_non_numeric_min_multiple(self):
        with pytest.raises(ValueError):
            dune.build_early_buyer_multiple_query(min_multiple="5x", lookback_days=30)

    def test_rejects_non_positive_lookback_days(self):
        with pytest.raises(ValueError):
            dune.build_early_buyer_multiple_query(min_multiple=5.0, lookback_days=0)

    def test_rejects_non_int_lookback_days(self):
        with pytest.raises(ValueError):
            dune.build_early_buyer_multiple_query(min_multiple=5.0, lookback_days=30.5)

    def test_dust_filter_applied_to_both_peak_and_launch_price(self):
        """#185, 15/07 -- correctif du bug réel trouvé en vérification live
        (peak_multiple aberrant ~10^22x, causé par une division sur un prix
        quasi-nul issu d'un trade de dust). Le plancher amount_usd doit
        s'appliquer aux DEUX côtés de la division (token_peak ET
        token_launch_price) -- le bug pouvait toucher n'importe quel côté,
        pas seulement le prix de lancement."""
        sql = dune.build_early_buyer_multiple_query(min_multiple=5.0, lookback_days=30, min_trade_usd=2.5)

        token_peak_cte = sql.split("token_peak AS (")[1].split("token_launch_price AS (")[0]
        token_launch_price_cte = sql.split("token_launch_price AS (")[1].split(")\nSELECT")[0]
        assert "amount_usd >= 2.5" in token_peak_cte
        assert "amount_usd >= 2.5" in token_launch_price_cte

    def test_min_trade_usd_defaults_to_one(self):
        sql = dune.build_early_buyer_multiple_query(min_multiple=5.0, lookback_days=30)
        assert "amount_usd >= 1.0" in sql

    def test_rejects_non_positive_min_trade_usd(self):
        with pytest.raises(ValueError):
            dune.build_early_buyer_multiple_query(min_multiple=5.0, lookback_days=30, min_trade_usd=0)

    def test_rejects_non_numeric_min_trade_usd(self):
        with pytest.raises(ValueError):
            dune.build_early_buyer_multiple_query(min_multiple=5.0, lookback_days=30, min_trade_usd="1$")


class TestBuildRecentBasePairsQuery:
    """Deuxième source de découverte de tokens Base (#134, 15/07) -- même
    Execute SQL API, aucun nouveau client. Portée de cette tâche : la
    requête et sa validation SEULEMENT (pas de branchement pipeline)."""

    def test_substitutes_parameters(self):
        sql = dune.build_recent_base_pairs_query(min_volume_usd=5000.0, lookback_hours=48)
        assert "5000.0" in sql
        assert "48" in sql
        assert "dex.trades" in sql
        assert "blockchain = 'base'" in sql

    def test_token_launch_filters_via_having_not_where(self):
        """Même piège que celui corrigé (15/07, avant merge) sur
        build_early_buyer_multiple_query : un token ÉTABLI dont le premier
        trade DANS la fenêtre lookback_hours tombe par hasard il y a
        `lookback_hours` heures ne doit JAMAIS être classé comme "vient de
        naître" -- l'agrégat MIN(block_time) doit porter sur l'historique
        complet (aucun filtre de date dans le WHERE de token_launch), et
        seul le résultat agrégé est filtré via HAVING."""
        sql = dune.build_recent_base_pairs_query(min_volume_usd=5000.0, lookback_hours=48)
        token_launch_cte = sql.split("token_launch AS (")[1].split("recent_volume AS (")[0]
        assert "HAVING MIN(block_time) >= NOW() - INTERVAL '48' hour" in token_launch_cte
        assert "WHERE blockchain = 'base'\n    GROUP BY" in token_launch_cte

    def test_recent_volume_bounded_by_window_not_a_regression(self):
        """recent_volume PEUT être borné directement par lookback_hours dans
        son WHERE (contrairement à token_launch) -- un token dont le
        launch_time tombe dans la fenêtre a par construction tous ses trades
        dans la fenêtre aussi, même raisonnement déjà appliqué à
        token_peak/token_launch_price dans build_early_buyer_multiple_query."""
        sql = dune.build_recent_base_pairs_query(min_volume_usd=5000.0, lookback_hours=48)
        recent_volume_cte = sql.split("recent_volume AS (")[1].split("SELECT\n    tl.token_address")[0]
        assert "block_time >= NOW() - INTERVAL '48' hour" in recent_volume_cte

    def test_min_volume_applied_in_outer_where(self):
        sql = dune.build_recent_base_pairs_query(min_volume_usd=5000.0, lookback_hours=48)
        assert "WHERE rv.volume_usd >= 5000.0" in sql

    def test_rejects_non_numeric_min_volume_usd(self):
        with pytest.raises(ValueError):
            dune.build_recent_base_pairs_query(min_volume_usd="lots", lookback_hours=48)

    def test_rejects_non_positive_min_volume_usd(self):
        with pytest.raises(ValueError):
            dune.build_recent_base_pairs_query(min_volume_usd=0, lookback_hours=48)

    def test_rejects_non_positive_lookback_hours(self):
        with pytest.raises(ValueError):
            dune.build_recent_base_pairs_query(min_volume_usd=5000.0, lookback_hours=0)

    def test_rejects_non_int_lookback_hours(self):
        with pytest.raises(ValueError):
            dune.build_recent_base_pairs_query(min_volume_usd=5000.0, lookback_hours=48.5)


WETH_BASE = "0x4200000000000000000000000000000000000006"
FUNDER = "0xe8a3ecea7d6a688ee903173024225357ddf29e93"


class TestBuildAddressesStatsQuery:
    def test_substitutes_addresses_lowercased(self):
        checksummed = "0x" + WETH_BASE[2:].upper()  # EIP-55 style casing (le préfixe "0x" reste toujours minuscule)
        sql = dune.build_addresses_stats_query([checksummed], blockchain="base")
        assert WETH_BASE in sql
        assert "blockchain = 'base'" in sql

    def test_addresses_emitted_as_bare_hex_literals_not_quoted(self):
        """`address` est `varbinary` dans `addresses.stats` (confirmé en
        exécution réelle) -- un littéral entre guillemets simples y échoue
        (« Cannot find common type between varbinary and varchar »)."""
        sql = dune.build_addresses_stats_query([WETH_BASE])
        assert f"IN ({WETH_BASE})" in sql
        assert f"'{WETH_BASE}'" not in sql

    def test_multiple_addresses_joined(self):
        sql = dune.build_addresses_stats_query([WETH_BASE, FUNDER])
        assert WETH_BASE in sql
        assert FUNDER in sql

    def test_rejects_empty_list(self):
        with pytest.raises(ValueError):
            dune.build_addresses_stats_query([])

    def test_rejects_malformed_address(self):
        with pytest.raises(ValueError):
            dune.build_addresses_stats_query(["not-an-address"])

    def test_rejects_short_address(self):
        with pytest.raises(ValueError):
            dune.build_addresses_stats_query(["0x1234"])

    def test_rejects_non_string_address(self):
        with pytest.raises(ValueError):
            dune.build_addresses_stats_query([12345])

    def test_rejects_invalid_blockchain(self):
        with pytest.raises(ValueError):
            dune.build_addresses_stats_query([WETH_BASE], blockchain="base; DROP TABLE x")


class TestGetFirstFundedBy:
    @pytest.mark.asyncio
    async def test_empty_addresses_no_network_call(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        holder = _patch_client(monkeypatch, [])

        result = await dune.get_first_funded_by([])

        assert result.available is True
        assert result.records == []
        assert holder["calls"] == []

    @pytest.mark.asyncio
    async def test_no_key_unavailable(self, monkeypatch):
        monkeypatch.delenv("DUNE_API_KEY", raising=False)

        result = await dune.get_first_funded_by([WETH_BASE])

        assert result.available is False

    @pytest.mark.asyncio
    async def test_happy_path_parses_records(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_PENDING"}),
                FakeResponse(
                    200,
                    {"execution_id": "exec-1", "state": "QUERY_STATE_COMPLETED", "is_execution_finished": True},
                ),
                FakeResponse(
                    200,
                    {
                        "execution_id": "exec-1",
                        "result": {
                            "rows": [
                                {
                                    "address": WETH_BASE,
                                    "first_funded_by": FUNDER,
                                    "first_funded_at": "2023-06-22 18:53:33",
                                    "is_eoa": True,
                                    "is_smart_contract": False,
                                }
                            ]
                        },
                    },
                ),
            ],
        )

        result = await dune.get_first_funded_by([WETH_BASE], performance="small")

        assert result.available is True
        assert len(result.records) == 1
        record = result.records[0]
        assert record.address == WETH_BASE
        assert record.first_funded_by == FUNDER
        assert record.is_eoa is True
        assert record.is_smart_contract is False

    @pytest.mark.asyncio
    async def test_malformed_rows_skipped_not_a_crash(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_PENDING"}),
                FakeResponse(
                    200,
                    {"execution_id": "exec-1", "state": "QUERY_STATE_COMPLETED", "is_execution_finished": True},
                ),
                FakeResponse(
                    200,
                    {
                        "execution_id": "exec-1",
                        "result": {
                            "rows": [
                                {"address": ""},  # adresse vide -> ignorée
                                {"no_address_field": True},  # champ absent -> ignorée
                                {"address": WETH_BASE, "first_funded_by": 12345},  # type invalide -> None, pas de crash
                            ]
                        },
                    },
                ),
            ],
        )

        result = await dune.get_first_funded_by([WETH_BASE])

        assert result.available is True
        assert len(result.records) == 1
        assert result.records[0].first_funded_by is None

    @pytest.mark.asyncio
    async def test_execution_failure_propagates_unavailable(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])

        result = await dune.get_first_funded_by([WETH_BASE])

        assert result.available is False
        assert result.error


class TestBuildPriceHistoryQuery:
    """Dernier recours de la cascade OHLCV #194 (16/07) -- reconstruit des
    bougies horaires depuis `prices.usd` (granularité minute), utilisée
    UNIQUEMENT quand GeckoTerminal ET CoinMarketCap ont échoué."""

    def test_substitutes_parameters(self):
        sql = dune.build_price_history_query(WETH_BASE, blockchain="base", lookback_hours=48)
        assert "48" in sql
        assert "prices.usd" in sql
        assert "blockchain = 'base'" in sql

    def test_address_emitted_as_bare_hex_literal_not_quoted(self):
        sql = dune.build_price_history_query(WETH_BASE)
        assert f"contract_address = {WETH_BASE}" in sql
        assert f"'{WETH_BASE}'" not in sql

    def test_rejects_malformed_address(self):
        with pytest.raises(ValueError):
            dune.build_price_history_query("not-an-address")

    def test_rejects_invalid_blockchain(self):
        with pytest.raises(ValueError):
            dune.build_price_history_query(WETH_BASE, blockchain="base; DROP TABLE x")

    def test_rejects_non_positive_lookback_hours(self):
        with pytest.raises(ValueError):
            dune.build_price_history_query(WETH_BASE, lookback_hours=0)

    def test_rejects_non_int_lookback_hours(self):
        with pytest.raises(ValueError):
            dune.build_price_history_query(WETH_BASE, lookback_hours=48.5)


class TestGetPriceHistory:
    @pytest.mark.asyncio
    async def test_no_key_unavailable(self, monkeypatch):
        monkeypatch.delenv("DUNE_API_KEY", raising=False)

        result = await dune.get_price_history(WETH_BASE)

        assert result.available is False

    @pytest.mark.asyncio
    async def test_invalid_address_unavailable_no_network_call(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        holder = _patch_client(monkeypatch, [])

        result = await dune.get_price_history("not-an-address")

        assert result.available is False
        assert holder["calls"] == []

    @pytest.mark.asyncio
    async def test_happy_path_parses_candles(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_PENDING"}),
                FakeResponse(
                    200,
                    {"execution_id": "exec-1", "state": "QUERY_STATE_COMPLETED", "is_execution_finished": True},
                ),
                FakeResponse(
                    200,
                    {
                        "execution_id": "exec-1",
                        "result": {
                            "rows": [
                                {"bucket": "2026-07-15 10:00:00", "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1},
                                {"bucket": "2026-07-15 11:00:00", "open": 1.1, "high": 1.3, "low": 1.0, "close": 1.25},
                            ]
                        },
                    },
                ),
            ],
        )

        result = await dune.get_price_history(WETH_BASE)

        assert result.available is True
        assert len(result.candles) == 2
        assert result.candles[0].close == 1.1
        assert result.candles[1].open == 1.1
        assert result.candles[0].volume == 0.0

    @pytest.mark.asyncio
    async def test_malformed_rows_skipped_not_a_crash(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(
            monkeypatch,
            [
                FakeResponse(200, {"execution_id": "exec-1", "state": "QUERY_STATE_PENDING"}),
                FakeResponse(
                    200,
                    {"execution_id": "exec-1", "state": "QUERY_STATE_COMPLETED", "is_execution_finished": True},
                ),
                FakeResponse(
                    200,
                    {
                        "execution_id": "exec-1",
                        "result": {
                            "rows": [
                                {"bucket": "2026-07-15 10:00:00", "open": None, "high": 1.2, "low": 0.9, "close": 1.1},
                                {"bucket": "not-a-date", "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1},
                            ]
                        },
                    },
                ),
            ],
        )

        result = await dune.get_price_history(WETH_BASE)

        assert result.available is True
        assert len(result.candles) == 1  # la 1re ligne (open=None) est ignorée, la 2e garde ts=0
        assert result.candles[0].ts == 0

    @pytest.mark.asyncio
    async def test_execution_failure_propagates_unavailable(self, monkeypatch):
        monkeypatch.setenv("DUNE_API_KEY", "k")
        monkeypatch.setattr(dune.asyncio, "sleep", _no_sleep)
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])

        result = await dune.get_price_history(WETH_BASE)

        assert result.available is False
        assert result.error
