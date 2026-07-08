"""Client SMTP minimal — envoi d'emails sortants (rapports VC).

Transport bas niveau uniquement : construit un MIME multipart (texte + HTML) et
l'envoie via SMTP+STARTTLS (ou SSL implicite sur le port 465). Aucune logique
métier ici — le rendu du rapport vit dans `skills/vc_report.py`, l'orchestration
(kill-switch + destinataire) dans `skills/vc_delivery.py`.

## Sécurité (garde-fous secrets + dôme)

- Le mot de passe (App Password Gmail) n'est lu que depuis l'environnement
  (`ARIA_SMTP_APP_PASSWORD`) — jamais en dur, jamais commité. Il n'apparaît
  dans AUCUN log : on ne journalise que host/port/user/destinataire/sujet.
- TLS avec vérification de certificat activée (`ssl.create_default_context()`).
  Aucun downgrade, aucun `check_hostname=False`.
- `send_email` ne lève jamais vers l'appelant : elle retourne `(ok, error)`.
  Le message d'erreur est nettoyé pour ne jamais contenir le secret.
- `smtplib` est synchrone → exécuté dans un thread via `asyncio.to_thread`,
  sans bloquer la boucle asyncio de l'hôte.
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
    """Construit la config SMTP depuis l'environnement. ``None`` si incomplète.

    Aucune valeur par défaut pour le secret : si `ARIA_SMTP_APP_PASSWORD` est
    absent, l'email est simplement désactivé (dégradation sûre), jamais deviné.
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
    """Filet de sécurité : retire toute occurrence accidentelle du secret d'un message."""
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


def _send_sync(config: MailerConfig, msg: EmailMessage) -> None:
    context = ssl.create_default_context()  # vérif certificat + hostname activées
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
    """Envoie un email HTML (avec fallback texte), pièce jointe optionnelle.
    Retourne ``(ok, error)``, ne lève jamais."""
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
        "mailer: envoi email host=%s port=%s user=%s to=%s subject=%r",
        cfg.host,
        cfg.port,
        cfg.user,
        to,
        subject[:80],
    )

    try:
        await asyncio.to_thread(_send_sync, cfg, msg)
    except smtplib.SMTPAuthenticationError:
        # Ne jamais inclure d'exception brute qui pourrait échoguer des identifiants.
        logger.error("mailer: authentification SMTP refusée (vérifier ARIA_SMTP_APP_PASSWORD)")
        return False, "authentification SMTP refusée"
    except Exception as exc:  # noqa: BLE001 — jamais bloquant, jamais de secret propagé
        detail = _redact(str(exc), cfg.app_password)
        logger.error("mailer: échec envoi (%s)", detail)
        return False, f"échec envoi email : {detail}"

    return True, None
