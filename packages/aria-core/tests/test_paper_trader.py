"""Portefeuille papier 1 M$ (simulation) — moteur déterministe, DB temporaire isolée."""
from __future__ import annotations

import asyncio

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
    # #196 -- pytest-asyncio donne une boucle événementielle FRAÎCHE à chaque test ;
    # _run_cycle_lock est un singleton créé une seule fois à l'import du module
    # (correct en production, un seul process/une seule boucle pendant toute sa vie),
    # mais réutiliser le MÊME objet Lock d'un test à l'autre le lierait à une boucle déjà
    # fermée -> RuntimeError. Un Lock frais par test, jamais un changement de comportement
    # en production.
    monkeypatch.setattr(pt, "_run_cycle_lock", asyncio.Lock())
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
async def test_run_paper_cycle_omits_funnel_key_when_nothing_evaluated(tmp_db):
    """Pas de bruit inutile dans ``actions`` quand il n'y a rien à évaluer ce tour."""
    await pt.reset_portfolio(1_000_000.0)

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, depeg_check=_no_depeg)
    assert "momentum_funnel" not in act


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


@pytest.mark.asyncio
async def test_default_price_lookup_uses_chain_aware_dexscreener(monkeypatch):
    from aria_core.services.dexscreener import PairSnapshot

    seen = {}

    async def fake_fetch_token_pairs(contract, *, chain="base"):
        seen["chain"] = chain
        return [PairSnapshot(pair_address="p", price_usd=3.5, liquidity_usd=10_000.0)]

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
        return [PairSnapshot(pair_address="p", price_usd=1.0, liquidity_usd=10_000.0)]

    monkeypatch.setattr("aria_core.services.dexscreener.fetch_token_pairs", fake_fetch_token_pairs)
    await pt.run_paper_cycle(candidates=[])

    assert "solana" in seen_chains


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

    async def fake_evaluate(contract, chain):
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
