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
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_GT_BASE = "https://api.geckoterminal.com/api/v2"
_DISCOVERY_PATHS = (
    "/networks/base/new_pools",
    "/networks/base/trending_pools",
)
# Top pools : le terrain de chasse des tokens ÉTABLIS (vérifiés, avec vraie
# profondeur) — pas la benne des lancements frais. C'est ici qu'on trouve les
# vrais builders du 85% VC. ``sort=h24_volume_usd_desc`` est EXPLICITE (suite
# audit #77 : le défaut GeckoTerminal pour cet endpoint est ``h24_tx_count_desc``
# — nombre de transactions 24h, pas profondeur/volume — biaisé vers l'activité
# brute (bots/snipers sur des tokens tout juste lancés) plutôt que vers des pools
# réellement établis. Le commentaire précédent affirmait un tri par volume/liquidité
# jamais imposé par le code — corrigé ici avec un paramètre de requête explicite).
_TOP_POOLS_PATH = "/networks/base/pools?sort=h24_volume_usd_desc"


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


def _pool_age_days(created_at: object) -> float | None:
    """Âge d'un pool en jours depuis ``attributes.pool_created_at`` (ISO 8601, GeckoTerminal).

    ``None`` si le champ est absent ou n'a pas pu être parsé (jamais d'exception —
    un âge inconnu n'est pas une erreur, juste une donnée manquante à traiter par
    l'appelant, cf. ``discover_top_pools``).
    """
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return (datetime.now(timezone.utc) - created).total_seconds() / 86_400.0


def _extract_tokens_with_liquidity(payload: object) -> list[tuple[str, float, float | None]]:
    """(adresse, réserve USD, âge en jours ou None) depuis une réponse pools GeckoTerminal.

    ``attributes.reserve_in_usd`` = liquidité du pool, ``attributes.pool_created_at``
    = date de création (ISO 8601). Permet de filtrer À LA DÉCOUVERTE : inutile de
    scanner un pool sous le plancher de liquidité ou trop jeune (il échouera/mûrira
    rarement au filtre de sécurité) — coût nul, ces deux champs sont déjà dans la
    même réponse GeckoTerminal, aucun appel réseau supplémentaire.
    """
    out: list[tuple[str, float, float | None]] = []
    if not isinstance(payload, dict):
        return out
    for item in payload.get("data", []) or []:
        try:
            tid = item["relationships"]["base_token"]["data"]["id"]
            attrs = item.get("attributes") or {}
            reserve = attrs.get("reserve_in_usd")
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
        age_days = _pool_age_days(attrs.get("pool_created_at"))
        out.append((addr.lower(), r, age_days))
    return out


async def discover_top_pools(
    *,
    fetch=None,
    limit: int = 100,
    min_liquidity_usd: float = 45_000.0,
    min_age_days: float | None = None,
) -> list[str]:
    """Tokens des TOP pools Base (établis, liquides), filtrés par un plancher de liquidité.

    Le vrai terrain de chasse du 85% VC : des tokens avec une profondeur réelle, pas
    des lancements frais illiquides. On ne ramène que ce qui peut PASSER le filtre.

    ``min_liquidity_usd`` (défaut 45 000 $, relevé depuis 30 000 $ le 12/07 — suite
    audit #77 diversification) : ce plancher checke ``reserve_in_usd`` via
    GeckoTerminal, alors que le gate réel dans ``safety_screen`` checke la liquidité
    via DexScreener (``scan_base_token``) — deux fournisseurs qui ne sont PAS garantis
    d'accord sur le même pool. Marge de sécurité empirique (échantillon du 12/07 :
    des candidats à $30k+ en `reserve_in_usd` scannaient à $0 côté DexScreener), pas
    un nouveau critère de sécurité — le seuil réel (30k$) dans ``safety_screen.py``
    reste inchangé, cette marge réduit juste le bruit envoyé à ``absorb()``.

    ``min_age_days`` (optionnel, défaut ``None`` = pas de filtre, comportement
    inchangé) : exclut les pools plus jeunes que ce seuil. Un âge inconnu (champ
    ``pool_created_at`` absent/imparsable) est traité comme trop jeune dès que
    ``min_age_days`` est fourni — fail-closed, cohérent avec le reste du pipeline
    (cf. ``safety_screen``), sans jamais toucher à ses gates de sécurité.
    """
    fetch = fetch or _fetch_gt
    payload = await fetch(_TOP_POOLS_PATH)
    seen: dict[str, None] = {}
    for addr, reserve, age_days in _extract_tokens_with_liquidity(payload):
        if reserve < min_liquidity_usd:
            continue
        if min_age_days is not None and (age_days is None or age_days < min_age_days):
            continue
        if addr not in seen:
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
    *,
    limit: int = 20,
    older_than_hours: int = 24,
    max_retries: int = 5,
    max_age_days: int = 7,
    lister=None,
    absorber=None,
    abandon_checker=None,
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

    Plafond anti-boucle-infinie (suite audit #77/#105 : 41/50 ``rejected`` trouvés
    sans signal dur, reliquats d'une version plus stricte du filtre, jamais retentés
    depuis — sans plafond, un candidat qui ne mûrit jamais serait retenté toutes les
    24h pour toujours). Si un candidat reste ``skip_incomplete`` (encore MOU) après ce
    nouveau passage, ``abandon_checker`` (défaut ``screened_pool.abandon_stale_pending``)
    vérifie ``max_retries``/``max_age_days`` et bascule en ``rejected`` (raison
    explicite) si dépassé. Encore une fois AUCUN nouveau critère de sécurité — juste
    une limite sur le nombre de passages, appliquée seulement après qu'``absorber``
    a déjà tranché que ce n'est ni mûri ('kept') ni malveillant confirmé ('rejected').
    """
    if lister is None:
        from aria_core import screened_pool

        async def lister():
            return await screened_pool.list_stale_pending(
                older_than_hours=older_than_hours, limit=limit
            )

    stale = await lister()

    # ``known_age_days`` (Volet C, 12/07 -- correctif du même jour : calculé
    # INCONDITIONNELLEMENT, PAS seulement quand ``absorber is None``. En prod,
    # ``heartbeat.py`` injecte TOUJOURS son propre ``absorber`` (wrapper Volet A qui
    # tague ``source``) -- une version antérieure de ce calcul, cantonnée à la branche
    # "absorber par défaut", ne s'exécutait donc jamais réellement (trouvé en
    # vérifiant la prod, pas en supposant que ça marchait). Dérivé de
    # ``first_screened_at`` -- borne conservative de l'âge on-chain réel (souvent
    # plus vieux que sa première détection par ARIA, jamais plus jeune pour les
    # candidats ``top_pools``/``bonding_direct``). Transmis à TOUT ``absorber``
    # (par défaut ou injecté) via un kwarg -- les wrappers Volet A
    # (``_absorb_top_pools``/``_absorb_radar`` dans ``heartbeat.py``) le
    # retransmettent déjà tel quel via leur ``**kw``, sans modification nécessaire
    # côté ``heartbeat.py``.
    _age_by_contract: dict[str, float] = {}
    for row in stale:
        if not isinstance(row, dict):
            continue
        contract_key = row.get("contract")
        first_screened = row.get("first_screened_at")
        if not (contract_key and first_screened):
            continue
        try:
            dt = datetime.fromisoformat(str(first_screened).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            _age_by_contract[contract_key] = (
                datetime.now(timezone.utc) - dt
            ).total_seconds() / 86_400.0
        except (ValueError, TypeError):
            continue

    if absorber is None:
        from aria_core.token_absorber import absorb as absorber

    if abandon_checker is None:
        from aria_core import screened_pool as _screened_pool

        async def abandon_checker(contract):
            return await _screened_pool.abandon_stale_pending(
                contract, max_retries=max_retries, max_age_days=max_age_days
            )

    counts: dict[str, int] = {}
    for row in stale:
        contract = row["contract"] if isinstance(row, dict) else row
        try:
            verdict = await absorber(contract, known_age_days=_age_by_contract.get(contract))
            # 'skip_prefiltered' (Volet C) est aussi une variante d'échec mou -- un
            # candidat structurellement bloqué doit finir par être abandonné, comme
            # 'skip_incomplete', pas retenté indéfiniment tous les 24h.
            if verdict in ("skip_incomplete", "skip_prefiltered") and await abandon_checker(contract):
                verdict = "abandoned"
        except Exception as exc:  # noqa: BLE001 — un candidat en échec n'arrête pas les autres
            logger.info("base_crawler: retry %s échoué (%s)", contract, exc)
            verdict = "error"
        counts[verdict] = counts.get(verdict, 0) + 1
    logger.info("base_crawler: retry pending -> %s tokens revus %s", sum(counts.values()), counts)
    return counts
