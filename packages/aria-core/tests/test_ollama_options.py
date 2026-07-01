import pytest


@pytest.mark.asyncio
async def test_ollama_payload_includes_num_ctx(monkeypatch):
    from aria_core import runtime
    from aria_core.llm import chat_with_context
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        settings=AriaRuntimeSettings(
            aria_llm_enabled=True,
            llm_provider="ollama",
            llm_model="qwen2.5:14b",
            aria_ollama_num_ctx=8192,
        ),
    )

    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("aria_core.llm.httpx.AsyncClient", lambda **kw: FakeClient())

    out = await chat_with_context("ping", "system", max_tokens=50)
    assert out == "OK"
    assert captured["json"]["options"]["num_ctx"] == 8192