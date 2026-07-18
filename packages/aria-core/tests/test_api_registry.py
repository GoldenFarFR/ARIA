"""Registre /api — inventaire des intégrations externes + formatage Telegram."""
from __future__ import annotations

import pytest

from aria_core.services.api_registry import (
    ApiEntry,
    build_api_inventory,
    format_api_inventory,
)


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self, response_map):
        self._map = response_map

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers=None):
        for prefix, response in self._map.items():
            if url.startswith(prefix):
                return response
        raise AssertionError(f"URL inattendue dans le test: {url}")


def _patch_http(monkeypatch, response_map):
    monkeypatch.setattr(
        "aria_core.services.api_registry.httpx.AsyncClient", lambda **kw: _FakeHttpClient(response_map),
    )


@pytest.mark.asyncio
async def test_build_inventory_never_fabricates_quota_without_key(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
    monkeypatch.delenv("XAI_MANAGEMENT_KEY", raising=False)
    monkeypatch.delenv("XAI_TEAM_ID", raising=False)

    entries = await build_api_inventory()

    github = next(e for e in entries if e.name == "GitHub API")
    assert github.configured is False
    assert github.live_quota is None

    cmc = next(e for e in entries if e.name == "CoinMarketCap")
    assert cmc.configured is False
    assert cmc.live_quota is None


@pytest.mark.asyncio
async def test_github_quota_parsed_from_real_shape(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    _patch_http(monkeypatch, {
        "https://api.github.com/rate_limit": _FakeResponse(
            200, {"resources": {"core": {"remaining": 4987, "limit": 5000}}}
        ),
    })

    entries = await build_api_inventory()

    github = next(e for e in entries if e.name == "GitHub API")
    assert github.configured is True
    assert github.live_quota == "4987/5000 requêtes (fenêtre horaire)"


@pytest.mark.asyncio
async def test_github_quota_handles_http_error_gracefully(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    _patch_http(monkeypatch, {
        "https://api.github.com/rate_limit": _FakeResponse(401),
    })

    entries = await build_api_inventory()

    github = next(e for e in entries if e.name == "GitHub API")
    assert "401" in github.live_quota


@pytest.mark.asyncio
async def test_coinmarketcap_quota_parsed_from_real_shape(monkeypatch):
    monkeypatch.setenv("COINMARKETCAP_API_KEY", "fake-key")
    _patch_http(monkeypatch, {
        "https://pro-api.coinmarketcap.com/v1/key/info": _FakeResponse(200, {
            "data": {
                "usage": {
                    "current_day": {"credits_used": 1, "credits_left": 3999},
                    "current_month": {"credits_used": 1, "credits_left": 119999},
                }
            }
        }),
    })

    entries = await build_api_inventory()

    cmc = next(e for e in entries if e.name == "CoinMarketCap")
    assert cmc.configured is True
    assert "120000" in cmc.live_quota or "1/120000" in cmc.live_quota


def test_keyless_apis_marked_configured_with_note():
    """DexScreener/GeckoTerminal/DefiLlama etc. n'exigent aucune clé -- ne doivent
    jamais apparaître comme "non configurées" (confusion avec une clé manquante)."""
    from aria_core.services.api_registry import _static_entries

    entries = _static_entries()
    dexscreener = next(e for e in entries if e.name == "DexScreener")
    assert dexscreener.configured is True
    assert "sans clé" in dexscreener.note


def test_format_api_inventory_splits_into_multiple_messages_when_long():
    entries = [
        ApiEntry(f"API-{i}", "LLM", f"https://api-{i}.example.com/a-fairly-long-path-segment", True, note="note de test assez longue pour peser dans le total")
        for i in range(80)
    ]
    messages = format_api_inventory(entries)
    assert len(messages) > 1
    for msg in messages:
        assert len(msg) <= 4000


def test_format_api_inventory_groups_by_category_and_marks_status():
    entries = [
        ApiEntry("Alpha", "LLM", "https://alpha.example.com", True, note="configurée"),
        ApiEntry("Beta", "Social", "https://beta.example.com", False, note="clé absente"),
    ]
    messages = format_api_inventory(entries)
    combined = "\n".join(messages)
    assert "LLM" in combined
    assert "Social" in combined
    assert "✅ Alpha" in combined
    assert "⬜ Beta" in combined


@pytest.mark.asyncio
async def test_x402_budget_reused_not_duplicated(monkeypatch):
    async def fake_weekly_status(now=None):
        return {"cap_usd": 5.0, "spent_usd": 1.23, "remaining_usd": 3.77, "week_started_at": "2026-07-14T00:00:00+00:00"}

    monkeypatch.setattr("aria_core.x402_budget.weekly_status", fake_weekly_status)

    entries = await build_api_inventory()

    x402 = next(e for e in entries if e.name == "Budget x402 (hebdomadaire, interne)")
    assert "1.23" in x402.live_quota
    assert "5.00" in x402.live_quota
