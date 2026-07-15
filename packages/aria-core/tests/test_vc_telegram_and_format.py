"""Étape B — commande Telegram /vc + formatage de l'ordre court.

Aucun appel réseau : analyze_vc est mocké. Vérifie la restriction admin, la
validation d'adresse, et le formatage de l'ordre (proposition, jamais exécution).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot
from aria_core.skills.vc_analysis import VCResult, format_telegram_order
from aria_core.skills.vc_judge import JudgeVerdict

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


# ----------------------- format_telegram_order -----------------------


def test_format_buy_order_contains_actionable_fields():
    out = format_telegram_order(_result())
    assert "Ordre proposé" in out
    assert "BUY" in out
    assert "5.0% du capital" in out
    assert "Invalidation" in out
    assert "Tangem" in out  # disclaimer validation manuelle
    assert "automatique" in out.lower()


def test_format_buy_order_with_capital_shows_dollar_amount():
    out = format_telegram_order(_result(taille_pct=5.0), capital_usd=1500)
    assert "5.0% du capital" in out
    assert "$75" in out  # 5% de 1500
    assert "$1,500" in out


def test_format_buy_order_without_capital_no_dollar_amount():
    out = format_telegram_order(_result())
    assert "≈ $" not in out


def test_format_buy_order_ignores_invalid_capital():
    out = format_telegram_order(_result(), capital_usd=0)
    assert "≈ $" not in out


def test_format_watch_has_no_order_and_no_size():
    out = format_telegram_order(_result(recommandation="WATCH", taille_pct=0.0))
    assert "pas d'ordre" in out.lower()
    assert "du capital" not in out


def test_format_fallback_flags_llm_disabled():
    out = format_telegram_order(
        _result(recommandation="WATCH", taille_pct=0.0, potentiel=None, llm_used=False)
    )
    assert "n/a" in out
    assert "llm désactivé" in out.lower()


def test_format_always_has_manual_execution_disclaimer():
    for reco in ("BUY", "SELL", "WATCH", "AVOID"):
        out = format_telegram_order(_result(recommandation=reco))
        assert "manuelle" in out.lower()


# ----------------------- /vc handler -----------------------


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []
        self.reply_markups: list[object] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:
        self.replies.append(text)
        self.reply_markups.append(reply_markup)

    async def edit_reply_markup(self, reply_markup=None) -> None:
        pass


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, text: str, user_id: int = 42):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


class FakeContext:
    def __init__(self, args: list[str] | None = None):
        self.args = args or []


class FakeQuery:
    def __init__(self, data: str, message: FakeMessage, user_id: int = 42):
        self.data = data
        self.message = message
        self.from_user = FakeUser(user_id)

    async def answer(self) -> None:
        pass


class FakeCallbackUpdate:
    def __init__(self, query: FakeQuery, user_id: int = 42):
        self.callback_query = query
        self.effective_user = FakeUser(user_id)
        self.message = None


async def _pick_vc_lang(update: FakeUpdate, *, lang: str, address: str = ADDR) -> None:
    """Simule le clic sur le bouton de langue après /vc <adresse> (flux réel, non-test)."""
    cb_update = FakeCallbackUpdate(FakeQuery(f"vclang:{lang}:{address}", update.message))
    await telegram_bot._handle_callback(cb_update, FakeContext())


@pytest.mark.asyncio
async def test_vc_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    analyze = AsyncMock()
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", analyze)

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())

    analyze.assert_not_called()


@pytest.mark.asyncio
async def test_vc_rejects_invalid_address(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    analyze = AsyncMock()
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", analyze)

    update = FakeUpdate("/vc pas-une-adresse")
    await telegram_bot._handle_vc(update, FakeContext())

    analyze.assert_not_called()
    assert "invalide" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_vc_first_asks_language_before_analyzing(monkeypatch):
    """Hors mode test : /vc n'analyse PAS tout de suite — elle demande la langue
    d'abord (boutons), jamais l'adresse email, jamais de confirmation séparée."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    analyze = AsyncMock(return_value=_result())
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", analyze)

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())

    analyze.assert_not_called()  # rien ne tourne avant le choix de langue
    assert len(update.message.replies) == 1
    assert update.message.reply_markups[0] is not None  # boutons FR/EN présents


@pytest.mark.asyncio
async def test_vc_valid_runs_analysis_and_sends_order(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    analyze = AsyncMock(return_value=(_result(), SimpleNamespace(best_pair=None)))
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc_with_context", analyze)
    # Auto-log prédiction mocké (pas de DB dans ce test).
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", AsyncMock(return_value=42))
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", AsyncMock(return_value=1))
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", AsyncMock(return_value=46))
    # Email mocké (succès) — on vérifie juste le câblage, pas l'envoi réel.
    send_report = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", send_report)

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())
    await _pick_vc_lang(update, lang="fr")

    analyze.assert_awaited_once_with(ADDR, lang="fr")
    send_report.assert_awaited_once()
    # replies : "🌐 langue ?", "en cours", ordre, log prédiction, statut email
    assert len(update.message.replies) == 5
    assert "BUY" in update.message.replies[2]
    assert "Tangem" in update.message.replies[2]
    assert "#42" in update.message.replies[3]
    assert "email" in update.message.replies[4].lower()
    # Numérotation transmise à l'envoi (n° par token = compteur+1, série globale = total+1).
    _, kwargs = send_report.call_args
    assert kwargs["report_number"] == 2
    assert kwargs["series_number"] == 47
    assert kwargs["lang"] == "fr"


@pytest.mark.asyncio
async def test_vc_real_production_analysis_records_entry_price_and_pool(monkeypatch):
    """15/07 -- régression : le chemin /vc réel (hors mode test) doit renseigner
    entry_price/pool_address/network sur la prédiction, sinon vc_predictions.live_wallet()
    (le chiffre "wallet ARIA" public) exclut silencieusement toute vraie analyse
    opérateur, alors que le tirage hebdomadaire automatique (weekly_training.py), lui,
    les renseigne déjà -- asymétrie corrigée en branchant analyze_vc_with_context ici."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    fake_pair = SimpleNamespace(price_usd=0.00042, pair_address="0xrealpool")
    analyze = AsyncMock(return_value=(_result(), SimpleNamespace(best_pair=fake_pair)))
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc_with_context", analyze)
    record = AsyncMock(return_value=99)
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", record)
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", AsyncMock(return_value=(True, None)))

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())
    await _pick_vc_lang(update, lang="fr")

    record.assert_awaited_once()
    _, kwargs = record.call_args
    assert kwargs["entry_price"] == 0.00042
    assert kwargs["pool_address"] == "0xrealpool"
    assert kwargs["network"] == "base"
    assert kwargs["strategy"] == "vc"


@pytest.mark.asyncio
async def test_vc_real_production_analysis_handles_no_pair_gracefully(monkeypatch):
    """Sans paire DEX trouvée (best_pair=None), pas de crash -- entry_price/pool_address
    restent à leurs défauts honnêtes (None/''), jamais une donnée inventée."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    analyze = AsyncMock(return_value=(_result(), SimpleNamespace(best_pair=None)))
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc_with_context", analyze)
    record = AsyncMock(return_value=99)
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", record)
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", AsyncMock(return_value=(True, None)))

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())
    await _pick_vc_lang(update, lang="fr")

    record.assert_awaited_once()
    _, kwargs = record.call_args
    assert kwargs["entry_price"] is None
    assert kwargs["pool_address"] == ""


@pytest.mark.asyncio
async def test_vc_uses_capital_env_var_for_dollar_amount(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(
        "aria_core.skills.vc_analysis.analyze_vc_with_context",
        AsyncMock(return_value=(_result(taille_pct=5.0), SimpleNamespace(best_pair=None))),
    )
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", AsyncMock(return_value=1))
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", AsyncMock(return_value=(True, None)))
    monkeypatch.setenv("ARIA_CAPITAL_USD", "1500")

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())
    await _pick_vc_lang(update, lang="fr")

    assert "$75" in update.message.replies[2]  # 5% de $1500


# ----------------------- /vc MODE TEST admin -----------------------


def _test_mode_mocks(monkeypatch, reasoning: str = "Analyse détaillée : Techno solide, équipe doxxée, traction on-chain réelle."):
    """Câble tous les mocks + renvoie les mocks sensibles (email + track-record)."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    result = _result(rapport_detaille=reasoning)
    # Les deux modes (test ET normal, 15/07) passent par analyze_vc_with_context
    # (→ result + ctx) -- ctx.best_pair=None ici, ces tests ne portent pas sur le
    # suivi wallet (entry_price/pool_address).
    analyze = AsyncMock(return_value=(result, SimpleNamespace(best_pair=None)))
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc_with_context", analyze)
    # Proof engine mocké (testé à part) — verdict neutre pour ne rien casser.
    monkeypatch.setattr(
        "aria_core.skills.vc_judge.judge_analysis",
        AsyncMock(return_value=JudgeVerdict(
            verdict="solide", score=8, coherence_rr=True,
            recommandation_juge="garder", resume="Audit OK.", llm_used=False,
        )),
    )
    record = AsyncMock(return_value=42)
    count = AsyncMock(return_value=1)
    total = AsyncMock(return_value=46)
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", record)
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", count)
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", total)
    send_report = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", send_report)
    return analyze, send_report, record, count, total


@pytest.mark.asyncio
async def test_vc_test_mode_shows_reasoning_no_email_no_record(monkeypatch):
    analyze, send_report, record, count, total = _test_mode_mocks(monkeypatch)

    update = FakeUpdate(f"/vc {ADDR} test")
    await telegram_bot._handle_vc(update, FakeContext())

    # L'analyse tourne normalement...
    analyze.assert_awaited_once_with(ADDR, lang="fr")
    # ...mais AUCUN email et AUCUNE écriture/incrément track-record.
    send_report.assert_not_called()
    record.assert_not_called()
    count.assert_not_called()
    total.assert_not_called()

    joined = "\n".join(update.message.replies)
    assert "MODE TEST" in joined
    assert "non envoyé" in joined.lower()
    assert "non enregistré" in joined.lower()
    # Raisonnement complet affiché.
    assert "Techno solide" in joined
    # Ordre formaté toujours affiché.
    assert "BUY" in joined


@pytest.mark.asyncio
async def test_vc_test_mode_flag_is_case_insensitive(monkeypatch):
    _analyze, send_report, record, _count, _total = _test_mode_mocks(monkeypatch)

    update = FakeUpdate(f"/vc {ADDR} TEST")
    await telegram_bot._handle_vc(update, FakeContext())

    send_report.assert_not_called()
    record.assert_not_called()
    assert "MODE TEST" in "\n".join(update.message.replies)


@pytest.mark.asyncio
async def test_vc_test_mode_truncates_long_reasoning(monkeypatch):
    long_reasoning = "X" * 5000
    _analyze, _send, _record, _count, _total = _test_mode_mocks(monkeypatch, reasoning=long_reasoning)

    update = FakeUpdate(f"/vc {ADDR} test")
    await telegram_bot._handle_vc(update, FakeContext())

    joined = "\n".join(update.message.replies)
    assert "tronqué" in joined
    # Chaque message reste sous la limite Telegram gérée par _reply (4000).
    assert all(len(r) <= 4000 for r in update.message.replies)


@pytest.mark.asyncio
async def test_vc_address_alone_is_normal_mode(monkeypatch):
    """Une adresse seule (sans `test`) = mode normal : email + enregistrement appelés."""
    _analyze, send_report, record, _count, _total = _test_mode_mocks(monkeypatch)

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())
    await _pick_vc_lang(update, lang="fr")

    send_report.assert_awaited_once()
    record.assert_awaited_once()
    joined = "\n".join(update.message.replies)
    assert "MODE TEST" not in joined


@pytest.mark.asyncio
async def test_vc_test_mode_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    analyze = AsyncMock()
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc_with_context", analyze)
    send_report = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", send_report)

    update = FakeUpdate(f"/vc {ADDR} test")
    await telegram_bot._handle_vc(update, FakeContext())

    analyze.assert_not_called()
    send_report.assert_not_called()


@pytest.mark.asyncio
async def test_vc_test_mode_runs_judge_and_shows_verdict(monkeypatch):
    """En mode test, le proof engine (juge) audite l'analyse et son verdict s'affiche."""
    _analyze, _send, _record, _count, _total = _test_mode_mocks(monkeypatch)
    verdict = JudgeVerdict(
        verdict="fragile", score=4, coherence_rr=False, recommandation_juge="ajuster",
        resume="Trous factuels détectés.", points_faibles=["Équipe non vérifiable."],
        claims_non_etayes=["« équipe » non corroborée par un fait on-chain."], llm_used=True,
    )
    judge = AsyncMock(return_value=verdict)
    monkeypatch.setattr("aria_core.skills.vc_judge.judge_analysis", judge)

    update = FakeUpdate(f"/vc {ADDR} test")
    await telegram_bot._handle_vc(update, FakeContext())

    # Le juge est appelé avec le VCResult (1er arg) et le ctx (2e arg).
    judge.assert_awaited_once()
    args, _ = judge.call_args
    assert isinstance(args[0], VCResult)
    joined = "\n".join(update.message.replies)
    assert "proof engine" in joined.lower()
    assert "fragile" in joined
    assert "Trous factuels" in joined
    assert "non étayées" in joined


@pytest.mark.asyncio
async def test_vc_reports_email_failure_without_crashing(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", AsyncMock(return_value=_result()))
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", AsyncMock(return_value=7))
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", AsyncMock(return_value=6))
    # Email en échec (SMTP non configuré) — le handler ne doit pas crasher.
    monkeypatch.setattr(
        "aria_core.skills.vc_delivery.send_vc_report",
        AsyncMock(return_value=(False, "SMTP non configuré")),
    )

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())
    await _pick_vc_lang(update, lang="fr")

    assert len(update.message.replies) == 5
    assert "non envoyé" in update.message.replies[4].lower()
