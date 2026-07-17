import pytest

from aria_core.llm import LlmRoute, _http_ok, _resolve_routes, is_llm_configured
from aria_core.runtime import get_settings


def test_http_ok_accepts_virtuals_201():
    assert _http_ok(200) is True
    assert _http_ok(201) is True
    assert _http_ok(401) is False


def test_virtuals_with_groq_fallback_chain():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = "spark-key"
    settings.llm_model = "deepseek-deepseek-v4-pro"
    settings.llm_fallback_provider = "groq"
    settings.llm_fallback_api_key = "gsk-fallback"
    settings.llm_fallback_model = "llama-3.3-70b-versatile"

    routes = _resolve_routes()
    assert len(routes) == 2
    assert routes[0].provider == "virtuals"
    assert routes[0].url == "https://compute.virtuals.io/v1/chat/completions"
    assert routes[0].auth_key == "spark-key"
    assert routes[0].model not in ("deepseek-deepseek-v4-pro", "")
    assert "grok" in routes[0].model.lower()
    assert routes[1].provider == "groq"
    assert routes[1].auth_key == "gsk-fallback"
    assert is_llm_configured() is True


def test_virtuals_never_uses_groq_llm_api_key():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = ""
    settings.llm_api_key = "gsk-should-not-be-used"
    settings.llm_model = "deepseek-deepseek-v4-pro"
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""
    settings.llm_fallback_model = ""

    routes = _resolve_routes()
    assert routes == []


def test_grok_direct_route_ignores_virtuals_catalog_llm_model():
    """17/07, bug réel trouvé en basculant hors de Virtuals (expiration crédits Spark
    18/07, test bout en bout réel sur le VPS) : ``settings.llm_model`` porte en
    permanence un ID catalogue Virtuals (ex. "x-ai-grok-4-3", dérivé de
    ARIA_LLM_MODEL_STANDARD) -- un provider direct (grok/xai) qui hérite de ce
    réglage envoie ce format à la vraie API x.ai, qui le rejette en 400 "Model not
    found". ``_route_for_provider`` ne doit proposer ``llm_model`` comme repli QUE
    pour le provider "virtuals"."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "grok"
    settings.grok_api_key = "xai-real-key"
    settings.llm_model = "x-ai-grok-4-3"  # ID catalogue Virtuals, jamais un vrai modèle x.ai
    settings.virtuals_api_key = ""
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].provider == "grok"
    assert routes[0].model == "grok-4.3"  # DEFAULT_MODELS["grok"], jamais l'ID catalogue
    assert routes[0].auth_key == "xai-real-key"


def test_grok_direct_route_uses_dedicated_grok_api_key_not_llm_api_key():
    """17/07 -- grok_api_key doit gagner sur llm_api_key (souvent une clé Groq, service
    différent malgré le nom proche) quand les deux sont présents."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "grok"
    settings.grok_api_key = "xai-dedicated-key"
    settings.llm_api_key = "gsk-groq-key-wrong-service"
    settings.virtuals_api_key = ""
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].auth_key == "xai-dedicated-key"


def test_deepseek_direct_route_no_virtuals_dependency():
    """DeepSeek en provider primaire direct (api.deepseek.com) — indépendant de Virtuals/
    Spark, aucune clé Virtuals nécessaire (16/07, seam relais Spark)."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "deepseek"
    settings.deepseek_api_key = "sk-deepseek-key"
    settings.virtuals_api_key = ""
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].provider == "deepseek"
    assert routes[0].url == "https://api.deepseek.com/v1/chat/completions"
    assert routes[0].auth_key == "sk-deepseek-key"
    assert routes[0].model == "deepseek-chat"
    assert is_llm_configured() is True


def test_deepseek_never_uses_generic_llm_api_key_when_own_key_set():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "deepseek"
    settings.deepseek_api_key = "sk-real-deepseek"
    settings.llm_api_key = "should-not-win"

    routes = _resolve_routes()
    assert routes[0].auth_key == "sk-real-deepseek"


def test_deepseek_falls_back_to_generic_llm_api_key_when_unset():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "deepseek"
    settings.deepseek_api_key = ""
    settings.llm_api_key = "generic-key"

    routes = _resolve_routes()
    assert routes[0].auth_key == "generic-key"


def test_groq_only_no_duplicate_fallback():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "groq"
    settings.llm_api_key = "gsk-main"
    settings.llm_fallback_provider = "groq"
    settings.llm_fallback_api_key = "gsk-fallback"

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].provider == "groq"


@pytest.mark.asyncio
async def test_chat_falls_back_to_groq(monkeypatch):
    from aria_core import llm as llm_mod

    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = "spark-key"
    settings.llm_fallback_provider = "groq"
    settings.llm_fallback_api_key = "gsk-fallback"
    settings.llm_fallback_model = "llama-3.3-70b-versatile"
    settings.aria_llm_temperature = 0.2

    calls: list[str] = []

    async def fake_post(route, **kwargs):
        calls.append(route.provider)
        if route.provider == "virtuals":
            return None
        return "fallback-ok"

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)

    from aria_core import llm_usage

    llm_usage.begin_chat_usage_tracking()
    try:
        out = await llm_mod.chat_with_context("hi", "sys", max_tokens=50)
        assert out == "fallback-ok"
        assert calls == ["virtuals", "groq"]
        # #135 : le tour de chat doit être marqué comme étant passé par le fallback.
        fallback_state = llm_usage.get_chat_fallback_state()
        assert fallback_state == {"used": True, "provider": "groq"}
    finally:
        llm_usage.clear_chat_usage_tracking()


@pytest.mark.asyncio
async def test_chat_primary_success_no_fallback_marked(monkeypatch):
    """#135 : quand la route primaire répond du premier coup, rien ne doit signaler un
    fallback -- silence total, condition nécessaire pour ne jamais bruiter le cas normal."""
    from aria_core import llm as llm_mod

    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = "spark-key"
    settings.llm_fallback_provider = "groq"
    settings.llm_fallback_api_key = "gsk-fallback"

    async def fake_post(route, **kwargs):
        return "spark-ok"

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)

    from aria_core import llm_usage

    llm_usage.begin_chat_usage_tracking()
    try:
        out = await llm_mod.chat_with_context("hi", "sys", max_tokens=50)
        assert out == "spark-ok"
        assert llm_usage.get_chat_fallback_state() == {"used": False, "provider": ""}
    finally:
        llm_usage.clear_chat_usage_tracking()


@pytest.mark.asyncio
async def test_truncated_response_logged_and_recorded(monkeypatch):
    # Incident réel (12/07) : une réponse coupée par l'API (finish_reason=length, budget
    # max_tokens atteint) était affichée telle quelle sans aucun signal -- ni log, ni
    # télémétrie. L'opérateur l'a vue s'arrêter net en plein mot sur Telegram.
    from aria_core import llm as llm_mod

    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "groq"
    settings.llm_api_key = "gsk-main"
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": "réponse coupée en plein mot..."},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 9000, "completion_tokens": 2400, "total_tokens": 11400},
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            return FakeResponse()

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", lambda **kw: FakeClient())

    recorded: dict = {}

    def fake_record(**kwargs):
        recorded.update(kwargs)

    import aria_core.llm_usage as llm_usage_mod

    monkeypatch.setattr(llm_usage_mod, "record_llm_usage", fake_record)

    warnings: list[str] = []
    monkeypatch.setattr(
        llm_mod.logger, "warning", lambda msg, *a, **kw: warnings.append(msg % a if a else msg)
    )

    out = await llm_mod.chat_with_context("prompt long", "sys", max_tokens=2400)
    assert out == "réponse coupée en plein mot..."
    assert recorded.get("truncated") is True
    assert any("truncated" in w.lower() for w in warnings)