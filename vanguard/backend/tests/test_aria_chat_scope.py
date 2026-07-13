"""#chat-scope-site : le widget site (/aria/chat) doit rester sur Vanguard/ARIA/ZHC/BASE --
une question d'actu générale (is_live_info_question) sans mot-clé du périmètre reçoit un
recadrage immédiat, sans jamais appeler le cerveau général (donc sans recherche web).

Le chat public Telegram (même brain.process, route distincte -- gateway/telegram_bot.py)
n'est PAS concerné : ce filtre vit uniquement dans app/api/routes/aria.py.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.rate_limit import reset_rate_limit
from app.main import app


def _client_for(ip: str = "203.0.113.99") -> AsyncClient:
    transport = ASGITransport(app=app, client=(ip, 12345))
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_general_news_question_is_redirected_without_calling_brain(monkeypatch):
    from aria_core.brain import aria_brain
    from aria_core import repertoire_db

    called = False

    async def _fake_process(*_a, **_k):
        nonlocal called
        called = True
        return {"reply": "stub"}

    async def _fake_save_message(*_a, **_k):
        return "stub-id"

    monkeypatch.setattr(aria_brain, "process", _fake_process)
    monkeypatch.setattr(repertoire_db, "save_message", _fake_save_message)

    ip = "203.0.113.99"
    reset_rate_limit(f"aria_chat:visitor-scope-test-1")
    reset_rate_limit(f"aria_chat_ip:{ip}")
    async with _client_for(ip) as client:
        res = await client.post(
            "/api/aria/chat",
            json={
                "message": "à quelle heure joue le PSG ce soir ?",
                "visitor_id": "visitor-scope-test-1",
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert data["data"]["scope_redirect"] is True
    assert data["data"]["skip_web"] is True
    assert not called
    reset_rate_limit(f"aria_chat:visitor-scope-test-1")
    reset_rate_limit(f"aria_chat_ip:{ip}")


@pytest.mark.asyncio
async def test_news_question_with_scope_keyword_still_reaches_brain(monkeypatch):
    from aria_core.brain import aria_brain
    from aria_core.models import ChatResponse

    called = False

    async def _fake_process(*_a, **_k):
        nonlocal called
        called = True
        return ChatResponse(reply="stub", actions_taken=[])

    monkeypatch.setattr(aria_brain, "process", _fake_process)

    ip = "203.0.113.98"
    reset_rate_limit("aria_chat:visitor-scope-test-2")
    reset_rate_limit(f"aria_chat_ip:{ip}")
    async with _client_for(ip) as client:
        res = await client.post(
            "/api/aria/chat",
            json={
                "message": "quel est le cours du token ARIA aujourd'hui ?",
                "visitor_id": "visitor-scope-test-2",
            },
        )

    assert res.status_code == 200
    assert called
    reset_rate_limit("aria_chat:visitor-scope-test-2")
    reset_rate_limit(f"aria_chat_ip:{ip}")


@pytest.mark.asyncio
async def test_non_news_question_reaches_brain_unfiltered(monkeypatch):
    from aria_core.brain import aria_brain
    from aria_core.models import ChatResponse

    called = False

    async def _fake_process(*_a, **_k):
        nonlocal called
        called = True
        return ChatResponse(reply="stub", actions_taken=[])

    monkeypatch.setattr(aria_brain, "process", _fake_process)

    ip = "203.0.113.97"
    reset_rate_limit("aria_chat:visitor-scope-test-3")
    reset_rate_limit(f"aria_chat_ip:{ip}")
    async with _client_for(ip) as client:
        res = await client.post(
            "/api/aria/chat",
            json={
                "message": "explique-moi la structure du holding",
                "visitor_id": "visitor-scope-test-3",
            },
        )

    assert res.status_code == 200
    assert called
    reset_rate_limit("aria_chat:visitor-scope-test-3")
    reset_rate_limit(f"aria_chat_ip:{ip}")


def test_scope_filter_helper_unit():
    from app.api.routes.aria import _is_out_of_scope_live_info

    assert _is_out_of_scope_live_info("à quelle heure joue le PSG ce soir ?")
    assert _is_out_of_scope_live_info("quel est le prix du bitcoin ?")
    assert not _is_out_of_scope_live_info("quel est le cours du token ARIA aujourd'hui ?")
    assert not _is_out_of_scope_live_info("quel est le prix du launchpad BASE ?")
    assert not _is_out_of_scope_live_info("explique-moi la méthodologie track record de ZHC")
    assert not _is_out_of_scope_live_info("comment vas-tu aujourd'hui ?")
    assert not _is_out_of_scope_live_info("explique-moi la structure du holding Vanguard")
