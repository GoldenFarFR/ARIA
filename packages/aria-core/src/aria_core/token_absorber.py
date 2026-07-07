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

from aria_core import screened_pool
from aria_core.skills.acp_onchain_scan import scan_base_token
from aria_core.skills.safety_screen import safety_screen

logger = logging.getLogger(__name__)


async def absorb(contract: str, *, scanner=None, force: bool = False, **screen_kwargs) -> str:
    """Scanne un contrat et le range : 'kept' / 'rejected' / 'skip_*'.

    Sans ``force`` : un contrat déjà 'rejected' ('jeté pour toujours') ou déjà
    'active' n'est PAS re-scanné (on renvoie 'skip_rejected' / 'skip_active').
    ``force=True`` (résurrection ou rafraîchissement) ignore ce court-circuit et
    réévalue. ``scanner`` est injectable (tests offline). ``screen_kwargs`` sont
    passés à ``safety_screen`` (seuils ajustables).
    """
    scan = scanner or scan_base_token
    if not force:
        status = await screened_pool.get_status(contract)
        if status == "rejected":
            return "skip_rejected"
        if status == "active":
            return "skip_active"

    ctx = await scan(contract)
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
        )
        return "kept"

    await screened_pool.record_rejected(
        contract=contract,
        reason="; ".join(result.reasons),
        symbol=(ctx.best_pair.base_symbol if ctx.best_pair else ""),
    )
    return "rejected"


async def reconsider_on_signal(contract: str, *, scanner=None, **screen_kwargs) -> str:
    """Un bruit a réapparu : ressuscite un rejeté et le réévalue sur les faits on-chain.

    Le signal ne décide de rien — il rouvre juste la porte, le re-scan tranche.
    Retourne le nouveau verdict ('kept' / 'rejected').
    """
    await screened_pool.reconsider(contract)
    return await absorb(contract, scanner=scanner, force=True, **screen_kwargs)
