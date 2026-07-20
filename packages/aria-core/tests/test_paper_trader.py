"""Portefeuille papier 1 M$ (simulation) — moteur déterministe, DB temporaire isolée."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from aria_core import momentum_funnel_log
from aria_core import paper_trader as pt
from aria_core.skills import market_sentiment

# 20/07 -- capturée à l'import, AVANT tout monkeypatch de session : permet aux tests
# dédiés à la re-vérification de fraîcheur (cf. plus bas) de restaurer le VRAI
# comportement pour eux-mêmes, malgré le bypass autouse ci-dessous.
_REAL_EXECUTION_RR_STILL_VALID = pt._execution_rr_still_valid


@pytest.fixture(autouse=True)
def _bypass_price_staleness_check(monkeypatch):
    """20/07 -- ``run_paper_cycle`` re-vérifie désormais le R/R au prix frais juste
    avant ``open_position`` (revue croisée Gemini, cf. ``_execution_rr_still_valid``)
    via un appel à ``price_lookup`` -- ce fichier teste le sizing/le pipeline en
    amont, pas ce garde spécifique (couvert par ses propres tests dédiés plus bas).
    Sans ce bypass, TOUT test qui atteint un BUY sans mocker explicitement
    ``price_lookup`` verrait le second appel (véritable, réseau) échouer en sandbox
    -> R/R frais impossible à calculer -> position jamais ouverte, un faux négatif,
    pas un vrai bug."""
    monkeypatch.setattr(pt, "_execution_rr_still_valid", lambda *_a, **_kw: True)


async def _backdate_pending_since(contract: str, seconds: float) -> None:
    """Recule ``pending_high_water_since`` de ``seconds`` -- simule l'écoulement du
    temps pour la confirmation temporelle du plus-haut (20/07) sans attendre pour de
    vrai dans les tests."""
    import aiosqlite

    async with aiosqlite.connect(pt.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT pending_high_water_since FROM paper_position WHERE contract = ?", (contract,)
            )
        ).fetchone()
        assert row and row[0], "aucune candidature pending_high_water_since à reculer"
        backdated = datetime.fromisoformat(row[0]) - timedelta(seconds=seconds)
        await db.execute(
            "UPDATE paper_position SET pending_high_water_since = ? WHERE contract = ?",
            (backdated.isoformat(), contract),
        )
        await db.commit()

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40
D = "0x" + "d" * 40
E = "0x" + "e" * 40
F = "0x" + "f" * 40


async def _no_depeg() -> float | None:
    """Fake ``depeg_check`` -- pas de dépeg, aucun appel réseau (#187)."""
    return 0.0


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    # #196 -- pytest-asyncio donne une boucle événementielle FRAÎCHE à chaque test ;
    # _run_cycle_lock est un singleton créé une seule fois à l'import du module
    # (correct en production, un seul process/une seule boucle pendant toute sa vie),
    # mais réutiliser le MÊME objet Lock d'un test à l'autre le lierait à une boucle déjà
    # fermée -> RuntimeError. Un Lock frais par test, jamais un changement de comportement
    # en production.
    monkeypatch.setattr(pt, "_run_cycle_lock", asyncio.Lock())
    # 19/07 -- run_paper_cycle persiste désormais le funnel via momentum_funnel_log.py,
    # dont le DB_PATH est calculé UNE FOIS à l'import (même piège que momentum_blacklist.py,
    # cf. test_momentum_blacklist.py) : sans cette isolation, tous les tests de ce fichier
    # écriraient silencieusement dans le même chemin figé au premier import du module.
    monkeypatch.setattr(momentum_funnel_log, "DB_PATH", str(tmp_path / "momentum_funnel.db"))
    # 20/07 -- Regime Switch : run_paper_cycle appelle désormais market_sentiment.
    # resolve_meta_regime() une fois par cycle -- MÊME piège que momentum_funnel_log
    # ci-dessus (DB_PATH calculé une seule fois à l'import du module), sans cette
    # isolation tous les tests de ce fichier liraient/écriraient silencieusement au
    # même chemin figé au premier import, potentiellement partagé entre tests.
    monkeypatch.setattr(market_sentiment, "DB_PATH", str(tmp_path / "market_sentiment.db"))
    return tmp_path


@pytest.mark.asyncio
async def test_reset_and_starting(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    assert await pt.starting_capital() == 1_000_000.0
    assert await pt.cash_available() == 1_000_000.0


@pytest.mark.asyncio
async def test_open_deducts_cash_and_no_double(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 2.0, target_price=3.0, invalidation_price=1.5, alloc_usd=50_000)
    assert pos is not None
    assert pos["qty"] == 25_000  # 50000 / 2
    assert await pt.cash_available() == 950_000.0
    assert await pt.open_position(A, "AAA", 2.0, alloc_usd=10_000) is None  # déjà ouverte


@pytest.mark.asyncio
async def test_close_profit(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)
    closed = await pt.close_position(A, 4.0, reason="cible")
    assert closed["pnl_usd"] == 50_000
    assert round(closed["pnl_pct"], 1) == 100.0
    assert await pt.cash_available() == 1_050_000.0
    s = await pt.portfolio_summary()
    assert round(s["equity"]) == 1_050_000
    assert round(s["return_pct"], 1) == 5.0
    assert s["win_rate"] == 100.0


@pytest.mark.asyncio
async def test_close_loss(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(B, "BBB", 1.0, alloc_usd=100_000)
    closed = await pt.close_position(B, 0.5, reason="invalidation")
    assert closed["pnl_usd"] == -50_000
    assert await pt.cash_available() == 950_000.0


@pytest.mark.asyncio
async def test_summary_marks_to_market(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(C, "CCC", 1.0, alloc_usd=100_000)

    async def price_lookup(contract):
        return 1.5

    s = await pt.portfolio_summary(price_lookup=price_lookup)
    assert round(s["equity"]) == 1_050_000  # cash 900k + 100k*1.5
    assert round(s["unrealized_pnl"]) == 50_000


@pytest.mark.asyncio
async def test_run_cycle_opens_then_stages_take_profit(tmp_db):
    """Remplace l'ancien tout-ou-rien à la cible : une hausse au-delà du 1er palier
    déclenche une prise de profit PARTIELLE, la position reste ouverte. 19/07 -- le
    1er palier est désormais ancré sur le target technique (2.0 pour une entrée à
    1.0 -> +100%, cf. ``_effective_tp_stages``), plus le fixe +50% historique."""
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {"action": "BUY", "symbol": "DDD", "price": 1.0, "target": 2.0, "invalidation": 0.5}

    prices = {"v": 1.0}

    async def price_lookup(contract):
        return prices["v"]

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    act = await pt.run_paper_cycle(
        candidates=[D], analyzer=analyzer, price_lookup=price_lookup, notifier=notifier, depeg_check=_no_depeg,
    )
    assert len(act["opened"]) == 1
    assert await pt.has_open(D)
    assert any("ACHAT FICTIF" in a for a in alerts)

    prices["v"] = 2.0  # +100 % -> franchit le 1er palier, ancré sur target=2.0 (19/07)
    act2 = await pt.run_paper_cycle(
        candidates=[D], analyzer=analyzer, price_lookup=price_lookup, notifier=notifier, depeg_check=_no_depeg,
    )
    assert act2["closed"] == []
    assert len(act2["partial"]) == 1
    assert await pt.has_open(D)  # reste ouverte, seulement réduite
    assert any("PRISE DE PROFIT PARTIELLE FICTIVE" in a for a in alerts)

    pos = await pt._get_open(D)
    assert pos["tp_stage_hit"] == 1
    # #186 -- invalidation=0.5 sur entrée=1.0 -> risque 50 % de l'alloc flat (ALLOC_PCT *
    # capital = 50 000 $), plafonné par size_position_by_risk à RISK_CAP_PCT (2 %) * capital
    # = 20 000 $ / 0.5 = 40 000 $ -> qty initiale 40 000 ; 1/3 vendu au palier 1.
    assert round(pos["qty"]) == round(40_000 * (2.0 / 3.0))


@pytest.mark.asyncio
async def test_run_paper_cycle_reports_momentum_funnel_by_reason_code(tmp_db):
    """Mandat #192 (16/07) -- ``run_paper_cycle`` doit agréger POURQUOI chaque
    candidat évalué n'a pas mené à un achat. Sans ça, une panne prolongée du seul
    garde-fou dur (GoPlus, aucun repli, cf. ``momentum_entry.py``) produit exactement
    le même symptôme observable (zéro nouvelle position) qu'un marché réellement sans
    candidat valable -- indiscernables sans lire les logs applicatifs un par un."""
    await pt.reset_portfolio(1_000_000.0)

    A_ = "0x" + "1" * 40
    B_ = "0x" + "2" * 40
    C_ = "0x" + "3" * 40
    E_ = "0x" + "5" * 40  # exception côté analyzer
    F_ = "0x" + "6" * 40  # HOLD sans hold_reason (ex. pilote VC-thesis historique)

    async def analyzer(contract):
        if contract == A_:
            return {"action": "HOLD", "hold_reason": "honeypot_unavailable"}
        if contract == B_:
            return {"action": "HOLD", "hold_reason": "honeypot_unavailable"}
        if contract == C_:
            return None  # pas de paire liquide avec un prix exploitable
        if contract == E_:
            raise RuntimeError("boom")
        if contract == F_:
            return {"action": "HOLD"}  # aucun hold_reason fourni
        return None

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(
        candidates=[A_, B_, C_, E_, F_], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert act["momentum_funnel"] == {
        "honeypot_unavailable": 2,
        "no_price_data": 1,
        "analyzer_error": 1,
        "unspecified": 1,
    }
    assert act["opened"] == []


@pytest.mark.asyncio
async def test_run_paper_cycle_persists_funnel_to_momentum_funnel_log(tmp_db):
    """19/07 -- le funnel calculé par run_paper_cycle doit aussi être PERSISTÉ (pas
    seulement retourné dans ``actions`` puis loggué et perdu, cf. commentaire dans
    paper_trader.py) -- réponse à la proposition d'ARIA de cumuler ce compteur dans
    le temps plutôt que de le voir disparaître à chaque cycle."""
    await pt.reset_portfolio(1_000_000.0)

    A_ = "0x" + "1" * 40

    async def analyzer(contract):
        return {"action": "HOLD", "hold_reason": "no_entry_signal"}

    async def price_lookup(contract):
        return 1.0

    await pt.run_paper_cycle(
        candidates=[A_], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    summary = await momentum_funnel_log.summarize_since(48)
    assert summary == {"no_entry_signal": 1}


@pytest.mark.asyncio
async def test_run_paper_cycle_omits_funnel_key_when_nothing_evaluated(tmp_db):
    """Pas de bruit inutile dans ``actions`` quand il n'y a rien à évaluer ce tour."""
    await pt.reset_portfolio(1_000_000.0)

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, depeg_check=_no_depeg)
    assert "momentum_funnel" not in act


# ── garde-fou de re-entrée (17/07, perte réelle BRIAN -- "une position doit être achetée
# 1 seule fois sauf si cas extrême de très très bons signaux") ─────────────────────────

@pytest.mark.asyncio
async def test_reentry_allowed_after_prior_close_on_normal_signal(tmp_db):
    """19/07 -- assoupli (décision opérateur explicite) : un contrat déjà clôturé une
    fois se rachète sur un signal simplement positif, même barre qu'une première
    entrée. Seule protection restante : jamais deux positions SIMULTANÉES (has_open)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)
    await pt.close_position(A, 0.8, reason="stop suiveur")

    async def normal_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 0.9, "rr": 1.6, "align_score": 1}

    async def price_lookup(contract):
        return 0.9

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=normal_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert len(act["opened"]) == 1
    assert await pt.has_open(A)
    reopened = await pt._get_open(A)
    assert "re-entrée" in (reopened.get("thesis") or "")


@pytest.mark.asyncio
async def test_reentry_allowed_when_analyzer_omits_signal_strength(tmp_db):
    """19/07 -- un analyzer qui ne fournit ni "rr" ni "align_score" (ex. l'ancien
    pilote VC-thesis) n'est plus bloqué : la barre de re-entrée est désormais
    identique à celle d'une première entrée, qui ne dépend pas de ces deux champs."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)
    await pt.close_position(A, 1.2, reason="cible")

    async def these_only_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 1.3, "these": "nouvelle thèse VC"}

    async def price_lookup(contract):
        return 1.3

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=these_only_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert len(act["opened"]) == 1


@pytest.mark.asyncio
async def test_reentry_still_blocked_while_position_currently_open(tmp_db):
    """Non-régression : la SEULE protection restante (has_open) empêche toujours
    deux positions simultanées sur le même contrat -- ce garde-fou n'a jamais
    dépendu du gate de re-entrée assoupli ci-dessus."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)

    async def normal_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 1.1, "rr": 1.6, "align_score": 1}

    async def price_lookup(contract):
        return 1.1

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=normal_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert act["opened"] == []


@pytest.mark.asyncio
async def test_first_entry_unaffected_by_reentry_gate(tmp_db):
    """Non-régression : un contrat JAMAIS clôturé auparavant s'ouvre normalement sur
    un signal simplement positif -- le garde-fou ne concerne que les re-entrées."""
    await pt.reset_portfolio(1_000_000.0)

    async def normal_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 0.9, "rr": 1.6, "align_score": 1}

    async def price_lookup(contract):
        return 0.9

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=normal_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert len(act["opened"]) == 1


@pytest.mark.asyncio
async def test_all_tp_stages_hit_in_one_jump_closes_fully(tmp_db):
    """Un bond de prix qui dépasse TOUS les paliers d'un coup ne laisse jamais une
    position résiduelle ouverte -- le dernier palier clôture ce qui reste."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000)

    async def price_lookup(contract):
        return 3.5  # +250 % : dépasse les 3 paliers (+50/+100/+200 %) d'un coup

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["partial"]) == 2   # paliers 1 et 2 : prises de profit partielles
    assert len(act["closed"]) == 1    # palier 3 (dernier) : clôture du reliquat
    assert not await pt.has_open(D)
    assert act["closed"][0]["close_reason"] == "palier 3/3 (clôture)"
    # 17/07 -- justification chiffrée présente sur chaque palier (partiel ET clôture finale)
    assert "Palier de profit 1/3" in act["partial"][0]["close_notes"]
    assert "+50%" in act["partial"][0]["close_notes"]
    assert "Dernier palier de profit 3/3" in act["closed"][0]["close_notes"]
    assert "+200%" in act["closed"][0]["close_notes"]


@pytest.mark.asyncio
async def test_stop_before_any_rise_uses_original_invalidation_label(tmp_db):
    """Avant toute hausse significative, le stop suiveur (15 % sous le plus haut) peut
    rester EN DESSOUS de l'invalidation d'origine -- c'est alors l'invalidation qui
    déclenche et doit être nommée comme telle, pas « stop suiveur »."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.9, alloc_usd=90_000)

    async def price_lookup(contract):
        return 0.89  # sous l'invalidation (0.9), au-dessus du stop suiveur pur (0.85)

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "invalidation"
    assert not await pt.has_open(D)
    # 17/07 -- la note doit nommer le vrai déclencheur ("invalidation"), pas "stop suiveur"
    assert "Invalidation technique atteinte" in act["closed"][0]["close_notes"]


@pytest.mark.asyncio
async def test_trailing_stop_tightens_then_closes_remainder(tmp_db):
    """Le stop suiveur monte avec le plus haut atteint et ne se relâche jamais : après une
    prise de profit partielle, un repli qui reste AU-DESSUS de l'invalidation d'origine
    mais SOUS le stop suiveur remonté doit quand même clôturer le reliquat."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000)

    prices = {"v": 1.5}  # cycle 1 : +50 % -> palier 1, prise de profit partielle

    async def price_lookup(contract):
        return prices["v"]

    act1 = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act1["partial"]) == 1
    assert await pt.has_open(D)

    prices["v"] = 2.5  # nouveau plus haut, franchit aussi le palier 2
    # 20/07 round 7 -- confirmation TEMPORELLE (_advance_high_water) : ce nouveau pic
    # ouvre une candidature mais ne ratche pas high_water tant qu'il n'a pas tenu
    # HIGH_WATER_CONFIRMATION_SECONDS (le palier de profit, lui, réagit toujours
    # instantanément au prix RÉEL -- gain_pct n'est jamais affecté par cette
    # confirmation, qui ne concerne que le stop suiveur).
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert await pt.has_open(D)
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(1.0)  # pas encore confirmé
    assert pos["pending_high_water"] == pytest.approx(2.5)
    assert pos["tp_stage_hit"] == 2  # le palier de profit, lui, a bien réagi au prix réel

    await _backdate_pending_since(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)  # confirme
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(2.5)
    assert pos["pending_high_water"] is None

    prices["v"] = 2.0  # repli sous le stop suiveur (2.5 * 0.85 = 2.125) mais largement
    # au-dessus de l'invalidation d'origine (0.5) -> c'est bien le stop suiveur
    act3 = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act3["closed"]) == 1
    assert act3["closed"][0]["close_reason"] == "stop suiveur"
    assert not await pt.has_open(D)
    # 17/07 -- la note cite le vrai plus haut atteint (2.5), pas l'invalidation d'origine
    assert "Stop suiveur déclenché" in act3["closed"][0]["close_notes"]
    assert "2.5" in act3["closed"][0]["close_notes"]


# ── stop suiveur adaptatif à la volatilité (19/07, revue croisée Gemini) ────────────


class TestEffectiveTrailPct:
    def test_none_falls_back_to_fixed_default(self):
        assert pt._effective_trail_pct(None) == pt.TRAIL_STOP_PCT

    def test_zero_or_negative_falls_back_to_fixed_default(self):
        assert pt._effective_trail_pct(0.0) == pt.TRAIL_STOP_PCT
        assert pt._effective_trail_pct(-0.05) == pt.TRAIL_STOP_PCT

    def test_mid_range_atr_multiplied_by_2_5(self):
        # 10 % d'ATR -> 25 %, dans les bornes [5 %, 40 %], aucun clamp.
        assert pt._effective_trail_pct(0.10) == pytest.approx(0.25)

    def test_low_atr_clamped_to_floor(self):
        # 1 % d'ATR * 2.5 = 2.5 %, sous le plancher 5 % -> clampé.
        assert pt._effective_trail_pct(0.01) == pt.MIN_ATR_TRAIL_PCT

    def test_high_atr_clamped_to_ceiling(self):
        # 50 % d'ATR * 2.5 = 125 %, largement au-dessus du plafond 40 % -> clampé.
        assert pt._effective_trail_pct(0.50) == pt.MAX_ATR_TRAIL_PCT


# ── TP1 ancré sur le target technique (19/07, revue croisée Gemini round 5) ─────────


class TestEffectiveTpStages:
    def test_none_target_falls_back_to_fixed(self):
        assert pt._effective_tp_stages(None, 1.0) == pt.TP_STAGES

    def test_none_entry_falls_back_to_fixed(self):
        assert pt._effective_tp_stages(1.5, None) == pt.TP_STAGES

    def test_target_below_entry_falls_back_to_fixed(self):
        assert pt._effective_tp_stages(0.9, 1.0) == pt.TP_STAGES

    def test_target_equal_entry_falls_back_to_fixed(self):
        assert pt._effective_tp_stages(1.0, 1.0) == pt.TP_STAGES

    def test_target_above_entry_anchors_stage1_and_scales_the_rest(self):
        # 19/07 round 6 (Gemini) -- Cible +20 % -> TP1 = 0.20 ; TP2/TP3 sont désormais
        # des MULTIPLES (2x/3x) de cette distance, pas des crans absolus fixes.
        stages = pt._effective_tp_stages(1.2, 1.0)
        assert stages == pytest.approx((0.2, 0.4, 0.6))

    def test_large_target_gain_keeps_strictly_increasing_stages(self):
        """Un target technique généreux (retracement profond, remontée vers le haut
        du range) peut impliquer un gain > aux paliers fixes historiques -- la
        séquence reste strictement croissante par construction, jamais un palier
        2/3 qui retomberait en dessous de TP1."""
        stages = pt._effective_tp_stages(4.0, 1.0)  # +300 % de cible
        assert stages == pytest.approx((3.0, 6.0, 9.0))
        assert stages[0] < stages[1] < stages[2]

    def test_small_target_gain_scales_stages_proportionally_smaller(self):
        """19/07 round 6 (Gemini) -- un setup SERRÉ (TP1 proche) doit obtenir des
        paliers 2/3 proportionnellement proches aussi, jamais tirés vers un cran
        absolu lointain qui laisserait filer un profit déjà acquis."""
        stages = pt._effective_tp_stages(1.05, 1.0)  # cible +5 % seulement
        assert stages == pytest.approx((0.05, 0.10, 0.15))


# ── confirmation temporelle du plus-haut (20/07, revue croisée Gemini round 7,
#    remplace le clamp de vitesse du round 6) ─────────────────────────────────────────


class TestAdvanceHighWater:
    def test_price_at_or_below_confirmed_high_clears_any_pending(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = pt._advance_high_water(2.0, 2.5, "2026-01-01T00:00:00+00:00", 1.9, now)
        assert result == (2.0, None, None)

    def test_new_high_opens_a_pending_candidacy_without_ratcheting(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        confirmed, pending, since = pt._advance_high_water(1.0, None, None, 1.5, now)
        assert confirmed == 1.0  # pas encore ratché -- seule une candidature s'ouvre
        assert pending == pytest.approx(1.5)
        assert since == now.isoformat()

    def test_pending_candidacy_tracks_the_running_max(self):
        since = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        confirmed, pending, kept_since = pt._advance_high_water(1.0, 1.3, since.isoformat(), 1.5, since)
        assert confirmed == 1.0
        assert pending == pytest.approx(1.5)  # le nouveau pic remplace l'ancien candidat
        assert kept_since == since.isoformat()  # l'horodatage de départ ne bouge pas

    def test_candidacy_confirmed_after_the_delay_ratchets_the_real_peak(self):
        since = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now = since + timedelta(seconds=pt.HIGH_WATER_CONFIRMATION_SECONDS)
        # le PIC réel de toute la fenêtre (1.6) est ratché, pas juste le prix de cet
        # instant précis (1.55, un léger repli en cours de confirmation).
        confirmed, pending, kept_since = pt._advance_high_water(1.0, 1.6, since.isoformat(), 1.55, now)
        assert confirmed == pytest.approx(1.6)
        assert pending is None
        assert kept_since is None

    def test_candidacy_not_yet_confirmed_stays_pending(self):
        since = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now = since + timedelta(seconds=pt.HIGH_WATER_CONFIRMATION_SECONDS - 1)
        confirmed, pending, kept_since = pt._advance_high_water(1.0, 1.6, since.isoformat(), 1.6, now)
        assert confirmed == 1.0
        assert pending == pytest.approx(1.6)
        assert kept_since == since.isoformat()

    def test_partial_pullback_above_confirmed_high_keeps_candidacy_and_its_peak(self):
        """Reproduit le scénario Gemini : mèche à +60%, repli à +10% -- tant que le
        repli reste AU-DESSUS du dernier plus-haut confirmé, la candidature n'est pas
        abandonnée (le chrono continue), et son pic RÉEL observé (1.6) n'est jamais
        écrasé par le repli (1.1)."""
        since = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        confirmed1, pending1, since1 = pt._advance_high_water(1.0, None, None, 1.6, since)
        assert confirmed1 == 1.0 and pending1 == pytest.approx(1.6)

        now2 = since + timedelta(seconds=10)
        confirmed2, pending2, since2 = pt._advance_high_water(confirmed1, pending1, since1, 1.1, now2)
        assert confirmed2 == 1.0  # toujours pas confirmé -- 10s << 75s
        assert pending2 == pytest.approx(1.6)  # le max observé n'a pas bougé
        assert since2 == since.isoformat()  # le chrono n'a pas été relancé

    def test_price_dropping_back_to_or_below_confirmed_discards_the_candidacy(self):
        since = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now2 = since + timedelta(seconds=10)
        confirmed, pending, kept_since = pt._advance_high_water(1.0, 1.6, since.isoformat(), 0.95, now2)
        assert confirmed == 1.0
        assert pending is None
        assert kept_since is None

    def test_corrupted_timestamp_restarts_a_fresh_candidacy(self):
        """Un horodatage illisible ne doit jamais planter ni bloquer -- repart d'une
        candidature fraîche plutôt que de faire confiance à une durée incalculable."""
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        confirmed, pending, since = pt._advance_high_water(1.0, 1.5, "pas-un-horodatage", 1.6, now)
        assert confirmed == 1.0
        assert pending == pytest.approx(1.6)
        assert since == now.isoformat()

    def test_amplitude_is_never_capped_only_duration_is_checked(self):
        """20/07 (Gemini round 7) : contrairement à l'ancien clamp de vitesse, un
        mouvement RÉEL et confirmé de n'importe quelle ampleur est ratché intégralement
        -- jamais de convergence progressive sur plusieurs cycles."""
        since = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now = since + timedelta(seconds=pt.HIGH_WATER_CONFIRMATION_SECONDS)
        confirmed, _pending, _since = pt._advance_high_water(1.0, 10.0, since.isoformat(), 10.0, now)
        assert confirmed == pytest.approx(10.0)  # +900%, ratché d'un coup


@pytest.mark.asyncio
async def test_wick_never_ratchets_the_confirmed_high_water(tmp_db):
    """20/07 (Gemini round 7) : une mèche isolée (+60% en un seul cycle, décrite comme
    un bot d'arbitrage/une erreur de slippage sur un pool peu liquide) qui se résorbe
    AVANT la fenêtre de confirmation ne doit jamais avoir touché le plus-haut confirmé
    -- le stop suiveur reste donc calé sur l'entrée pendant toute la mèche, jamais sur
    un prix qui n'a existé qu'un instant."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000)

    prices = {"v": 1.6}  # cycle 1 : mèche isolée, +60 % en un seul cycle

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(1.0)  # jamais touché par la mèche
    assert pos["pending_high_water"] == pytest.approx(1.6)  # candidature ouverte, pas confirmée

    prices["v"] = 1.05  # cycle 2 (quelques secondes plus tard) : la mèche se résorbe
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    assert await pt.has_open(D)  # le stop, calé sur l'entrée, n'a jamais été menacé
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_sustained_move_ratchets_the_full_peak_once_confirmed(tmp_db):
    """20/07 (Gemini round 7) : un mouvement RÉEL qui tient toute la fenêtre de
    confirmation est ratché à son pic RÉEL d'un seul coup une fois confirmé -- jamais
    une convergence progressive sur plusieurs cycles (contrairement à l'ancien clamp de
    vitesse du round 6)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000)

    # +150 % -- massif mais sous le dernier palier de profit (+200%, TP_STAGES[2]) pour
    # que la position reste ouverte (2 prises de profit partielles, pas une clôture
    # totale) et permette d'observer la confirmation du plus-haut.
    prices = {"v": 2.5}

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(1.0)  # pas encore confirmé

    await _backdate_pending_since(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(2.5)  # le pic RÉEL, d'un seul coup


# ── Breakeven Hard Floor (20/07, revue croisée Gemini "Piste B") ────────────────────

def test_breakeven_floor_threshold_half_of_tp1_distance_when_above_floor():
    """target_price loin -> 50% de la distance entrée->TP1, sans plafonnement."""
    threshold = pt._breakeven_floor_threshold(1.4, 1.0)  # TP1 = +40%
    assert threshold == pytest.approx(0.20)  # 50% * 40%


def test_breakeven_floor_threshold_clamped_to_absolute_floor_when_tp1_close():
    """target_price proche (TP1 = +10%) -> 50% donnerait +5%, trop serré (bruit de
    marché normal) -> le plancher absolu de bon sens (8%) prend le relais."""
    threshold = pt._breakeven_floor_threshold(1.1, 1.0)  # TP1 = +10%
    assert threshold == pytest.approx(pt.BREAKEVEN_FLOOR_MIN_PCT)


def test_breakeven_floor_threshold_falls_back_to_fixed_tp_stage_without_target():
    """Aucun target_price connu (ex. ancien pilote VC-thesis) -> repli sur
    TP_STAGES[0] (+50% fixe) comme _effective_tp_stages -> seuil flash +25%."""
    threshold = pt._breakeven_floor_threshold(None, 1.0)
    assert threshold == pytest.approx(0.5 * pt.TP_STAGES[0])


def test_breakeven_floor_threshold_none_without_entry_price():
    assert pt._breakeven_floor_threshold(1.4, None) is None
    assert pt._breakeven_floor_threshold(1.4, 0.0) is None


@pytest.mark.asyncio
async def test_breakeven_floor_locks_on_flash_touch_and_protects_against_deeper_crash(tmp_db):
    """Cas central (Gemini, Point 2, Piste B) : un pump-puis-dump rapide qui retombe
    AVANT la confirmation temporelle du plus-haut (75s) n'aurait laissé AUCUNE trace
    dans high_water_price (cf. test_wick_never_ratchets_the_confirmed_high_water) --
    le stop suiveur serait resté calé sur l'entrée -0.85 (-15%). Le point mort
    verrouillé protège malgré tout : une fois le seuil flash touché, même un seul
    cycle, un crash qui suit ne peut plus faire perdre plus que ~le prix d'entrée."""
    await pt.reset_portfolio(1_000_000.0)
    # TP1 = +40% -> seuil flash = 50%*40% = +20% (prix 1.20), au-dessus du plancher 8%.
    await pt.open_position(D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000)

    prices = {"v": 1.25}  # touche le seuil flash (+20%), mèche isolée -- jamais confirmée

    async def price_lookup(contract):
        return prices["v"]

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []  # 1.25 > tout stop actif ce cycle, position reste ouverte
    pos = await pt._get_open(D)
    assert pos["breakeven_locked"] == 1
    assert pos["high_water_price"] == pytest.approx(1.0)  # jamais confirmé (comme la mèche)

    prices["v"] = 0.95  # crash -- SOUS le point mort (1.0), au-dessus de l'ancien stop (0.85)
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    closed = act["closed"][0]
    assert closed["close_reason"] == "breakeven hard floor"
    assert closed["exit_price"] == pytest.approx(0.95)
    assert "Point mort verrouillé" in closed["close_notes"]
    assert "+20%" in closed["close_notes"]


@pytest.mark.asyncio
async def test_breakeven_floor_never_triggers_when_price_never_touches_flash_threshold(tmp_db):
    """Non-régression : une position dont le prix ne dépasse jamais le seuil flash se
    comporte exactement comme avant ce correctif (stop suiveur classique)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000)

    prices = {"v": 1.05}  # +5% -- sous le seuil flash (+20%)

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["breakeven_locked"] == 0

    prices["v"] = 0.86  # au-dessus du stop suiveur fixe (0.85), position doit rester ouverte
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    assert await pt.has_open(D)


@pytest.mark.asyncio
async def test_breakeven_floor_does_not_override_a_higher_trailing_stop(tmp_db):
    """Le point mort est un PLANCHER, jamais un plafond : une fois le stop suiveur
    naturellement remonté AU-DESSUS du point mort (rally confirmé et soutenu), il doit
    continuer de gouverner la sortie -- jamais une régression vers un stop plus bas.
    Prix choisi sous TP1 (+40%) pour ne pas interférer avec la prise de profit par
    tiers (même piège déjà rencontré et corrigé sur d'autres tests de ce fichier)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000)

    prices = {"v": 1.30}  # +30% -- au-dessus du seuil flash (+20%), sous TP1 (+40%)

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["breakeven_locked"] == 1

    await _backdate_pending_since(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(1.30)  # confirmé

    # Stop suiveur = 1.30*0.85 = 1.105, déjà au-dessus du point mort (1.0) -- un repli
    # à 1.10 doit déclencher le STOP SUIVEUR, jamais le point mort verrouillé.
    prices["v"] = 1.10
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "stop suiveur"


@pytest.mark.asyncio
async def test_breakeven_floor_stays_locked_across_multiple_cycles(tmp_db):
    """Irrévocabilité : une fois verrouillé, le point mort reste actif plusieurs
    cycles plus tard même si le prix reste au-dessus entre-temps (jamais réinitialisé
    par un cycle qui ne le retouche pas)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000)

    prices = {"v": 1.25}

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["breakeven_locked"] == 1

    # Plusieurs cycles où le prix reste au-dessus du point mort -- ne doit jamais
    # réinitialiser le verrou (aucune fonction ne remet breakeven_locked à 0).
    for v in (1.10, 1.15, 1.05):
        prices["v"] = v
        act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
        assert act["closed"] == []
        pos = await pt._get_open(D)
        assert pos["breakeven_locked"] == 1

    prices["v"] = 0.97  # enfin sous le point mort, plusieurs cycles après le verrouillage
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "breakeven hard floor"


# ── Regime Switch dynamique (20/07, revue croisée Gemini, feu vert opérateur
#    explicite "200k mais à garder à l'œil") ────────────────────────────────────────

class TestApplyRegimeToTpStages:
    """_apply_regime_to_tp_stages -- fonction pure, aucun DB requis."""

    def test_fear_truncates_to_two_stages(self):
        assert pt._apply_regime_to_tp_stages((0.2, 0.4, 0.6), "peur") == (0.2, 0.4)

    def test_euphoria_neutralizes_third_stage(self):
        stages = pt._apply_regime_to_tp_stages((0.2, 0.4, 0.6), "euphorie")
        assert stages[:2] == (0.2, 0.4)
        assert stages[2] == float("inf")

    def test_neutral_or_unknown_or_none_unchanged(self):
        base = (0.2, 0.4, 0.6)
        for regime in ("neutre", None, "regime_inconnu"):
            assert pt._apply_regime_to_tp_stages(base, regime) == base

    def test_short_tuple_never_indexed_out_of_range(self):
        """Défensif : TP_STAGES/_effective_tp_stages fournissent toujours 3 éléments
        en pratique, mais cette fonction ne doit jamais planter si ce n'est pas le cas."""
        assert pt._apply_regime_to_tp_stages((0.5,), "peur") == (0.5,)
        assert pt._apply_regime_to_tp_stages((), "euphorie") == ()


@pytest.mark.asyncio
async def test_run_cycle_fear_regime_halves_new_entry_allocation(tmp_db, monkeypatch):
    """Feu vert opérateur explicite (20/07) : régime macro Peur confirmé -> allocation
    des NOUVELLES entrées divisée par 2 (préserve le capital)."""
    from aria_core import momentum_entry

    async def fake_resolve():
        return "peur"

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", fake_resolve)

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
            "regime": "peur",
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=_no_depeg)
    assert len(act["opened"]) == 1
    # Palier fort (5%) * régime Peur (0.5) = 2.5% du capital de départ = 25 000$.
    assert act["opened"][0]["cost_usd"] == pytest.approx(25_000.0, rel=0.01)


@pytest.mark.asyncio
async def test_run_cycle_persists_entry_regime_on_open_position(tmp_db, monkeypatch):
    from aria_core import momentum_entry

    async def fake_resolve():
        return "euphorie"

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", fake_resolve)

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
            "regime": "euphorie",
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    await pt.run_paper_cycle(depeg_check=_no_depeg)
    pos = await pt._get_open(D)
    assert pos["entry_regime"] == "euphorie"


@pytest.mark.asyncio
async def test_run_cycle_fear_exit_sells_everything_at_old_tp2_level(tmp_db, monkeypatch):
    """Sortie ultra-rapide en régime Peur : TP1 prend son tiers normalement, puis TOUT
    le reliquat se vend au niveau de l'ancien TP2 -- jamais de 3e palier."""
    async def fake_resolve():
        return "peur"

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", fake_resolve)

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000,
        entry_regime="peur",
    )
    # TP1 (target technique) = +40%, TP2 = 2x cette distance = +80%.
    prices = {"v": 1.4}

    async def price_lookup(contract):
        return prices["v"]

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["partial"]) == 1  # TP1 -- vente partielle normale
    assert await pt.has_open(D)

    prices["v"] = 1.8  # niveau de l'ancien TP2 (+80%)
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1  # clôture COMPLÈTE, pas une 2e vente partielle
    assert not await pt.has_open(D)


@pytest.mark.asyncio
async def test_run_cycle_euphoria_exit_never_force_closes_at_old_tp3(tmp_db, monkeypatch):
    """Moon bag pur : régime Euphorie confirmé À L'ENTRÉE ET EN GESTION -- le dernier
    tiers ne se vend JAMAIS via un palier mécanique, même à un gain massif au-delà de
    l'ancien TP3 -- seul le stop suiveur ATR peut encore le sortir."""
    async def fake_resolve():
        return "euphorie"

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", fake_resolve)

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000,
        entry_regime="euphorie",
    )
    # TP1 = +40%, TP2 = +80% (2x) -- les deux prennent leur tiers normalement.
    prices = {"v": 1.4}

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    prices["v"] = 1.8
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert await pt.has_open(D)  # TP1+TP2 pris, reliquat encore ouvert

    # Bien au-delà de l'ancien TP3 (+120%, prix 2.2) -- jamais vendu par un palier.
    prices["v"] = 5.0
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["partial"] == []
    assert act["closed"] == []
    assert await pt.has_open(D)


@pytest.mark.asyncio
async def test_run_cycle_ratchet_keeps_fear_discipline_even_if_regime_later_improves(tmp_db, monkeypatch):
    """Le ratchet ne s'assouplit JAMAIS : une position ouverte en Peur garde sa
    discipline de sortie à 2 paliers même si le régime courant redevient Euphorie
    plus tard -- jamais une réactivation d'un 3e palier ou d'un moon bag."""
    current = {"regime": "peur"}

    async def fake_resolve():
        return current["regime"]

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", fake_resolve)

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000,
        entry_regime="peur",
    )

    current["regime"] = "euphorie"  # le marché s'est retourné après l'ouverture
    prices = {"v": 1.4}

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    prices["v"] = 1.8  # niveau de l'ancien TP2
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    # Toujours le comportement Peur (clôture complète au niveau TP2) malgré le
    # régime COURANT désormais Euphorie -- le ratchet retient le pire (Peur) observé.
    assert len(act["closed"]) == 1
    assert not await pt.has_open(D)


@pytest.mark.asyncio
async def test_tp1_anchors_on_technical_target_not_fixed_percentage(tmp_db):
    """19/07 (Gemini round 5) : un R/R calculé sur un target technique proche (+20 %)
    ne doit plus jamais attendre le +50% fixe historique pour prendre le premier
    profit -- sinon un retournement entre les deux fait manquer la cible qui avait
    justifié l'entrée."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, target_price=1.2, invalidation_price=0.5, alloc_usd=90_000)

    async def price_lookup(contract):
        return 1.2  # exactement la cible technique -- +20 % seulement

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["partial"]) == 1
    assert "+20%" in act["partial"][0]["close_notes"]
    assert await pt.has_open(D)


@pytest.mark.asyncio
async def test_tp1_without_target_price_falls_back_to_fixed_50pct(tmp_db):
    """Non-régression : une position SANS target_price connu (ex. ancien pilote
    VC-thesis dormant) garde le comportement historique -- TP1 fixe +50%, donc un
    gain de seulement +20% ne déclenche encore rien."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000)

    async def price_lookup(contract):
        return 1.2  # +20 % -- sous le fixe +50%, ne doit rien déclencher

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["partial"] == []
    assert act["closed"] == []
    assert await pt.has_open(D)


@pytest.mark.asyncio
async def test_high_volatility_position_survives_a_retracement_that_would_stop_out_flat(tmp_db):
    """entry_atr_pct=0.10 (10 %) -> stop suiveur adaptatif 25 % (2,5x, dans les bornes)
    au lieu du 15 % fixe. Plus haut atteint 2.0 -> stop fixe aurait été 1.70 (2.0*0.85),
    stop adaptatif est 1.50 (2.0*0.75). Un repli à 1.6 reste au-dessus du stop adaptatif
    mais SOUS le stop fixe -- non-régression : la position doit rester ouverte avec
    l'ATR, alors qu'elle aurait clôturé sans lui."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, entry_atr_pct=0.10,
    )

    prices = {"v": 2.0}

    async def price_lookup(contract):
        return prices["v"]

    # 20/07 round 7 -- confirmation TEMPORELLE (_advance_high_water) : le nouveau pic
    # ouvre une candidature, confirmée seulement après avoir tenu
    # HIGH_WATER_CONFIRMATION_SECONDS -- simulé ici en reculant l'horodatage plutôt
    # qu'en attendant pour de vrai.
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    await _backdate_pending_since(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(2.0)

    prices["v"] = 1.6  # sous le stop fixe (1.70), au-dessus du stop adaptatif (1.50)
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    assert await pt.has_open(D)


@pytest.mark.asyncio
async def test_low_volatility_position_stops_out_tighter_than_flat(tmp_db):
    """entry_atr_pct=0.01 (1 %) -> stop suiveur adaptatif clampé au plancher 5 % (2,5x
    donnerait 2,5 %, trop serré) au lieu du 15 % fixe. Plus haut atteint 2.0 -> stop
    adaptatif 1.90 (2.0*0.95), stop fixe aurait été 1.70. Un repli à 1.89 déclenche le
    stop adaptatif (plus serré) alors que le stop fixe ne l'aurait pas fait."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, entry_atr_pct=0.01,
    )

    prices = {"v": 2.0}

    async def price_lookup(contract):
        return prices["v"]

    # 20/07 round 7 -- confirmation temporelle : cf. test précédent, même raison.
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    await _backdate_pending_since(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(2.0)

    prices["v"] = 1.89  # sous le stop adaptatif (1.90), au-dessus du stop fixe (1.70)
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "stop suiveur"
    assert "adapté à l'ATR" in act["closed"][0]["close_notes"]
    assert not await pt.has_open(D)


@pytest.mark.asyncio
async def test_open_position_persists_entry_atr_pct(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, entry_atr_pct=0.08)
    assert pos["entry_atr_pct"] == pytest.approx(0.08)


@pytest.mark.asyncio
async def test_open_position_entry_atr_pct_defaults_to_none(tmp_db):
    """Non-régression : positions ouvertes sans ATR (ex. ancien pilote VC-thesis) --
    ``entry_atr_pct`` reste ``None``, comportement de stop suiveur inchangé."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)
    assert pos["entry_atr_pct"] is None


@pytest.mark.asyncio
async def test_run_paper_cycle_threads_entry_atr_pct_from_analyzer(tmp_db):
    """Bout en bout : un analyzer momentum-style (avec ``entry_atr_pct``, comme
    ``momentum_entry.evaluate_momentum_entry`` en fournit désormais) voit sa valeur
    réellement persistée par ``run_paper_cycle``, pas seulement testable via
    ``open_position`` directement."""
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "symbol": "VOLA", "price": 1.0, "target": 2.0,
            "invalidation": 0.5, "rr": 3.0, "align_score": 3, "chain": "base",
            "entry_atr_pct": 0.12, "reasons": ["setup test"],
        }

    await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer)
    opens = await pt.get_open_positions()
    assert len(opens) == 1
    assert opens[0]["entry_atr_pct"] == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_run_paper_cycle_volume_not_confirmed_caps_sizing_at_moderate(tmp_db):
    """Bout en bout : volume_confirmed=False (RVOL non vérifiable, revue croisée
    Gemini) plafonne le sizing au palier modéré même si R/R+alignement mériteraient
    le palier fort -- vérifié via run_paper_cycle, pas seulement risk_guard en
    isolation."""
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "symbol": "NOVOL", "price": 1.0, "target": 2.0,
            "invalidation": 0.5, "rr": 3.0, "align_score": 3, "chain": "base",
            "volume_confirmed": False, "reasons": ["setup test"],
        }

    await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer)
    opens = await pt.get_open_positions()
    assert len(opens) == 1
    # Palier modéré (3.5 % = 35 000$), pas le palier fort (5 % = 50 000$) qu'un
    # R/R=3.0 + alignement=3 auraient normalement mérité.
    assert opens[0]["cost_usd"] == pytest.approx(35_000.0)


@pytest.mark.asyncio
async def test_reduce_position_accounting(tmp_db):
    """Vérifie la base de coût réduite proportionnellement et le P&L partiel accumulé,
    indépendamment du cycle heartbeat."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(C, "CCC", 1.0, alloc_usd=90_000)  # qty = 90_000

    partial = await pt.reduce_position(C, 1.5, 30_000, stage=1, reason="palier 1/3")
    assert partial is not None
    assert partial["sold_qty"] == 30_000
    assert round(partial["pnl_usd"]) == 15_000  # (30000*1.5) - (90000*(30000/90000))
    assert partial["remaining_qty"] == 60_000

    pos = await pt._get_open(C)
    assert pos["qty"] == 60_000
    assert round(pos["cost_usd"]) == 60_000
    assert round(pos["realized_pnl_partial"]) == 15_000

    assert round(await pt.cash_available()) == round(1_000_000 - 60_000 + 15_000)


@pytest.mark.asyncio
async def test_close_position_includes_prior_partial_pnl(tmp_db):
    """19/07 -- reproduction du bug réel trouvé sur la position #21 (paper-trading 1M$) :
    close_position() ne devait sommer que le dernier palier, alors que
    portfolio_summary() ne lit realized_pnl_partial QUE pour les positions encore
    'open' -- une fois 'closed', le P&L des paliers déjà réalisés disparaissait
    silencieusement du capital agrégé pile au moment de la clôture finale."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(E, "EEE", 1.0, alloc_usd=90_000)  # qty = 90_000

    partial = await pt.reduce_position(E, 1.5, 30_000, stage=1, reason="palier 1/3")
    assert round(partial["pnl_usd"]) == 15_000  # (30000*1.5) - (90000*(30000/90000))

    closed = await pt.close_position(E, 2.0, reason="cible")
    # dernière tranche seule : (60000*2.0) - 60000 = 60_000 -- mais le P&L final
    # DOIT inclure le palier 1 déjà réalisé (15_000), soit un total de 75_000.
    assert round(closed["pnl_usd"]) == 75_000
    assert round(closed["realized_pnl_partial"]) == 15_000  # historique préservé, inchangé

    s = await pt.portfolio_summary()
    assert round(s["equity"]) == round(1_000_000 + 75_000)
    assert round(s["realized_pnl"]) == 75_000


@pytest.mark.asyncio
async def test_migration_adds_position_management_columns(tmp_db):
    """Une DB créée AVANT ces colonnes (ancien schéma) doit migrer sans planter et sans
    perdre les positions déjà ouvertes."""
    import aiosqlite

    async with aiosqlite.connect(pt.DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE paper_position (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                symbol TEXT,
                cost_usd REAL NOT NULL,
                entry_price REAL NOT NULL,
                qty REAL NOT NULL,
                target_price REAL,
                invalidation_price REAL,
                opened_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                exit_price REAL,
                closed_at TEXT,
                pnl_usd REAL,
                pnl_pct REAL,
                close_reason TEXT
            )
            """
        )
        await db.execute(
            "INSERT INTO paper_position (contract, symbol, cost_usd, entry_price, qty, opened_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'open')",
            (A, "AAA", 1000.0, 1.0, 1000.0, "2026-01-01T00:00:00+00:00"),
        )
        await db.commit()

    await pt._ensure_tables()  # ne doit jamais planter, ni sur une DB neuve ni sur une ancienne

    pos = await pt._get_open(A)
    assert pos is not None
    assert pos["tp_stage_hit"] == 0
    assert pos["realized_pnl_partial"] == 0.0
    assert pos["high_water_price"] is None
    assert pos["initial_qty"] is None
    assert pos["category"] == ""
    assert pos["entry_security_json"] is None
    assert pos["chain"] == "base"


@pytest.mark.asyncio
async def test_cycle_ignores_non_buy(tmp_db):
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {"action": "HOLD"}

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg)
    assert act["opened"] == []


@pytest.mark.asyncio
async def test_max_positions_capped(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    for i in range(pt.MAX_POSITIONS):
        c = "0x" + f"{i:040x}"
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=1_000) is not None
    # au-delà du plafond, refus
    assert await pt.open_position("0x" + "f" * 40, "OVER", 1.0, alloc_usd=1_000) is None


def test_alerts_labeled_simulation():
    buy = pt.format_buy_alert(
        {"symbol": "AAA", "contract": A, "entry_price": 2.0, "cost_usd": 50_000,
         "target_price": 3.0, "invalidation_price": 1.5}
    )
    assert "SIMULATION" in buy and "FICTIF" in buy
    sell = pt.format_sell_alert(
        {"symbol": "AAA", "contract": A, "exit_price": 3.0, "pnl_usd": 25_000,
         "pnl_pct": 50.0, "close_reason": "cible"}
    )
    assert "SIMULATION" in sell and "FICTIVE" in sell


# ── #187 : plafond de concentration + surveillance continue + dépeg USDC ─────────────

@pytest.mark.asyncio
async def test_open_position_stores_category(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, category="clanker")
    assert pos["category"] == "clanker"


@pytest.mark.asyncio
async def test_concentration_cap_shrinks_alloc_near_the_limit(tmp_db):
    """Plafond 40% de 1M = 400k. 7 positions de 50k (350k) déjà ouvertes dans la même
    catégorie -- une 8e demandant 50k ne doit tenir que 50k (350k+50k=400k, pile le
    plafond), pas être refusée pour autant."""
    await pt.reset_portfolio(1_000_000.0)
    for i in range(7):
        c = "0x" + f"{i:040x}"
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=50_000, category="clanker") is not None

    pos = await pt.open_position("0x" + "7" * 40, "T7", 1.0, alloc_usd=50_000, category="clanker")
    assert pos is not None
    assert pos["cost_usd"] == 50_000


@pytest.mark.asyncio
async def test_concentration_cap_skips_when_room_too_small(tmp_db):
    """8 positions de 50k (400k, pile le plafond) -- une 9e n'a plus AUCUNE place :
    skip (None), pas une position poussière."""
    await pt.reset_portfolio(1_000_000.0)
    for i in range(8):
        c = "0x" + f"{i:040x}"
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=50_000, category="clanker") is not None

    over = await pt.open_position("0x" + "8" * 40, "T8", 1.0, alloc_usd=50_000, category="clanker")
    assert over is None


@pytest.mark.asyncio
async def test_concentration_cap_does_not_affect_other_categories(tmp_db):
    """Le plafond est PAR catégorie -- une catégorie saturée ne bloque pas les autres."""
    await pt.reset_portfolio(1_000_000.0)
    for i in range(8):
        c = "0x" + f"{i:040x}"
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=50_000, category="clanker") is not None


@pytest.mark.asyncio
async def test_momentum_positions_now_respect_concentration_cap(tmp_db, monkeypatch):
    """19/07 -- trou réel trouvé (revue croisée externe, confirmé dans le code) : les
    positions momentum n'avaient JAMAIS de catégorie -> le plafond de concentration
    (#187) ne s'appliquait jamais à elles, contrairement au pipeline VC-thesis. Fix :
    evaluate_momentum_entry renvoie désormais "category": "momentum-{chain}" -- ce
    test vérifie le câblage bout en bout (funnel momentum réel -> plafond appliqué),
    pas juste open_position() en isolation (déjà couvert ci-dessus)."""
    from aria_core import momentum_entry

    contracts = [f"0x{i:040x}" for i in range(9)]
    call_index = {"n": 0}

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": c, "chain": "base"} for c in contracts]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        call_index["n"] += 1
        return {
            "action": "BUY", "chain": "base", "symbol": f"T{call_index['n']}", "price": 1.0,
            # 19/07 -- rr=3.0/align=3 (palier FORT, redesign 3 paliers) pour préserver
            # le calcul documenté ci-dessous (50k$/position) -- ce test vérifie le
            # plafond de CONCENTRATION, pas le sizing par conviction (déjà couvert
            # ailleurs, cf. test_run_cycle_conviction_tiers_scale_alloc_...).
            "target": 1.5, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
            "category": "momentum-base",
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=_no_depeg)

    # Plafond 40% de 1M = 400k, allocation standard 50k/position -> 8 positions max
    # (400k), la 9e doit être refusée par le plafond de concentration.
    total_deployed = sum(p["cost_usd"] for p in act["opened"])
    assert total_deployed <= 400_000
    assert len(act["opened"]) <= 8

    other = await pt.open_position("0x" + "9" * 40, "OTHER", 1.0, alloc_usd=50_000, category="virtuals_bonding")
    assert other is not None
    assert other["cost_usd"] == 50_000


@pytest.mark.asyncio
async def test_run_cycle_closes_position_on_new_security_signal(tmp_db, monkeypatch):
    from aria_core import paper_trader_risk as risk

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000,
        entry_security_json=risk.EntrySecuritySnapshot(is_honeypot=False).to_json(),
    )

    async def fake_rescan(position, *, pair=None):
        return {"contract": position["contract"], "reasons": ["honeypot détecté (absent à l'entrée)"]}

    monkeypatch.setattr(risk, "rescan_open_position", fake_rescan)

    async def price_lookup(contract):
        return 1.2  # au-dessus du stop -- sans le re-scan, rien ne fermerait ce tour

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "sécurité re-scan"
    assert not await pt.has_open(A)
    assert act["security_alerts"][0]["reasons"] == ["honeypot détecté (absent à l'entrée)"]
    assert any("⚠️" in a and "honeypot" in a for a in alerts)
    # 17/07 -- la justification persistée (close_notes) reprend la vraie raison du re-scan
    assert "honeypot détecté" in act["closed"][0]["close_notes"]


@pytest.mark.asyncio
async def test_run_cycle_closes_position_on_wash_trading_ratio_detected_post_entry(tmp_db, monkeypatch):
    """Bout en bout, chemin RÉEL (price_lookup PAR DÉFAUT, pas injecté) : un token entré
    proprement dont le pool bascule en wash-trading pendant la détention doit être fermé,
    pas suivi aveuglément par le stop suiveur."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)  # sans entry_security_json

    async def fake_pair_lookup(contract, *, chain="base"):
        from aria_core.services.dexscreener import PairSnapshot

        return PairSnapshot(
            pair_address="0xpool", price_usd=1.2, liquidity_usd=372_766.0,
            volume_24h_usd=33_859_669.0, base_symbol="AAA",
        )

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    act = await pt.run_paper_cycle(candidates=[], notifier=notifier)  # price_lookup PAR DÉFAUT
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "sécurité re-scan"
    assert not await pt.has_open(A)
    assert any("wash-trading" in r for r in act["security_alerts"][0]["reasons"])


@pytest.mark.asyncio
async def test_run_cycle_ignores_positions_without_security_signal(tmp_db, monkeypatch):
    """Sans instantané d'entrée (position pré-#187), le re-scan réel ne fabrique jamais
    un signal -- la gestion normale (stop/TP) continue de s'appliquer."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)  # pas d'entry_security_json

    async def price_lookup(contract):
        return 1.0  # ni stop ni TP déclenché

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_run_cycle_blocks_new_entries_on_usdc_depeg(tmp_db):
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 1.0, "target": 2.0, "invalidation": 0.5}

    async def price_lookup(contract):
        return 1.0

    async def depegged():
        return 0.02  # 2% > seuil 1%

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=depegged,
    )
    assert act["opened"] == []
    assert act["depeg_blocked"] is True
    assert round(act["usdc_depeg_pct"], 2) == 0.02
    assert not await pt.has_open(A)


@pytest.mark.asyncio
async def test_run_cycle_depeg_does_not_block_existing_position_management(tmp_db):
    """Le dépeg bloque les NOUVELLES entrées -- les positions déjà ouvertes continuent
    d'être gérées normalement (stop/TP) ce même tour."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(B, "BBB", 1.0, invalidation_price=0.9, alloc_usd=50_000)

    async def price_lookup(contract):
        return 0.5  # sous l'invalidation -> doit fermer malgré le dépeg

    async def depegged():
        return 0.02

    act = await pt.run_paper_cycle(candidates=[A], price_lookup=price_lookup, depeg_check=depegged)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["contract"] == B
    assert act["depeg_blocked"] is True


# ── #194 : pivot momentum multi-chaînes ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_position_stores_chain(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, chain="solana")
    assert pos["chain"] == "solana"


@pytest.mark.asyncio
async def test_open_position_defaults_chain_to_base(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)
    assert pos["chain"] == "base"


# ── 18/07 : bug réel -- casse Solana corrompue avant stockage ────────────────────────
# Trouvé en diagnostic live (RugCheck rejetait en 400 "Bad Request" une adresse
# lowercased -- confirmé que la vraie casse fonctionne). Un .lower() uniforme dans
# open_position() aurait défait le correctif de momentum_entry.py une couche plus
# bas : la position se serait stockée avec une adresse corrompue, rendant tout
# re-scan/prix ultérieur (paper_trader_risk.py) inopérant sur la vraie chaîne.
SOL_MIXED_CASE = "Sol1111111111111111111111111111111111111"


@pytest.mark.asyncio
async def test_open_position_preserves_solana_case(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(SOL_MIXED_CASE, "SOL", 1.0, alloc_usd=50_000, chain="solana")
    assert pos["contract"] == SOL_MIXED_CASE  # jamais lowercased


@pytest.mark.asyncio
async def test_open_position_still_lowercases_base_contract(tmp_db):
    """Comportement EVM inchangé -- seul Solana est exempté du lowercase."""
    await pt.reset_portfolio(1_000_000.0)
    mixed = "0x" + "A" * 40
    pos = await pt.open_position(mixed, "AAA", 1.0, alloc_usd=50_000, chain="base")
    assert pos["contract"] == mixed.lower()


@pytest.mark.asyncio
async def test_has_open_finds_solana_position_case_insensitively(tmp_db):
    """_get_open (via has_open) n'a pas de paramètre chain -- doit retrouver une
    position Solana stockée en casse mixte même en cherchant en minuscules."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(SOL_MIXED_CASE, "SOL", 1.0, alloc_usd=50_000, chain="solana")
    assert await pt.has_open(SOL_MIXED_CASE) is True
    assert await pt.has_open(SOL_MIXED_CASE.lower()) is True


@pytest.mark.asyncio
async def test_list_positions_for_contract_finds_solana_case_insensitively(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(SOL_MIXED_CASE, "SOL", 1.0, alloc_usd=50_000, chain="solana")
    found = await pt.list_positions_for_contract(SOL_MIXED_CASE.lower())
    assert len(found) == 1
    assert found[0]["contract"] == SOL_MIXED_CASE  # la valeur stockée garde sa vraie casse


@pytest.mark.asyncio
async def test_default_price_lookup_uses_chain_aware_dexscreener(monkeypatch):
    from aria_core.services.dexscreener import PairSnapshot

    seen = {}

    async def fake_fetch_token_pairs(contract, *, chain="base"):
        seen["chain"] = chain
        return [PairSnapshot(pair_address="p", price_usd=3.5, liquidity_usd=10_000.0, base_address=A)]

    monkeypatch.setattr("aria_core.services.dexscreener.fetch_token_pairs", fake_fetch_token_pairs)
    price = await pt._default_price_lookup(A, chain="solana")
    assert price == 3.5
    assert seen["chain"] == "solana"


@pytest.mark.asyncio
async def test_run_cycle_prices_open_position_with_its_own_chain(tmp_db, monkeypatch):
    """Le price_lookup PAR DÉFAUT doit interroger la chaîne PERSISTÉE de chaque
    position, pas toujours 'base' -- sinon une position Solana ne serait jamais
    re-priced correctement une fois ouverte."""
    from aria_core.services.dexscreener import PairSnapshot

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, chain="solana")

    seen_chains = []

    async def fake_fetch_token_pairs(contract, *, chain="base"):
        seen_chains.append(chain)
        return [PairSnapshot(pair_address="p", price_usd=1.0, liquidity_usd=10_000.0, base_address=A)]

    monkeypatch.setattr("aria_core.services.dexscreener.fetch_token_pairs", fake_fetch_token_pairs)
    await pt.run_paper_cycle(candidates=[])

    assert "solana" in seen_chains


@pytest.mark.asyncio
async def test_default_pair_lookup_ignores_pair_where_contract_is_only_quote(monkeypatch):
    """19/07 -- même correctif que ``momentum_entry._best_pair`` (reproduction de
    l'incident réel PLAZM #21, en fait ESHARE) : ``fetch_token_pairs`` peut renvoyer
    une paire où ``contract`` est le token QUOTE d'un pool bien plus liquide
    appartenant à un AUTRE token de base -- cette fonction alimente le suivi
    périodique Telegram des positions ouvertes, elle ne doit JAMAIS retourner le
    prix d'un token différent de celui réellement détenu."""
    from aria_core.services.dexscreener import PairSnapshot

    other_token_as_base = PairSnapshot(
        pair_address="other_pool", price_usd=0.01759, liquidity_usd=56_917.98,
        base_address="0xa1fbb38bf486b97108aa87e92008187ca06998f6",
    )
    own_pair = PairSnapshot(
        pair_address="own_pool", price_usd=5.84, liquidity_usd=32_316.40, base_address=A,
    )

    async def fake_fetch_token_pairs(contract, *, chain="base"):
        return [other_token_as_base, own_pair]

    monkeypatch.setattr("aria_core.services.dexscreener.fetch_token_pairs", fake_fetch_token_pairs)
    result = await pt._default_pair_lookup(A)
    assert result.pair_address == "own_pool"
    assert result.price_usd == 5.84


@pytest.mark.asyncio
async def test_default_pair_lookup_none_when_contract_never_the_base(monkeypatch):
    from aria_core.services.dexscreener import PairSnapshot

    other_token_as_base = PairSnapshot(
        pair_address="other_pool", price_usd=0.01759, liquidity_usd=56_917.98,
        base_address="0xa1fbb38bf486b97108aa87e92008187ca06998f6",
    )

    async def fake_fetch_token_pairs(contract, *, chain="base"):
        return [other_token_as_base]

    monkeypatch.setattr("aria_core.services.dexscreener.fetch_token_pairs", fake_fetch_token_pairs)
    assert await pt._default_pair_lookup(A) is None


@pytest.mark.asyncio
async def test_run_cycle_defaults_to_momentum_pipeline_when_nothing_injected(tmp_db, monkeypatch):
    """#194 : quand ni candidates ni analyzer ne sont fournis (le vrai appel
    heartbeat), le défaut devient le pipeline momentum -- plus candidate_ranking."""
    from aria_core import momentum_entry

    top_candidates_called = False

    async def fake_top_candidates(n, **kw):
        nonlocal top_candidates_called
        top_candidates_called = True
        return []

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "solana"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        assert chain == "solana"
        return {"action": "BUY", "symbol": "DDD", "price": 1.0, "target": 2.0,
                "invalidation": 0.5, "chain": chain}

    async def fake_depeg():
        return None  # pas de dépeg -- ne doit jamais bloquer ce test (#187 x #194)

    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates)
    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=fake_depeg)

    assert top_candidates_called is False
    assert len(act["opened"]) == 1
    assert act["opened"][0]["chain"] == "solana"


# ── #194+18/07 : contexte de rythme hebdo + sizing par conviction (pipeline momentum) ──

@pytest.mark.asyncio
async def test_run_cycle_threads_weekly_context_to_momentum_analyzer(tmp_db, monkeypatch):
    """Le contexte de rythme (jour X/7, équité vs objectif) est calculé UNE FOIS par
    cycle et transmis au pipeline momentum -- valeurs cohérentes avec un portefeuille
    tout juste réinitialisé (cycle #1, jour 1, équité == capital de départ)."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    captured = {}

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        captured["weekly_context"] = weekly_context
        return {"action": "HOLD", "chain": chain, "hold_reason": "test"}

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    await pt.run_paper_cycle(depeg_check=_no_depeg)

    ctx = captured["weekly_context"]
    assert ctx is not None
    assert ctx["cycle_number"] == 1
    assert ctx["day"] == 1
    assert ctx["days_total"] == pt.WEEKLY_CYCLE_DAYS
    assert ctx["equity"] == 1_000_000.0
    assert ctx["target_equity"] == 1_100_000.0
    assert ctx["progress_pct"] == 0.0
    assert ctx["remaining_pct"] == pytest.approx(10.0)  # objectif +10 %, rien parcouru


@pytest.mark.asyncio
async def test_run_cycle_sizes_strong_conviction_signal_at_hard_cap(tmp_db, monkeypatch):
    """19/07 -- redesign 3 paliers (feedback opérateur direct : "les positions sont
    trop grosses, l'achat maxi doit etre de 5% et mini de 2%") : R/R >= 2.5 ET
    alignement parfait (3/3) -> palier FORT, 5 % du capital de départ EXACTEMENT (le
    plafond dur désormais, plus jamais 8 % -- ``CONVICTION_SIZE_MULTIPLIER=1.6``
    retiré). Le plafond de perte (risk_guard) reste appliqué PAR-DESSUS, inchangé."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.5, "rr": 3.0, "align_score": 3,
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    # 5 % * 1.0 (MAX_ALLOC_MULTIPLIER, palier fort) * 1M = 50 000 $ demandés -- mais
    # invalidation à 0.5 sur une entrée à 1.0 (risque 50 %) dépasse le plafond risk_guard
    # (2 % * 1M = 20k / 0.5 = 40k) -> PLAFONNÉ à 40 000 $ : le garde-fou de perte prime
    # (même résultat numérique qu'avant ce chantier -- le plafond de perte dominait déjà).
    assert round(act["opened"][0]["cost_usd"]) == 40_000


@pytest.mark.asyncio
async def test_run_cycle_conviction_tiers_scale_alloc_when_risk_cap_not_binding(tmp_db, monkeypatch):
    """19/07 -- isole l'effet RÉEL des 3 paliers de conviction, avec une invalidation
    assez proche de l'entrée pour que le plafond de perte (risk_guard) ne masque jamais
    la différence entre paliers (contrairement au test ci-dessus, où il domine)."""
    from aria_core import momentum_entry

    tiers = [
        (D, 3.0, 3, 50_000.0),   # palier FORT (R/R>=2.5, align>=2) -> 5 %
        (E, 2.0, 3, 35_000.0),   # palier MODÉRÉ (R/R>=2.0, sous le seuil fort) -> 3.5 %
        (F, 1.0, 1, 20_000.0),   # palier FAIBLE (sous le plancher d'achat direct) -> 2 %
    ]

    for contract, rr, align_score, expected_cost in tiers:
        async def fake_discover(
            *, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30, _c=contract,
        ):
            return [{"contract": _c, "chain": "base"}]

        async def fake_evaluate(
            contract, chain, *, weekly_context=None, _rr=rr, _align=align_score, **_kwargs,
        ):
            return {
                "action": "BUY", "chain": chain, "symbol": "TIER", "price": 1.0,
                "target": 2.0, "invalidation": 0.9, "rr": _rr, "align_score": _align,
            }

        monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
        monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

        await pt.reset_portfolio(1_000_000.0)
        act = await pt.run_paper_cycle(depeg_check=_no_depeg)

        assert len(act["opened"]) == 1, f"palier {rr}/{align_score} n'a pas ouvert de position"
        assert act["opened"][0]["cost_usd"] == expected_cost, f"palier {rr}/{align_score}"


@pytest.mark.asyncio
async def test_run_cycle_wide_atr_stop_reduces_allocation_below_flat_tier(tmp_db, monkeypatch):
    """20/07 (Gemini round 7) : le coeur du sizing hybride risque-cible/ATR, bout en
    bout via run_paper_cycle. Palier FORT (R/R=3.0, align=3) avec un ATR large
    (entry_atr_pct=0.20 -> stop suiveur adaptatif clampé au plafond 40%) doit réduire
    l'allocation SOUS le plancher historique 5% (50 000$) -- jamais la même allocation
    qu'un token calme au même palier de conviction (cf. test ci-dessus, 50 000$ sans
    ATR connu)."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
            "entry_atr_pct": 0.20,
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    # trail_pct = _effective_trail_pct(0.20) = min(0.40, 2.5*0.20) = 0.40 (plafond ATR).
    # budget de risque FORT (1.5%) / 0.40 = 37 500$ -- sous le plafond absolu (50 000$,
    # jamais atteint ici) ET sous le plafond de perte invalidation (37 500*10% = 3 750$
    # << 20 000$ de plafond) -- la réduction observée vient BIEN du sizing par
    # risque/ATR, pas d'un des deux autres garde-fous.
    assert round(act["opened"][0]["cost_usd"]) == 37_500


@pytest.mark.asyncio
async def test_run_cycle_tight_atr_stop_is_capped_at_the_historical_ceiling(tmp_db, monkeypatch):
    """20/07 -- un stop ATR très serré donnerait une allocation brute énorme (1.5% /
    5% = 30% du capital) -- le plafond absolu (5%, même maximum que l'ancien système à
    paliers fixes) doit toujours l'emporter, ce mécanisme ne fait jamais GROSSIR une
    position au-delà du maximum historique."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
            "entry_atr_pct": 0.01,  # -> trail_pct clampé au plancher ATR (5%)
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    assert round(act["opened"][0]["cost_usd"]) == 50_000  # plafonné, jamais 300 000$ brut


@pytest.mark.asyncio
async def test_run_cycle_moderate_tier_tight_atr_capped_at_moderate_ceiling_not_strong(tmp_db, monkeypatch):
    """20/07 (suite) -- bug réel trouvé en répondant à une question opérateur : le
    ceiling utilisait TOUJOURS le plafond FORT (5%), quel que soit le palier réel du
    signal. Un stop serré sur un signal MODÉRÉ (R/R=2.0) doit être plafonné à 3.5%
    (35 000$), jamais remonter jusqu'à 5% (50 000$, le plafond du palier FORT) --
    sinon un signal moins convaincant peut recevoir la même mise qu'un signal plus
    fort, inversant l'intention des paliers de conviction."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 2.0, "align_score": 3,
            "entry_atr_pct": 0.01,  # -> trail_pct clampé au plancher ATR (5%)
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    assert round(act["opened"][0]["cost_usd"]) == 35_000  # plafond MODÉRÉ, jamais 50 000$


@pytest.mark.asyncio
async def test_run_cycle_weak_tier_tight_atr_capped_at_weak_ceiling_not_strong(tmp_db, monkeypatch):
    """20/07 (suite) -- même bug, palier FAIBLE (R/R=1.0). Un stop serré ne doit
    jamais laisser un signal FAIBLE atteindre 5% (le plafond du palier FORT) -- doit
    rester plafonné à 2% (20 000$)."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 1.0, "align_score": 1,
            "entry_atr_pct": 0.01,  # -> trail_pct clampé au plancher ATR (5%)
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    assert round(act["opened"][0]["cost_usd"]) == 20_000  # plafond FAIBLE, jamais 50 000$


@pytest.mark.asyncio
async def test_run_cycle_weak_fundamental_downgrades_strong_tier_to_moderate(tmp_db, monkeypatch):
    """19/07 -- même setup technique fort que test_run_cycle_sizes_strong_conviction_
    signal_at_hard_cap, mais avec un potential_score CONFIRMÉ faible
    (conviction_research.py) -- le palier fort (5%) est refusé, RÉTROGRADE au palier
    modéré (3.5%), jamais directement au plancher faible (la conviction technique reste
    réelle, seul le bonus maximal est refusé)."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
            "potential_score": 1.5,  # confirmé faible -- rétrograde le palier fort
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    assert act["opened"][0]["cost_usd"] == 35_000.0  # palier modéré (3.5%) -- jamais 50 000$ (fort)


@pytest.mark.asyncio
async def test_run_cycle_unknown_fundamental_never_blocks_technical_bonus(tmp_db, monkeypatch):
    """potential_score absent (None) -- fail-open sur inconnu, le bonus technique reste
    intact, exactement comme avant ce chantier (jamais réduit sous la baseline)."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.5, "rr": 3.0, "align_score": 3,
            "potential_score": None,
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    assert round(act["opened"][0]["cost_usd"]) == 40_000  # même plafonnement risk_guard qu'avant


async def _reach_weekly_target(min_equity: float = 1_100_000.0) -> None:
    """Pousse l'équité du portefeuille au-dessus de l'objectif hebdo (+10 %) via un
    aller-retour gagnant réel -- pas un état fabriqué en base."""
    await pt.open_position(C, "WIN", 1.0, alloc_usd=500_000.0)
    await pt.close_position(C, 1.3, reason="test -- atteint l'objectif hebdo")
    summary = await pt.portfolio_summary()
    assert summary["equity"] >= min_equity


@pytest.mark.asyncio
async def test_run_cycle_dampens_moderate_tier_once_weekly_target_reached(tmp_db, monkeypatch):
    """Frein à main (18/07, revue croisée validée) : objectif hebdo déjà atteint ->
    allocation du palier MODÉRÉ (3.5 %) réduite de moitié (-> 1.75 %), jamais bloquée
    à zéro. 19/07 -- rr=2.0/align=2 est désormais le palier MODÉRÉ (redesign 3
    paliers), plus le "défaut" flat 5% d'avant ce chantier."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.95, "rr": 2.0, "align_score": 2,
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    await _reach_weekly_target()

    act = await pt.run_paper_cycle(depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    assert act["opened"][0]["cost_usd"] == 17_500.0  # 3.5 % (palier modéré) * 1M * 0.5 (frein à main)


@pytest.mark.asyncio
async def test_run_cycle_dampens_strong_tier_once_weekly_target_reached(tmp_db, monkeypatch):
    """Le cas décrit par la revue : setup fort (5 % de conviction, plafond dur depuis
    le redesign 19/07) + objectif hebdo déjà atteint -> 2.5 %, jamais 5 % plein ni 0 %."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.95, "rr": 3.0, "align_score": 3,
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    await _reach_weekly_target()

    act = await pt.run_paper_cycle(depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    assert act["opened"][0]["cost_usd"] == 25_000.0  # 5 % (palier fort) * 1M * 0.5 (frein à main)


@pytest.mark.asyncio
async def test_run_cycle_preserves_old_default_when_candidates_explicit(tmp_db, monkeypatch):
    """Un appelant qui fournit SES PROPRES candidates (mais pas d'analyzer) garde
    l'analyzer VC historique -- le pivot momentum ne s'applique QUE quand rien du
    tout n'est injecté (comportement heartbeat réel)."""
    from aria_core import momentum_entry

    momentum_called = False

    async def fake_evaluate(contract, chain):
        nonlocal momentum_called
        momentum_called = True
        return None

    async def fake_default_analyzer(contract):
        return {"action": "HOLD"}

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)
    monkeypatch.setattr(pt, "_default_analyzer", fake_default_analyzer)

    await pt.reset_portfolio(1_000_000.0)
    await pt.run_paper_cycle(candidates=[A])

    assert momentum_called is False


# ── #197 : thèse VC persistée + suivi périodique des positions ──────────────────────────

@pytest.mark.asyncio
async def test_open_position_persists_thesis(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, thesis="Bon momentum, holders sains.")
    assert pos["thesis"] == "Bon momentum, holders sains."


@pytest.mark.asyncio
async def test_open_position_thesis_defaults_to_none(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)
    assert pos["thesis"] is None


# ── pool_liquidity_usd -> risk_guard.cap_alloc_to_price_impact (19/07, revue Gemini) ──


@pytest.mark.asyncio
async def test_open_position_shrinks_alloc_on_thin_pool(tmp_db):
    """50k$ demandés sur un pool à 100k$ (la moitié du pool) -- même cas que
    TestCapAllocToPriceImpact.test_shrinks_on_thin_pool_matches_hand_computed_
    breakeven (test_risk_guard.py) : réduit à 10 000$ (vérifié à la main)."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(
        A, "AAA", 1.0, target_price=1.5, invalidation_price=0.9,
        alloc_usd=50_000, pool_liquidity_usd=100_000.0,
    )
    assert pos["cost_usd"] == pytest.approx(10_000.0, rel=1e-6)


@pytest.mark.asyncio
async def test_open_position_pool_liquidity_none_unchanged(tmp_db):
    """Non-régression : ``pool_liquidity_usd`` non fourni (défaut ``None``, ex. l'ancien
    pilote VC-thesis) -- comportement inchangé, aucun rétrécissement lié à l'impact de
    prix."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(
        A, "AAA", 1.0, target_price=1.5, invalidation_price=0.9, alloc_usd=50_000,
    )
    assert pos["cost_usd"] == 50_000.0


@pytest.mark.asyncio
async def test_run_paper_cycle_threads_liquidity_usd_from_analyzer_to_sizing(tmp_db):
    """Bout en bout : un analyzer momentum-style (dict avec ``liquidity_usd``, comme
    ``momentum_entry.evaluate_momentum_entry`` en fournit désormais) voit sa taille de
    position réellement réduite par ``run_paper_cycle`` -- pas seulement testable en
    appelant ``open_position`` directement."""
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "symbol": "THIN", "price": 1.0, "target": 1.5,
            "invalidation": 0.9, "rr": 5.0, "align_score": 3, "chain": "base",
            "liquidity_usd": 100_000.0, "reasons": ["setup test"],
        }

    await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer)
    opens = await pt.get_open_positions()
    assert len(opens) == 1
    assert opens[0]["cost_usd"] == pytest.approx(10_000.0, rel=1e-6)


@pytest.mark.asyncio
async def test_default_analyzer_surfaces_these(monkeypatch):
    """VCResult.these était déjà calculée par analyze_vc_with_context mais jamais
    remontée par _default_analyzer avant ce chantier -- vrai gap trouvé (15/07)."""
    from types import SimpleNamespace

    from aria_core import paper_trader_risk as risk

    class FakePair:
        base_symbol = "AAA"
        price_usd = 2.0
        liquidity_usd = 50_000.0

    class FakeCtx:
        best_pair = FakePair()
        ta_entry = None
        launchpad = None
        bonding_phase = False

    fake_result = SimpleNamespace(
        recommandation="BUY", these="Thèse de test : forte traction sociale.",
        cible="3.0", invalidation="1.5",
    )

    async def fake_analyze(contract, lang="fr"):
        return fake_result, FakeCtx()

    async def fake_snapshot(contract, ctx):
        return risk.EntrySecuritySnapshot()

    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc_with_context", fake_analyze)
    monkeypatch.setattr(risk, "capture_entry_snapshot", fake_snapshot)

    sig = await pt._default_analyzer(A)

    assert sig["these"] == "Thèse de test : forte traction sociale."


@pytest.mark.asyncio
async def test_run_cycle_threads_thesis_from_analyzer_to_open_position(tmp_db):
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {
            "action": "BUY", "symbol": "DDD", "price": 1.0, "target": 2.0,
            "invalidation": 0.5, "these": "Raisonnement complet de test.",
        }

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[D], analyzer=analyzer, price_lookup=price_lookup)

    assert act["opened"][0]["thesis"] == "Raisonnement complet de test."
    pos = await pt._get_open(D)
    assert pos["thesis"] == "Raisonnement complet de test."


@pytest.mark.asyncio
async def test_run_cycle_threads_momentum_reasons_into_thesis_when_no_these(tmp_db):
    """Bug du 17/07 : evaluate_momentum_entry() (#194) ne pose jamais "these" (clé
    propre à l'ancien analyseur VC-thesis) -- ses "reasons" doivent quand même remonter
    dans `thesis`, sinon toute décision momentum reste silencieusement sans rationnel."""
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {
            "action": "BUY", "symbol": "DDD", "price": 1.0, "target": 2.0,
            "invalidation": 0.5,
            "reasons": ["R/R franc (4.0) + alignement technique -- décision directe"],
        }

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[D], analyzer=analyzer, price_lookup=price_lookup)

    assert act["opened"][0]["thesis"] == "R/R franc (4.0) + alignement technique -- décision directe"
    pos = await pt._get_open(D)
    assert pos["thesis"] == "R/R franc (4.0) + alignement technique -- décision directe"


def test_format_buy_alert_includes_thesis_and_contract():
    buy = pt.format_buy_alert(
        {"symbol": "AAA", "contract": A, "entry_price": 2.0, "cost_usd": 50_000,
         "target_price": 3.0, "invalidation_price": 1.5, "thesis": "Raison précise d'entrée."}
    )
    assert "Raison précise d'entrée." in buy
    assert A in buy


def test_format_buy_alert_no_thesis_no_crash():
    """thesis absente/None (position ouverte avant ce chantier, ou analyzer sans these)
    -- pas de ligne "Thèse", pas de crash."""
    buy = pt.format_buy_alert(
        {"symbol": "AAA", "contract": A, "entry_price": 2.0, "cost_usd": 50_000}
    )
    assert "Thèse" not in buy


def test_format_sell_alert_includes_close_notes():
    sell = pt.format_sell_alert(
        {"symbol": "AAA", "contract": A, "exit_price": 1.5, "pnl_usd": -500, "pnl_pct": -2.0,
         "close_reason": "invalidation", "close_notes": "Invalidation technique atteinte : détail précis."}
    )
    assert "Pourquoi : Invalidation technique atteinte : détail précis." in sell


def test_format_sell_alert_no_close_notes_no_crash():
    sell = pt.format_sell_alert(
        {"symbol": "AAA", "contract": A, "exit_price": 1.5, "pnl_usd": -500, "pnl_pct": -2.0,
         "close_reason": "invalidation"}
    )
    assert "Pourquoi" not in sell


def test_format_partial_exit_alert_includes_close_notes():
    partial = pt.format_partial_exit_alert(
        {"symbol": "AAA", "contract": A, "exit_price": 1.5, "pnl_usd": 500, "pnl_pct": 50.0,
         "close_reason": "palier 1/3", "close_notes": "Palier de profit 1/3 atteint : détail précis.",
         "remaining_qty": 20_000}
    )
    assert "Pourquoi : Palier de profit 1/3 atteint : détail précis." in partial


def test_format_buy_alert_includes_dexscreener_link():
    """17/07, demande opérateur : chaque position reliée à son vrai graphique DexScreener."""
    buy = pt.format_buy_alert(
        {"symbol": "AAA", "contract": A, "entry_price": 2.0, "cost_usd": 50_000, "chain": "solana"}
    )
    assert f"https://dexscreener.com/solana/{A}" in buy


def test_format_buy_alert_defaults_to_base_chain_for_dexscreener_link():
    buy = pt.format_buy_alert({"symbol": "AAA", "contract": A, "entry_price": 2.0, "cost_usd": 50_000})
    assert f"https://dexscreener.com/base/{A}" in buy


def test_format_sell_alert_includes_dexscreener_link():
    sell = pt.format_sell_alert(
        {"symbol": "AAA", "contract": A, "exit_price": 1.5, "pnl_usd": -500, "pnl_pct": -2.0,
         "close_reason": "invalidation", "chain": "robinhood"}
    )
    assert f"https://dexscreener.com/robinhood/{A}" in sell


def test_format_partial_exit_alert_includes_dexscreener_link():
    partial = pt.format_partial_exit_alert(
        {"symbol": "AAA", "contract": A, "exit_price": 1.5, "pnl_usd": 500, "pnl_pct": 50.0,
         "close_reason": "palier 1/3", "remaining_qty": 20_000, "chain": "base"}
    )
    assert f"https://dexscreener.com/base/{A}" in partial


def test_format_position_tracking_alert_empty_list():
    assert pt.format_position_tracking_alert([]) == ""


def test_format_position_tracking_alert_shows_latent_pnl():
    msg = pt.format_position_tracking_alert([
        {"contract": A, "symbol": "AAA", "entry_price": 1.0, "price": 1.5, "qty": 1000.0, "cost_usd": 1000.0}
    ])
    assert "AAA" in msg
    assert "+50.0%" in msg or "+50" in msg
    assert "SIMULATION" in msg


def test_format_position_tracking_alert_without_cash_equity_uses_generic_label():
    """Trouvé en conditions réelles (17/07) : l'opérateur ne pouvait pas savoir combien
    il restait de capital, l'en-tête affichait "1 M$" en dur peu importe la vraie valeur.
    Sans cash/equity fournis (dégradation), l'ancien libellé générique reste affiché --
    jamais un chiffre inventé."""
    msg = pt.format_position_tracking_alert([
        {"contract": A, "symbol": "AAA", "entry_price": 1.0, "price": 1.5, "qty": 1000.0, "cost_usd": 1000.0}
    ])
    assert "portefeuille papier 1 M$" in msg


def test_format_position_tracking_alert_shows_real_equity_when_provided():
    msg = pt.format_position_tracking_alert(
        [{"contract": A, "symbol": "AAA", "entry_price": 1.0, "price": 1.5, "qty": 1000.0, "cost_usd": 1000.0}],
        cash=998_415.0, equity=999_915.0,
    )
    assert "998,415" in msg or "998415" in msg
    assert "999,915" in msg or "999915" in msg
    assert "portefeuille papier 1 M$" not in msg  # jamais le libelle generique si le vrai chiffre est connu


def test_format_position_tracking_alert_shows_capital_and_pct_of_starting_capital():
    """17/07, demande opérateur explicite : "sur le suivi je veux aussi le capital
    investi avec le % sur le capital total au moment de l'achat" -- STARTING_CAPITAL_USD,
    pas l'équité courante (c'est la base réelle sur laquelle new_entry_alloc_usd
    dimensionne chaque position à l'ouverture)."""
    msg = pt.format_position_tracking_alert([
        {"contract": A, "symbol": "AAA", "entry_price": 1.0, "price": 1.5, "qty": 50_000.0, "cost_usd": 50_000.0}
    ])
    assert "50,000" in msg or "50000" in msg
    assert "5.0%" in msg  # 50 000 $ / 1 000 000 $ de capital de départ


def test_format_buy_alert_shows_pct_of_starting_capital():
    buy = pt.format_buy_alert(
        {"symbol": "AAA", "contract": A, "entry_price": 2.0, "cost_usd": 25_000}
    )
    assert "2.5%" in buy  # 25 000 $ / 1 000 000 $ de capital de départ


@pytest.mark.asyncio
async def test_run_cycle_notifies_position_tracking_for_still_open_positions(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=10_000)

    async def price_lookup(contract):
        return 1.1  # petit mouvement, aucun palier/stop franchi -- reste ouverte

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)

    assert act["closed"] == []
    assert len(act["tracked"]) == 1
    assert any("suivi positions ouvertes" in a for a in alerts)
    # 17/07 -- lien DexScreener présent dans le suivi périodique aussi, pas seulement achat/vente
    assert any(f"https://dexscreener.com/base/{D}" in a for a in alerts)


@pytest.mark.asyncio
async def test_run_cycle_tracking_alert_shows_real_equity_not_generic_1m(tmp_db):
    """Non-régression du bug réel (17/07) : le cycle doit calculer et transmettre le
    cash/equity RÉELS à l'alerte de suivi, jamais le libellé générique "1 M$"."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=10_000)

    async def price_lookup(contract):
        return 1.1  # petit mouvement, position reste ouverte

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)

    tracking_alerts = [a for a in alerts if "suivi positions ouvertes" in a]
    assert len(tracking_alerts) == 1
    assert "portefeuille papier 1 M$" not in tracking_alerts[0]
    assert "équité" in tracking_alerts[0].lower()


@pytest.mark.asyncio
async def test_run_cycle_tracking_alert_excludes_positions_closed_this_cycle(tmp_db):
    """Une position fermée CE tour ne doit JAMAIS apparaître aussi dans le suivi
    périodique -- déjà couverte par l'alerte de vente, pas de doublon."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.9, alloc_usd=90_000)

    async def price_lookup(contract):
        return 0.89  # sous l'invalidation -> se ferme ce tour

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)

    assert len(act["closed"]) == 1
    assert act["tracked"] == []
    assert not any("suivi positions ouvertes" in a for a in alerts)


@pytest.mark.asyncio
async def test_run_cycle_tracking_alert_throttled_to_every_other_cycle(tmp_db):
    """17/07, demande opérateur explicite : réduire de moitié le bruit Telegram de
    l'alerte de suivi -- un cycle qui suit de trop près le précédent (même position
    ouverte, rien d'autre ne change) ne renvoie pas l'alerte."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=10_000)

    async def price_lookup(contract):
        return 1.1  # petit mouvement, aucun palier/stop franchi -- reste ouverte

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)
    assert sum(1 for a in alerts if "suivi positions ouvertes" in a) == 1

    # cycle immédiatement suivant : trop tôt, l'alerte est sautée (mais act["tracked"]
    # reste calculé normalement -- seule la NOTIFICATION est throttlée, jamais la donnée)
    act2 = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)
    assert len(act2["tracked"]) == 1
    assert sum(1 for a in alerts if "suivi positions ouvertes" in a) == 1

    # on recule artificiellement le dernier envoi au-delà de la fenêtre -> ré-autorisé
    from datetime import datetime, timedelta, timezone
    old_ts = (
        datetime.now(timezone.utc) - timedelta(minutes=pt.TRACKING_ALERT_MIN_INTERVAL_MINUTES + 1)
    ).isoformat()
    await pt.set_last_tracking_alert_at(old_ts)

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)
    assert sum(1 for a in alerts if "suivi positions ouvertes" in a) == 2


@pytest.mark.asyncio
async def test_skip_position_management_leaves_open_positions_untouched(tmp_db):
    """#196 -- avec skip_position_management=True, une position qui aurait
    normalement déclenché le stop suiveur/invalidation reste INTOUCHÉE (ni
    re-scan sécurité, ni clôture) -- réservé au service websocket momentum,
    qui ne doit gérer QUE les nouvelles entrées, jamais les positions déjà
    ouvertes (ça reste le rôle du cycle heartbeat normal)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.9, alloc_usd=90_000)

    async def price_lookup(contract):
        return 0.5  # bien sous l'invalidation -- se fermerait normalement ce tour

    act = await pt.run_paper_cycle(
        candidates=[], price_lookup=price_lookup, depeg_check=_no_depeg,
        skip_position_management=True,
    )

    assert act["closed"] == []
    assert act["checked"] == 0
    assert act["tracked"] == []
    assert await pt.has_open(D)  # toujours ouverte, rien touché


@pytest.mark.asyncio
async def test_skip_position_management_still_opens_new_positions(tmp_db):
    """#196 -- skip_position_management=True saute UNIQUEMENT l'étape 1 (gestion des
    positions déjà ouvertes) ; l'étape 2 (nouvelles entrées) et la photo de risque
    portefeuille (#186, étape 1ter) continuent de s'appliquer normalement."""
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 1.0, "target": 2.0, "invalidation": 0.5}

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
        skip_position_management=True,
    )

    assert len(act["opened"]) == 1
    assert await pt.has_open(A)
    assert "risk_state" in act  # 1ter (#186) reste exécutée même en mode skip


@pytest.mark.asyncio
async def test_concurrent_cycles_never_overlap(tmp_db):
    """#196 -- correctif obligatoire (relecture opérateur) : deux appels concurrents à
    run_paper_cycle() (heartbeat + service websocket, par ex.) ne doivent JAMAIS
    s'exécuter en parallèle -- sinon risque réel de double-allocation de capital ou de
    dépassement de MAX_POSITIONS (les deux liraient l'état avant que l'un des deux
    n'écrive). Un analyzer qui dort prouve la sérialisation : si le verrou ne
    fonctionnait pas, les deux exécutions se chevaucheraient dans la fenêtre de sommeil."""
    await pt.reset_portfolio(1_000_000.0)

    in_progress = False
    overlap_detected = False

    async def analyzer(contract):
        nonlocal in_progress, overlap_detected
        if in_progress:
            overlap_detected = True
        in_progress = True
        await asyncio.sleep(0.05)
        in_progress = False
        return None  # HOLD -- le test porte sur la sérialisation, pas sur l'achat

    async def price_lookup(contract):
        return 1.0

    await asyncio.gather(
        pt.run_paper_cycle(candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg),
        pt.run_paper_cycle(candidates=[B], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg),
    )

    assert overlap_detected is False


@pytest.mark.asyncio
async def test_concurrent_cycles_lock_serializes_websocket_and_heartbeat_style_calls(tmp_db):
    """#196 -- même garde-fou que ci-dessus, mais avec un mélange réaliste : un appel
    ``skip_position_management=True`` (style websocket) et un appel normal (style
    heartbeat) déclenchés en même temps ne doivent jamais se chevaucher non plus."""
    await pt.reset_portfolio(1_000_000.0)

    in_progress = False
    overlap_detected = False

    async def analyzer(contract):
        nonlocal in_progress, overlap_detected
        if in_progress:
            overlap_detected = True
        in_progress = True
        await asyncio.sleep(0.05)
        in_progress = False
        return None

    async def price_lookup(contract):
        return 1.0

    await asyncio.gather(
        pt.run_paper_cycle(
            candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
            skip_position_management=True,
        ),
        pt.run_paper_cycle(candidates=[B], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg),
    )

    assert overlap_detected is False


# ── Formule B (discipline de sortie VC, 20/07) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_position_defaults_strategy_to_momentum(tmp_db):
    """Rétrocompatibilité : tout appelant qui ne précise pas ``strategy`` (positions déjà
    ouvertes, appels directs) reste "momentum" -- comportement historique inchangé."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)
    assert pos["strategy"] == "momentum"


@pytest.mark.asyncio
async def test_open_position_persists_vc_thesis_strategy_and_entry_liquidity(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=80_000.0,
    )
    assert pos["strategy"] == "vc_thesis"
    assert pos["entry_liquidity_usd"] == 80_000.0


@pytest.mark.asyncio
async def test_default_analyzer_tags_strategy_vc_thesis(monkeypatch):
    from types import SimpleNamespace

    class FakePair:
        base_symbol = "AAA"
        price_usd = 2.0
        liquidity_usd = 50_000.0

    class FakeCtx:
        best_pair = FakePair()
        ta_entry = None
        launchpad = None
        bonding_phase = False

    fake_result = SimpleNamespace(recommandation="BUY", these="Thèse test.", cible="3.0", invalidation="1.5")

    async def fake_analyze(contract, lang="fr"):
        return fake_result, FakeCtx()

    async def fake_snapshot(contract, ctx):
        from aria_core import paper_trader_risk as risk

        return risk.EntrySecuritySnapshot()

    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc_with_context", fake_analyze)
    monkeypatch.setattr("aria_core.paper_trader_risk.capture_entry_snapshot", fake_snapshot)

    sig = await pt._default_analyzer(A)
    assert sig["strategy"] == "vc_thesis"
    assert sig["liquidity_usd"] == 50_000.0


@pytest.mark.asyncio
async def test_default_analyzer_surfaces_taille_pct(monkeypatch):
    """#174 -- taille_pct (0-10%, jugement LLM) doit être remonté jusqu'au sizing réel,
    même patron que le fix ``these`` du 15/07 (#197) déjà commenté juste au-dessus."""
    from types import SimpleNamespace

    class FakePair:
        base_symbol = "AAA"
        price_usd = 2.0
        liquidity_usd = 50_000.0

    class FakeCtx:
        best_pair = FakePair()
        ta_entry = None
        launchpad = None
        bonding_phase = False

    fake_result = SimpleNamespace(
        recommandation="BUY", these="Thèse test.", cible="3.0", invalidation="1.5", taille_pct=7.5,
    )

    async def fake_analyze(contract, lang="fr"):
        return fake_result, FakeCtx()

    async def fake_snapshot(contract, ctx):
        from aria_core import paper_trader_risk as risk

        return risk.EntrySecuritySnapshot()

    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc_with_context", fake_analyze)
    monkeypatch.setattr("aria_core.paper_trader_risk.capture_entry_snapshot", fake_snapshot)

    sig = await pt._default_analyzer(A)
    assert sig["taille_pct"] == pytest.approx(7.5)


@pytest.mark.asyncio
async def test_default_analyzer_taille_pct_none_when_absent(monkeypatch):
    """Non-régression -- un VCResult sans l'attribut (ex. ancien mock/version de
    vc_analysis) ne doit jamais planter, juste dégrader vers None (repli conviction-tier)."""
    from types import SimpleNamespace

    class FakePair:
        base_symbol = "AAA"
        price_usd = 2.0
        liquidity_usd = 50_000.0

    class FakeCtx:
        best_pair = FakePair()
        ta_entry = None
        launchpad = None
        bonding_phase = False

    fake_result = SimpleNamespace(recommandation="BUY", these="Thèse test.", cible="3.0", invalidation="1.5")

    async def fake_analyze(contract, lang="fr"):
        return fake_result, FakeCtx()

    async def fake_snapshot(contract, ctx):
        from aria_core import paper_trader_risk as risk

        return risk.EntrySecuritySnapshot()

    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc_with_context", fake_analyze)
    monkeypatch.setattr("aria_core.paper_trader_risk.capture_entry_snapshot", fake_snapshot)

    sig = await pt._default_analyzer(A)
    assert sig["taille_pct"] is None


@pytest.mark.asyncio
async def test_run_cycle_vc_thesis_taille_pct_drives_sizing(tmp_db):
    """#174 -- avant ce correctif, une position vc_thesis retombait TOUJOURS sur le
    plafond MAX (5%, 50 000$) quel que soit le jugement réel du LLM -- ici 7.5% doit
    produire 75 000$, pas 50 000$."""
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {
            "action": "BUY", "symbol": "AAA", "price": 1.0,
            "target": 3.0, "invalidation": 0.99,
            "strategy": "vc_thesis", "taille_pct": 7.5,
        }

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg)
    assert len(act["opened"]) == 1
    assert act["opened"][0]["cost_usd"] == pytest.approx(75_000.0, rel=0.01)


@pytest.mark.asyncio
async def test_run_cycle_vc_thesis_without_taille_pct_falls_back_to_max_tier(tmp_db):
    """Non-régression -- un analyzer vc_thesis qui ne fournit pas ``taille_pct``
    (ex. ancien mock, ou VCResult sans l'attribut) garde EXACTEMENT le comportement
    historique (palier MAX, 5% -> 50 000$), jamais un changement de comportement pour
    un appelant qui ne fournit pas le nouveau champ."""
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {
            "action": "BUY", "symbol": "AAA", "price": 1.0,
            "target": 3.0, "invalidation": 0.99,
            "strategy": "vc_thesis",
        }

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg)
    assert len(act["opened"]) == 1
    assert act["opened"][0]["cost_usd"] == pytest.approx(50_000.0, rel=0.01)


@pytest.mark.asyncio
async def test_run_cycle_vc_thesis_taille_pct_above_ten_is_clamped(tmp_db):
    """Défensif -- ne devrait jamais arriver (déjà clampé à la source par
    vc_analysis.MAX_POSITION_SIZE_PCT), mais un LLM à 25% ne doit jamais produire
    2.5x le plafond produit (100 000$, pas 250 000$)."""
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {
            "action": "BUY", "symbol": "AAA", "price": 1.0,
            "target": 3.0, "invalidation": 0.99,
            "strategy": "vc_thesis", "taille_pct": 25.0,
        }

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg)
    assert len(act["opened"]) == 1
    assert act["opened"][0]["cost_usd"] == pytest.approx(100_000.0, rel=0.01)


@pytest.mark.asyncio
async def test_run_cycle_momentum_signal_unaffected_by_taille_pct_branch(tmp_db, monkeypatch):
    """Non-régression croisée -- un signal momentum (rr/align_score fournis, jamais
    taille_pct) continue de suivre le système de paliers de conviction existant,
    totalement inchangé par ce chantier."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": A, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None, **_kwargs):
        return {
            "action": "BUY", "chain": chain, "symbol": "AAA", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
        }

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_evaluate)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(depeg_check=_no_depeg)
    assert len(act["opened"]) == 1
    # Palier fort (rr=3.0>=2.5, align=3>=2) -> 5% flat, comportement historique inchangé.
    assert act["opened"][0]["cost_usd"] == pytest.approx(50_000.0, rel=0.01)


def _vc_position_pair_lookup(*, price, liquidity_usd):
    async def fake_pair_lookup(contract, *, chain="base"):
        from aria_core.services.dexscreener import PairSnapshot

        return PairSnapshot(
            pair_address="0xpool", price_usd=price, liquidity_usd=liquidity_usd,
            volume_24h_usd=10_000.0, base_symbol="AAA",
        )

    return fake_pair_lookup


@pytest.mark.asyncio
async def test_vc_thesis_position_closes_on_absolute_liquidity_floor(tmp_db, monkeypatch):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=80_000.0,
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=1.1, liquidity_usd=25_000.0),  # < VC_MIN_LIQUIDITY_FLOOR_USD
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "invalidation fondamentale (liquidité)"
    assert not await pt.has_open(A)


@pytest.mark.asyncio
async def test_vc_thesis_position_closes_on_relative_liquidity_drop(tmp_db, monkeypatch):
    """Liquidité toujours au-dessus du plancher absolu (30k$) mais en chute de plus de
    50% depuis l'entrée -- doit quand même invalider la thèse (signal structurel réel,
    pas juste 'encore au-dessus d'un seuil arbitraire')."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=200_000.0,
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=1.1, liquidity_usd=90_000.0),  # 55% de chute, > plancher absolu
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "invalidation fondamentale (liquidité)"


@pytest.mark.asyncio
async def test_vc_thesis_position_survives_a_minor_liquidity_dip(tmp_db, monkeypatch):
    """Non-régression : une baisse de liquidité modeste (bruit normal, pas une chute
    structurelle) ne doit jamais déclencher l'invalidation."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=200_000.0,
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=1.05, liquidity_usd=170_000.0),  # -15%, bruit normal
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert act["closed"] == []
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_vc_thesis_position_closes_on_full_target(tmp_db, monkeypatch):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, target_price=3.0, alloc_usd=50_000,
        strategy="vc_thesis", pool_liquidity_usd=200_000.0,
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=3.2, liquidity_usd=200_000.0),
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "cible thèse VC"


@pytest.mark.asyncio
async def test_vc_thesis_position_never_stopped_out_on_a_deep_pullback(tmp_db, monkeypatch):
    """Le point central de la Formule B (Gemini) : une correction de -50% depuis le plus
    haut, normale pour une thèse VC moyen terme, ne doit JAMAIS déclencher de sortie --
    contrairement à la discipline momentum (stop suiveur ATR)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, target_price=10.0, alloc_usd=50_000,
        strategy="vc_thesis", pool_liquidity_usd=200_000.0,
    )
    # Premier cycle : le prix monte à 3x (au-dessus du seuil Take Seed) -- laisse la
    # sortie partielle se déclencher pour isoler ensuite le seul comportement de
    # non-stop sur la suite.
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=3.0, liquidity_usd=200_000.0),
    )
    await pt.run_paper_cycle(candidates=[])
    assert await pt.has_open(A)

    # Deuxième cycle : correction de -50% depuis ce plus haut (1.5, encore largement
    # au-dessus de l'entrée à 1.0) -- liquidité restée saine (pas d'invalidation
    # fondamentale). Une discipline momentum aurait stoppé sur ce retracement.
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=1.5, liquidity_usd=200_000.0),
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert act["closed"] == []
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_vc_thesis_take_seed_recovers_exactly_initial_cost(tmp_db, monkeypatch):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, target_price=10.0, alloc_usd=50_000,
        strategy="vc_thesis", pool_liquidity_usd=200_000.0,
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=2.0, liquidity_usd=200_000.0),  # exactement 2x
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert len(act["partial"]) == 1
    partial = act["partial"][0]
    assert partial["close_reason"] == "take seed 2x"
    # Recouvre exactement la mise initiale (50 000$) au prix de vente (2.0) -> 25 000 qty.
    assert partial["sold_qty"] == pytest.approx(25_000.0)
    assert partial["pnl_usd"] == pytest.approx(25_000.0)  # vendu 50k$, coût 25k$ sur cette tranche
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_vc_thesis_take_seed_never_fires_twice(tmp_db, monkeypatch):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, target_price=10.0, alloc_usd=50_000,
        strategy="vc_thesis", pool_liquidity_usd=200_000.0,
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=2.5, liquidity_usd=200_000.0),
    )
    first = await pt.run_paper_cycle(candidates=[])
    assert len(first["partial"]) == 1

    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=4.0, liquidity_usd=200_000.0),  # toujours >= 2x
    )
    second = await pt.run_paper_cycle(candidates=[])
    assert second["partial"] == []  # déjà "seedé", jamais une 2e fois
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_vc_thesis_position_untouched_below_take_seed_threshold(tmp_db, monkeypatch):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, target_price=10.0, alloc_usd=50_000,
        strategy="vc_thesis", pool_liquidity_usd=200_000.0,
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=1.3, liquidity_usd=200_000.0),  # < 2x, saine
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert act["closed"] == []
    assert act["partial"] == []
    assert await pt.has_open(A)
    assert act["tracked"] and act["tracked"][0]["contract"] == A


@pytest.mark.asyncio
async def test_momentum_strategy_position_unaffected_by_vc_thesis_branch(tmp_db):
    """Non-régression explicite : une position "momentum" (défaut) reste gérée par le
    stop suiveur ATR/fixe -- la nouvelle branche Formule B ne doit jamais s'appliquer
    à elle. Même patron que test_trailing_stop_tightens_then_closes_remainder (prix
    modéré, sous le premier palier TP, pour isoler le seul comportement de stop)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=50_000)

    prices = {"v": 1.3}  # +30 %, sous le premier palier TP (+50 %) -- pas de prise de profit

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert await pt.has_open(D)

    await _backdate_pending_since(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)  # confirme le plus haut à 1.3
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(1.3)

    prices["v"] = 1.05  # sous le stop suiveur (1.3 * 0.85 = 1.105), au-dessus de l'invalidation (0.5)
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "stop suiveur"


# ── Fraîcheur d'exécution -- recalcul R/R au prix frais (20/07, remplace le 1er design
#    à seuil % aveugle, revue croisée Gemini) ──────────────────────────────────────────

def test_fresh_rr_computes_correctly():
    # target=3.0, invalidation=0.5, prix frais=1.0 -> (3.0-1.0)/(1.0-0.5) = 4.0
    assert pt._fresh_rr(1.0, 3.0, 0.5) == pytest.approx(4.0)


def test_fresh_rr_none_when_setup_already_resolved():
    assert pt._fresh_rr(3.5, 3.0, 0.5) is None  # au-delà de la cible
    assert pt._fresh_rr(0.4, 3.0, 0.5) is None  # sous l'invalidation
    assert pt._fresh_rr(0.5, 3.0, 0.5) is None  # pile sur l'invalidation, plus de marge


def test_fresh_rr_none_on_missing_data():
    assert pt._fresh_rr(None, 3.0, 0.5) is None
    assert pt._fresh_rr(1.0, None, 0.5) is None
    assert pt._fresh_rr(1.0, 3.0, None) is None
    assert pt._fresh_rr(0.0, 3.0, 0.5) is None


def test_execution_rr_still_valid_uses_direct_buy_bar_when_signal_was_direct(monkeypatch):
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    # signal_rr=2.5 >= _RR_MIN_FOR_DIRECT_BUY (2.0) -> barre = 2.0
    assert pt._execution_rr_still_valid(2.5, 2.1) is True
    assert pt._execution_rr_still_valid(2.5, 1.9) is False


def test_execution_rr_still_valid_uses_ambiguous_bar_when_signal_was_ambiguous(monkeypatch):
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    # signal_rr=1.3 < _RR_MIN_FOR_DIRECT_BUY -> barre = _RR_AMBIGUOUS_FLOOR (1.0)
    assert pt._execution_rr_still_valid(1.3, 1.05) is True
    assert pt._execution_rr_still_valid(1.3, 0.95) is False


def test_execution_rr_still_valid_fail_closed_on_missing_fresh_rr(monkeypatch):
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    assert pt._execution_rr_still_valid(2.5, None) is False


@pytest.mark.asyncio
async def test_run_cycle_executes_when_price_pumped_favorably_and_rr_still_valid(tmp_db, monkeypatch):
    """LE scénario central de la revue Gemini : un token qui continue de pomper
    PENDANT la réflexion du LLM (+30%, aurait été rejeté par l'ancien seuil % de 3%)
    doit quand même s'exécuter si le R/R recalculé au prix réel tient encore la
    barre -- l'ancien design aurait filtré exactement les meilleurs setups."""
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        # R/R au signal (prix 1.0) = (3.0-1.0)/(1.0-0.5) = 4.0
        return {
            "action": "BUY", "symbol": "HOT", "price": 1.0, "target": 3.0,
            "invalidation": 0.5, "rr": 4.0, "align_score": 3, "chain": "base",
        }

    async def price_lookup(contract):
        return 1.3  # +30% depuis le signal -- aurait été rejeté par l'ancien seuil 3%

    act = await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer, price_lookup=price_lookup)
    assert len(act["opened"]) == 1
    # Exécuté au prix FRAIS (1.3), pas au prix du signal (1.0).
    assert act["opened"][0]["entry_price"] == pytest.approx(1.3)


@pytest.mark.asyncio
async def test_run_cycle_gets_a_discount_when_price_dipped_without_touching_invalidation(tmp_db, monkeypatch):
    """Symétrique : un léger repli SANS toucher l'invalidation améliore mécaniquement
    le R/R ("rabais" sur la thèse) -- doit aussi s'exécuter, au prix réduit."""
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "symbol": "DIP", "price": 1.0, "target": 3.0,
            "invalidation": 0.5, "rr": 4.0, "align_score": 3, "chain": "base",
        }

    async def price_lookup(contract):
        return 0.98  # -2%, loin de l'invalidation (0.5)

    act = await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer, price_lookup=price_lookup)
    assert len(act["opened"]) == 1
    assert act["opened"][0]["entry_price"] == pytest.approx(0.98)


@pytest.mark.asyncio
async def test_run_cycle_rejects_when_fresh_rr_collapses_below_the_original_bar(tmp_db, monkeypatch):
    """Le vrai cas à rejeter : le prix a couru si près de la cible que le R/R
    structurel ne tient plus la barre franchie à l'origine -- ARIA n'achète pas un
    setup dégradé, contrairement à ce qu'un simple seuil % aurait pu laisser passer
    ou rejeter arbitrairement."""
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        # R/R au signal (prix 1.0) = (3.0-1.0)/(1.0-0.5) = 4.0 -- achat direct (>= 2.0)
        return {
            "action": "BUY", "symbol": "RUN", "price": 1.0, "target": 3.0,
            "invalidation": 0.5, "rr": 4.0, "align_score": 3, "chain": "base",
        }

    async def price_lookup(contract):
        return 2.7  # tout près de la cible -- R/R frais = (3.0-2.7)/(2.7-0.5) = 0.136

    act = await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer, price_lookup=price_lookup)
    assert act["opened"] == []
    assert not await pt.has_open(A)


@pytest.mark.asyncio
async def test_run_cycle_rejects_when_fresh_price_lookup_fails(tmp_db, monkeypatch):
    """Fail-closed : une panne réseau sur la re-vérification ne doit jamais forcer
    une exécution sur l'ancien prix du signal."""
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "symbol": "ERR", "price": 1.0, "target": 3.0,
            "invalidation": 0.5, "rr": 4.0, "align_score": 3, "chain": "base",
        }

    async def failing_price_lookup(contract):
        raise RuntimeError("panne réseau simulée")

    act = await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer, price_lookup=failing_price_lookup)
    assert act["opened"] == []
