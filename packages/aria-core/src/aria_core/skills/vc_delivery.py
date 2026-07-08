"""Livraison du rapport VC par email — orchestration (Étape C — envoi).

Relie le rendu (`vc_report`, `vc_report_pdf`) au transport SMTP (`services/mailer`),
sous garde-fous :

- **Kill-switch fail-closed** : si ARIA est en pause (ou l'état de pause est
  illisible), aucun email ne part (`outgoing_pause.is_paused(strict=True)`).
- **Dégradation sûre** : SMTP non configuré → pas d'envoi, message clair, jamais
  d'erreur bloquante.
- **Destinataire** : `ARIA_VC_REPORT_TO` (défaut `ARIA_SMTP_USER`) — la boîte
  d'ARIA.

Le corps de l'email est un TEASER COURT (badges + R/R, aucune thèse ni analyse
détaillée) — l'analyse complète vit exclusivement dans le **PDF sécurisé joint**
(permissions anti-copie/extraction + filigrane nominatif traçable). Sans cette
séparation, le PDF anti-copie n'aurait aucun sens : le même contenu serait
copiable directement depuis le corps de l'email.

Ce module n'exécute jamais de trade et ne touche jamais au wallet : il ne fait
qu'envoyer un document d'analyse.
"""
from __future__ import annotations

import logging
import os
import secrets

from aria_core import outgoing_pause
from aria_core.services.mailer import send_email
from aria_core.skills.vc_analysis import VCResult
from aria_core.skills.vc_i18n import norm_lang
from aria_core.skills.vc_report import email_subject, email_teaser_text, render_email_teaser_html
from aria_core.skills.vc_report_pdf import render_pdf_report, secure_pdf_bytes

logger = logging.getLogger(__name__)


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
    lang: str = "fr",
) -> tuple[bool, str | None]:
    """Rend le rapport (PDF sécurisé) et l'envoie par email. Retourne ``(ok, error)``,
    ne lève jamais.

    Garde-fous appliqués dans l'ordre : kill-switch → destinataire → rendu → envoi SMTP.
    ``report_number`` (optionnel) permet à un abonné de distinguer plusieurs
    analyses suivies du même token (« Rapport n°2 »). ``series_number``
    (optionnel) affiche le numéro de série global (« Série 00.047 »).
    ``capital_usd`` (optionnel) permet d'afficher la position en dollars (taille
    suggérée x capital du client) — même montant que celui utilisé côté Telegram.
    ``tier`` (« premium » par défaut, ou « standard ») sélectionne l'édition du
    rapport. ``lang`` (« fr » par défaut, ou « en ») choisit la langue des
    libellés fixes du rapport (la prose LLM est déjà générée dans cette langue
    en amont, cf. ``vc_analysis.analyze_vc(lang=...)``).
    """
    lang = norm_lang(lang)

    # 1. Kill-switch fail-closed : en pause (ou état illisible) → on n'envoie rien.
    if outgoing_pause.is_paused(strict=True):
        logger.info("send_vc_report: ARIA en pause (ou état illisible) — email non envoyé (fail-closed)")
        return False, "ARIA en pause — email suspendu (kill-switch)"

    # 2. Destinataire.
    to = _recipient()
    if not to:
        return False, "destinataire non configuré (ARIA_VC_REPORT_TO / ARIA_SMTP_USER absents)"

    # 3. Rendu : PDF complet (secured) + teaser email court (jamais le contenu détaillé).
    pdf_raw = render_pdf_report(
        result,
        generated_at=generated_at,
        recipient=to,
        report_number=report_number,
        series_number=series_number,
        capital_usd=capital_usd,
        tier=tier,
        lang=lang,
    )
    # Mot de passe PROPRIÉTAIRE jetable : sert uniquement à figer les permissions
    # (anti-copie), jamais stocké ni requis pour OUVRIR le document (mot de passe
    # UTILISATEUR vide — cf. avertissement dans vc_report_pdf : dissuasif, pas inviolable).
    owner_password = secrets.token_urlsafe(24)
    pdf_secured = secure_pdf_bytes(pdf_raw, owner_password=owner_password)

    html_body = render_email_teaser_html(result, lang=lang)
    text_body = email_teaser_text(result, lang=lang)
    subject = email_subject(result, generated_at=generated_at, report_number=report_number, lang=lang)
    filename = f"ARIA-{(result.symbol or result.contract[:10]).strip('.')}-{result.recommandation}.pdf"

    ok, error = await send_email(
        to=to,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        attachment=pdf_secured,
        attachment_filename=filename,
    )
    if ok:
        logger.info("send_vc_report: rapport (PDF sécurisé) envoyé à %s", to)
    return ok, error
