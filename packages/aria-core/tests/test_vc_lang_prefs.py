"""Langue de sortie VC (FR/EN) -- i18n + préférence persistée.

- i18n : le FR reste l'existant, l'EN traduit libellés + code de risque.
- préférence : /langue mémorise le choix (persisté en base temporaire ici).

Aucun réseau : DB (chemin temporaire) isolée.
"""
from __future__ import annotations

import pytest

from aria_core.gateway import telegram_bot
from aria_core.skills import vc_i18n

ADDR = "0x" + "a" * 40


# --------------------------- Fakes Telegram ---------------------------
class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, reply_markup=None, **kwargs) -> None:
        self.replies.append(text)


class FakeUser:
    def __init__(self, user_id: int = 42):
        self.id = user_id


class FakeUpdate:
    def __init__(self, text: str, user_id: int = 42):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


class FakeContext:
    def __init__(self, args: list[str] | None = None):
        self.args = args or []


# ----------------------------- i18n -----------------------------------
def test_norm_lang_defaults_to_fr():
    assert vc_i18n.norm_lang("EN") == "en"
    assert vc_i18n.norm_lang("fr") == "fr"
    assert vc_i18n.norm_lang("zz") == "fr"
    assert vc_i18n.norm_lang(None) == "fr"


def test_llm_directive_only_in_english():
    assert vc_i18n.llm_language_directive("fr") == ""
    directive = vc_i18n.llm_language_directive("en")
    assert "ENGLISH" in directive
    # Les codes d'enum doivent rester protégés (jamais traduits).
    assert "FAIBLE|MODÉRÉ|ÉLEVÉ|EXTRÊME" in directive
    assert "solide|fragile|rejeté" in directive


def test_risk_label_translation():
    assert vc_i18n.risk_label("EXTRÊME", "en") == "EXTREME"
    assert vc_i18n.risk_label("MODÉRÉ", "en") == "MODERATE"
    assert vc_i18n.risk_label("FAIBLE", "en") == "LOW"
    # FR = identité.
    assert vc_i18n.risk_label("EXTRÊME", "fr") == "EXTRÊME"


# --------------------------- préférence -------------------------------
@pytest.mark.asyncio
async def test_prefs_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("aria_core.paths._DATA_DIR", tmp_path)
    from aria_core.skills import vc_prefs

    assert await vc_prefs.get_output_lang() == "fr"        # défaut
    assert await vc_prefs.set_output_lang("en") == "en"
    assert await vc_prefs.get_output_lang() == "en"         # persisté
    assert await vc_prefs.set_output_lang("fr") == "fr"
    assert await vc_prefs.get_output_lang() == "fr"
    with pytest.raises(ValueError):
        await vc_prefs.set_output_lang("zz")


# --------------------------- /langue ----------------------------------
@pytest.mark.asyncio
async def test_langue_command_sets_and_shows(tmp_path, monkeypatch):
    monkeypatch.setattr("aria_core.paths._DATA_DIR", tmp_path)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    from aria_core.skills import vc_prefs

    # Réglage EN.
    update = FakeUpdate("/langue en")
    await telegram_bot._handle_langue(update, FakeContext(["en"]))
    assert any("English" in r for r in update.message.replies)
    assert await vc_prefs.get_output_lang() == "en"

    # Sans argument : affiche l'actuelle.
    update2 = FakeUpdate("/langue")
    await telegram_bot._handle_langue(update2, FakeContext())
    assert any("en" in r for r in update2.message.replies)

    # Argument invalide : message d'usage, préférence inchangée.
    update3 = FakeUpdate("/langue zz")
    await telegram_bot._handle_langue(update3, FakeContext(["zz"]))
    assert any("Usage" in r for r in update3.message.replies)
    assert await vc_prefs.get_output_lang() == "en"
