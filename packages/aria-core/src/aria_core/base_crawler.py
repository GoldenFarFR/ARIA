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
# Top pools (triés par volume/liquidité) : le terrain de chasse des tokens ÉTABLIS
# (vérifiés, avec vraie profondeur) — pas la benne des lancements frais. C'est ici
# qu'on trouve les vrais builders du 85% VC.
_TOP_POOLS_PATH = "/networks/base/pools"


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


def _extract_tokens_with_liquidity(payload: object) -> list[tuple[str, float]]:
    """(adresse, réserve USD du pool) depuis une réponse pools GeckoTerminal.

    ``attributes.reserve_in_usd`` = liquidité du pool. Permet de filtrer À LA
    DÉCOUVERTE : inutile de scanner un pool sous le plancher (il échouera au filtre).
    """
    out: list[tuple[str, float]] = []
    if not isinstance(payload, dict):
        return out
    for item in payload.get("data", []) or []:
        try:
            tid = item["relationships"]["base_token"]["data"]["id"]
            reserve = (item.get("attributes") or {}).get("reserve_in_usd")
        except (KeyError, TypeError, AttributeError):
            continue
        if not isinstance(tid, str):
            continue
        addr = tid.split("_", 1)[1] if "_" in tid else tid
        if not (addr.startswith("0x") and len(addr) == 42):
            continue
        try:
            r = float(reserve) if reserve is not None else 0.0
        except (TypeError, ValueError):
            r = 0.0
        out.append((addr.lower(), r))
    return out


async def discover_top_pools(
    *, fetch=None, limit: int = 100, min_liquidity_usd: float = 30_000.0
) -> list[str]:
    """Tokens des TOP pools Base (établis, liquides), filtrés par un plancher de liquidité.

    Le vrai terrain de chasse du 85% VC : des tokens avec une profondeur réelle, pas
    des lancements frais illiquides. On ne ramène que ce qui peut PASSER le filtre.
    """
    fetch = fetch or _fetch_gt
    payload = await fetch(_TOP_POOLS_PATH)
    seen: dict[str, None] = {}
    for addr, reserve in _extract_tokens_with_liquidity(payload):
        if reserve >= min_liquidity_usd and addr not in seen:
            seen[addr] = None
        if len(seen) >= limit:
            break
    return list(seen.keys())


async def discover_virtuals_tokens(*, client=None, limit: int = 50) -> list[str]:
    """Tokens Virtuals en bonding (la niche 15%) — vrais builders d'agents IA.

    ATTENTION : ces tokens sont en courbe de bonding (liquidité mince, souvent non
    vérifiés au sens Blockscout) -> ils N'entrent PAS dans l'absorbeur standard (ils
    échoueraient à tort). Réservé au futur pipeline bonding dédié (mode d'analyse
    adapté). Exposé ici pour ce pipeline, pas pour le crawl VC standard.
    """
    if client is None:
        from aria_core.services.virtuals import virtuals_client as client
    try:
        protos = await client.fetch_prototypes()
    except Exception as exc:  # noqa: BLE001 — jamais bloquant
        logger.info("base_crawler: découverte Virtuals échouée (%s)", exc)
        return []
    seen: dict[str, None] = {}
    for vt in protos or []:
        addr = (getattr(vt, "token_address", None) or "").lower()
        if addr.startswith("0x") and len(addr) == 42 and addr not in seen:
            seen[addr] = None
        if len(seen) >= limit:
            break
    return list(seen.keys())


async def discover_virtuals_graduated_tokens(*, client=None, limit: int = 50) -> list[str]:
    """Tokens Virtuals ayant récemment gradué — vraie liquidité DEX, pipeline STANDARD.

    Contrairement à ``discover_virtuals_tokens`` (bonding, niche 15%), ces tokens ont
    une paire DEX réelle post-graduation : ils rejoignent l'absorbeur générique
    (``token_absorber.absorb``, pool 85% VC) comme n'importe quel token Base, sans
    traitement spécial. Exposé pour un pickup plus rapide qu'attendre leur apparition
    dans ``discover_top_pools`` (seuil de liquidité, peuvent être encore fins juste
    après graduation).
    """
    if client is None:
        from aria_core.services.virtuals import virtuals_client as client
    try:
        tokens = await client.fetch_graduated()
    except Exception as exc:  # noqa: BLE001 — jamais bloquant
        logger.info("base_crawler: découverte Virtuals gradués échouée (%s)", exc)
        return []
    seen: dict[str, None] = {}
    for vt in tokens or []:
        addr = (getattr(vt, "token_address", None) or "").lower()
        if addr.startswith("0x") and len(addr) == 42 and addr not in seen:
            seen[addr] = None
        if len(seen) >= limit:
            break
    return list(seen.keys())


async def crawl_and_absorb(
    *, discover=None, absorber=None, limit: int = 50, max_age_days: int | None = None
) -> dict:
    """Découvre des tokens Base et les absorbe. Retourne le compte par verdict.

    ``discover()`` → liste de contrats (défaut : GeckoTerminal). ``absorber(contract)``
    → 'kept'/'rejected'/'skip_*' (défaut : token_absorber.absorb). L'absorbeur
    court-circuite déjà les tokens connus, donc pas de gaspillage. ``max_age_days``
    (optionnel) : transmis à l'absorbeur, hors-scope ('skip_too_old') au-delà.
    """
    # Défaut : le terrain de chasse « top pools » (établis, liquides) — pas la benne
    # des lancements frais. C'est là que vivent les vrais builders du 85% VC.
    disc = discover or discover_top_pools
    if absorber is None:
        from aria_core.token_absorber import absorb as absorber

    tokens = await disc() if callable(disc) else disc
    counts: dict[str, int] = {}
    for contract in list(tokens)[:limit]:
        try:
            if max_age_days is not None:
                verdict = await absorber(contract, max_age_days=max_age_days)
            else:
                verdict = await absorber(contract)
        except Exception as exc:  # noqa: BLE001 — un token qui plante n'arrête pas le crawl
            logger.info("base_crawler: absorb %s échoué (%s)", contract, exc)
            verdict = "error"
        counts[verdict] = counts.get(verdict, 0) + 1
    logger.info("base_crawler: %s tokens traités %s", sum(counts.values()), counts)
    return counts


async def retry_stale_pending(
    *, limit: int = 20, older_than_hours: int = 24, lister=None, absorber=None
) -> dict:
    """Retente délibérément les candidats ``pending`` (échec MOU) laissés de côté.

    ``crawl_and_absorb`` ne revoit un candidat ``pending`` QUE s'il réapparaît par
    hasard dans une découverte ultérieure (``token_absorber.absorb`` ne court-circuite
    déjà pas 'pending', cf. ``test_soft_fail_pending_is_still_rescanned_next_cycle``)
    — mais rien ne va délibérément le repêcher si le marché ne le remet pas sous le
    nez du crawl. Résultat mesuré (audit #77) : le pool ``active`` reste à 0 malgré
    un flux de découverte correct, parce que les candidats « pas encore mûrs »
    (contrat pas encore vérifié, holders pas encore lisibles, liquidité en train de
    monter) ne sont jamais revisités une fois leurs données susceptibles d'avoir mûri.

    Ne duplique aucun filtre : ``lister`` (défaut ``screened_pool.list_stale_pending``)
    trouve juste QUI retenter, ``absorber`` (défaut ``token_absorber.absorb``) est le
    MÊME code de filtrage que le crawl normal — un candidat encore immature reste
    'pending' (nouvel essai au prochain passage), un candidat désormais malveillant
    confirmé devient 'rejected', un candidat qui a mûri devient enfin 'active'.
    """
    if lister is None:
        from aria_core import screened_pool

        async def lister():
            return await screened_pool.list_stale_pending(
                older_than_hours=older_than_hours, limit=limit
            )

    if absorber is None:
        from aria_core.token_absorber import absorb as absorber

    stale = await lister()
    counts: dict[str, int] = {}
    for row in stale:
        contract = row["contract"] if isinstance(row, dict) else row
        try:
            verdict = await absorber(contract)
        except Exception as exc:  # noqa: BLE001 — un candidat en échec n'arrête pas les autres
            logger.info("base_crawler: retry %s échoué (%s)", contract, exc)
            verdict = "error"
        counts[verdict] = counts.get(verdict, 0) + 1
    logger.info("base_crawler: retry pending -> %s tokens revus %s", sum(counts.values()), counts)
    return counts
