"""Livraison du rapport VC par email — orchestration (Étape C — envoi).

Relie le rendu HTML (`vc_report`) au transport SMTP (`services/mailer`), sous
garde-fous :

- **Kill-switch fail-closed** : si ARIA est en pause (ou l'état de pause est
  illisible), aucun email ne part (`outgoing_pause.is_paused(strict=True)`).
- **Dégradation sûre** : SMTP non configuré → pas d'envoi, message clair, jamais
  d'erreur bloquante.
- **Destinataire** : `ARIA_VC_REPORT_TO` (défaut `ARIA_SMTP_USER`) — la boîte
  d'ARIA.

Ce module n'exécute jamais de trade et ne touche jamais au wallet : il ne fait
qu'envoyer un document d'analyse.
"""
from __future__ import annotations

import logging
import os

from aria_core import outgoing_pause
from aria_core.services.mailer import send_email
from aria_core.skills.vc_analysis import VCResult
from aria_core.skills.vc_report import email_subject, render_html_report

logger = logging.getLogger(__name__)


def _plain_fallback(result: VCResult) -> str:
    """Version texte du rapport (clients sans HTML) — champs structurés, sans mise en forme."""
    potentiel = f"{result.potentiel}/10" if result.potentiel is not None else "n/a"
    lines = [
        "ARIA Vanguard ZHC — Analyse d'investissement",
        f"Token : {result.contract}",
        f"Recommandation : {result.recommandation} | Potentiel : {potentiel} | Risque : {result.risque}"
        f" | Confiance : {result.confiance_globale}",
        "",
    ]
    if result.resume_executif:
        lines += [f"En bref : {result.resume_executif}", ""]
    lines.append(f"Thèse : {result.these}")
    if result.scenarios:
        lines.append("")
        lines.append("Scénarios :")
        for sc in result.scenarios:
            lines.append(
                f"- {sc.get('nom')} : {sc.get('cible')} "
                f"(prob. {sc.get('probabilite')}%, confiance {sc.get('confiance')})"
            )
    if result.actionable and result.recommandation == "BUY":
        lines += [
            f"Taille suggérée : {result.taille_pct:.1f}% du capital",
            f"Entrée : {result.entree} | Invalidation : {result.invalidation} | Cible : {result.cible}",
        ]
    if result.donnees_insuffisantes:
        lines.append("Données insuffisantes : " + " ; ".join(result.donnees_insuffisantes))
    lines += [
        "",
        result.rapport_detaille,
        "",
        "Proposition soumise à validation humaine — aucune exécution automatique. Pas un conseil en investissement.",
    ]
    return "\n".join(lines)


def _recipient(env: dict[str, str] | None = None) -> str:
    src = env if env is not None else os.environ
    return (src.get("ARIA_VC_REPORT_TO") or src.get("ARIA_SMTP_USER") or "").strip()


async def send_vc_report(
    result: VCResult,
    *,
    generated_at: str,
    report_number: int | None = None,
    series_number: int | None = None,
    capital_usd: float | None = None,
    tier: str = "premium",
) -> tuple[bool, str | None]:
    """Rend le rapport et l'envoie par email. Retourne ``(ok, error)``, ne lève jamais.

    Garde-fous appliqués dans l'ordre : kill-switch → destinataire → envoi SMTP.
    ``report_number`` (optionnel) permet à un abonné de distinguer plusieurs
    analyses suivies du même token (« Rapport n°2 »). ``series_number``
    (optionnel) affiche le numéro de série global (« Série 00.047 »).
    ``capital_usd`` (optionnel) permet d'afficher la position en dollars (taille
    suggérée x capital du client) — même montant que celui utilisé côté Telegram.
    ``tier`` (« premium » par défaut, ou « standard ») sélectionne l'édition du
    rapport transmise à ``render_html_report`` — voir ce module pour le détail.
    """
    # 1. Kill-switch fail-closed : en pause (ou état illisible) → on n'envoie rien.
    if outgoing_pause.is_paused(strict=True):
        logger.info("send_vc_report: ARIA en pause (ou état illisible) — email non envoyé (fail-closed)")
        return False, "ARIA en pause — email suspendu (kill-switch)"

    # 2. Destinataire.
    to = _recipient()
    if not to:
        return False, "destinataire non configuré (ARIA_VC_REPORT_TO / ARIA_SMTP_USER absents)"

    # 3. Rendu + envoi.
    html_body = render_html_report(
        result,
        generated_at=generated_at,
        recipient=to,
        report_number=report_number,
        series_number=series_number,
        capital_usd=capital_usd,
        tier=tier,
    )
    subject = email_subject(result, generated_at=generated_at, report_number=report_number)
    text_body = _plain_fallback(result)

    ok, error = await send_email(to=to, subject=subject, html_body=html_body, text_body=text_body)
    if ok:
        logger.info("send_vc_report: rapport envoyé à %s", to)
    return ok, error
