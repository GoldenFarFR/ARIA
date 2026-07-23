"""Verifies an address via Cybercentry (x402, paid) and remembers the result in
vector memory -- the first real caller of `memory/vector/lancedb_store.py`
(#199, 07/17, operator decision: pay for whatever feeds vector memory the
most). A verified fact, never invented -- if the call fails, nothing is
stored (honest degradation, not a placeholder).

**Cache before payment (07/18, real bug fixed): this function used to pay on
EVERY call, without ever checking whether a recent result already existed in
vector memory.** Found while designing the agent-wallet pilot (the
"unlock via x402" feature proposed by the operator could have re-paid for the
same address on every heartbeat cycle without this fix). Fixed: searches
first (``_find_cached_insight``, free, local LanceDB) for a result less than
``max_age_days`` old for this address -- only pays if nothing recent enough
exists."""
from __future__ import annotations

import json
from datetime import datetime, timezone

DEFAULT_MAX_AGE_DAYS = 7


def _format_wallet_insight(address: str, raw: dict) -> str:
    """Readable text from the raw Cybercentry response -- the JSON
    structure isn't guaranteed stable over time, defensive reading (`.get` everywhere)."""
    lines = [f"Cybercentry verification (wallet-verification) — {address}"]
    for key in ("risk", "risk_level", "is_sanctioned", "is_fraud", "score", "summary", "verdict"):
        if key in raw:
            lines.append(f"{key}: {raw[key]}")
    if len(lines) == 1:
        lines.append(f"raw response: {raw}")
    return "\n".join(lines)


def _source_id(address: str, *, on: str | None = None) -> str:
    date = on or datetime.now(timezone.utc).date().isoformat()
    return f"cybercentry-wallet-{address.lower()}-{date}"


async def _find_cached_insight(address: str, *, max_age_days: int) -> dict | None:
    """Looks for an already-paid Cybercentry result for ``address`` in vector
    memory -- semantic search (the stored text contains the exact
    address, so a close match is reliable), then filtered by EXACT
    match on ``source_id`` (never a false positive on a neighboring address) and
    by freshness. ``None`` if nothing recent enough (memory disabled, never
    queried before, or everything that exists is too old)."""
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
    """Pays Cybercentry to verify ``address`` -- UNLESS a result less than
    ``max_age_days`` old already exists in vector memory (free cache,
    checked before any payment). Stores every newly paid result
    as an ``insight`` (metadata source=cybercentry, topic=wallet-security,
    ``raw_json`` to reconstruct the raw result on a future cache hit).
    Returns the result + ``vector_doc_id`` + ``cached`` (``True`` if served
    from memory, no payment made this time)."""
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
