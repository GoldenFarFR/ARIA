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
    # 17/07 -- deepseek-deepseek-v4-pro n'est plus banni (décision opérateur explicite,
    # "il n'y a aucune raison qu'il le soit") -- un modèle non banni est désormais utilisé
    # tel quel, jamais remplacé par le défaut "grok".
    settings.llm_model = "deepseek-deepseek-v4-pro"
    settings.llm_fallback_provider = "groq"
    settings.llm_fallback_api_key = "gsk-fallback"
    settings.llm_fallback_model = "llama-3.3-70b-versatile"

    routes = _resolve_routes()
    assert len(routes) == 2
    assert routes[0].provider == "virtuals"
    assert routes[0].url == "https://compute.virtuals.io/v1/chat/completions"
    assert routes[0].auth_key == "spark-key"
    assert routes[0].model == "deepseek-deepseek-v4-pro"
    assert routes[1].provider == "groq"
    assert routes[1].auth_key == "gsk-fallback"
    assert is_llm_configured() is True


def test_virtuals_skips_a_genuinely_banned_model(monkeypatch):
    """Le mécanisme de ban lui-même reste fonctionnel -- testé sur une valeur SYNTHÉTIQUE
    plutôt que sur un vrai nom de modèle (17/07 : le seul exemple réel utilisé jusqu'ici,
    deepseek-deepseek-v4-pro, n'a plus de raison d'être banni)."""
    from aria_core import spark_config

    # ``_resolve_model`` (llm.py) réimporte BANNED_VIRTUALS_PRIMARY_MODELS depuis
    # spark_config à chaque appel (import local) -- patcher ici suffit, aucun besoin de
    # toucher llm.py.
    monkeypatch.setattr(spark_config, "BANNED_VIRTUALS_PRIMARY_MODELS", frozenset({"synthetic-banned-model"}))

    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = "spark-key"
    settings.llm_model = "synthetic-banned-model"
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].model != "synthetic-banned-model"


def test_virtuals_never_uses_groq_llm_api_key():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = ""
    settings.llm_api_key = "gsk-should-not-be-used"
    settings.llm_model = "some-model"
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


def test_gemini_direct_route_no_virtuals_dependency():
    """17/07 -- Gemini en provider primaire direct (point d'accès compatible OpenAI
    officiel Google), même patron que deepseek/grok."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "gemini"
    settings.gemini_api_key = "gm-real-key"
    settings.virtuals_api_key = ""
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].provider == "gemini"
    assert routes[0].url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert routes[0].auth_key == "gm-real-key"
    assert routes[0].model == "gemini-3.5-flash"
    assert is_llm_configured() is True


def test_gemini_never_uses_generic_llm_api_key_when_own_key_set():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "gemini"
    settings.gemini_api_key = "gm-real-key"
    settings.llm_api_key = "should-not-win"

    routes = _resolve_routes()
    assert routes[0].auth_key == "gm-real-key"


def test_gemini_ignores_virtuals_catalog_llm_model():
    """Même bug que grok/x.ai corrigé le 17/07 (ef65ce92) : settings.llm_model porte un
    ID catalogue Virtuals -- un provider direct ne doit jamais l'hériter."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "gemini"
    settings.gemini_api_key = "gm-real-key"
    settings.llm_model = "x-ai-grok-4-3"

    routes = _resolve_routes()
    assert routes[0].model == "gemini-3.5-flash"


def test_mistral_direct_route_no_virtuals_dependency():
    """17/07 -- Mistral en provider primaire direct, même patron que gemini/deepseek."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "mistral"
    settings.mistral_api_key = "ms-real-key"
    settings.virtuals_api_key = ""
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].provider == "mistral"
    assert routes[0].url == "https://api.mistral.ai/v1/chat/completions"
    assert routes[0].auth_key == "ms-real-key"
    assert routes[0].model == "mistral-small-2603"
    assert is_llm_configured() is True


def test_mistral_never_uses_generic_llm_api_key_when_own_key_set():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "mistral"
    settings.mistral_api_key = "ms-real-key"
    settings.llm_api_key = "should-not-win"

    routes = _resolve_routes()
    assert routes[0].auth_key == "ms-real-key"


@pytest.mark.asyncio
async def test_mistral_payload_forces_reasoning_effort_none(monkeypatch):
    """17/07 -- même piège que Gemini le même soir (budget de tokens englouti par un
    raisonnement invisible) : Mistral expose un vrai levier documenté
    (reasoning_effort="none", docs.mistral.ai/api) -- forcé systématiquement sur ce
    provider, jamais laissé au défaut de l'API."""
    import aria_core.llm as llm_mod

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "HOLD"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers=None, json=None):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", FakeAsyncClient)

    route = llm_mod.LlmRoute("mistral", "https://api.mistral.ai/v1/chat/completions", "mistral-small-2603", "ms-key")
    reply = await llm_mod._post_chat(
        route, messages=[{"role": "user", "content": "BUY ou HOLD ?"}],
        temperature=0.0, max_tokens=10, prompt_est=10, depth="brief",
    )
    assert reply == "HOLD"
    assert captured["json"]["reasoning_effort"] == "none"


def test_openai_direct_route_uses_dedicated_key_and_current_default_model():
    """17/07 -- gpt-4o-mini (avril 2025) remplacé par gpt-5-mini (août 2025, positionné
    par OpenAI pour les workloads low-latency/high-volume) ; clé dédiée openai_api_key
    plutôt que llm_api_key générique (même doctrine que grok/gemini/mistral)."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "openai"
    settings.openai_api_key = "oa-real-key"
    settings.llm_api_key = "should-not-win"
    settings.virtuals_api_key = ""
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].provider == "openai"
    assert routes[0].url == "https://api.openai.com/v1/chat/completions"
    assert routes[0].auth_key == "oa-real-key"
    assert routes[0].model == "gpt-5-mini"
    assert is_llm_configured() is True


def test_openrouter_direct_route_uses_dedicated_key():
    """17/07 -- OpenRouter agrège plusieurs providers derrière un seul compte payé en
    USDC (plutôt que multiplier les comptes fiat par provider) -- clé dédiée
    openrouter_api_key, même doctrine que les autres providers directs."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "openrouter"
    settings.openrouter_api_key = "or-real-key"
    settings.llm_api_key = "should-not-win"
    settings.virtuals_api_key = ""
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].provider == "openrouter"
    assert routes[0].url == "https://openrouter.ai/api/v1/chat/completions"
    assert routes[0].auth_key == "or-real-key"
    assert is_llm_configured() is True


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


# ── Anthropic direct, format natif (17/07) ──────────────────────────────────────────

def test_anthropic_direct_route_uses_dedicated_key():
    """Préparé pour la bascule OpenRouter -> Anthropic direct (0 crédit au 17/07,
    testé en direct contre api.anthropic.com : clé authentifiée, 400 billing-only)."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "anthropic"
    settings.anthropic_api_key = "sk-ant-real-key"
    settings.virtuals_api_key = ""
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].provider == "anthropic"
    assert routes[0].url == "https://api.anthropic.com/v1/messages"
    assert routes[0].auth_key == "sk-ant-real-key"
    assert routes[0].model == "claude-haiku-4-5-20251001"
    assert is_llm_configured() is True


def test_anthropic_never_falls_back_to_generic_llm_api_key():
    """Contrairement aux autres providers directs (grok/deepseek/gemini/mistral/openai/
    openrouter), Anthropic n'a délibérément AUCUN repli sur ``llm_api_key`` -- c'est un
    provider tout neuf, mieux vaut échouer explicitement (pas de route) que risquer une
    mauvaise clé générique silencieuse (même famille de bug que l'incident grok_api_key
    du 17/07)."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "anthropic"
    settings.anthropic_api_key = ""
    settings.llm_api_key = "should-not-be-used"

    routes = _resolve_routes()
    assert routes == []


def test_anthropic_headers_use_x_api_key_not_bearer():
    import aria_core.llm as llm_mod

    route = llm_mod.LlmRoute("anthropic", "https://api.anthropic.com/v1/messages", "claude-haiku-4-5-20251001", "sk-ant-key")
    headers = llm_mod._headers_for_route(route)
    assert headers["x-api-key"] == "sk-ant-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_anthropic_payload_extracts_system_top_level_and_parses_content_blocks(monkeypatch):
    """Le point clé qui distingue Anthropic de tous les autres providers de ce fichier
    (tous compatibles OpenAI) : ``system`` est un champ top-level, PAS un message de
    rôle "system" ; la réponse est un tableau ``content`` de blocs typés, PAS
    ``choices[0].message.content``. Vérifié en direct contre api.anthropic.com le
    17/07 (auth OK, 400 billing-only -- juste 0 crédit sur le compte)."""
    import aria_core.llm as llm_mod

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "content": [{"type": "text", "text": "HOLD"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 42, "output_tokens": 3},
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", FakeAsyncClient)

    recorded: dict = {}
    import aria_core.llm_usage as llm_usage_mod
    monkeypatch.setattr(llm_usage_mod, "record_llm_usage", lambda **kw: recorded.update(kw))

    route = llm_mod.LlmRoute("anthropic", "https://api.anthropic.com/v1/messages", "claude-haiku-4-5-20251001", "sk-ant-key")
    reply = await llm_mod._post_chat(
        route,
        messages=[
            {"role": "system", "content": "Tu es un juge BUY/HOLD."},
            {"role": "user", "content": "BUY ou HOLD ?"},
        ],
        temperature=0.0, max_tokens=10, prompt_est=20, depth="brief",
    )
    assert reply == "HOLD"
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    # system extrait, jamais un message de rôle "system" dans le tableau messages
    assert captured["json"]["system"] == "Tu es un juge BUY/HOLD."
    assert all(m["role"] != "system" for m in captured["json"]["messages"])
    assert captured["json"]["messages"] == [{"role": "user", "content": "BUY ou HOLD ?"}]
    assert recorded.get("input_tokens") == 42
    assert recorded.get("output_tokens") == 3
    assert recorded.get("truncated") is False


@pytest.mark.asyncio
async def test_anthropic_truncation_detected_via_stop_reason_max_tokens(monkeypatch):
    """Anthropic signale une réponse tronquée via ``stop_reason=max_tokens`` (pas
    ``finish_reason=length`` comme les providers compatibles OpenAI) -- même garde-fou
    de télémétrie (#incident 12/07) doit s'appliquer ici aussi."""
    import aria_core.llm as llm_mod

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "content": [{"type": "text", "text": "réponse coupée en plein"}],
                "stop_reason": "max_tokens",
                "usage": {"input_tokens": 900, "output_tokens": 900},
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers=None, json=None):
            return FakeResponse()

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", FakeAsyncClient)

    recorded: dict = {}
    import aria_core.llm_usage as llm_usage_mod
    monkeypatch.setattr(llm_usage_mod, "record_llm_usage", lambda **kw: recorded.update(kw))

    route = llm_mod.LlmRoute("anthropic", "https://api.anthropic.com/v1/messages", "claude-sonnet-5", "sk-ant-key")
    reply = await llm_mod._post_chat(
        route, messages=[{"role": "user", "content": "prompt"}],
        temperature=0.0, max_tokens=900, prompt_est=900, depth="develop",
    )
    assert reply == "réponse coupée en plein"
    assert recorded.get("truncated") is True


# ── routage explicite par appel (17/07, tie-breaker Haiku + develop Sonnet 5) ───────

def test_resolve_routes_explicit_provider_overrides_global():
    """L'appelant qui a besoin d'un modèle précis (ex. le tie-breaker momentum sur
    Haiku via OpenRouter) ne doit pas dépendre du LLM_PROVIDER global (Grok/Spark) --
    tout le reste d'ARIA continue d'utiliser ce dernier normalement."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "grok"
    settings.grok_api_key = "grok-key"
    settings.openrouter_api_key = "or-key"
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes(
        "anthropic/claude-haiku-4.5", provider="openrouter", require_llm_enabled=True,
    )
    assert len(routes) == 1
    assert routes[0].provider == "openrouter"
    assert routes[0].model == "anthropic/claude-haiku-4.5"

    # Un appel SANS provider explicite reste sur le comportement global inchangé.
    default_routes = _resolve_routes(require_llm_enabled=True)
    assert default_routes[0].provider == "grok"


def test_resolve_routes_explicit_fallback_same_provider_as_primary_not_deduped():
    """Bug trouvé en construisant ce chantier : le dédoublonnage EXISTANT (fallback
    global) compare par provider seul -- correct pour son usage d'origine, mais aurait
    supprimé à tort un secours explicite Sonnet 5 -> Haiku (même provider OpenRouter,
    modèle différent). Corrigé : le secours explicite se dédoublonne par (provider,
    model), jamais provider seul."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "openrouter"
    settings.openrouter_api_key = "or-key"
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    routes = _resolve_routes(
        "anthropic/claude-sonnet-5",
        provider="openrouter",
        fallback_provider="openrouter",
        fallback_model="anthropic/claude-haiku-4.5",
        require_llm_enabled=True,
    )
    assert len(routes) == 2
    assert routes[0].provider == "openrouter" and routes[0].model == "anthropic/claude-sonnet-5"
    assert routes[1].provider == "openrouter" and routes[1].model == "anthropic/claude-haiku-4.5"


def test_resolve_routes_explicit_fallback_plus_global_fallback_as_third_tier():
    """Décision opérateur explicite (17/07) : si OpenRouter entier tombe (pas juste un
    modèle), le repli global existant (Grok/Groq, infrastructure totalement
    différente) reste un 3e filet -- réutilisé tel quel, aucune nouvelle config."""
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "openrouter"
    settings.openrouter_api_key = "or-key"
    settings.llm_fallback_provider = "groq"
    settings.llm_fallback_api_key = "gsk-fallback"

    routes = _resolve_routes(
        "anthropic/claude-sonnet-5",
        provider="openrouter",
        fallback_provider="openrouter",
        fallback_model="anthropic/claude-haiku-4.5",
        require_llm_enabled=True,
    )
    assert len(routes) == 3
    assert [r.provider for r in routes] == ["openrouter", "openrouter", "groq"]
    assert routes[2].auth_key == "gsk-fallback"


@pytest.mark.asyncio
async def test_chat_with_context_forwards_provider_and_fallback_kwargs(monkeypatch):
    """Bout-en-bout : ``chat_with_context`` transmet bien provider/fallback_provider/
    fallback_model à ``_resolve_routes`` -- pas seulement testé au niveau interne."""
    import aria_core.llm as llm_mod

    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "grok"
    settings.grok_api_key = "grok-key"
    settings.openrouter_api_key = "or-key"
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""

    captured_routes = {}

    async def fake_post_chat(route, **kwargs):
        captured_routes["provider"] = route.provider
        captured_routes["model"] = route.model
        return "HOLD"

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post_chat)

    out = await llm_mod.chat_with_context(
        "user", "system", max_tokens=10, temperature=0.0,
        provider="openrouter", model="anthropic/claude-haiku-4.5",
    )
    assert out == "HOLD"
    assert captured_routes["provider"] == "openrouter"
    assert captured_routes["model"] == "anthropic/claude-haiku-4.5"