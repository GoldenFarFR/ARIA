"""Client TikTok (#34, patron dôme identique à tavily.py) + gate publish OFF par défaut.

Aucun réseau réel : httpx.AsyncClient est monkeypatché. Les credentials ne sont jamais
écrits en dur -- posés via monkeypatch.setenv, jamais un vrai compte TikTok requis.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aria_core.gateway import tiktok


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                "err", request=None, response=_FakeHttpResp(self.status_code)
            )


class _FakeHttpResp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeAsyncClient:
    """Remplace httpx.AsyncClient : file de réponses POST/PUT programmées, capture des appels."""

    _post_responses: list = []
    _put_response = None
    _captured_posts: list = []
    _captured_put = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, data=None, headers=None):
        type(self)._captured_posts.append((url, json, data, headers))
        return type(self)._post_responses.pop(0)

    async def put(self, url, content=None, headers=None):
        type(self)._captured_put = (url, content, headers)
        return type(self)._put_response


def _reset_fake(monkeypatch):
    _FakeAsyncClient._post_responses = []
    _FakeAsyncClient._put_response = None
    _FakeAsyncClient._captured_posts = []
    _FakeAsyncClient._captured_put = None
    monkeypatch.setattr(tiktok.httpx, "AsyncClient", _FakeAsyncClient)


@pytest.fixture
def _configured_env(monkeypatch):
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "ck-test")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "cs-test")
    monkeypatch.setenv("TIKTOK_REFRESH_TOKEN", "rt-test")
    _reset_fake(monkeypatch)


# ── configuration / gate ────────────────────────────────────────────────────────────


def test_is_tiktok_configured_requires_all_three(monkeypatch):
    monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
    monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TIKTOK_REFRESH_TOKEN", raising=False)
    assert tiktok.is_tiktok_configured() is False

    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "ck")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "cs")
    assert tiktok.is_tiktok_configured() is False  # refresh_token manquant

    monkeypatch.setenv("TIKTOK_REFRESH_TOKEN", "rt")
    assert tiktok.is_tiktok_configured() is True


def test_publish_enabled_false_by_default_even_when_configured(monkeypatch, _configured_env):
    """#34 -- gate OFF par défaut : credentials seuls ne suffisent jamais à publier."""
    monkeypatch.delenv("ARIA_TIKTOK_PUBLISH_ENABLED", raising=False)
    assert tiktok.is_tiktok_publish_enabled() is False


def test_publish_enabled_false_when_gate_on_but_not_configured(monkeypatch):
    monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
    monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TIKTOK_REFRESH_TOKEN", raising=False)
    monkeypatch.setenv("ARIA_TIKTOK_PUBLISH_ENABLED", "true")
    assert tiktok.is_tiktok_publish_enabled() is False


def test_publish_enabled_true_only_when_gate_on_and_configured(monkeypatch, _configured_env):
    monkeypatch.setenv("ARIA_TIKTOK_PUBLISH_ENABLED", "true")
    assert tiktok.is_tiktok_publish_enabled() is True


# ── refresh_access_token ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_access_token_without_credentials_is_false(monkeypatch):
    monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
    monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TIKTOK_REFRESH_TOKEN", raising=False)
    _reset_fake(monkeypatch)
    client = tiktok.TikTokClient(min_interval=0.0)
    assert await client.refresh_access_token() is False
    assert _FakeAsyncClient._captured_posts == []  # aucun appel réseau sans creds


@pytest.mark.asyncio
async def test_refresh_access_token_success(monkeypatch, _configured_env):
    client = tiktok.TikTokClient(min_interval=0.0)
    _FakeAsyncClient._post_responses = [_FakeResponse(200, {"access_token": "act.xyz"})]
    ok = await client.refresh_access_token()
    assert ok is True
    assert client._access_token == "act.xyz"
    # OAuth2 en x-www-form-urlencoded (data=), pas JSON -- contrainte TikTok.
    url, json_payload, data_payload, headers = _FakeAsyncClient._captured_posts[0]
    assert url == tiktok.TOKEN_URL
    assert json_payload is None
    assert data_payload["client_key"] == "ck-test"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"


@pytest.mark.asyncio
async def test_refresh_access_token_http_error_degrades_softly(monkeypatch, _configured_env):
    client = tiktok.TikTokClient(min_interval=0.0)
    _FakeAsyncClient._post_responses = [_FakeResponse(401, {})]
    assert await client.refresh_access_token() is False
    assert client._access_token == ""


# ── publish_video ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_video_refuses_when_gate_off(monkeypatch, _configured_env, tmp_path):
    monkeypatch.delenv("ARIA_TIKTOK_PUBLISH_ENABLED", raising=False)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-mp4-bytes")
    client = tiktok.TikTokClient(min_interval=0.0)
    result = await client.publish_video(video, caption="pitch")
    assert result.published is False
    assert "désactivé" in result.error
    assert _FakeAsyncClient._captured_posts == []  # aucun appel réseau, gate fermé


@pytest.mark.asyncio
async def test_publish_video_missing_file(monkeypatch, _configured_env):
    monkeypatch.setenv("ARIA_TIKTOK_PUBLISH_ENABLED", "true")
    client = tiktok.TikTokClient(min_interval=0.0)
    result = await client.publish_video(Path("/tmp/does-not-exist-tiktok.mp4"), caption="x")
    assert result.published is False
    assert "introuvable" in result.error


@pytest.mark.asyncio
async def test_publish_video_full_success(monkeypatch, _configured_env, tmp_path):
    monkeypatch.setenv("ARIA_TIKTOK_PUBLISH_ENABLED", "true")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-mp4-bytes")
    client = tiktok.TikTokClient(min_interval=0.0)
    _FakeAsyncClient._post_responses = [
        _FakeResponse(200, {"access_token": "act.xyz"}),  # refresh
        _FakeResponse(200, {"data": {"publish_id": "v_pub_1", "upload_url": "https://upload.example/x"}}),  # init
    ]
    _FakeAsyncClient._put_response = _FakeResponse(201, {})

    result = await client.publish_video(video, caption="Nouvelle sortie ARIA")
    assert result.published is True
    assert result.publish_id == "v_pub_1"
    put_url, put_content, put_headers = _FakeAsyncClient._captured_put
    assert put_url == "https://upload.example/x"
    assert put_content == b"fake-mp4-bytes"
    assert "Content-Range" in put_headers


@pytest.mark.asyncio
async def test_publish_video_init_failure_degrades_softly(monkeypatch, _configured_env, tmp_path):
    monkeypatch.setenv("ARIA_TIKTOK_PUBLISH_ENABLED", "true")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    client = tiktok.TikTokClient(min_interval=0.0)
    _FakeAsyncClient._post_responses = [
        _FakeResponse(200, {"access_token": "act.xyz"}),  # refresh
        _FakeResponse(401, {}),  # init refusé
    ]
    result = await client.publish_video(video, caption="x")
    assert result.published is False
    assert result.error is not None
    assert _FakeAsyncClient._captured_put is None  # jamais d'upload sans init réussi


@pytest.mark.asyncio
async def test_publish_video_upload_failure_keeps_publish_id(monkeypatch, _configured_env, tmp_path):
    monkeypatch.setenv("ARIA_TIKTOK_PUBLISH_ENABLED", "true")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    client = tiktok.TikTokClient(min_interval=0.0)
    _FakeAsyncClient._post_responses = [
        _FakeResponse(200, {"access_token": "act.xyz"}),
        _FakeResponse(200, {"data": {"publish_id": "v_pub_2", "upload_url": "https://upload.example/y"}}),
    ]
    _FakeAsyncClient._put_response = _FakeResponse(500, {})
    result = await client.publish_video(video, caption="x")
    assert result.published is False
    assert result.publish_id == "v_pub_2"  # traçable même en échec d'upload


# ── fetch_publish_status ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_publish_status_success(monkeypatch, _configured_env):
    client = tiktok.TikTokClient(min_interval=0.0)
    _FakeAsyncClient._post_responses = [
        _FakeResponse(200, {"access_token": "act.xyz"}),
        _FakeResponse(200, {"data": {"status": "PUBLISH_COMPLETE"}}),
    ]
    status = await client.fetch_publish_status("v_pub_1")
    assert status == "PUBLISH_COMPLETE"


@pytest.mark.asyncio
async def test_fetch_publish_status_without_auth_is_none(monkeypatch):
    monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
    monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TIKTOK_REFRESH_TOKEN", raising=False)
    _reset_fake(monkeypatch)
    client = tiktok.TikTokClient(min_interval=0.0)
    assert await client.fetch_publish_status("v_pub_1") is None


# ── adaptateur release_pipeline ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_publisher_inert_when_gate_off(monkeypatch, _configured_env):
    monkeypatch.delenv("ARIA_TIKTOK_PUBLISH_ENABLED", raising=False)
    assert await tiktok.tiktok_release_publisher("pitch", release=object()) is False


@pytest.mark.asyncio
async def test_release_publisher_inert_even_when_gate_on(monkeypatch, _configured_env):
    """#34 -- aucun pipeline vidéo n'existe : jamais un faux succès, même gate armé."""
    monkeypatch.setenv("ARIA_TIKTOK_PUBLISH_ENABLED", "true")
    assert await tiktok.tiktok_release_publisher("pitch", release=object()) is False


@pytest.mark.asyncio
async def test_release_publisher_matches_injectable_signature(tmp_path, monkeypatch):
    """Compatible avec release_pipeline.publish_release(tiktok_publisher=...) : ne casse
    jamais le canal X, et ne revendique jamais 'tiktok' comme publié à tort.

    Découverte au passage en livrant #34, corrigée en #127 : un publisher injecté qui
    renvoie False SANS lever atterrit désormais dans `pending_channels` (même sort qu'un
    canal sans publisher configuré) au lieu de disparaître silencieusement des deux
    listes -- cf. `release_pipeline.py::publish_release`.

    DB isolée (même patron que test_release_pipeline.py::_tmp_db) : release_pipeline.DB_PATH
    est un module-level constant figé à l'import -- sans ce monkeypatch, ce test partage la
    même base sqlite que tout autre test du process qui importe release_pipeline sans
    l'isoler, et échoue de façon non-déterministe une fois que le manifeste est épuisé par
    un test antérieur (même classe de bug que #149, jamais corrigée ici spécifiquement)."""
    from aria_core import release_pipeline as rp

    monkeypatch.setattr(rp, "DB_PATH", str(tmp_path / "rel.db"))
    await rp.arm_campaign()

    async def x_pub(text, rel):
        return True

    res = await rp.publish_release(x_publisher=x_pub, tiktok_publisher=tiktok.tiktok_release_publisher)
    assert "x" in res["published_to"]
    assert "tiktok" not in res["published_to"]  # jamais revendiqué publié sans vidéo réelle
    assert "tiktok" in res["pending_channels"]  # #127 -- plus jamais silencieusement absent
