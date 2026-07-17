"""Vérifie une adresse via Cybercentry (x402, payant) et mémorise le résultat en
mémoire vectorielle -- premier appelant réel de `memory/vector/lancedb_store.py`
(#199, 17/07, décision opérateur : payer ce qui alimente le plus la mémoire
vectorielle). Un fait vérifié, jamais inventé -- si l'appel échoue, rien n'est
stocké (dégradation honnête, pas un placeholder)."""
from __future__ import annotations

from datetime import datetime, timezone


def _format_wallet_insight(address: str, raw: dict) -> str:
    """Texte lisible à partir de la réponse brute Cybercentry -- structure du
    JSON pas garantie stable dans le temps, lecture défensive (`.get` partout)."""
    lines = [f"Vérification Cybercentry (wallet-verification) — {address}"]
    for key in ("risk", "risk_level", "is_sanctioned", "is_fraud", "score", "summary", "verdict"):
        if key in raw:
            lines.append(f"{key}: {raw[key]}")
    if len(lines) == 1:
        lines.append(f"réponse brute: {raw}")
    return "\n".join(lines)


async def verify_and_remember_wallet(address: str) -> dict:
    """Paie Cybercentry pour vérifier ``address``, puis stocke le résultat comme
    un ``insight`` en mémoire vectorielle (metadata source=cybercentry,
    topic=wallet-security). Renvoie le résultat brut de la vérification +
    ``vector_doc_id`` (``None`` si le stockage a échoué ou est désactivé)."""
    from aria_core.services.cybercentry import verify_wallet
    from aria_core.memory.vector import lancedb_store

    result = await verify_wallet(address)
    if not result["available"]:
        return {**result, "vector_doc_id": None}

    text = _format_wallet_insight(address, result["raw"])
    doc_id = await lancedb_store.store(
        "insight",
        text,
        metadata={
            "source": "cybercentry",
            "topic": "wallet-security",
            "source_id": f"cybercentry-wallet-{address.lower()}-{datetime.now(timezone.utc).date().isoformat()}",
        },
    )
    return {**result, "vector_doc_id": doc_id}
