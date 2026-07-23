"""Delivering the VC report by email — orchestration (Step C — sending).

Connects the rendering (`vc_report`, `vc_report_pdf`) to SMTP transport
(`services/mailer`), under guard-rails:

- **Fail-closed kill-switch**: if ARIA is paused (or the pause state is
  unreadable), no email goes out (`outgoing_pause.is_paused(strict=True)`).
- **Safe degradation**: SMTP not configured -> no send, clear message, never
  a blocking error.
- **Recipient**: `ARIA_VC_REPORT_TO` (default `ARIA_SMTP_USER`) — ARIA's
  own inbox.

The email body is a SHORT TEASER (badges + R/R, no thesis or detailed
analysis) — the full analysis lives exclusively in the **attached secured
PDF** (anti-copy/extraction permissions + traceable named watermark).
Without this separation, the anti-copy PDF would be pointless: the same
content would be copyable directly from the email body.

This module never executes a trade and never touches the wallet: it only
sends an analysis document.
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
    """Renders the report (secured PDF) and sends it by email. Returns
    ``(ok, error)``, never raises.

    Guard-rails applied in order: kill-switch -> recipient -> rendering ->
    SMTP send. ``report_number`` (optional) lets a subscriber distinguish
    several analyses tracked on the same token ("Report #2"). ``series_number``
    (optional) displays the global series number ("Series 00.047").
    ``capital_usd`` (optional) lets the position be shown in dollars
    (suggested size x client capital) — same amount as used on the Telegram
    side. ``tier`` ("premium" by default, or "standard") selects the report
    edition. ``lang`` ("fr" by default, or "en") chooses the language of the
    report's fixed labels (the LLM prose is already generated in that
    language upstream, see ``vc_analysis.analyze_vc(lang=...)``).
    """
    lang = norm_lang(lang)

    # 1. Fail-closed kill-switch: paused (or unreadable state) -> nothing is sent.
    if outgoing_pause.is_paused(strict=True):
        logger.info("send_vc_report: ARIA paused (or unreadable state) — email not sent (fail-closed)")
        return False, "ARIA en pause — email suspendu (kill-switch)"

    # 2. Recipient.
    to = _recipient()
    if not to:
        return False, "destinataire non configuré (ARIA_VC_REPORT_TO / ARIA_SMTP_USER absents)"

    # 3. Rendering: full (secured) PDF + short email teaser (never the detailed content).
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
    # Disposable OWNER password: only used to lock down permissions
    # (anti-copy), never stored or required to OPEN the document (empty USER
    # password — see the warning in vc_report_pdf: a deterrent, not tamper-proof).
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
        logger.info("send_vc_report: report (secured PDF) sent to %s", to)
    return ok, error
