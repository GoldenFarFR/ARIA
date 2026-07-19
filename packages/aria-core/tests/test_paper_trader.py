"""Portefeuille papier 1 M$ (simulation) — moteur déterministe, DB temporaire isolée."""
from __future__ import annotations

import asyncio

import pytest

from aria_core import momentum_funnel_log
from aria_core import paper_trader as pt

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
    (+50 %) déclenche une prise de profit PARTIELLE, la position reste ouverte."""
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

    prices["v"] = 1.5  # +50 % -> franchit le 1er palier (TP_STAGES[0])
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

    prices["v"] = 2.5  # cycle 2 : nouveau plus haut, franchit aussi le palier 2
    act2 = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act2["partial"]) == 1
    assert await pt.has_open(D)
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == 2.5
    assert pos["tp_stage_hit"] == 2

    prices["v"] = 2.0  # cycle 3 : repli sous le stop suiveur (2.5 * 0.85 = 2.125) mais
    # largement au-dessus de l'invalidation d'origine (0.5) -> c'est bien le stop suiveur
    act3 = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act3["closed"]) == 1
    assert act3["closed"][0]["close_reason"] == "stop suiveur"
    assert not await pt.has_open(D)
    # 17/07 -- la note cite le vrai plus haut atteint (2.5), pas l'invalidation d'origine
    assert "Stop suiveur déclenché" in act3["closed"][0]["close_notes"]
    assert "2.5" in act3["closed"][0]["close_notes"]


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

    async def fake_evaluate(contract, chain, *, weekly_context=None):
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

    async def fake_evaluate(contract, chain, *, weekly_context=None):
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

    async def fake_evaluate(contract, chain, *, weekly_context=None):
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

    async def fake_evaluate(contract, chain, *, weekly_context=None):
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
            contract, chain, *, weekly_context=None, _rr=rr, _align=align_score,
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
async def test_run_cycle_weak_fundamental_downgrades_strong_tier_to_moderate(tmp_db, monkeypatch):
    """19/07 -- même setup technique fort que test_run_cycle_sizes_strong_conviction_
    signal_at_hard_cap, mais avec un potential_score CONFIRMÉ faible
    (conviction_research.py) -- le palier fort (5%) est refusé, RÉTROGRADE au palier
    modéré (3.5%), jamais directement au plancher faible (la conviction technique reste
    réelle, seul le bonus maximal est refusé)."""
    from aria_core import momentum_entry

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": D, "chain": "base"}]

    async def fake_evaluate(contract, chain, *, weekly_context=None):
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

    async def fake_evaluate(contract, chain, *, weekly_context=None):
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

    async def fake_evaluate(contract, chain, *, weekly_context=None):
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

    async def fake_evaluate(contract, chain, *, weekly_context=None):
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


@pytest.mark.asyncio
async def test_default_analyzer_surfaces_these(monkeypatch):
    """VCResult.these était déjà calculée par analyze_vc_with_context mais jamais
    remontée par _default_analyzer avant ce chantier -- vrai gap trouvé (15/07)."""
    from types import SimpleNamespace

    from aria_core import paper_trader_risk as risk

    class FakePair:
        base_symbol = "AAA"
        price_usd = 2.0

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
