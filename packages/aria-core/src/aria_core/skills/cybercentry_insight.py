"""Vérifie une adresse via Cybercentry (x402, payant) et mémorise le résultat en
mémoire vectorielle -- premier appelant réel de `memory/vector/lancedb_store.py`
(#199, 17/07, décision opérateur : payer ce qui alimente le plus la mémoire
vectorielle). Un fait vérifié, jamais inventé -- si l'appel échoue, rien n'est
stocké (dégradation honnête, pas un placeholder).

**Cache avant paiement (18/07, bug réel corrigé) : cette fonction payait à
CHAQUE appel, sans jamais vérifier si un résultat récent existait déjà en
mémoire vectorielle.** Trouvé en concevant le pilote agent-wallet (le
"débloquer via x402" proposé par l'opérateur aurait pu re-payer pour la même
adresse à chaque cycle heartbeat sans ce correctif). Corrigé : recherche
d'abord (``_find_cached_insight``, gratuit, LanceDB local) un résultat de
moins de ``max_age_days`` pour cette adresse -- ne paie que si rien d'assez
récent n'existe."""
from __future__ import annotations

import json
from datetime import datetime, timezone

DEFAULT_MAX_AGE_DAYS = 7


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


def _source_id(address: str, *, on: str | None = None) -> str:
    date = on or datetime.now(timezone.utc).date().isoformat()
    return f"cybercentry-wallet-{address.lower()}-{date}"


async def _find_cached_insight(address: str, *, max_age_days: int) -> dict | None:
    """Cherche un résultat Cybercentry déjà payé pour ``address`` en mémoire
    vectorielle -- recherche sémantique (le texte stocké contient l'adresse
    exacte, donc un match proche est fiable), filtré ensuite par correspondance
    EXACTE du ``source_id`` (jamais un faux positif sur une adresse voisine) et
    par fraîcheur. ``None`` si rien d'assez récent (mémoire désactivée, jamais
    interrogée avant, ou tout ce qui existe est trop vieux)."""
    from aria_core.memory.vector import lancedb_store

    addr = address.strip().lower()
    prefix = f"cybercentry-wallet-{addr}-"
    matches = await lancedb_store.search(address, entry_type="insight", limit=5)
    for m in matches:
        meta = m.get("metadata") or {}
        source_id = str(meta.get("source_id") or "")
        if not source_id.startswith(prefix):
            continue
        date_str = source_id[len(prefix):]
        try:
            found_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        age_days = (datetime.now(timezone.utc).date() - found_date).days
        if age_days < 0 or age_days > max_age_days:
            continue
        try:
            raw = json.loads(meta.get("raw_json") or "null")
        except (TypeError, ValueError):
            raw = None
        return {
            "available": True, "raw": raw, "error": None,
            "amount_usd": 0.0, "vector_doc_id": m.get("id"), "cached": True,
        }
    return None


async def verify_and_remember_wallet(address: str, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """Paie Cybercentry pour vérifier ``address`` -- SAUF si un résultat de
    moins de ``max_age_days`` existe déjà en mémoire vectorielle (cache
    gratuit, vérifié avant tout paiement). Stocke tout nouveau résultat payé
    comme un ``insight`` (metadata source=cybercentry, topic=wallet-security,
    ``raw_json`` pour reconstruire le résultat brut sur un futur cache hit).
    Renvoie le résultat + ``vector_doc_id`` + ``cached`` (``True`` si servi
    depuis la mémoire, aucun paiement effectué cette fois)."""
    from aria_core.services.cybercentry import verify_wallet
    from aria_core.memory.vector import lancedb_store

    addr = (address or "").strip()
    if not addr:
        return {
            "available": False, "raw": None, "error": "adresse vide",
            "amount_usd": 0.0, "vector_doc_id": None, "cached": False,
        }

    cached = await _find_cached_insight(addr, max_age_days=max_age_days)
    if cached is not None:
        return cached

    result = await verify_wallet(addr)
    if not result["available"]:
        return {**result, "vector_doc_id": None, "cached": False}

    text = _format_wallet_insight(addr, result["raw"])
    doc_id = await lancedb_store.store(
        "insight",
        text,
        metadata={
            "source": "cybercentry",
            "topic": "wallet-security",
            "source_id": _source_id(addr),
            "raw_json": json.dumps(result["raw"]),
        },
    )
    return {**result, "vector_doc_id": doc_id, "cached": False}
