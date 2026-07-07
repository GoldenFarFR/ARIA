"""Cache TTL des analyses VC + logs de timing.

- Unitaire : put/get, expiration (horloge injectée), désactivation, borne.
- Intégration : un même (contrat, langue) redemandé dans la fenêtre TTL ne
  refait PAS l'appel LLM ; désactivé par défaut (aucune pollution des tests).

Aucun réseau : scan et chat_with_context mockés.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aria_core.skills import vc_analysis as vc
from aria_core.skills import vc_cache
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext

ADDR = "0x" + "b" * 40


@pytest.fixture(autouse=True)
def _clean_cache():
    vc_cache.clear()
    yield
    vc_cache.clear()


# ------------------------------ unitaire ------------------------------
def test_put_get_roundtrip():
    vc_cache.put(("k", "fr"), "value", ttl=100)
    assert vc_cache.get(("k", "fr")) == "value"
    assert vc_cache.get(("k", "en")) is None  # clé distincte


def test_ttl_zero_is_noop():
    vc_cache.put(("k", "fr"), "value", ttl=0)
    assert vc_cache.get(("k", "fr")) is None
    assert vc_cache.size() == 0


def test_expiry_with_injected_clock(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(vc_cache, "_now", lambda: clock["t"])
    vc_cache.put(("k", "fr"), "value", ttl=60)
    assert vc_cache.get(("k", "fr")) == "value"      # avant expiration
    clock["t"] = 1061.0                               # +61 s > TTL
    assert vc_cache.get(("k", "fr")) is None          # expiré → évincé
    assert vc_cache.size() == 0


def test_cap_evicts_oldest():
    for i in range(vc_cache._CAP + 10):
        vc_cache.put((i, "fr"), i, ttl=100)
    assert vc_cache.size() <= vc_cache._CAP
    assert vc_cache.get((0, "fr")) is None            # le plus ancien évincé


# ---------------------------- helpers intégration ----------------------------
def _ctx() -> TokenScanContext:
    ctx = TokenScanContext(
        contract=ADDR, valid_address=True, pairs_found=1, security_score=60,
        lite_verdict="CAUTION", data_source="dexscreener",
        risk_flags=["Liquidité modérée ($8,000)."],
    )
    ctx.best_pair = PairSnapshot(
        pair_address="0xpair", dex_id="aerodrome", liquidity_usd=8000,
        volume_24h_usd=3000, base_symbol="TOK", quote_symbol="WETH",
    )
    return ctx


def _valid_llm_json() -> str:
    return json.dumps({
        "potentiel": 7, "risque": "MODÉRÉ", "these": "Traction réelle.",
        "recommandation": "BUY", "taille_pct": 5, "entree": "marché",
        "invalidation": "perte support $5k", "cible": "x2 6 mois",
        "donnees_insuffisantes": [], "rapport_detaille": "Analyse complète.",
    })


# ------------------------------ intégration ------------------------------
@pytest.mark.asyncio
async def test_second_call_hits_cache_when_enabled(monkeypatch):
    monkeypatch.setenv("ARIA_VC_CACHE_TTL", "300")
    monkeypatch.setattr(vc, "scan_base_token", AsyncMock(return_value=_ctx()))
    monkeypatch.setattr(vc, "list_theses_for_token", AsyncMock(return_value=[]))
    chat = AsyncMock(return_value=_valid_llm_json())
    monkeypatch.setattr(vc, "chat_with_context", chat)

    r1, _ = await vc.analyze_vc_with_context(ADDR, lang="fr")
    r2, _ = await vc.analyze_vc_with_context(ADDR, lang="fr")

    assert r1.recommandation == "BUY" and r2.recommandation == "BUY"
    chat.assert_awaited_once()  # 2e appel servi par le cache → 1 seul appel LLM


@pytest.mark.asyncio
async def test_disabled_by_default_calls_llm_each_time(monkeypatch):
    monkeypatch.delenv("ARIA_VC_CACHE_TTL", raising=False)  # défaut = off
    monkeypatch.setattr(vc, "scan_base_token", AsyncMock(return_value=_ctx()))
    monkeypatch.setattr(vc, "list_theses_for_token", AsyncMock(return_value=[]))
    chat = AsyncMock(return_value=_valid_llm_json())
    monkeypatch.setattr(vc, "chat_with_context", chat)

    await vc.analyze_vc_with_context(ADDR, lang="fr")
    await vc.analyze_vc_with_context(ADDR, lang="fr")

    assert chat.await_count == 2  # cache off → LLM à chaque fois


@pytest.mark.asyncio
async def test_different_language_is_separate_cache_entry(monkeypatch):
    monkeypatch.setenv("ARIA_VC_CACHE_TTL", "300")
    monkeypatch.setattr(vc, "scan_base_token", AsyncMock(return_value=_ctx()))
    monkeypatch.setattr(vc, "list_theses_for_token", AsyncMock(return_value=[]))
    chat = AsyncMock(return_value=_valid_llm_json())
    monkeypatch.setattr(vc, "chat_with_context", chat)

    await vc.analyze_vc_with_context(ADDR, lang="fr")
    await vc.analyze_vc_with_context(ADDR, lang="en")  # langue différente → miss

    assert chat.await_count == 2


@pytest.mark.asyncio
async def test_fallback_not_cached(monkeypatch):
    """Un fallback dégradé (LLM off) ne doit pas être mis en cache."""
    monkeypatch.setenv("ARIA_VC_CACHE_TTL", "300")
    monkeypatch.setattr(vc, "scan_base_token", AsyncMock(return_value=_ctx()))
    monkeypatch.setattr(vc, "list_theses_for_token", AsyncMock(return_value=[]))
    chat = AsyncMock(return_value=None)  # LLM indispo → fallback
    monkeypatch.setattr(vc, "chat_with_context", chat)

    await vc.analyze_vc_with_context(ADDR, lang="fr")
    assert vc_cache.size() == 0  # rien mis en cache
