import pytest

from aria_core import operator_readiness
from aria_core.knowledge.web_verify import is_operator_local_question, should_use_web_verify
from aria_core.operator_readiness import (
    parse_readiness_goal,
    wants_operator_go_ahead,
    wants_operator_readiness,
    wants_operator_status_pulse,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Simule httpx.AsyncClient utilisé par _probe_local_health -- pas d'appel réseau réel."""

    def __init__(self, *, response=None, raises=None):
        self._response = response
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        if self._raises:
            raise self._raises
        return self._response


def _patch_httpx(monkeypatch, *, response=None, raises=None):
    monkeypatch.setattr(
        operator_readiness.httpx, "AsyncClient",
        lambda **kw: _FakeAsyncClient(response=response, raises=raises),
    )


def test_readiness_phrase_operator():
    msg = (
        "ok et maintenant tout est pret, qu'est-ce qu'il manque "
        "pour que tu puisses publier sur le site"
    )
    assert wants_operator_readiness(msg)
    assert "publier sur le site" in parse_readiness_goal(msg)


def test_go_ahead_benefique():
    assert wants_operator_go_ahead("si c'est benefique pour toi fait le")


def test_not_readiness_random():
    assert not wants_operator_readiness("comment va le marché crypto ?")


def test_not_readiness_ok_maintenant_unrelated_request():
    # Bug vécu (10/07) : "ok" + "maintenant" à moins de 40 caractères faisait
    # basculer une vraie demande de recherche de token sur l'audit de readiness.
    assert not wants_operator_readiness(
        "ok trouve moi maintenant un jeton qui repond a t'es critere BUY"
    )


def test_status_pulse_operator():
    assert wants_operator_status_pulse("rien de nouveau a declarer ?")
    assert wants_operator_status_pulse("quoi de neuf ?")
    assert not wants_operator_status_pulse("bitcoin aujourd'hui")


def test_operator_local_blocks_web(monkeypatch):
    monkeypatch.setenv("ARIA_PUBLIC_MODE", "false")
    from aria_core.runtime import settings

    settings.aria_public_mode = False
    assert is_operator_local_question("rien de nouveau a declarer ?")
    assert not should_use_web_verify("rien de nouveau a declarer ?")
    assert should_use_web_verify("rugby stade toulousain aujourd'hui")


@pytest.mark.asyncio
async def test_collect_gaps_returns_structure():
    from aria_core.operator_readiness import collect_readiness_gaps

    gaps, ok = await collect_readiness_gaps()
    assert isinstance(gaps, list)
    assert isinstance(ok, list)


@pytest.mark.asyncio
async def test_status_pulse_human_format(monkeypatch, tmp_path):
    from aria_core.memory import collegue as collegue_mod
    from aria_core.operator_readiness import execute_operator_status_pulse

    journal = tmp_path / "JOURNAL.md"
    journal.write_text(
        "20h00 — ancien\n21h00 — milieu\n22h07 — fix pulse\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(collegue_mod, "_ops_memoire_candidates", lambda: [tmp_path])

    async def _fake_gaps(**_):
        return [], ["API locale :8000 OK"]

    monkeypatch.setattr(
        "aria_core.operator_readiness.collect_readiness_gaps",
        _fake_gaps,
    )

    reply, data = await execute_operator_status_pulse("rien de nouveau a declarer ?", lang="fr")
    assert "Rien à déclarer" in reply
    assert "22h07 — fix pulse" in reply
    assert "Structure corporale" not in reply


@pytest.mark.asyncio
async def test_probe_local_health_ok(monkeypatch):
    _patch_httpx(monkeypatch, response=_FakeResponse(200, {"status": "ok"}))
    ok, detail = await operator_readiness._probe_local_health()
    assert ok is True
    assert "OK" in detail


@pytest.mark.asyncio
async def test_probe_local_health_wrong_status_code(monkeypatch):
    _patch_httpx(monkeypatch, response=_FakeResponse(500, {"status": "ok"}))
    ok, detail = await operator_readiness._probe_local_health()
    assert ok is False
    assert "500" in detail


@pytest.mark.asyncio
async def test_probe_local_health_status_not_ok(monkeypatch):
    _patch_httpx(monkeypatch, response=_FakeResponse(200, {"status": "degraded"}))
    ok, _detail = await operator_readiness._probe_local_health()
    assert ok is False


@pytest.mark.asyncio
async def test_probe_local_health_unreachable(monkeypatch):
    _patch_httpx(monkeypatch, raises=ConnectionError("refused"))
    ok, detail = await operator_readiness._probe_local_health()
    assert ok is False
    assert "injoignable" in detail


@pytest.mark.asyncio
async def test_collect_gaps_flags_missing_llm_and_github_token(monkeypatch):
    monkeypatch.setattr(operator_readiness, "is_llm_configured", lambda: False)
    monkeypatch.setattr(operator_readiness.settings, "github_token", "")
    _patch_httpx(monkeypatch, response=_FakeResponse(200, {"status": "ok"}))

    gaps, ok_items = await operator_readiness.collect_readiness_gaps()
    gap_ids = {g["id"] for g in gaps}
    assert "llm_config" in gap_ids
    assert "github_token" in gap_ids
    assert any("API locale" in item for item in ok_items)


@pytest.mark.asyncio
async def test_collect_gaps_reports_llm_and_github_ok_when_configured(monkeypatch):
    monkeypatch.setattr(operator_readiness, "is_llm_configured", lambda: True)
    monkeypatch.setattr(operator_readiness.settings, "llm_provider", "groq")
    monkeypatch.setattr(operator_readiness.settings, "llm_model", "llama-3")
    monkeypatch.setattr(operator_readiness.settings, "github_token", "ghp_x")
    _patch_httpx(monkeypatch, response=_FakeResponse(200, {"status": "ok"}))

    gaps, ok_items = await operator_readiness.collect_readiness_gaps()
    assert not any(g["id"] in ("llm_config", "github_token") for g in gaps)
    assert any("groq" in item for item in ok_items)
    assert any("GitHub token" in item for item in ok_items)


@pytest.mark.asyncio
async def test_collect_gaps_flags_unreachable_local_health(monkeypatch):
    monkeypatch.setattr(operator_readiness, "is_llm_configured", lambda: True)
    monkeypatch.setattr(operator_readiness.settings, "github_token", "ghp_x")
    _patch_httpx(monkeypatch, raises=ConnectionError("refused"))

    gaps, _ok = await operator_readiness.collect_readiness_gaps()
    health_gap = next((g for g in gaps if g["id"] == "local_health"), None)
    assert health_gap is not None
    assert health_gap["capability_id"] == "health_render_regression"


@pytest.mark.asyncio
async def test_collect_gaps_x_banner_branch_missing_oauth(monkeypatch):
    monkeypatch.setattr(operator_readiness, "is_llm_configured", lambda: True)
    monkeypatch.setattr(operator_readiness.settings, "github_token", "ghp_x")
    _patch_httpx(monkeypatch, response=_FakeResponse(200, {"status": "ok"}))

    async def _fake_banner_status():
        return {"x_configured": False, "has_banner": False}

    monkeypatch.setattr("aria_core.x_banner.get_x_banner_status", _fake_banner_status)

    gaps, _ok = await operator_readiness.collect_readiness_gaps(goal="publier une bannière X")
    assert any(g["id"] == "x_oauth" for g in gaps)


@pytest.mark.asyncio
async def test_collect_gaps_x_banner_branch_missing_banner_only(monkeypatch):
    monkeypatch.setattr(operator_readiness, "is_llm_configured", lambda: True)
    monkeypatch.setattr(operator_readiness.settings, "github_token", "ghp_x")
    _patch_httpx(monkeypatch, response=_FakeResponse(200, {"status": "ok"}))

    async def _fake_banner_status():
        return {"x_configured": True, "has_banner": False}

    monkeypatch.setattr("aria_core.x_banner.get_x_banner_status", _fake_banner_status)

    gaps, _ok = await operator_readiness.collect_readiness_gaps(goal="profil X a jour")
    assert any(g["id"] == "x_banner" for g in gaps)


@pytest.mark.asyncio
async def test_collect_gaps_x_banner_branch_all_ok(monkeypatch):
    monkeypatch.setattr(operator_readiness, "is_llm_configured", lambda: True)
    monkeypatch.setattr(operator_readiness.settings, "github_token", "ghp_x")
    _patch_httpx(monkeypatch, response=_FakeResponse(200, {"status": "ok"}))

    async def _fake_banner_status():
        return {"x_configured": True, "has_banner": True}

    monkeypatch.setattr("aria_core.x_banner.get_x_banner_status", _fake_banner_status)

    gaps, ok_items = await operator_readiness.collect_readiness_gaps(goal="@aria profil check")
    assert not any(g["id"] in ("x_oauth", "x_banner") for g in gaps)
    assert any("Bannière X" in item for item in ok_items)


@pytest.mark.asyncio
async def test_collect_gaps_x_banner_branch_degrades_on_exception(monkeypatch):
    """Une panne du sous-module bannière ne doit jamais casser l'audit global."""
    monkeypatch.setattr(operator_readiness, "is_llm_configured", lambda: True)
    monkeypatch.setattr(operator_readiness.settings, "github_token", "ghp_x")
    _patch_httpx(monkeypatch, response=_FakeResponse(200, {"status": "ok"}))

    async def _broken(*a, **k):
        raise RuntimeError("X API down")

    monkeypatch.setattr("aria_core.x_banner.get_x_banner_status", _broken)

    gaps, _ok = await operator_readiness.collect_readiness_gaps(goal="bannière X")
    assert not any(g["id"] in ("x_oauth", "x_banner") for g in gaps)


@pytest.mark.asyncio
async def test_execute_operator_readiness_reports_goal_and_gaps_fr(monkeypatch):
    async def _fake_gaps(**_):
        return (
            [{"id": "llm_config", "label": "LLM non configuré", "worker": "configurer vault"}],
            ["GitHub token présent"],
        )

    monkeypatch.setattr(operator_readiness, "collect_readiness_gaps", _fake_gaps)

    reply, data = await operator_readiness.execute_operator_readiness(
        "ok tout est pret qu'est-ce qu'il manque pour que tu puisses publier", lang="fr",
    )
    assert "publier" in reply
    assert "LLM non configuré" in reply
    assert "configurer vault" in reply
    assert data["operator_readiness"] is True
    assert data["gaps"][0]["id"] == "llm_config"


@pytest.mark.asyncio
async def test_execute_operator_readiness_reports_nothing_blocking_fr(monkeypatch):
    async def _fake_gaps(**_):
        return [], ["tout est prêt"]

    monkeypatch.setattr(operator_readiness, "collect_readiness_gaps", _fake_gaps)

    reply, _data = await operator_readiness.execute_operator_readiness(
        "qu'est-ce qu'il manque pour que tu puisses avancer", lang="fr",
    )
    assert "Rien de bloquant détecté" in reply


@pytest.mark.asyncio
async def test_execute_operator_readiness_english_branch(monkeypatch):
    async def _fake_gaps(**_):
        return (
            [{"id": "github_token", "label": "GITHUB_TOKEN missing"}],
            ["LLM cloud: groq / llama-3"],
        )

    monkeypatch.setattr(operator_readiness, "collect_readiness_gaps", _fake_gaps)

    reply, data = await operator_readiness.execute_operator_readiness(
        "what is missing for you to publish", lang="en",
    )
    assert "MISSING GITHUB_TOKEN missing" in reply
    assert "OK LLM cloud: groq / llama-3" in reply
    assert data["go_ahead"] is False


@pytest.mark.asyncio
async def test_execute_operator_readiness_detects_go_ahead():
    async def _fake_gaps(**_):
        return [], []

    reply_data = await operator_readiness.execute_operator_readiness(
        "si c'est bénéfique pour toi vas-y", lang="fr",
    )
    assert reply_data[1]["go_ahead"] is True


@pytest.mark.asyncio
async def test_status_pulse_reports_gaps_when_present(monkeypatch, tmp_path):
    from aria_core.memory import collegue as collegue_mod

    monkeypatch.setattr(collegue_mod, "_ops_memoire_candidates", lambda: [tmp_path])

    async def _fake_gaps(**_):
        return (
            [
                {"id": "llm_config", "label": "LLM non configuré", "worker": "configurer vault"},
                {"id": "github_token", "label": "GITHUB_TOKEN absent"},
            ],
            [],
        )

    monkeypatch.setattr(operator_readiness, "collect_readiness_gaps", _fake_gaps)

    reply, data = await operator_readiness.execute_operator_status_pulse(
        "rien de nouveau a declarer ?", lang="fr",
    )
    assert "2 points à traiter" in reply
    assert "LLM non configuré" in reply
    assert "configurer vault" in reply
    assert data["gaps"][0]["id"] == "llm_config"


@pytest.mark.asyncio
async def test_status_pulse_english_branch(monkeypatch, tmp_path):
    from aria_core.memory import collegue as collegue_mod

    monkeypatch.setattr(collegue_mod, "_ops_memoire_candidates", lambda: [tmp_path])

    async def _fake_gaps(**_):
        return [{"id": "llm_config", "label": "LLM not configured"}], []

    monkeypatch.setattr(operator_readiness, "collect_readiness_gaps", _fake_gaps)

    reply, _data = await operator_readiness.execute_operator_status_pulse(
        "anything new to report?", lang="en",
    )
    assert "1 item(s) need attention" in reply
    assert "LLM not configured" in reply


def test_parse_readiness_goal_empty_when_no_match():
    assert parse_readiness_goal("bonjour, comment ça va ?") == ""


def test_parse_readiness_goal_truncates_long_goal():
    long_goal = "faire " + "x" * 300
    msg = f"qu'est-ce qu'il manque pour que tu puisses {long_goal}"
    goal = parse_readiness_goal(msg)
    assert len(goal) <= 200