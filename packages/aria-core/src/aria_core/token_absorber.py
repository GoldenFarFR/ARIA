"""Absorbeur de tokens — « dénicheur de talents » d'ARIA.

Scanne un contrat et tranche, intransigeant :
  - **valeur réelle** (passe le filtre de sécurité) → **gardé** dans la base
    (`screened_pool`, status active) ;
  - **rien** → **rejeté « pour toujours »** (status rejected) : on ne le re-scanne
    plus (efficacité), on garde juste la raison.

**Résurrection** : si un bruit réapparaît (radar / pic d'activité), on appelle
``reconsider_on_signal`` — le bruit **réveille** un rejeté, puis le re-scan
**réévalue sur les faits on-chain**. Le bruit filtre/réveille, il ne décide jamais
(dôme : un signal social ne déclenche pas d'action, il déclenche une ré-analyse).

Aucune écriture on-chain, aucune signature : c'est de la lecture + un journal.
"""
from __future__ import annotations

import logging
import time

from aria_core import screened_pool
from aria_core.services.blockscout import blockscout_client
from aria_core.skills.acp_onchain_scan import scan_base_token
from aria_core.skills.safety_screen import safety_screen

logger = logging.getLogger(__name__)

# Pré-filtre découverte (Volet C, 12/07) : sous ce seuil, un candidat est traité
# comme "pas encore mûr" (contrat pas encore vérifié, holders pas encore indexés
# par Blockscout) plutôt que comme "structurellement bloqué" — garde-fou anti-faux-
# négatif pour les tokens tout juste déployés (cf. ``_prefilter_reason``).
_PREFILTER_MIN_AGE_DAYS = 2.0

_PREFILTER_REASON_PREFIX = "pré-filtre découverte (Blockscout)"


def _prefilter_reason(info) -> str | None:
    """``None`` si le candidat doit passer au scan complet, sinon le motif à tracer.

    Ne tranche QUE sur des faits Blockscout disponibles (``info.available``) — toute
    donnée manquante (429, timeout, adresse introuvable) fait passer au scan complet
    (fail-open, jamais de rejet sur absence de donnée, cf. politique ``blockscout.py``).
    """
    if info is None or not info.available:
        return None
    unverified = info.is_verified is False
    holders_unknown = info.holders_count is None or info.holders_count == 0
    if not (unverified or holders_unknown):
        return None
    bits = []
    if unverified:
        bits.append("contrat non vérifié")
    if holders_unknown:
        bits.append("holders non indexés")
    return f"{_PREFILTER_REASON_PREFIX} : {' et '.join(bits)} — écarté avant scan complet"


async def absorb(
    contract: str,
    *,
    scanner=None,
    force: bool = False,
    max_age_days: int | None = None,
    known_age_days: float | None = None,
    ctx=None,
    source: str = "",
    **screen_kwargs,
) -> str:
    """Scanne un contrat et le range : 'kept' / 'rejected' / 'skip_*'.

    Sans ``force`` : un contrat déjà 'rejected' ('jeté pour toujours') ou déjà
    'active' n'est PAS re-scanné (on renvoie 'skip_rejected' / 'skip_active').
    ``force=True`` (résurrection ou rafraîchissement) ignore ce court-circuit et
    réévalue. ``scanner`` est injectable (tests offline). ``screen_kwargs`` sont
    passés à ``safety_screen`` (seuils ajustables). ``max_age_days`` (optionnel) :
    hors-scope (pas fraude/légitime — 'skip_too_old') si la paire est plus vieille ;
    vérifié avant le filtre de sécurité pour économiser le scan honeypot. ``ctx``
    (optionnel) : contexte déjà scanné (évite un second scan réseau si l'appelant
    a déjà dû regarder ``ctx.best_pair`` avant de décider d'appeler ``absorb`` —
    cf. ``bonding_absorber.absorb_direct_candidate``). ``source`` (optionnel, ex.
    ``'top_pools'``/``'radar_x'``) : pipeline de découverte d'origine, transmis tel
    quel à ``screened_pool`` — pure traçabilité, n'affecte aucune décision de filtrage
    (suite audit #77 diversification, 12/07). ``known_age_days`` (optionnel, Volet C
    12/07) : âge on-chain déjà connu de l'appelant (ex. ``first_screened_at`` côté
    ``retry_stale_pending``) — si ``>= _PREFILTER_MIN_AGE_DAYS`` ET qu'aucun ``ctx``
    n'est déjà fourni, un appel Blockscout léger (``get_address_info``) tranche AVANT
    le scan complet : contrat toujours non vérifié et/ou holders jamais indexés après
    ce délai -> ``'skip_prefiltered'`` (échec mou, retracé en ``pending``, jamais
    ``rejected`` — un candidat peut toujours mûrir plus tard). ``None`` (défaut) ou
    valeur sous le seuil : comportement inchangé, scan complet systématique — ne
    jamais rejeter sur une donnée manquante ou un candidat encore trop frais.
    """
    scan = scanner or scan_base_token
    if not force:
        status = await screened_pool.get_status(contract)
        if status == "rejected":
            return "skip_rejected"
        if status == "active":
            return "skip_active"

    if ctx is None and known_age_days is not None and known_age_days >= _PREFILTER_MIN_AGE_DAYS:
        info = await blockscout_client.get_address_info(contract)
        reason = _prefilter_reason(info)
        if reason is not None:
            logger.info("absorb %s : pré-filtré (%s) — scan complet évité", contract, reason)
            await screened_pool.record_pending(
                contract=contract,
                reason=reason,
                source=source,
            )
            return "skip_prefiltered"

    # Honeypot ACTIF au filtre d'entrée : un token honeypot / à taxe extractive / owner
    # réversible ne doit pas entrer dans le pool, pas seulement être signalé à l'analyse.
    if ctx is None:
        ctx = await scan(contract, include_honeypot=True)

    if max_age_days is not None:
        created_ms = ctx.best_pair.pair_created_at if ctx.best_pair else None
        if created_ms:
            age_days = (time.time() * 1000 - created_ms) / 86_400_000
            if age_days > max_age_days:
                return "skip_too_old"

    result = safety_screen(ctx, **screen_kwargs)

    if result.passed:
        best = ctx.best_pair
        await screened_pool.upsert_screened(
            contract=contract,
            symbol=(best.base_symbol if best else ""),
            liquidity_usd=result.liquidity_usd,
            security_score=result.security_score,
            top_holder_pct=ctx.top_holder_pct,
            verdict=result.verdict,
            pool_address=(best.pair_address if best else ""),
            screen_reason=result.reasons[0] if result.reasons else "",
            source=source,
        )
        return "kept"

    # Échec MOU (données indisponibles : 429/timeout, holders non renvoyés) : on ne
    # bannit PAS « pour toujours » — un re-scan plus tard pourra trancher. Sinon un bon
    # token scanné pendant un pic d'indisponibilité serait perdu définitivement.
    if not result.hard_fail:
        # Transparence exigée : si le token est PROMETTEUR mais OPAQUE, ARIA remonte
        # une requête de recalibrage à l'opérateur au lieu de trancher dans le noir.
        try:
            from aria_core.recalibration import maybe_escalate

            await maybe_escalate(ctx, symbol=(ctx.best_pair.base_symbol if ctx.best_pair else ""))
        except Exception as exc:  # noqa: BLE001 — l'escalade ne doit jamais casser l'absorption
            logger.info("absorb %s : escalade recalibrage échouée (%s)", contract, exc)
        reason = "; ".join(result.reasons) if result.reasons else "raison indisponible"
        logger.info("absorb %s : échec mou (%s) — non banni, à réessayer", contract, reason)
        # Trace consultable (status='pending', ne court-circuite pas le re-scan) : avant
        # ce correctif, un échec mou ne laissait AUCUNE donnée nulle part (audit #77).
        # liquidity_usd/security_score/verdict transmis (15/07) : le scan complet a déjà
        # tourné ici (contrairement au pré-filtre Volet C ci-dessus), ne pas laisser un
        # candidat pending prometteur indiscernable d'un candidat sans aucun signal.
        await screened_pool.record_pending(
            contract=contract,
            reason=reason,
            symbol=(ctx.best_pair.base_symbol if ctx.best_pair else ""),
            source=source,
            liquidity_usd=result.liquidity_usd,
            security_score=result.security_score,
            verdict=result.verdict,
            top_holder_pct=ctx.top_holder_pct,
        )
        return "skip_incomplete"

    # Même correctif (15/07) : un rejet dur a lui aussi un scan complet en main,
    # ne pas le laisser indiscernable d'un rejet sans aucun signal.
    await screened_pool.record_rejected(
        contract=contract,
        reason="; ".join(result.reasons),
        symbol=(ctx.best_pair.base_symbol if ctx.best_pair else ""),
        source=source,
        liquidity_usd=result.liquidity_usd,
        security_score=result.security_score,
        verdict=result.verdict,
        top_holder_pct=ctx.top_holder_pct,
    )
    return "rejected"


async def reconsider_on_signal(
    contract: str, *, scanner=None, source: str = "", **screen_kwargs
) -> str:
    """Un bruit a réapparu : ressuscite un rejeté et le réévalue sur les faits on-chain.

    Le signal ne décide de rien — il rouvre juste la porte, le re-scan tranche.
    Retourne le nouveau verdict ('kept' / 'rejected'). ``source`` : même paramètre
    que ``absorb`` (le signal qui réveille EST le pipeline d'origine ici, ex. 'radar_x').
    """
    await screened_pool.reconsider(contract)
    return await absorb(contract, scanner=scanner, force=True, source=source, **screen_kwargs)
