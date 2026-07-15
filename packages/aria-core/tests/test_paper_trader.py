"""Portefeuille papier 1 M$ (simulation) — moteur déterministe, DB temporaire isolée."""
from __future__ import annotations

import pytest

from aria_core import paper_trader as pt

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40
D = "0x" + "d" * 40


async def _no_depeg() -> float | None:
    """Fake ``depeg_check`` -- pas de dépeg, aucun appel réseau (#187)."""
    return 0.0


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
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
    # Alloc par défaut (candidat ouvert via run_paper_cycle, pas d'alloc_usd explicite) =
    # ALLOC_PCT * capital = 50 000 $ -> qty initiale 50 000 ; 1/3 vendu au palier 1.
    assert round(pos["qty"]) == round(50_000 * (2.0 / 3.0))


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

    async def fake_rescan(position):
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
