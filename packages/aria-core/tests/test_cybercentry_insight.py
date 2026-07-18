"""verify_and_remember_wallet -- vérifie le câblage vérification x402 + mémoire
vectorielle, aucun appel réseau réel (les deux couches sont mockées)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core.skills import cybercentry_insight


@pytest.mark.asyncio
async def test_verify_and_remember_stores_insight_on_success(monkeypatch):
    async def fake_verify(address):
        return {"available": True, "raw": {"risk": "low"}, "error": None, "amount_usd": 0.02}

    stored = {}

    async def fake_store(entry_type, content, *, metadata=None):
        stored["entry_type"] = entry_type
        stored["content"] = content
        stored["metadata"] = metadata
        return "doc-123"

    monkeypatch.setattr("aria_core.services.cybercentry.verify_wallet", fake_verify)
    monkeypatch.setattr("aria_core.memory.vector.lancedb_store.store", fake_store)

    result = await cybercentry_insight.verify_and_remember_wallet("0xABC")

    assert result["available"] is True
    assert result["vector_doc_id"] == "doc-123"
    assert stored["entry_type"] == "insight"
    assert "0xABC" in stored["content"]
    assert "risk: low" in stored["content"]
    assert stored["metadata"]["source"] == "cybercentry"
    assert stored["metadata"]["topic"] == "wallet-security"
    assert "0xabc" in stored["metadata"]["source_id"]  # normalisé en minuscules


@pytest.mark.asyncio
async def test_verify_and_remember_no_storage_on_failure(monkeypatch):
    """Jamais un placeholder inventé en mémoire vectorielle si la vérification échoue."""
    async def fake_verify(address):
        return {"available": False, "raw": None, "error": "rate limit", "amount_usd": 0.0}

    store_called = False

    async def fake_store(*args, **kwargs):
        nonlocal store_called
        store_called = True

    monkeypatch.setattr("aria_core.services.cybercentry.verify_wallet", fake_verify)
    monkeypatch.setattr("aria_core.memory.vector.lancedb_store.store", fake_store)

    result = await cybercentry_insight.verify_and_remember_wallet("0xabc")

    assert result["available"] is False
    assert result["vector_doc_id"] is None
    assert store_called is False


# ── cache avant paiement (18/07, bug réel corrigé) ──────────────────────────

@pytest.mark.asyncio
async def test_verify_and_remember_uses_cache_never_pays_when_recent(monkeypatch):
    """Un résultat déjà payé aujourd'hui pour la même adresse -- ne repaie jamais."""
    today = datetime.now(timezone.utc).date().isoformat()
    address = "0xABC"

    async def fake_search(query, *, entry_type=None, limit=8):
        return [{
            "id": "doc-cached",
            "content": "Vérification Cybercentry (wallet-verification) — 0xabc\nrisk: low",
            "metadata": {
                "source": "cybercentry", "topic": "wallet-security",
                "source_id": f"cybercentry-wallet-0xabc-{today}",
                "raw_json": '{"risk": "low"}',
            },
            "distance": 0.01,
        }]

    verify_called = False

    async def fake_verify(addr):
        nonlocal verify_called
        verify_called = True
        return {"available": True, "raw": {"risk": "low"}, "error": None, "amount_usd": 0.02}

    monkeypatch.setattr("aria_core.memory.vector.lancedb_store.search", fake_search)
    monkeypatch.setattr("aria_core.services.cybercentry.verify_wallet", fake_verify)

    result = await cybercentry_insight.verify_and_remember_wallet(address)

    assert verify_called is False, "ne doit JAMAIS payer si un résultat récent existe déjà"
    assert result["cached"] is True
    assert result["available"] is True
    assert result["amount_usd"] == 0.0
    assert result["raw"] == {"risk": "low"}


@pytest.mark.asyncio
async def test_verify_and_remember_pays_again_when_cache_too_old(monkeypatch):
    """Un résultat de plus de max_age_days -- repaie, jamais un fait périmé servi indéfiniment."""
    old_date = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
    address = "0xdef"

    async def fake_search(query, *, entry_type=None, limit=8):
        return [{
            "id": "doc-old",
            "content": "vieux résultat",
            "metadata": {
                "source": "cybercentry", "topic": "wallet-security",
                "source_id": f"cybercentry-wallet-{address}-{old_date}",
                "raw_json": '{"risk": "unknown"}',
            },
            "distance": 0.01,
        }]

    async def fake_verify(addr):
        return {"available": True, "raw": {"risk": "fresh"}, "error": None, "amount_usd": 0.02}

    async def fake_store(entry_type, content, *, metadata=None):
        return "doc-new"

    monkeypatch.setattr("aria_core.memory.vector.lancedb_store.search", fake_search)
    monkeypatch.setattr("aria_core.services.cybercentry.verify_wallet", fake_verify)
    monkeypatch.setattr("aria_core.memory.vector.lancedb_store.store", fake_store)

    result = await cybercentry_insight.verify_and_remember_wallet(address, max_age_days=7)

    assert result["cached"] is False
    assert result["raw"] == {"risk": "fresh"}


@pytest.mark.asyncio
async def test_verify_and_remember_ignores_unrelated_search_matches(monkeypatch):
    """Un résultat de recherche sémantique pour une AUTRE adresse -- jamais confondu
    (correspondance EXACTE du source_id exigée, pas juste "proche")."""
    today = datetime.now(timezone.utc).date().isoformat()

    async def fake_search(query, *, entry_type=None, limit=8):
        return [{
            "id": "doc-other",
            "content": "autre adresse",
            "metadata": {
                "source": "cybercentry", "topic": "wallet-security",
                "source_id": f"cybercentry-wallet-0xother-{today}",
                "raw_json": '{"risk": "low"}',
            },
            "distance": 0.3,
        }]

    verify_called = False

    async def fake_verify(addr):
        nonlocal verify_called
        verify_called = True
        return {"available": True, "raw": {"risk": "fresh"}, "error": None, "amount_usd": 0.02}

    async def fake_store(entry_type, content, *, metadata=None):
        return "doc-new"

    monkeypatch.setattr("aria_core.memory.vector.lancedb_store.search", fake_search)
    monkeypatch.setattr("aria_core.services.cybercentry.verify_wallet", fake_verify)
    monkeypatch.setattr("aria_core.memory.vector.lancedb_store.store", fake_store)

    result = await cybercentry_insight.verify_and_remember_wallet("0xthis")

    assert verify_called is True, "une adresse différente ne doit jamais servir de cache"
    assert result["cached"] is False


@pytest.mark.asyncio
async def test_verify_and_remember_empty_address_never_pays(monkeypatch):
    verify_called = False

    async def fake_verify(addr):
        nonlocal verify_called
        verify_called = True
        return {"available": True, "raw": {}, "error": None, "amount_usd": 0.02}

    monkeypatch.setattr("aria_core.services.cybercentry.verify_wallet", fake_verify)

    result = await cybercentry_insight.verify_and_remember_wallet("  ")

    assert verify_called is False
    assert result["available"] is False
    assert result["error"] == "adresse vide"
