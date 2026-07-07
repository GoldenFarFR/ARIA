"""Crawler Base — découvre les tokens et les fait passer par l'absorbeur.

« Tout scanner » : on récupère en continu les pools Base (nouveaux + tendance via
GeckoTerminal), on extrait les contrats de token, et on les passe à
``token_absorber.absorb`` → gardé dans la base propriétaire ou rejeté pour toujours
(sauf résurrection). Un token déjà connu (actif/rejeté) est court-circuité par
l'absorbeur, donc re-crawler ne coûte rien.

La découverte réseau (GeckoTerminal) est **injectable** → testable hors-ligne. En
prod, le défaut interroge l'API publique (tourne sur le VPS, réseau autorisé).
Lecture seule, aucune signature.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_GT_BASE = "https://api.geckoterminal.com/api/v2"
_DISCOVERY_PATHS = (
    "/networks/base/new_pools",
    "/networks/base/trending_pools",
)


def _extract_token_contracts(payload: object) -> list[str]:
    """Extrait les adresses de token (base_token) d'une réponse pools GeckoTerminal.

    ``data[].relationships.base_token.data.id`` = ``"base_0x…"`` → on retire le
    préfixe réseau. Ignore silencieusement toute entrée malformée (jamais d'exception).
    """
    out: list[str] = []
    if not isinstance(payload, dict):
        return out
    for item in payload.get("data", []) or []:
        try:
            tid = item["relationships"]["base_token"]["data"]["id"]
        except (KeyError, TypeError):
            continue
        if isinstance(tid, str):
            # "base_0xabc..." -> "0xabc..."
            addr = tid.split("_", 1)[1] if "_" in tid else tid
            if addr.startswith("0x") and len(addr) == 42:
                out.append(addr.lower())
    return out


async def _fetch_gt(path: str) -> object | None:
    """GET GeckoTerminal (dégradation gracieuse : None sur toute erreur, jamais bloquant)."""
    url = f"{_GT_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as exc:  # noqa: BLE001 — jamais bloquant
        logger.info("base_crawler: fetch %s échoué (%s)", path, exc)
        return None


async def discover_base_tokens(*, fetch=None, limit: int = 100) -> list[str]:
    """Contrats de token Base à candidater (nouveaux + tendance), dédoublonnés."""
    fetch = fetch or _fetch_gt
    seen: dict[str, None] = {}
    for path in _DISCOVERY_PATHS:
        payload = await fetch(path)
        for addr in _extract_token_contracts(payload):
            if addr not in seen:
                seen[addr] = None
            if len(seen) >= limit:
                break
        if len(seen) >= limit:
            break
    return list(seen.keys())


async def crawl_and_absorb(*, discover=None, absorber=None, limit: int = 50) -> dict:
    """Découvre des tokens Base et les absorbe. Retourne le compte par verdict.

    ``discover()`` → liste de contrats (défaut : GeckoTerminal). ``absorber(contract)``
    → 'kept'/'rejected'/'skip_*' (défaut : token_absorber.absorb). L'absorbeur
    court-circuite déjà les tokens connus, donc pas de gaspillage.
    """
    disc = discover or discover_base_tokens
    if absorber is None:
        from aria_core.token_absorber import absorb as absorber

    tokens = await disc() if callable(disc) else disc
    counts: dict[str, int] = {}
    for contract in list(tokens)[:limit]:
        try:
            verdict = await absorber(contract)
        except Exception as exc:  # noqa: BLE001 — un token qui plante n'arrête pas le crawl
            logger.info("base_crawler: absorb %s échoué (%s)", contract, exc)
            verdict = "error"
        counts[verdict] = counts.get(verdict, 0) + 1
    logger.info("base_crawler: %s tokens traités %s", sum(counts.values()), counts)
    return counts
