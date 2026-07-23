"""Minimal SMTP client — sends outgoing emails (VC reports).

Low-level transport only: builds a MIME multipart message (text + HTML) and
sends it via SMTP+STARTTLS (or implicit SSL on port 465). No business logic
here — report rendering lives in `skills/vc_report.py`, orchestration
(kill-switch + recipient) in `skills/vc_delivery.py`.

## Security (secret guard rails + dome)

- The password (Gmail App Password) is only read from the environment
  (`ARIA_SMTP_APP_PASSWORD`) — never hardcoded, never committed. It never
  appears in ANY log: only host/port/user/recipient/subject are logged.
- TLS with certificate verification enabled (`ssl.create_default_context()`).
  No downgrade, no `check_hostname=False`.
- `send_email` never raises to the caller: it returns `(ok, error)`.
  The error message is cleaned to never contain the secret.
- `smtplib` is synchronous → run in a thread via `asyncio.to_thread`,
  without blocking the host's asyncio loop.
"""
from __future__ import annotations

import asyncio
import logging
import os
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import smtplib

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 20.0


@dataclass(frozen=True)
class MailerConfig:
    host: str
    port: int
    user: str
    app_password: str
    sender: str

    @property
    def configured(self) -> bool:
        return bool(self.host and self.port and self.user and self.app_password)


def mailer_config_from_env(env: dict[str, str] | None = None) -> MailerConfig | None:
    """Builds the SMTP config from the environment. ``None`` if incomplete.

    No default value for the secret: if `ARIA_SMTP_APP_PASSWORD` is missing,
    email is simply disabled (safe degradation), never guessed.
    """
    src = env if env is not None else os.environ
    host = (src.get("ARIA_SMTP_HOST") or "smtp.gmail.com").strip()
    user = (src.get("ARIA_SMTP_USER") or "").strip()
    password = (src.get("ARIA_SMTP_APP_PASSWORD") or "").strip()
    sender = (src.get("ARIA_SMTP_SENDER") or user).strip()
    try:
        port = int((src.get("ARIA_SMTP_PORT") or "587").strip())
    except (TypeError, ValueError):
        port = 587

    if not (user and password):
        return None
    return MailerConfig(host=host, port=port, user=user, app_password=password, sender=sender)


def _redact(text: str, secret: str) -> str:
    """Safety net: strips any accidental occurrence of the secret from a message."""
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


def _send_sync(config: MailerConfig, msg: EmailMessage) -> None:
    context = ssl.create_default_context()  # certificate + hostname verification enabled
    if config.port == 465:
        with smtplib.SMTP_SSL(config.host, config.port, context=context, timeout=_DEFAULT_TIMEOUT) as server:
            server.login(config.user, config.app_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(config.host, config.port, timeout=_DEFAULT_TIMEOUT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(config.user, config.app_password)
            server.send_message(msg)


async def send_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    config: MailerConfig | None = None,
    attachment: bytes | None = None,
    attachment_filename: str | None = None,
    attachment_maintype: str = "application",
    attachment_subtype: str = "pdf",
) -> tuple[bool, str | None]:
    """Sends an HTML email (with text fallback), optional attachment.
    Returns ``(ok, error)``, never raises."""
    cfg = config or mailer_config_from_env()
    if cfg is None or not cfg.configured:
        return False, "SMTP non configuré (ARIA_SMTP_USER / ARIA_SMTP_APP_PASSWORD absents)"

    if not to:
        return False, "destinataire manquant"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = to
    msg.set_content(text_body or "Votre client mail n'affiche pas le HTML. Rapport en pièce HTML.")
    msg.add_alternative(html_body, subtype="html")
    if attachment:
        msg.add_attachment(
            attachment,
            maintype=attachment_maintype,
            subtype=attachment_subtype,
            filename=attachment_filename or "rapport.pdf",
        )

    logger.info(
        "mailer: sending email host=%s port=%s user=%s to=%s subject=%r",
        cfg.host,
        cfg.port,
        cfg.user,
        to,
        subject[:80],
    )

    try:
        await asyncio.to_thread(_send_sync, cfg, msg)
    except smtplib.SMTPAuthenticationError:
        # Never include a raw exception that could leak credentials.
        logger.error("mailer: SMTP authentication refused (check ARIA_SMTP_APP_PASSWORD)")
        return False, "authentification SMTP refusée"
    except Exception as exc:  # noqa: BLE001 — never blocking, never leaks the secret
        detail = _redact(str(exc), cfg.app_password)
        logger.error("mailer: send failed (%s)", detail)
        return False, f"échec envoi email : {detail}"

    return True, None
