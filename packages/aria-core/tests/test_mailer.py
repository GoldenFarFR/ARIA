"""Client SMTP — transport bas niveau. Aucun envoi réel : smtplib est mocké.

Vérifie : config depuis env (dégradation sûre si secret absent), non-fuite du
secret dans les logs/erreurs, TLS, et que send_email ne lève jamais.
"""
from __future__ import annotations

import smtplib

import pytest

from aria_core.services import mailer
from aria_core.services.mailer import MailerConfig, mailer_config_from_env, send_email


def _cfg() -> MailerConfig:
    return MailerConfig(
        host="smtp.gmail.com", port=587, user="agentaria.zhc@gmail.com",
        app_password="secretpw16chars", sender="agentaria.zhc@gmail.com",
    )


def test_config_from_env_complete():
    env = {
        "ARIA_SMTP_HOST": "smtp.gmail.com",
        "ARIA_SMTP_PORT": "587",
        "ARIA_SMTP_USER": "agentaria.zhc@gmail.com",
        "ARIA_SMTP_APP_PASSWORD": "abcd efgh ijkl mnop".replace(" ", ""),
    }
    cfg = mailer_config_from_env(env)
    assert cfg is not None
    assert cfg.configured is True
    assert cfg.sender == "agentaria.zhc@gmail.com"  # défaut = user


def test_config_from_env_missing_password_returns_none():
    env = {"ARIA_SMTP_USER": "agentaria.zhc@gmail.com"}  # pas de password
    assert mailer_config_from_env(env) is None


def test_config_from_env_bad_port_defaults_587():
    env = {
        "ARIA_SMTP_USER": "a@b.com",
        "ARIA_SMTP_APP_PASSWORD": "x",
        "ARIA_SMTP_PORT": "not-a-number",
    }
    cfg = mailer_config_from_env(env)
    assert cfg is not None
    assert cfg.port == 587


@pytest.mark.asyncio
async def test_send_email_unconfigured_is_safe():
    ok, error = await send_email(
        to="x@y.com", subject="s", html_body="<b>h</b>",
        config=MailerConfig(host="", port=0, user="", app_password="", sender=""),
    )
    assert ok is False
    assert "non configuré" in error


@pytest.mark.asyncio
async def test_send_email_success_starttls(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host
            captured["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def ehlo(self):
            captured["ehlo"] = captured.get("ehlo", 0) + 1

        def starttls(self, context=None):
            captured["starttls"] = context is not None

        def login(self, user, password):
            captured["login"] = (user, password)

        def send_message(self, msg):
            captured["sent"] = msg

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    ok, error = await send_email(
        to="dest@gmail.com", subject="Analyse VC", html_body="<h1>ok</h1>",
        text_body="ok", config=_cfg(),
    )

    assert ok is True
    assert error is None
    assert captured["starttls"] is True  # TLS activé
    assert captured["login"][0] == "agentaria.zhc@gmail.com"
    assert captured["sent"]["To"] == "dest@gmail.com"
    assert captured["sent"]["Subject"] == "Analyse VC"


@pytest.mark.asyncio
async def test_send_email_uses_ssl_on_465(monkeypatch):
    captured = {}

    class FakeSMTPSSL:
        def __init__(self, host, port, context=None, timeout=None):
            captured["ssl"] = True
            captured["context"] = context is not None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def login(self, user, password):
            pass

        def send_message(self, msg):
            captured["sent"] = True

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTPSSL)
    cfg = MailerConfig(host="smtp.gmail.com", port=465, user="u@g.com", app_password="p", sender="u@g.com")

    ok, _ = await send_email(to="d@g.com", subject="s", html_body="<b>h</b>", config=cfg)

    assert ok is True
    assert captured["ssl"] is True
    assert captured["context"] is True  # SSL context présent


@pytest.mark.asyncio
async def test_send_email_auth_error_no_secret_leak(monkeypatch):
    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad creds")

        def send_message(self, msg):
            pass

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    ok, error = await send_email(to="d@g.com", subject="s", html_body="<b>h</b>", config=_cfg())

    assert ok is False
    assert "secretpw16chars" not in error  # le secret ne fuite jamais
    assert "authentification" in error.lower()


@pytest.mark.asyncio
async def test_send_email_generic_error_redacts_secret(monkeypatch):
    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            # Exception dont le message contiendrait par accident le secret.
            raise RuntimeError(f"connexion échouée avec {password}")

        def send_message(self, msg):
            pass

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    ok, error = await send_email(to="d@g.com", subject="s", html_body="<b>h</b>", config=_cfg())

    assert ok is False
    assert "secretpw16chars" not in error  # redaction du secret
    assert "***" in error
