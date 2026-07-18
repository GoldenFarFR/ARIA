"""_handle_photo — point d'entrée unique pour les messages photo Telegram.

Avant ce correctif, AUCUN handler photo n'était enregistré : toute image envoyée à
ARIA (avatar OU vision) était ignorée en silence. Vérifie le dispatch par légende
(avatar vs vision), le gate ARIA_VISION_ENABLED, la restriction admin, et le
téléchargement/encodage de l'image.
"""
from __future__ import annotations

import pytest

from aria_core import brain as brain_mod
from aria_core.gateway import telegram_bot


class FakeMessage:
    def __init__(self, *, caption: str = "", photo=None):
        self.caption = caption
        self.photo = photo if photo is not None else [FakePhotoSize()]
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakePhotoSize:
    def __init__(self, file_id: str = "file123"):
        self.file_id = file_id


class FakeTgFile:
    def __init__(self, data: bytes = b"fake-jpeg-bytes"):
        self._data = data

    async def download_as_bytearray(self):
        return bytearray(self._data)


class FakeBot:
    def __init__(self, tg_file: FakeTgFile | None = None, *, raise_on_get_file: bool = False):
        self._tg_file = tg_file or FakeTgFile()
        self._raise = raise_on_get_file

    async def get_file(self, file_id: str):
        if self._raise:
            raise RuntimeError("telegram down")
        return self._tg_file


class FakeUser:
    def __init__(self, user_id: int = 42):
        self.id = user_id


class FakeUpdate:
    def __init__(self, *, caption: str = "", user_id: int = 42, photo=None):
        self.message = FakeMessage(caption=caption, photo=photo)
        self.effective_user = FakeUser(user_id)


class FakeContext:
    def __init__(self, bot: FakeBot | None = None):
        self.bot = bot or FakeBot()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda uid: uid == 42)
    monkeypatch.delenv("ARIA_VISION_ENABLED", raising=False)
    yield


@pytest.mark.asyncio
async def test_no_photo_is_noop():
    update = FakeUpdate()
    update.message.photo = []
    await telegram_bot._handle_photo(update, FakeContext())
    assert update.message.replies == []


@pytest.mark.asyncio
async def test_empty_caption_routes_to_vision_flow_not_avatar(monkeypatch):
    """Incident #147 (12/07) : une photo sans légende (envoyée pour tester la vision)
    déclenchait AUTREFOIS un changement de photo de profil publique par défaut --
    appliqué réellement en prod (avatar du bot remplacé par un portrait tiers sans
    confirmation). Une légende vide ou ambiguë ne doit plus jamais toucher l'identité
    visuelle publique -- seul un signal explicite (/avatar, mot-clé avatar) le fait
    encore (cf. test_avatar_keyword_caption_routes_to_avatar_flow ci-dessous)."""
    called = {}

    async def fake_avatar(update, context):
        called["avatar"] = True

    async def fake_vision(update, context, caption):
        called["vision"] = True

    monkeypatch.setattr(telegram_bot, "_handle_avatar_photo", fake_avatar)
    monkeypatch.setattr(telegram_bot, "_handle_vision_photo", fake_vision)

    update = FakeUpdate(caption="")
    await telegram_bot._handle_photo(update, FakeContext())

    assert called == {"vision": True}


@pytest.mark.asyncio
async def test_avatar_keyword_caption_routes_to_avatar_flow(monkeypatch):
    called = {}

    async def fake_avatar(update, context):
        called["avatar"] = True

    async def fake_vision(update, context, caption):
        called["vision"] = True

    monkeypatch.setattr(telegram_bot, "_handle_avatar_photo", fake_avatar)
    monkeypatch.setattr(telegram_bot, "_handle_vision_photo", fake_vision)

    update = FakeUpdate(caption="mets cette photo de profil")
    await telegram_bot._handle_photo(update, FakeContext())

    assert called == {"avatar": True}


@pytest.mark.asyncio
async def test_normal_caption_routes_to_vision_flow(monkeypatch):
    called = {}

    async def fake_avatar(update, context):
        called["avatar"] = True

    async def fake_vision(update, context, caption):
        called["vision"] = True
        called["caption"] = caption

    monkeypatch.setattr(telegram_bot, "_handle_avatar_photo", fake_avatar)
    monkeypatch.setattr(telegram_bot, "_handle_vision_photo", fake_vision)

    update = FakeUpdate(caption="juge cette situation")
    await telegram_bot._handle_photo(update, FakeContext())

    assert called == {"vision": True, "caption": "juge cette situation"}


@pytest.mark.asyncio
async def test_incident_147_exact_caption_routes_to_vision_not_avatar(monkeypatch):
    """Reproduit le texte exact de l'incident #147 (12/07) : "c'est qui ?" envoyé en
    légende d'une photo -- ne doit jamais déclencher un changement d'avatar public."""
    called = {}

    async def fake_avatar(update, context):
        called["avatar"] = True

    async def fake_vision(update, context, caption):
        called["vision"] = True

    monkeypatch.setattr(telegram_bot, "_handle_avatar_photo", fake_avatar)
    monkeypatch.setattr(telegram_bot, "_handle_vision_photo", fake_vision)

    update = FakeUpdate(caption="c'est qui ?")
    await telegram_bot._handle_photo(update, FakeContext())

    assert called == {"vision": True}


class TestCaptionIsAvatarUpload:
    """Tests directs sur _caption_is_avatar_upload (incident #147, 12/07)."""

    def test_empty_caption_is_not_avatar_upload(self):
        assert telegram_bot._caption_is_avatar_upload("") is False
        assert telegram_bot._caption_is_avatar_upload("   ") is False

    def test_slash_avatar_still_triggers_upload(self):
        assert telegram_bot._caption_is_avatar_upload("/avatar") is True
        assert telegram_bot._caption_is_avatar_upload("/avatar identity") is True

    def test_explicit_avatar_keyword_still_triggers_upload(self):
        assert telegram_bot._caption_is_avatar_upload("mets cette photo de profil") is True
        assert telegram_bot._caption_is_avatar_upload("change ton avatar") is True

    def test_ambiguous_question_never_triggers_upload(self):
        assert telegram_bot._caption_is_avatar_upload("c'est qui ?") is False
        assert telegram_bot._caption_is_avatar_upload("qu'en penses-tu ?") is False


@pytest.mark.asyncio
async def test_vision_non_admin_declines_without_llm_call(monkeypatch):
    async def fail_llm(*a, **kw):
        raise AssertionError("ne doit jamais appeler le LLM pour un visiteur public")

    monkeypatch.setattr(brain_mod.aria_brain, "_llm_response", fail_llm)
    monkeypatch.setenv("ARIA_VISION_ENABLED", "1")

    update = FakeUpdate(caption="analyse ce graphique", user_id=999)
    await telegram_bot._handle_vision_photo(update, FakeContext(), "analyse ce graphique")

    assert len(update.message.replies) == 1
    assert "équipe" in update.message.replies[0] or "pas encore" in update.message.replies[0]


@pytest.mark.asyncio
async def test_vision_gated_off_declines_honestly(monkeypatch):
    async def fail_llm(*a, **kw):
        raise AssertionError("gate OFF ne doit jamais appeler le LLM")

    monkeypatch.setattr(brain_mod.aria_brain, "_llm_response", fail_llm)

    update = FakeUpdate(caption="analyse ce graphique", user_id=42)
    await telegram_bot._handle_vision_photo(update, FakeContext(), "analyse ce graphique")

    assert len(update.message.replies) == 1
    assert "pas encore activée" in update.message.replies[0]


@pytest.mark.asyncio
async def test_vision_enabled_admin_calls_llm_with_data_uri(monkeypatch):
    captured = {}

    async def fake_llm_response(message, lang, *, public=False, image_data_uri=None, **kw):
        captured["message"] = message
        captured["image_data_uri"] = image_data_uri
        return "voici ma lecture"

    monkeypatch.setattr(brain_mod.aria_brain, "_llm_response", fake_llm_response)
    monkeypatch.setenv("ARIA_VISION_ENABLED", "1")

    update = FakeUpdate(caption="juge cette situation", user_id=42)
    await telegram_bot._handle_vision_photo(update, FakeContext(), "juge cette situation")

    assert captured["message"] == "juge cette situation"
    assert captured["image_data_uri"].startswith("data:image/jpeg;base64,")
    assert update.message.replies == ["voici ma lecture"]


@pytest.mark.asyncio
async def test_vision_why_not_bought_caption_short_circuits_without_llm_call(monkeypatch):
    """Incident réel (18/07) : "pourquoi tu n'as pas acheté cette divergence sur aeon ?"
    envoyé avec une image a reçu une réponse LLM confabulée ("aucun capital réel
    déployé... pas achat live") -- _handle_vision_photo appelle _llm_response()
    DIRECTEMENT, contournant tous les interceptors déterministes de process(). Ce test
    verrouille le correctif : la légende doit être interceptée AVANT tout téléchargement
    d'image ou appel LLM."""
    llm_called = {"value": False}

    async def fake_llm_response(*a, **kw):
        llm_called["value"] = True
        return "ne devrait jamais être appelé"

    monkeypatch.setattr(brain_mod.aria_brain, "_llm_response", fake_llm_response)
    monkeypatch.setenv("ARIA_VISION_ENABLED", "1")

    caption = "pourquoi tu n'as pas acheté cette divergence sur aeon ?"
    update = FakeUpdate(caption=caption, user_id=42)
    await telegram_bot._handle_vision_photo(update, FakeContext(), caption)

    assert llm_called["value"] is False
    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "momentum_entry" in reply
    assert "aucun capital réel" not in reply
    assert "track-record" not in reply


@pytest.mark.asyncio
async def test_vision_analysis_methodology_caption_short_circuits_without_llm_call(monkeypatch):
    llm_called = {"value": False}

    async def fake_llm_response(*a, **kw):
        llm_called["value"] = True
        return "ne devrait jamais être appelé"

    monkeypatch.setattr(brain_mod.aria_brain, "_llm_response", fake_llm_response)
    monkeypatch.setenv("ARIA_VISION_ENABLED", "1")

    caption = "quelles sont les conditions pour qu'un token t'intéresse ?"
    update = FakeUpdate(caption=caption, user_id=42)
    await telegram_bot._handle_vision_photo(update, FakeContext(), caption)

    assert llm_called["value"] is False
    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "momentum_entry" in reply
    assert "goplus" in reply


@pytest.mark.asyncio
async def test_vision_scan_scope_caption_short_circuits_without_llm_call(monkeypatch):
    llm_called = {"value": False}

    async def fake_llm_response(*a, **kw):
        llm_called["value"] = True
        return "ne devrait jamais être appelé"

    monkeypatch.setattr(brain_mod.aria_brain, "_llm_response", fake_llm_response)
    monkeypatch.setenv("ARIA_VISION_ENABLED", "1")

    caption = "je croyais que tu scanner tous les jetons sur base dans dexscreener ?"
    update = FakeUpdate(caption=caption, user_id=42)
    await telegram_bot._handle_vision_photo(update, FakeContext(), caption)

    assert llm_called["value"] is False
    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "momentum_entry" in reply
    assert "dexscreener" in reply


@pytest.mark.asyncio
async def test_vision_ordinary_caption_still_reaches_llm(monkeypatch):
    """Non-régression : une légende qui ne matche aucun détecteur déterministe continue
    de suivre le chemin LLM normal (comportement historique inchangé)."""
    captured = {}

    async def fake_llm_response(message, lang, *, public=False, image_data_uri=None, **kw):
        captured["called"] = True
        return "voici ma lecture"

    monkeypatch.setattr(brain_mod.aria_brain, "_llm_response", fake_llm_response)
    monkeypatch.setenv("ARIA_VISION_ENABLED", "1")

    update = FakeUpdate(caption="juge cette situation", user_id=42)
    await telegram_bot._handle_vision_photo(update, FakeContext(), "juge cette situation")

    assert captured.get("called") is True
    assert update.message.replies == ["voici ma lecture"]


@pytest.mark.asyncio
async def test_vision_download_failure_replies_honestly(monkeypatch):
    monkeypatch.setenv("ARIA_VISION_ENABLED", "1")
    bot = FakeBot(raise_on_get_file=True)
    update = FakeUpdate(caption="juge cette situation", user_id=42)

    await telegram_bot._handle_vision_photo(update, FakeContext(bot), "juge cette situation")

    assert len(update.message.replies) == 1
    assert "pas réussi" in update.message.replies[0] or "réessaie" in update.message.replies[0]


@pytest.mark.asyncio
async def test_vision_llm_none_replies_honestly(monkeypatch):
    async def fake_llm_response(*a, **kw):
        return None

    monkeypatch.setattr(brain_mod.aria_brain, "_llm_response", fake_llm_response)
    monkeypatch.setenv("ARIA_VISION_ENABLED", "1")

    update = FakeUpdate(caption="juge cette situation", user_id=42)
    await telegram_bot._handle_vision_photo(update, FakeContext(), "juge cette situation")

    assert len(update.message.replies) == 1
    assert "échoué" in update.message.replies[0] or "indisponible" in update.message.replies[0]


def test_vision_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_VISION_ENABLED", raising=False)
    assert telegram_bot.vision_enabled() is False


def test_vision_gate_on_via_env(monkeypatch):
    monkeypatch.setenv("ARIA_VISION_ENABLED", "1")
    assert telegram_bot.vision_enabled() is True
