"""verify_and_remember_wallet -- vérifie le câblage vérification x402 + mémoire
vectorielle, aucun appel réseau réel (les deux couches sont mockées)."""
from __future__ import annotations

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
