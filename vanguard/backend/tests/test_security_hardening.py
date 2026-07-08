"""Garde-fous SÉCURITÉ — verrouillent les correctifs d'auth de la session.

Ces tests CODIFIENT des invariants : si un refactor rouvre une des failles fermées ici,
la CI casse. Tout est hors-ligne (ASGI in-process), aucun secret réel, aucun réseau.

Failles fermées :
  1. Webhook Telegram fail-CLOSED (secret absent -> 503 ; mauvais secret -> 403).
  2. Secret opérateur : header seul, JAMAIS en query-string (fuite logs/historique).
  3. Usurpation via `handle` : un handle opérateur revendiqué sans le secret admin est
     neutralisé (pas de privilège, pas de publication X autonome).
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import init_db
from app.main import app


@pytest.fixture(autouse=True)
def _reset_totp_throttle():
    """L'état anti-force-brute est au niveau module : on le remet à zéro entre chaque test."""
    import aria_core.public_mode as pm

    pm._TOTP_FAILS.clear()
    yield
    pm._TOTP_FAILS.clear()


@pytest.fixture
async def client(tmp_path, monkeypatch):
    dexpulse_db = tmp_path / "dexpulse.db"
    monkeypatch.setattr("app.database.DB_PATH", str(dexpulse_db))
    # Mode public, gate d'accès désactivée : on isole la logique d'auth des routes.
    monkeypatch.setattr(settings, "access_code_enabled", False)
    monkeypatch.setattr(settings, "aria_public_mode", True)
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── 1. Webhook Telegram fail-closed ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_telegram_webhook_rejects_when_secret_unset(client, monkeypatch):
    from app.api.routes import telegram_route

    monkeypatch.setattr(telegram_route.telegram_bot, "is_running", lambda: True)
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")
    res = await client.post("/api/telegram/webhook", json={"update_id": 1})
    assert res.status_code == 503  # fail-closed : pas de secret => on refuse


@pytest.mark.asyncio
async def test_telegram_webhook_rejects_wrong_secret(client, monkeypatch):
    from app.api.routes import telegram_route

    monkeypatch.setattr(telegram_route.telegram_bot, "is_running", lambda: True)
    monkeypatch.setattr(settings, "telegram_webhook_secret", "goodsecret")
    res = await client.post(
        "/api/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "WRONG"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_telegram_webhook_accepts_correct_secret(client, monkeypatch):
    from app.api.routes import telegram_route

    monkeypatch.setattr(telegram_route.telegram_bot, "is_running", lambda: True)

    async def noop(_payload):
        return None

    monkeypatch.setattr(telegram_route.telegram_bot, "process_webhook_update", noop)
    monkeypatch.setattr(settings, "telegram_webhook_secret", "goodsecret")
    res = await client.post(
        "/api/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "goodsecret"},
    )
    assert res.status_code == 200


# ── 2. Secret opérateur : header seul, jamais en query-string ─────────────────

@pytest.mark.asyncio
async def test_operator_secret_in_query_string_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    # ?secret= ne doit PLUS authentifier (fuite dans logs/historique/Referer).
    res = await client.get("/api/aria/directives?secret=s3cr3t")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_operator_secret_in_header_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)  # 2FA OFF => secret seul
    res = await client.get(
        "/api/aria/directives",
        headers={"X-Admin-Secret": "s3cr3t"},
    )
    assert res.status_code == 200


# ── 2bis. 2FA opérateur (TOTP) — opt-in via ADMIN_TOTP_SECRET ──────────────────

@pytest.mark.asyncio
async def test_operator_2fa_requires_totp_when_enabled(client, monkeypatch):
    """Avec ADMIN_TOTP_SECRET défini, le secret admin SEUL ne suffit plus."""
    from aria_core.admin_totp import generate_secret

    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.setenv("ADMIN_TOTP_SECRET", generate_secret())
    res = await client.get("/api/aria/directives", headers={"X-Admin-Secret": "s3cr3t"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_operator_2fa_accepts_secret_plus_valid_code(client, monkeypatch):
    """Secret admin + code TOTP valide => accès opérateur ; code faux => refus."""
    from aria_core.admin_totp import generate_secret, totp_code

    totp_secret = generate_secret()
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.setenv("ADMIN_TOTP_SECRET", totp_secret)

    ok = await client.get(
        "/api/aria/directives",
        headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": totp_code(totp_secret)},
    )
    assert ok.status_code == 200

    bad = await client.get(
        "/api/aria/directives",
        headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": "000000"},
    )
    assert bad.status_code == 403


@pytest.mark.asyncio
async def test_operator_2fa_bruteforce_lockout(client, monkeypatch):
    """Au-delà du seuil d'échecs TOTP, l'IP est verrouillée — même un code correct est refusé."""
    from aria_core.admin_totp import generate_secret, totp_code
    import aria_core.public_mode as pm

    totp_secret = generate_secret()
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.setenv("ADMIN_TOTP_SECRET", totp_secret)

    for _ in range(pm._TOTP_MAX_FAILS):
        r = await client.get(
            "/api/aria/directives",
            headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": "000000"},
        )
        assert r.status_code == 403

    # IP verrouillée : un code VALIDE est désormais refusé le temps de la fenêtre.
    locked = await client.get(
        "/api/aria/directives",
        headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": totp_code(totp_secret)},
    )
    assert locked.status_code == 403


# ── 3. Anti-usurpation via `handle` sur le endpoint public ────────────────────

@pytest.mark.asyncio
async def test_community_feedback_operator_handle_neutralized_when_anonymous(client, monkeypatch):
    """Handle opérateur revendiqué SANS secret admin => transmis comme handle vide."""
    import aria_core.community_feedback as mod

    seen = {}

    async def capture(text, *, handle="", **kwargs):
        seen["handle"] = handle
        return {"ok": True, "verdict": "noted", "queued": False, "reply": "ok"}

    monkeypatch.setattr(mod, "submit_community_feedback", capture)
    res = await client.post(
        "/api/aria/community-feedback",
        json={"message": "great vanguard build here", "handle": "GoldenFarFR", "lang": "en"},
        headers={"X-Visitor-Id": "visitor-anon-12345678"},
    )
    assert res.status_code == 200
    assert seen["handle"] == ""  # usurpation neutralisée


@pytest.mark.asyncio
async def test_community_feedback_operator_handle_kept_with_admin_secret(client, monkeypatch):
    """Avec le secret admin, le handle opérateur est légitimement conservé."""
    import aria_core.community_feedback as mod

    seen = {}

    async def capture(text, *, handle="", **kwargs):
        seen["handle"] = handle
        return {"ok": True, "verdict": "noted", "queued": False, "reply": "ok"}

    monkeypatch.setattr(mod, "submit_community_feedback", capture)
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    res = await client.post(
        "/api/aria/community-feedback",
        json={"message": "great vanguard build here", "handle": "GoldenFarFR", "lang": "en"},
        headers={"X-Visitor-Id": "visitor-op-12345678", "X-Admin-Secret": "s3cr3t"},
    )
    assert res.status_code == 200
    assert seen["handle"] == "GoldenFarFR"
