"""Absorbeur dédié à la niche bonding (15%) — le pendant de ``token_absorber.py``.

Découvre des candidats ENCORE en courbe de bonding (``services/launchpad_discovery``,
adaptateurs catégorie ``"bonding"``), les scanne (``scan_base_token``, qui résout déjà
``ctx.bonding_phase``/``ctx.mint_authority``/``ctx.dev_signal``), les filtre
(``bonding_screen.bonding_safety_screen``, PAS le filtre standard qui exigerait à tort
une paire DEX) et les range dans ``screened_pool`` sous ``network="base-bonding"`` —
**jamais** ``network="base"`` (le pool 85% VC), pour ne jamais contaminer le tirage
hebdomadaire (``weekly_training.draw_lottery``).

Même doctrine que ``token_absorber.py`` :
  - un rejet **confirmé** (``hard_fail``) est définitif (``status='rejected'``, pas
    re-scanné sans une résurrection explicite) ;
  - un échec **mou** (donnée indisponible : statut bonding pas encore confirmé,
    autorité du mint indéterminable...) laisse une trace (``status='pending'``) et
    sera retenté au prochain cycle, jamais banni à tort.

Aucune écriture on-chain, aucune signature : lecture + un journal.
"""

from __future__ import annotations

import logging
import os

from aria_core import screened_pool
from aria_core.skills.acp_onchain_scan import scan_base_token
from aria_core.skills.bonding_screen import bonding_safety_screen

logger = logging.getLogger(__name__)

BONDING_NETWORK = "base-bonding"


async def absorb_bonding_candidate(
    contract: str, *, scanner=None, force: bool = False, **screen_kwargs
) -> str:
    """Scanne un candidat bonding et le range : 'kept' / 'rejected' / 'skip_*'.

    Symétrique à ``token_absorber.absorb`` mais sur le pool ``base-bonding`` et via
    ``bonding_safety_screen`` (aucune exigence de paire DEX). ``force=True`` ignore le
    court-circuit statut connu (résurrection / rafraîchissement).
    """
    scan = scanner or scan_base_token
    if not force:
        status = await screened_pool.get_status(contract)
        if status == "rejected":
            return "skip_rejected"
        if status == "active":
            return "skip_active"

    ctx = await scan(contract, include_dev_behavior=True)
    result = bonding_safety_screen(ctx, **screen_kwargs)

    if result.passed:
        await screened_pool.upsert_screened(
            contract=contract,
            symbol="",
            liquidity_usd=0.0,  # pas de liquidité DEX en bonding, par construction
            security_score=result.security_score,
            verdict=result.verdict,
            network=BONDING_NETWORK,
            screen_reason=(
                f"bonding {result.bonding_progress:.0%} vers graduation"
                if result.bonding_progress is not None
                else "bonding (progression inconnue)"
            ),
        )
        return "kept"

    if not result.hard_fail:
        reason = "; ".join(result.reasons) if result.reasons else "raison indisponible"
        logger.info("bonding_absorb %s : échec mou (%s) — non banni, à réessayer", contract, reason)
        await screened_pool.record_pending(contract=contract, reason=reason, network=BONDING_NETWORK)
        return "skip_incomplete"

    await screened_pool.record_rejected(
        contract=contract, reason="; ".join(result.reasons), network=BONDING_NETWORK
    )
    return "rejected"


async def discover_and_absorb_bonding(*, discover=None, absorber=None, limit_per_launchpad: int = 50) -> dict:
    """Découvre puis absorbe les candidats bonding de TOUS les launchpads actifs.

    Retourne le compte par verdict, agrégé tous launchpads confondus (même forme que
    ``base_crawler.crawl_and_absorb``, pour un branchement heartbeat symétrique).
    """
    if discover is None:
        from aria_core.services.launchpad_discovery import discover_bonding_candidates as discover
    absorb = absorber or absorb_bonding_candidate

    by_launchpad = await discover(limit_per_launchpad=limit_per_launchpad)
    counts: dict[str, int] = {}
    for _launchpad_key, addresses in (by_launchpad or {}).items():
        for contract in addresses:
            try:
                verdict = await absorb(contract)
            except Exception as exc:  # noqa: BLE001 — un candidat en échec n'arrête pas les autres
                logger.info("bonding_absorb %s : échec inattendu (%s)", contract, exc)
                verdict = "error"
            counts[verdict] = counts.get(verdict, 0) + 1
    return counts


async def absorb_direct_candidate(contract: str, *, scanner=None) -> str:
    """Absorbe un candidat DEX-direct fraîchement découvert (Clanker, y compris via
    Bankr qui déploie dessus -- adresses vanity reconnaissables, vérifié 10/07).

    Simple relais vers ``token_absorber.absorb`` (même jugement que le pipeline
    standard, pool 85% VC). L'absence de paire DEX/liquidité insuffisante/contrat
    pas encore vérifié n'est PLUS un rejet définitif depuis le correctif du
    10/07 sur ``safety_screen.hard_fail`` (décision opérateur : seul un mécanisme
    malveillant CONFIRMÉ dans le contrat justifie un bannissement définitif -- la
    liquidité/vérification/paire sont des aspects d'investissement qui évoluent
    avec la maturité du projet, "comme tous les autres tokens"). Un token tout
    juste déployé atterrit donc correctement en ``pending`` (retry) plutôt que
    ``rejected`` sans logique dédiée ici -- scan unique, réutilisé via ``ctx=``.
    """
    from aria_core.token_absorber import absorb as absorb_standard

    scan = scanner or scan_base_token
    status = await screened_pool.get_status(contract)
    if status == "rejected":
        return "skip_rejected"
    if status == "active":
        return "skip_active"

    ctx = await scan(contract, include_honeypot=True)
    return await absorb_standard(contract, scanner=scanner, ctx=ctx)


def bonding_discovery_enabled() -> bool:
    """Seam gaté OFF par défaut. Le cycle heartbeat de découverte multi-launchpad ne
    tourne qu'une fois ce flag activé par l'opérateur (nouveaux appels réseau)."""
    return os.environ.get("ARIA_BONDING_DISCOVERY_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def run_bonding_discovery_cycle(*, limit_per_launchpad: int = 50) -> dict:
    """Un cycle complet : découverte + absorption sur TOUS les launchpads actifs.

    Deux volets INDÉPENDANTS (un échec de l'un n'efface jamais le succès de l'autre) :
      - ``bonding`` : candidats encore en courbe (niche 15%, ``network="base-bonding"``) ;
      - ``direct`` : candidats à liquidité DEX réelle (Clanker, Virtuals gradués) —
        passent par ``absorb_direct_candidate`` (grâce period « pas encore de paire »
        avant de rejoindre le pipeline STANDARD ``token_absorber.absorb``, pool 85%).
    """
    bonding_counts = await discover_and_absorb_bonding(limit_per_launchpad=limit_per_launchpad)

    direct_counts: dict[str, int] = {}
    try:
        from aria_core.services.launchpad_discovery import discover_direct_candidates

        by_launchpad = await discover_direct_candidates(limit_per_launchpad=limit_per_launchpad)
        for _launchpad_key, addresses in (by_launchpad or {}).items():
            for contract in addresses:
                try:
                    verdict = await absorb_direct_candidate(contract)
                except Exception as exc:  # noqa: BLE001
                    logger.info("bonding_discovery_cycle: absorb direct %s échoué (%s)", contract, exc)
                    verdict = "error"
                direct_counts[verdict] = direct_counts.get(verdict, 0) + 1
    except Exception as exc:  # noqa: BLE001 — le volet direct ne doit jamais casser le volet bonding
        logger.info("bonding_discovery_cycle: volet direct échoué (%s)", exc)

    return {"bonding": bonding_counts, "direct": direct_counts}
