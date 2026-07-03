"""Banniere X — generation creative text-to-image (sans ancre visage)."""

import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image

from aria_core.portrait_scene import (
    XAI_IMAGE_GENERATIONS_URL,
    generate_banner_creative,
)


@pytest.mark.asyncio
async def test_generate_banner_creative_uses_generations_not_anchor(monkeypatch):
    img = Image.new("RGB", (2000, 1000), (20, 20, 30))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    payload = base64.b64encode(buf.getvalue()).decode("ascii")

    calls: list[dict] = []

    async def fake_post(self, url, **kwargs):
        calls.append({"url": url, "json": kwargs.get("json")})
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"data": [{"b64_json": payload}]},
            request=request,
        )

    monkeypatch.setattr("aria_core.portrait_scene._image_api_key", lambda: "xai-test")
    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    out = await generate_banner_creative(brand_brief="GoldenFar ZHC", scene="dark gold vault")

    assert out is not None
    assert len(calls) == 1
    assert calls[0]["url"] == XAI_IMAGE_GENERATIONS_URL
    assert "image" not in (calls[0]["json"] or {})
    prompt = calls[0]["json"]["prompt"]
    assert "no people" in prompt.lower()
    assert "no faces" in prompt.lower()
    assert "GoldenFar ZHC" in prompt