"""Tests du client RugCheck.xyz (#207, 18/07) -- second avis Solana pour
momentum_entry._check_honeypot. Aucun appel réseau réel, tout est mocké
(même patron que test_dexscreener_client.py : client module-level, pas de
classe)."""
from __future__ import annotations

import pytest

from aria_core.services.rugcheck import RugCheckResult, get_report_summary

MINT = "EPKPcUPmhcDfpRq1LtN46FuysHo49D5Q6W2L2oPmpump"


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
        return False

    async def get(self, url, **kwargs):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.rugcheck.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.rugcheck.asyncio.sleep", _fake_sleep)


def _url(mint: str) -> str:
    return f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"


# ── get_report_summary -- cas clean (vérifié en direct : SOL natif, score 1) ──

@pytest.mark.asyncio
async def test_confirmed_clean_when_no_risks(monkeypatch):
    _patch_no_sleep(monkeypatch)
    payload = {"risks": [], "score_normalised": 1, "rugged": False}
    _patch_client(monkeypatch, {_url(MINT): FakeResponse(200, payload)})

    result = await get_report_summary(MINT)
    assert result.available is True
    assert result.rugged is False
    assert result.danger_risks == []
    assert result.confirmed_clean is True


# ── cas danger réel (vérifié en direct, 18/07 : "Creator history of rugged tokens") ──

@pytest.mark.asyncio
async def test_danger_level_risk_detected_and_not_confirmed_clean(monkeypatch):
    _patch_no_sleep(monkeypatch)
    payload = {
        "risks": [
            {
                "name": "Creator history of rugged tokens",
                "description": "Creator has a history of rugging tokens.",
                "score": 24000,
                "level": "danger",
            }
        ],
        "score_normalised": 62,
        "rugged": False,
    }
    _patch_client(monkeypatch, {_url(MINT): FakeResponse(200, payload)})

    result = await get_report_summary(MINT)
    assert result.available is True
    assert result.danger_risks == ["Creator history of rugged tokens"]
    assert result.confirmed_clean is False


@pytest.mark.asyncio
async def test_non_danger_level_risk_does_not_block(monkeypatch):
    """Un risque de niveau inférieur ("warn"/autre) n'est jamais confondu avec
    "danger" -- seul "danger" bloque, doctrine assumée et documentée (pas un
    seuil numérique arbitraire calé sur 3 points de données)."""
    _patch_no_sleep(monkeypatch)
    payload = {
        "risks": [{"name": "Low liquidity", "score": 500, "level": "warn"}],
        "score_normalised": 5,
        "rugged": False,
    }
    _patch_client(monkeypatch, {_url(MINT): FakeResponse(200, payload)})

    result = await get_report_summary(MINT)
    assert result.danger_risks == []
    assert result.confirmed_clean is True


@pytest.mark.asyncio
async def test_rugged_flag_true_blocks_even_without_named_risk(monkeypatch):
    _patch_no_sleep(monkeypatch)
    payload = {"risks": [], "score_normalised": 10, "rugged": True}
    _patch_client(monkeypatch, {_url(MINT): FakeResponse(200, payload)})

    result = await get_report_summary(MINT)
    assert result.rugged is True
    assert result.confirmed_clean is False


# ── indisponibilité -- jamais confondu avec "clean" (fail-closed) ──

@pytest.mark.asyncio
async def test_unavailable_on_network_failure_never_confirmed_clean(monkeypatch):
    _patch_no_sleep(monkeypatch)
    _patch_client(
        monkeypatch,
        {_url(MINT): [FakeResponse(429), FakeResponse(429), FakeResponse(429)]},
    )

    result = await get_report_summary(MINT)
    assert result.available is False
    assert result.confirmed_clean is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_unavailable_on_bad_mint_format(monkeypatch):
    """Vérifié en direct : une adresse mal formée renvoie 400 "invalid length" --
    géré par la branche générique raise_for_status, jamais un crash."""
    _patch_no_sleep(monkeypatch)
    _patch_client(monkeypatch, {_url(MINT): FakeResponse(400, {"error": "invalid length"})})

    result = await get_report_summary(MINT)
    assert result.available is False
    assert result.confirmed_clean is False


@pytest.mark.asyncio
async def test_empty_address_short_circuits_without_network_call(monkeypatch):
    result = await get_report_summary("")
    assert result.available is False
    assert result.error == "adresse vide"


@pytest.mark.asyncio
async def test_429_retries_then_succeeds(monkeypatch):
    _patch_no_sleep(monkeypatch)
    payload = {"risks": [], "score_normalised": 1, "rugged": False}
    _patch_client(
        monkeypatch,
        {_url(MINT): [FakeResponse(429), FakeResponse(200, payload)]},
    )

    result = await get_report_summary(MINT)
    assert result.available is True
    assert result.confirmed_clean is True


def test_result_defaults_are_fail_closed():
    """Un RugCheckResult jamais interrogé (constructeur nu) ne doit jamais se
    faire passer pour "clean" -- confirmed_clean exige available=True explicite."""
    r = RugCheckResult(address=MINT)
    assert r.confirmed_clean is False
