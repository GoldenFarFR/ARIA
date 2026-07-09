"""Tests du client Arena Virtuals (lecture seule) — aucun appel réseau réel, tout mocké.

Phase 0 du pilote Arena (backlog #60) : zéro wallet, zéro clé, zéro exécution.
"""

import httpx
import pytest

from aria_core.services.virtuals_arena import (
    LEADERBOARD_ENDPOINT,
    ArenaClient,
    _parse_entry,
    _sanitize,
)


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
    """Simule httpx.AsyncClient: renvoie la prochaine réponse d'une file par URL."""

    def __init__(self, responses: dict):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers=None, params=None):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.virtuals_arena.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.virtuals_arena.asyncio.sleep", _fake_sleep)


def _default_url(limit: int = 20, offset: int = 0) -> str:
    return f"{LEADERBOARD_ENDPOINT}?limit={limit}&offset={offset}"


def _agent_entry(**overrides) -> dict:
    entry = {
        "id": "1101",
        "name": "Koby",
        "tokenSymbol": "KOBYAI",
        "tokenAddress": "0xcCc7cddA0977c7FabFf7519DECcC8E8f97D1EA1F",
        "agentAddress": "0x98f5eb506842a74719bf2d14881a6184bd445bea",
        "performance": {
            "totalRealizedPnl": 5.24535,
            "unrealizedPnl": 0,
            "holdingsValueUsd": 15.245373,
            "totalTradeCount": 1,
            "winCount": 1,
            "lossCount": 0,
            "winRate": 1,
            "returnPct": 0.21229849,
            "totalTradeVolume": 24.71,
            "lastTradeAt": "2026-06-02T19:33:25.038Z",
        },
    }
    entry.update(overrides)
    return entry


def _leaderboard_payload(entries=None) -> dict:
    entries = entries if entries is not None else [_agent_entry()]
    return {
        "success": True,
        "data": entries,
        "timeRange": "lifetime",
        "pagination": {"total": 274, "limit": 20, "offset": 0, "hasMore": True},
    }


class TestSanitize:
    def test_none_stays_none(self):
        assert _sanitize(None) is None

    def test_neutralizes_angle_brackets(self):
        assert _sanitize("<script>evil</script>") == "‹script›evil‹/script›"

    def test_truncates(self):
        assert len(_sanitize("x" * 500, max_len=10)) == 10


class TestParseEntry:
    def test_parses_real_shape(self):
        entry = _parse_entry(_agent_entry())
        assert entry is not None
        assert entry.id == "1101"
        assert entry.name == "Koby"
        assert entry.token_symbol == "KOBYAI"
        assert entry.total_realized_pnl == 5.24535
        assert entry.win_rate == 1
        assert entry.total_trade_count == 1

    def test_non_dict_returns_none(self):
        assert _parse_entry("not a dict") is None

    def test_missing_performance_never_raises(self):
        entry = _parse_entry({"id": "1", "name": "NoPerf"})
        assert entry is not None
        assert entry.total_realized_pnl is None

    def test_hostile_name_sanitized(self):
        entry = _parse_entry(_agent_entry(name="<img src=x onerror=alert(1)>"))
        assert "<" not in entry.name
        assert ">" not in entry.name


@pytest.mark.asyncio
class TestFetchLeaderboard:
    async def test_success(self, monkeypatch):
        _patch_client(monkeypatch, {_default_url(): FakeResponse(200, _leaderboard_payload())})
        client = ArenaClient()
        result = await client.fetch_leaderboard()
        assert result is not None
        assert result.time_range == "lifetime"
        assert result.total == 274
        assert len(result.entries) == 1
        assert result.entries[0].name == "Koby"

    async def test_success_false_returns_none(self, monkeypatch):
        payload = _leaderboard_payload()
        payload["success"] = False
        _patch_client(monkeypatch, {_default_url(): FakeResponse(200, payload)})
        client = ArenaClient()
        assert await client.fetch_leaderboard() is None

    async def test_limit_offset_clamped_and_forwarded(self, monkeypatch):
        # limit demandé au-dessus du plafond -> clampé à 100 ; offset négatif -> 0
        url = _default_url(limit=100, offset=0)
        _patch_client(monkeypatch, {url: FakeResponse(200, _leaderboard_payload())})
        client = ArenaClient()
        result = await client.fetch_leaderboard(limit=9999, offset=-5)
        assert result is not None

    async def test_404_returns_none(self, monkeypatch):
        _patch_client(monkeypatch, {_default_url(): FakeResponse(404, None)})
        client = ArenaClient()
        assert await client.fetch_leaderboard() is None

    async def test_429_then_success(self, monkeypatch):
        _patch_no_sleep(monkeypatch)
        _patch_client(
            monkeypatch,
            {_default_url(): [FakeResponse(429), FakeResponse(200, _leaderboard_payload())]},
        )
        client = ArenaClient()
        result = await client.fetch_leaderboard()
        assert result is not None
        assert len(result.entries) == 1

    async def test_timeout_then_success(self, monkeypatch):
        _patch_no_sleep(monkeypatch)

        class _RaisingThenOkClient:
            def __init__(self):
                self._calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, headers=None, params=None):
                self._calls += 1
                if self._calls == 1:
                    raise httpx.TransportError("boom")
                return FakeResponse(200, _leaderboard_payload())

        shared_instance = _RaisingThenOkClient()
        monkeypatch.setattr(
            "aria_core.services.virtuals_arena.httpx.AsyncClient",
            lambda **kw: shared_instance,
        )
        client = ArenaClient()
        result = await client.fetch_leaderboard()
        assert result is not None

    async def test_persistent_timeout_returns_none_never_raises(self, monkeypatch):
        _patch_no_sleep(monkeypatch)

        class _AlwaysRaisingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, headers=None, params=None):
                raise httpx.TransportError("boom")

        monkeypatch.setattr(
            "aria_core.services.virtuals_arena.httpx.AsyncClient",
            lambda **kw: _AlwaysRaisingClient(),
        )
        client = ArenaClient()
        assert await client.fetch_leaderboard() is None

    async def test_malformed_payload_never_raises(self, monkeypatch):
        _patch_client(monkeypatch, {_default_url(): FakeResponse(200, {"success": True, "data": "not a list"})})
        client = ArenaClient()
        assert await client.fetch_leaderboard() is None

    async def test_entries_with_hostile_data_never_raise(self, monkeypatch):
        payload = _leaderboard_payload(entries=[_agent_entry(name=None), "not a dict", 42])
        _patch_client(monkeypatch, {_default_url(): FakeResponse(200, payload)})
        client = ArenaClient()
        result = await client.fetch_leaderboard()
        assert result is not None
        # seul l'entrée dict valide (name=None toléré) survit au parsing
        assert len(result.entries) == 1
