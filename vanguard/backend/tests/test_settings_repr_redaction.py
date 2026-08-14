"""Real incident (14/08): a test that made Settings() raise an AttributeError caused
pytest to print the FULL repr of the global `settings` instance in its traceback --
which loads the REAL prod .env, so every real secret (admin_api_secret,
deploy_activation_secret, telegram_bot_token, all LLM/X/GitHub keys...) leaked into
the session's visible output. Root cause: pydantic's default __repr__ shows every
field's raw value, and `settings` is a module-level singleton built from the real
.env -- any future test/exception touching it repeats the leak. Fix: Settings never
shows secret-shaped field values in repr/str, regardless of what triggers the repr."""
from __future__ import annotations

from app.config import Settings


def test_settings_repr_never_leaks_secret_shaped_field_values(monkeypatch):
    monkeypatch.setenv("ADMIN_API_SECRET", "totally-fake-admin-secret-xyz")
    monkeypatch.setenv("DEPLOY_ACTIVATION_SECRET", "totally-fake-deploy-secret-xyz")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "totally-fake-bot-token-xyz")
    monkeypatch.setenv("GITHUB_TOKEN", "totally-fake-github-token-xyz")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "totally-fake-anthropic-key-xyz")
    s = Settings()
    r = repr(s)
    st = str(s)
    for leaked_value in (
        "totally-fake-admin-secret-xyz",
        "totally-fake-deploy-secret-xyz",
        "totally-fake-bot-token-xyz",
        "totally-fake-github-token-xyz",
        "totally-fake-anthropic-key-xyz",
    ):
        assert leaked_value not in r
        assert leaked_value not in st


def test_settings_repr_still_shows_non_sensitive_fields(monkeypatch):
    """Redaction must not turn the whole object into a black box -- ordinary
    debugging (e.g. checking `debug`/`port`/`app_name`) must keep working."""
    s = Settings()
    r = repr(s)
    assert f"app_name={s.app_name!r}" in r
    assert f"port={s.port!r}" in r


def test_settings_repr_redacts_field_regardless_of_value_content(monkeypatch):
    """The redaction must key off the FIELD NAME (key/secret/token/password),
    never sniff the value itself -- an empty secret must still show as redacted,
    never as an empty string that could be mistaken for "field not sensitive"."""
    monkeypatch.setenv("ADMIN_API_SECRET", "")
    s = Settings()
    r = repr(s)
    assert "admin_api_secret=''" not in r
