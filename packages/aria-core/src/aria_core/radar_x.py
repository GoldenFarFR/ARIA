"""Radar X — sourcing social filtré on-chain (Voûte 4).

Écoute le bruit social (``services/x_social``), en retient les candidats assez
bruyants (seuil anti-astroturf), puis les fait **arbitrer par l'on-chain** via
l'absorbeur :
  - contrat inconnu / actif  → ``absorb`` (le scan tranche : gardé ou rejeté) ;
  - contrat déjà **rejeté**  → ``reconsider_on_signal`` (le bruit RÉVEILLE un
    rejeté, le re-scan réévalue sur les faits).

LIGNE ROUGE (dôme) : le social ne déclenche JAMAIS un achat/vente. Il ne fait que
**sourcer** de nouveaux candidats et **rouvrir la porte** à des rejetés. La décision
appartient toujours à l'analyse on-chain. ARIA n'est jamais le mégaphone d'un cabal :
un consensus social ne vaut pas une thèse.

Tout est injectable → testable hors-ligne. Lecture seule, aucune signature.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Seuils anti-astroturf : sous ces valeurs, le bruit n'est pas assez crédible pour
# valoir un scan (un seul auteur qui spamme n'est pas un signal). Ajustables.
_MIN_MENTIONS = 2
_MIN_DISTINCT_AUTHORS = 2


async def run_radar(
    *,
    social_client=None,
    query: str = "base token 0x",
    absorber=None,
    resonator=None,
    pool_status=None,
    min_mentions: int = _MIN_MENTIONS,
    min_distinct_authors: int = _MIN_DISTINCT_AUTHORS,
    limit: int = 50,
) -> dict:
    """Fait un tour de radar social → arbitrage on-chain. Retourne un rapport de comptes.

    Injectables (défauts prod entre parenthèses) :
      - ``social_client`` (``x_social.x_social_client``) : source du bruit ;
      - ``absorber(contract)`` (``token_absorber.absorb``) : scanne un candidat neuf ;
      - ``resonator(contract)`` (``token_absorber.reconsider_on_signal``) : réveille un rejeté ;
      - ``pool_status(contract)`` (``screened_pool.get_status``) : statut connu ('rejected'/'active'/None).

    Rapport : ``{sourced, above_threshold, kept, rejected, resurrected, skipped, error}``.
    """
    if social_client is None:
        from aria_core.services.x_social import x_social_client as social_client
    if absorber is None:
        from aria_core.token_absorber import absorb as absorber
    if resonator is None:
        from aria_core.token_absorber import reconsider_on_signal as resonator
    if pool_status is None:
        from aria_core.screened_pool import get_status as pool_status

    signals = await social_client.scan_mentions(query, limit=limit * 4)
    report = {
        "sourced": len(signals),
        "above_threshold": 0,
        "kept": 0,
        "rejected": 0,
        "resurrected": 0,
        "skipped": 0,
        "error": 0,
    }

    processed = 0
    for sig in signals:
        if processed >= limit:
            break
        # Filtre bruit : le social doit être assez crédible pour mériter un scan.
        if sig.mentions < min_mentions or sig.distinct_authors < min_distinct_authors:
            continue
        report["above_threshold"] += 1
        processed += 1

        try:
            status = await pool_status(sig.contract)
            if status == "rejected":
                # Le bruit réveille un rejeté ; le re-scan on-chain tranche.
                verdict = await resonator(sig.contract)
                if verdict == "kept":
                    report["resurrected"] += 1
                else:
                    report["rejected"] += 1
            elif status == "active":
                report["skipped"] += 1
            else:
                verdict = await absorber(sig.contract)
                if verdict == "kept":
                    report["kept"] += 1
                elif verdict == "rejected":
                    report["rejected"] += 1
                else:
                    report["skipped"] += 1
        except Exception as exc:  # noqa: BLE001 — un candidat qui plante n'arrête pas le radar
            logger.info("radar_x: traitement %s échoué (%s)", sig.contract, exc)
            report["error"] += 1

    logger.info("radar_x: tour terminé %s", report)
    return report
