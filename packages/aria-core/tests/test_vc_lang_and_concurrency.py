"""Langue de sortie VC (FR/EN) + garde de concurrence /vc.

- i18n : le FR reste l'existant, l'EN traduit libellés + code de risque.
- préférence : /langue mémorise le choix (persisté en base temporaire ici).
- concurrence : /vc refuse net quand la file d'attente est pleine.

Aucun réseau : analyse, LLM et DB (chemin temporaire) sont isolés.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot
from aria_core.skills.vc_analysis import VCResult, format_telegram_order
from aria_core.skills import vc_i18n

ADDR = "0x" + "a" * 40


def _result(**kw) -> VCResult:
    base = dict(
        contract=ADDR,
        potentiel=7,
        risque="MODÉRÉ",
        these="Traction on-chain réelle.",
        recommandation="BUY",
        taille_pct=5.0,
        entree="marché",
        invalidation="perte support $5k",
        cible="x2 6 mois",
        llm_used=True,
    )
    base.update(kw)
    return VCResult(**base)


# --------------------------- Fakes Telegram ---------------------------
class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:
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


# ---------------------- format_telegram_order -------------------------
def test_format_order_fr_unchanged():
    """Le FR doit rester exactement le comportement historique validé."""
    out = format_telegram_order(_result(), lang="fr")
    assert "📊 ARIA — Ordre proposé" in out
    assert "Risque MODÉRÉ" in out
    assert "Taille suggérée : 5.0% du capital" in out
    assert "Thèse :" in out
    assert "Aucune exécution automatique." in out


def test_format_order_english():
    out = format_telegram_order(_result(), lang="en")
    assert "📊 ARIA — Proposed order" in out
    assert "Risk MODERATE" in out           # code FR traduit à l'affichage
    assert "Suggested size : 5.0% of capital" in out
    assert "Thesis :" in out
    assert "No automatic execution." in out
    # Les chiffres et la reco NE changent pas.
    assert "BUY" in out
    assert "7/10" in out


def test_format_order_english_capital_amount():
    out = format_telegram_order(_result(taille_pct=5.0), capital_usd=1500, lang="en")
    assert "$75" in out
    assert "of $1,500" in out


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


# ------------------------ garde de concurrence ------------------------
# La garde de concurrence vit dans `_run_vc_analysis` (partagée par le mode test
# et le chemin réel déclenché après choix de langue) — on la teste directement.
@pytest.mark.asyncio
async def test_vc_rejects_when_overloaded(monkeypatch):
    core = AsyncMock()
    monkeypatch.setattr(telegram_bot, "_vc_analyze_and_reply", core)

    # Sature le sémaphore et remplit la file d'attente.
    for _ in range(telegram_bot._VC_MAX_CONCURRENT):
        await telegram_bot._vc_semaphore.acquire()
    monkeypatch.setattr(telegram_bot, "_vc_waiters", telegram_bot._VC_MAX_WAITERS)
    try:
        message = FakeMessage(f"/vc {ADDR}")
        await telegram_bot._run_vc_analysis(message, ADDR, test_mode=False, lang="fr")
        # Refus net + AUCUNE analyse lancée.
        assert any("charge maximale" in r for r in message.replies)
        core.assert_not_called()
    finally:
        for _ in range(telegram_bot._VC_MAX_CONCURRENT):
            telegram_bot._vc_semaphore.release()


@pytest.mark.asyncio
async def test_vc_runs_when_free(monkeypatch):
    """Chemin nominal : sémaphore libre -> l'analyse est bien appelée une fois."""
    core = AsyncMock()
    monkeypatch.setattr(telegram_bot, "_vc_analyze_and_reply", core)

    message = FakeMessage(f"/vc {ADDR}")
    await telegram_bot._run_vc_analysis(message, ADDR, test_mode=False, lang="fr")
    core.assert_awaited_once()
    # Sémaphore relâché après usage (aucun permis fuité).
    assert not telegram_bot._vc_semaphore.locked()
