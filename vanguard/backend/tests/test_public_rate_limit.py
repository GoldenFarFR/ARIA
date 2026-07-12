"""#22 — filet applicatif anti-scraping pour les endpoints publics /api/ (visiteurs
anonymes). Complète le pare-feu edge Cloudflare (hors de portée de ce dépôt, cf.
docs/edge-firewall-cloudflare.md) plutôt que de le remplacer.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.access_code import init_auth_db
from app.auth.rate_limit import reset_rate_limit
from app.config import settings
from app.database import init_db
from app.main import app


def _client_for(ip: str) -> AsyncClient:
    transport = ASGITransport(app=app, client=(ip, 12345))
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _tight_budget(monkeypatch):
    """Budget resserré pour des tests rapides et déterministes."""
    monkeypatch.setattr(settings, "public_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "public_rate_limit_attempts", 3)
    monkeypatch.setattr(settings, "public_rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "access_code_enabled", False)
    yield


@pytest.mark.asyncio
async def test_public_endpoint_blocked_after_budget_exhausted():
    ip = "203.0.113.10"
    reset_rate_limit(f"public_api_ip:{ip}")
    async with _client_for(ip) as client:
        for _ in range(3):
            res = await client.get("/api/health")
            assert res.status_code == 200
        res = await client.get("/api/health")
        assert res.status_code == 429
    reset_rate_limit(f"public_api_ip:{ip}")


@pytest.mark.asyncio
async def test_budget_is_shared_across_distinct_public_endpoints():
    """Le plafond est un budget PARTAGÉ par IP sur l'ensemble des endpoints publics --
    un bot qui varie les routes pour éviter un plafond par-endpoint ne gagne rien."""
    ip = "203.0.113.11"
    reset_rate_limit(f"public_api_ip:{ip}")
    async with _client_for(ip) as client:
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/api/pulse")).status_code == 200
        assert (await client.get("/api/aria/content/faq")).status_code == 200
        res = await client.get("/api/health")
        assert res.status_code == 429
    reset_rate_limit(f"public_api_ip:{ip}")


@pytest.mark.asyncio
async def test_distinct_ips_have_independent_budgets():
    ip_a, ip_b = "203.0.113.20", "203.0.113.21"
    reset_rate_limit(f"public_api_ip:{ip_a}")
    reset_rate_limit(f"public_api_ip:{ip_b}")
    async with _client_for(ip_a) as client_a:
        for _ in range(3):
            assert (await client_a.get("/api/health")).status_code == 200
        assert (await client_a.get("/api/health")).status_code == 429
    async with _client_for(ip_b) as client_b:
        assert (await client_b.get("/api/health")).status_code == 200
    reset_rate_limit(f"public_api_ip:{ip_a}")
    reset_rate_limit(f"public_api_ip:{ip_b}")


@pytest.mark.asyncio
async def test_chat_endpoint_exempt_from_public_rate_limit(monkeypatch):
    """Consigne opérateur explicite (#22) : ne rien ajouter de plus sur le chat --
    il a déjà son propre limiteur (par visiteur + par IP)."""
    from aria_core.brain import aria_brain

    async def _fake_process(*_a, **_k):
        return {"reply": "stub", "meta": {}}

    monkeypatch.setattr(aria_brain, "process", _fake_process)

    ip = "203.0.113.30"
    reset_rate_limit(f"public_api_ip:{ip}")
    async with _client_for(ip) as client:
        for _ in range(3):
            assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/api/health")).status_code == 429
        # Budget /api/ générique épuisé, mais le chat n'y puise pas -- toujours accessible
        # (jusqu'à SON propre plafond, testé ailleurs dans test_aria_* / test_rate_limit).
        res = await client.post(
            "/api/aria/chat",
            json={"message": "salut", "visitor_id": "visitor-test-2345678"},
        )
        assert res.status_code != 429
    reset_rate_limit(f"public_api_ip:{ip}")


@pytest.mark.asyncio
async def test_member_only_route_untouched_by_public_limiter(tmp_path, monkeypatch):
    """Une route membre (gate Privy) n'est pas la cible de ce filet -- elle continue de
    répondre 401 (pas 429) hors session, quel que soit le volume de requêtes publiques."""
    auth_db = tmp_path / "auth.db"
    dexpulse_db = tmp_path / "dexpulse.db"
    monkeypatch.setattr("app.auth.access_code._DB_FILE", auth_db)
    monkeypatch.setattr("app.auth.access_code.DB_PATH", str(auth_db))
    monkeypatch.setattr("app.database.DB_PATH", str(dexpulse_db))
    monkeypatch.setattr(settings, "access_code_enabled", True)
    monkeypatch.setattr(settings, "aria_public_mode", False)
    await init_auth_db()
    await init_db()

    ip = "203.0.113.40"
    reset_rate_limit(f"public_api_ip:{ip}")
    async with _client_for(ip) as client:
        for _ in range(5):
            res = await client.get("/api/watchlist")
            assert res.status_code == 401
    reset_rate_limit(f"public_api_ip:{ip}")


@pytest.mark.asyncio
async def test_disabled_flag_lets_requests_through(monkeypatch):
    monkeypatch.setattr(settings, "public_rate_limit_enabled", False)
    ip = "203.0.113.50"
    reset_rate_limit(f"public_api_ip:{ip}")
    async with _client_for(ip) as client:
        for _ in range(5):
            assert (await client.get("/api/health")).status_code == 200
    reset_rate_limit(f"public_api_ip:{ip}")


@pytest.mark.asyncio
async def test_no_client_ip_never_blocks():
    """Sans IP déterminable (pas de proxy-headers), on ne bloque jamais à l'aveugle --
    même doctrine que check_rate_limit ailleurs dans le code (cf. client_ip docstring)."""
    transport = ASGITransport(app=app)  # défaut = ('127.0.0.1', 123) -> client_ip() -> None
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            assert (await client.get("/api/health")).status_code == 200
