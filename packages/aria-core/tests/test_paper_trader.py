"""Portefeuille papier 1 M$ (simulation) — moteur déterministe, DB temporaire isolée."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

import pytest

from aria_core import momentum_funnel_log
from aria_core import paper_trader as pt
from aria_core.skills import market_sentiment

# 20/07 -- capturée à l'import, AVANT tout monkeypatch de session : permet aux tests
# dédiés à la re-vérification de fraîcheur (cf. plus bas) de restaurer le VRAI
# comportement pour eux-mêmes, malgré le bypass autouse ci-dessous.
_REAL_EXECUTION_RR_STILL_VALID = pt._execution_rr_still_valid
_REAL_BONDING_CANDIDATES = pt._bonding_candidates


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


@pytest.fixture(autouse=True)
def _bypass_bonding_candidates_network_call(monkeypatch):
    """Pre-existing gap, not introduced by this session's changes but found
    while verifying them: `_momentum_candidates_and_chain_map` ALWAYS calls
    `_bonding_candidates()` (real network call to the Virtuals API,
    `services/launchpad_discovery.discover_bonding_candidates`, since the
    24/07 bonding chantier) even when a test only mocks `momentum_entry.
    discover_momentum_candidates` -- this can hang for a long time (TCP
    connect timeout) rather than failing fast, depending on sandbox network
    conditions. Neutral mock (empty list, unchanged historical default for
    any test that doesn't care about bonding sourcing) -- tests dedicated to
    bonding discovery (e.g. Item #157's own tests) override this locally."""
    async def _fake_bonding_candidates(*, limit=20):
        return []

    monkeypatch.setattr(pt, "_bonding_candidates", _fake_bonding_candidates)


@pytest.fixture(autouse=True)
def _bypass_btc_cycles_network_call(monkeypatch):
    """Item #165, 28/07: the bonding sizing path now calls btc_cycles.
    fetch_current_macro_phase() (real network call, module-level 1h cache) --
    without this default mock, any test exercising that path would either
    hit the real network (slow/flaky in a sandbox) or silently share a
    cached value across test runs. Neutral by default (None -> 1.0x
    multiplier, unchanged historical sizing) -- tests dedicated to Item #165
    itself override this locally to exercise the late-cycle reduction."""
    from aria_core.skills import btc_cycles

    async def _fake_fetch_current_macro_phase(*, client=None, force_refresh=False):
        return None

    monkeypatch.setattr(btc_cycles, "fetch_current_macro_phase", _fake_fetch_current_macro_phase)


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


async def _backdate_breakeven_pending(contract: str, seconds: float) -> None:
    """Recule ``breakeven_pending_since`` de ``seconds`` -- même patron que
    ``_backdate_pending_since`` ci-dessus, pour la confirmation temporelle du
    Breakeven Hard Floor (20/07, revue croisée externe)."""
    import aiosqlite

    async with aiosqlite.connect(pt.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT breakeven_pending_since FROM paper_position WHERE contract = ?", (contract,)
            )
        ).fetchone()
        assert row and row[0], "aucune candidature breakeven_pending_since à reculer"
        backdated = datetime.fromisoformat(row[0]) - timedelta(seconds=seconds)
        await db.execute(
            "UPDATE paper_position SET breakeven_pending_since = ? WHERE contract = ?",
            (backdated.isoformat(), contract),
        )
        await db.commit()


async def _price_lookup_one(contract: str) -> float:
    """Trivial constant-price coroutine (08/01) -- price_lookup must be
    awaitable, a plain lambda silently breaks the position-management loop
    (real mistake caught while writing the 5-variant architecture tests)."""
    return 1.0


async def _backdate_opened_at(contract: str, hours: float) -> None:
    """Recule ``opened_at`` de ``hours`` -- simule une position ouverte depuis
    longtemps (08/01, timeout de stagnation scalping) sans attendre pour de vrai."""
    import aiosqlite

    async with aiosqlite.connect(pt.DB_PATH) as db:
        row = await (
            await db.execute("SELECT opened_at FROM paper_position WHERE contract = ?", (contract,))
        ).fetchone()
        assert row and row[0], "aucune position à reculer"
        backdated = datetime.fromisoformat(row[0]) - timedelta(hours=hours)
        await db.execute(
            "UPDATE paper_position SET opened_at = ? WHERE contract = ?", (backdated.isoformat(), contract),
        )
        await db.commit()

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40
D = "0x" + "d" * 40
E = "0x" + "e" * 40
F = "0x" + "f" * 40
G = "0x" + "1" * 40


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
    # 07/23 -- limit-order mechanism: same DB_PATH-computed-once-at-import trap
    # as momentum_funnel_log/market_sentiment above -- without this, a test that
    # reaches the limit-order path would silently read/write the real default
    # DB path shared across the whole process.
    from aria_core import limit_orders

    monkeypatch.setattr(limit_orders, "DB_PATH", str(tmp_path / "paper.db"))
    # Item #193 (28/07) -- same DB_PATH-computed-once-at-import trap as
    # momentum_funnel_log/market_sentiment/limit_orders above: _open_new_
    # entries_for_wallet now also records every evaluation via
    # momentum_scan_log.record_scan.
    from aria_core import momentum_scan_log

    monkeypatch.setattr(momentum_scan_log, "DB_PATH", str(tmp_path / "momentum_scan.db"))
    # Item #236 (30/07) -- same DB_PATH-computed-once-at-import trap as the
    # others above: open_position now also cleans up manual_candidates (the
    # /add queue) on every successful buy.
    from aria_core import manual_candidates

    monkeypatch.setattr(manual_candidates, "DB_PATH", str(tmp_path / "manual_candidates.db"))
    # 02/08 -- same DB_PATH-computed-once-at-import trap as the others above:
    # the multi-pocket loop now also queries fixed_watchlist.
    # list_watchlist_candidates() unconditionally (same doctrine as
    # vc_candidates) for the "megacap" pocket.
    from aria_core import fixed_watchlist

    monkeypatch.setattr(fixed_watchlist, "DB_PATH", str(tmp_path / "fixed_watchlist.db"))
    return tmp_path


@pytest.mark.asyncio
async def test_reset_and_starting(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    assert await pt.starting_capital() == 1_000_000.0
    assert await pt.cash_available() == 1_000_000.0


@pytest.mark.asyncio
async def test_reset_portfolio_archives_closed_positions_before_dropping(tmp_db):
    """24/07 -- 5-agent audit finding: reset_portfolio() used to DROP
    paper_position without ever archiving it first (unlike run_weekly_reset),
    silently losing every already-closed position's history on a manual
    reset. Now archives whatever is still in the live table before the DROP."""
    import aiosqlite

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000, wallet="swing")
    await pt.close_position(A, 4.0, reason="cible")

    await pt.reset_portfolio(1_000_000.0)  # manual reset -- must archive AAA first

    async with aiosqlite.connect(pt.DB_PATH) as db:
        row = await (
            await db.execute("SELECT contract, close_reason FROM paper_position_archive WHERE contract = ?", (A,))
        ).fetchone()
    assert row is not None
    assert row[1] == "cible"
    # Fresh portfolio afterward, unaffected by the archive step.
    assert await pt.starting_capital() == 1_000_000.0
    assert await pt.cash_available() == 1_000_000.0
    assert await pt.get_open_positions() == []


@pytest.mark.asyncio
async def test_reset_portfolio_archives_open_positions_too(tmp_db):
    """A position still OPEN at reset time (e.g. an out-of-band incident
    restart, cf. the 22/07 CNX case) must also be archived, not silently
    dropped -- even without a proper close_reason/exit_price."""
    import aiosqlite

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000, wallet="swing")

    await pt.reset_portfolio(1_000_000.0)

    async with aiosqlite.connect(pt.DB_PATH) as db:
        row = await (
            await db.execute("SELECT contract FROM paper_position_archive WHERE contract = ?", (A,))
        ).fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_reset_portfolio_creates_row_for_wallet_with_no_prior_state(tmp_db):
    """08/01 -- real bug found live (operator-triggered full reset across all
    pockets, including the 5 scalping variants which never had a paper_state
    row): the plain UPDATE silently affected 0 rows for a wallet with no
    existing state, leaving it row-less even after an explicit reset. Now a
    row is guaranteed to exist (INSERT OR IGNORE before the UPDATE)."""
    import aiosqlite

    await pt.reset_portfolio(1_000_000.0, wallet="scalping_v3")  # never seen this wallet before

    async with aiosqlite.connect(pt.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT starting_capital, equity_high_water_mark, cycle_number FROM paper_state "
                "WHERE wallet = ?", ("scalping_v3",),
            )
        ).fetchone()
    assert row is not None
    assert row == (1_000_000.0, 1_000_000.0, 1)


# ── migrate_legacy_wallet_rows (08/01, legacy "scalping" -> "scalping_v6") ──


def _point_wallet_scoped_modules_at_tmp_db(monkeypatch):
    """pending_limit_order/momentum_scan_log/rsi_divergence_log each own
    their own module-level DB_PATH -- all point at the same real
    aria_db_path() in production, but a test with its own isolated tmp_db
    (only pt.DB_PATH by default) must repoint these 3 too, or migrate_
    legacy_wallet_rows's own _ensure_table() calls create/read a DIFFERENT
    file than the one this test seeds."""
    import aiosqlite  # noqa: F401 -- keeps the import grouping consistent with callers

    from aria_core import limit_orders, momentum_scan_log, rsi_divergence_log

    monkeypatch.setattr(limit_orders, "DB_PATH", pt.DB_PATH)
    monkeypatch.setattr(momentum_scan_log, "DB_PATH", pt.DB_PATH)
    monkeypatch.setattr(rsi_divergence_log, "DB_PATH", pt.DB_PATH)


@pytest.mark.asyncio
async def test_migrate_legacy_wallet_rows_moves_every_scoped_table(tmp_db, monkeypatch):
    """Real DB evidence the migration exists to preserve: 642 pending_limit_
    order rows, 3 open paper_position rows, months of momentum_scan_log/
    rsi_divergence_log history on wallet="scalping" -- none of it discarded
    just because the pocket is renamed."""
    import aiosqlite

    _point_wallet_scoped_modules_at_tmp_db(monkeypatch)
    from aria_core import limit_orders, momentum_scan_log, rsi_divergence_log

    await pt.reset_portfolio(1_000_000.0, wallet="scalping")
    await pt.open_position("0xAAA", "AAA", 1.0, wallet="scalping", alloc_usd=100.0)
    # These 3 tables are each owned by their own module -- created here
    # (rather than assumed pre-existing) so this test's own raw INSERTs
    # below don't race the schema, same as migrate_legacy_wallet_rows's own
    # _ensure_table() calls handle in production.
    await limit_orders._ensure_table()
    await momentum_scan_log._ensure_table()
    await rsi_divergence_log._ensure_table()

    async with aiosqlite.connect(pt.DB_PATH) as db:
        await db.execute(
            "INSERT INTO paper_position_archive (cycle_number, contract, cost_usd, entry_price, qty, "
            "opened_at, status, wallet) VALUES (1, '0xBBB', 50.0, 1.0, 50.0, '2026-08-01T00:00:00', "
            "'closed', 'scalping')"
        )
        await db.execute(
            "INSERT INTO pending_limit_order (contract, chain, symbol, target_price, signal_json, state, "
            "created_at, expires_at, wallet) VALUES ('0xCCC', 'base', 'CCC', 1.0, '{}', 'watching', "
            "'2026-08-01T00:00:00', '2026-08-02T00:00:00', 'scalping')"
        )
        await db.execute(
            "INSERT INTO paper_weekly_cycle (wallet, cycle_number, started_at, target_equity, start_capital) "
            # SQLite doesn't understand Python's "_" thousands-separator literal syntax
            # in a raw SQL string (unlike a real bound parameter) -- must be plain digits.
            "VALUES ('scalping', 1, '2026-08-01T00:00:00', 1100000.0, 1000000.0)"
        )
        await db.execute(
            "INSERT INTO momentum_scan_log (contract, chain, hold_reason, wallet, scanned_at) "
            "VALUES ('0xDDD', 'base', 'no_entry_signal', 'scalping', '2026-08-01T00:00:00')"
        )
        await db.execute(
            "INSERT INTO rsi_divergence_log (contract, chain, wallet, outcome, recorded_at) "
            "VALUES ('0xEEE', 'base', 'scalping', 'expired_unconfirmed', '2026-08-01T00:00:00')"
        )
        # A DIFFERENT wallet's row -- must never be touched by this migration.
        await db.execute(
            "INSERT INTO momentum_scan_log (contract, chain, hold_reason, wallet, scanned_at) "
            "VALUES ('0xFFF', 'base', 'no_entry_signal', 'swing', '2026-08-01T00:00:00')"
        )
        await db.commit()

    counts = await pt.migrate_legacy_wallet_rows("scalping", "scalping_v6")

    assert counts == {
        "paper_state": 1, "paper_position": 1, "paper_position_archive": 1,
        "pending_limit_order": 1, "paper_weekly_cycle": 1, "momentum_scan_log": 1,
        "rsi_divergence_log": 1,
    }

    async with aiosqlite.connect(pt.DB_PATH) as db:
        for table in pt._WALLET_SCOPED_TABLES:
            row = await (await db.execute(f"SELECT COUNT(*) FROM {table} WHERE wallet = 'scalping'")).fetchone()
            assert row[0] == 0, f"{table} still has a 'scalping' row after migration"
            row = await (
                await db.execute(f"SELECT COUNT(*) FROM {table} WHERE wallet = 'scalping_v6'")
            ).fetchone()
            assert row[0] == 1, f"{table} missing its migrated 'scalping_v6' row"
        # the "swing" row in momentum_scan_log must be completely untouched
        row = await (await db.execute("SELECT COUNT(*) FROM momentum_scan_log WHERE wallet = 'swing'")).fetchone()
        assert row[0] == 1


@pytest.mark.asyncio
async def test_migrate_legacy_wallet_rows_is_idempotent(tmp_db, monkeypatch):
    _point_wallet_scoped_modules_at_tmp_db(monkeypatch)
    await pt.reset_portfolio(1_000_000.0, wallet="scalping")
    first = await pt.migrate_legacy_wallet_rows("scalping", "scalping_v6")
    assert first["paper_state"] == 1

    second = await pt.migrate_legacy_wallet_rows("scalping", "scalping_v6")
    assert all(v == 0 for v in second.values())


@pytest.mark.asyncio
async def test_migrate_legacy_wallet_rows_nothing_to_migrate_returns_zeros(tmp_db, monkeypatch):
    _point_wallet_scoped_modules_at_tmp_db(monkeypatch)
    counts = await pt.migrate_legacy_wallet_rows("scalping", "scalping_v6")
    assert all(v == 0 for v in counts.values())


@pytest.mark.asyncio
async def test_open_deducts_cash_and_no_double(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 2.0, target_price=3.0, invalidation_price=1.5, alloc_usd=50_000, wallet="swing")
    assert pos is not None
    assert pos["qty"] == 25_000  # 50000 / 2
    assert await pt.cash_available() == 950_000.0
    assert await pt.open_position(A, "AAA", 2.0, alloc_usd=10_000, wallet="swing") is None  # déjà ouverte


@pytest.mark.asyncio
async def test_close_profit(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000, wallet="swing")
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
    await pt.open_position(B, "BBB", 1.0, alloc_usd=100_000, wallet="swing")
    closed = await pt.close_position(B, 0.5, reason="invalidation")
    assert closed["pnl_usd"] == -50_000
    assert await pt.cash_available() == 950_000.0


@pytest.mark.asyncio
async def test_summary_marks_to_market(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(C, "CCC", 1.0, alloc_usd=100_000, wallet="swing")

    async def price_lookup(contract):
        return 1.5

    s = await pt.portfolio_summary(price_lookup=price_lookup)
    assert round(s["equity"]) == 1_050_000  # cash 900k + 100k*1.5
    assert round(s["unrealized_pnl"]) == 50_000


@pytest.mark.asyncio
async def test_summary_applies_exit_impact_decote_on_thin_pool(tmp_db):
    """22/07 -- item #18 (stress-test) : sur un pool devenu mince, le PnL affiché
    ne doit plus supposer une liquidation sans aucun glissement -- le prix spot
    seul (1.5) surestimerait la valeur réelle liquidable de cette position."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(C, "CCC", 1.0, alloc_usd=100_000, pool_liquidity_usd=50_000.0, wallet="swing")

    async def price_lookup(contract):
        return 1.5

    s = await pt.portfolio_summary(price_lookup=price_lookup)

    # Position vaut 150k$ au prix spot -- impact de sortie sur un pool de 50k$
    # (PRICE_IMPACT_RATIO=2.0 -> impact = 2*150000/50000 = 6.0 -> plafonné à 0%
    # côté prix, jamais négatif). Vérifie seulement que la valeur affichée est
    # RÉDUITE par rapport au calcul spot naïf (150k), jamais égale ou supérieure.
    assert s["equity"] < 1_050_000
    assert s["unrealized_pnl"] < 50_000


@pytest.mark.asyncio
async def test_summary_deep_pool_negligible_decote(tmp_db):
    """Sur un pool très profond par rapport à la position, la décote doit rester
    quasi imperceptible -- jamais une pénalité arbitraire hors de proportion."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(C, "CCC", 1.0, alloc_usd=100_000, pool_liquidity_usd=100_000_000.0, wallet="swing")

    async def price_lookup(contract):
        return 1.5

    s = await pt.portfolio_summary(price_lookup=price_lookup)

    assert s["equity"] == pytest.approx(1_050_000, rel=1e-3)  # écart négligeable (<0.1%)


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
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
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
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
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
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")

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


# ── compteur de pertes consécutives par contrat (20/07, revue croisée externe --
# angle mort réel dans le garde-fou de re-entrée assoupli ci-dessus : rien n'empêchait
# une boucle perte->rachat->perte sur LE MÊME contrat, exactement le motif de
# l'incident BRIAN) ──────────────────────────────────────────────────────────────────

class TestConsecutiveLossesForContract:
    """Tests unitaires purs -- même patron que risk_guard.evaluate_portfolio_risk
    (portefeuille entier), scopé à un seul contrat."""

    @pytest.mark.asyncio
    async def test_no_positions_zero_streak(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        assert await pt._consecutive_losses_for_contract(A) == 0

    @pytest.mark.asyncio
    async def test_two_losses_in_a_row(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
        await pt.close_position(A, 0.8, reason="stop suiveur")
        await pt.open_position(A, "AAA", 0.8, alloc_usd=50_000, wallet="swing")
        await pt.close_position(A, 0.6, reason="stop suiveur")
        assert await pt._consecutive_losses_for_contract(A) == 2

    @pytest.mark.asyncio
    async def test_win_resets_streak_to_zero(self, tmp_db):
        """Perte, puis gain, puis perte -- le compteur s'arrête au gain le plus
        récent (une seule perte comptée, pas deux)."""
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
        await pt.close_position(A, 0.8, reason="stop suiveur")  # perte
        await pt.open_position(A, "AAA", 0.8, alloc_usd=50_000, wallet="swing")
        await pt.close_position(A, 1.2, reason="cible")  # gain -- remet à zéro
        await pt.open_position(A, "AAA", 1.2, alloc_usd=50_000, wallet="swing")
        await pt.close_position(A, 1.0, reason="stop suiveur")  # perte
        assert await pt._consecutive_losses_for_contract(A) == 1

    @pytest.mark.asyncio
    async def test_open_position_ignored_never_counted(self, tmp_db):
        """Une position encore OUVERTE n'a pas de pnl_usd définitif -- ignorée par le
        compteur (robuste même appelée avant le has_open() du chemin normal)."""
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
        await pt.close_position(A, 0.8, reason="stop suiveur")
        await pt.open_position(A, "AAA", 0.8, alloc_usd=50_000, wallet="swing")  # encore ouverte
        assert await pt._consecutive_losses_for_contract(A) == 1


@pytest.mark.asyncio
async def test_reentry_blocked_after_max_consecutive_losses_on_same_contract(tmp_db):
    """20/07 -- exactement le motif de l'incident BRIAN (17/07, rachetée 2 fois de
    suite après deux stop suiveur, -18 561$ cumulés) : au-delà de
    MAX_CONSECUTIVE_LOSSES_PER_CONTRACT pertes d'affilée sur CE contrat, un nouveau
    signal BUY par ailleurs valide est rejeté."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    await pt.close_position(A, 0.8, reason="stop suiveur")
    await pt.open_position(A, "AAA", 0.8, alloc_usd=50_000, wallet="swing")
    await pt.close_position(A, 0.6, reason="stop suiveur")

    async def normal_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 0.7, "rr": 2.5, "align_score": 3}

    async def price_lookup(contract):
        return 0.7

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=normal_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert act["opened"] == []
    assert not await pt.has_open(A)


@pytest.mark.asyncio
async def test_scalping_mode_blocks_reentry_after_a_single_loss(tmp_db):
    """08/02 -- real incident found live (behavior audit of scalping_v6's
    real P&L, operator go-ahead to fix): REI was rebought ~30min after its
    FIRST loss (stop suiveur, -$3,228), same ~$50k size, then lost again
    (-$2,901) on the SAME contract -- these 2 losses alone cost more than
    the pocket's entire net profit. The 26/07 threshold (3, see
    SCALPING_MAX_CONSECUTIVE_LOSSES_PER_CONTRACT's own comment for the full
    statistical reasoning behind THAT decision) never even caught this
    exact pattern -- it would have taken a 3rd loss. Reversed down to 1:
    a SINGLE loss on a contract now blocks re-entry until a win elsewhere
    resets the streak."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.set_trading_mode("scalping")
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    await pt.close_position(A, 0.8, reason="stop suiveur")

    async def normal_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 0.7, "rr": 2.5, "align_score": 3}

    async def price_lookup(contract):
        return 0.7

    # Une seule perte -- desormais bloque immediatement en mode scalping (seuil = 1).
    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=normal_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert act["opened"] == []
    assert not await pt.has_open(A)


@pytest.mark.asyncio
async def test_paper_risk_circuit_breakers_disabled_skips_the_per_contract_cooldown(tmp_db, monkeypatch):
    """08/02 -- operator explicit call, live incident (a hard portfolio
    circuit breaker had just armed on scalping_v3): "les coupe circuit ne
    servent à rien à paper test ... tu peux les supprimer". Same exact setup
    as test_scalping_mode_blocks_reentry_after_a_single_loss above (a single
    loss would normally block re-entry in scalping mode) -- with the gate
    on, the cooldown is never even queried, the re-entry goes through."""
    monkeypatch.setenv("ARIA_PAPER_RISK_CIRCUIT_BREAKERS_DISABLED", "true")
    await pt.reset_portfolio(1_000_000.0)
    await pt.set_trading_mode("scalping")
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    await pt.close_position(A, 0.8, reason="stop suiveur")

    async def normal_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 0.7, "rr": 2.5, "align_score": 3}

    async def price_lookup(contract):
        return 0.7

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=normal_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert len(act["opened"]) == 1
    assert await pt.has_open(A)


# ── rsi_divergence_log wiring (Item #247, 30/07) ─────────────────────────────

@pytest.mark.asyncio
async def test_run_cycle_logs_bought_direct_when_rsi_gap_span_present(tmp_db, monkeypatch):
    """A direct BUY whose signal carries rsi_gap/rsi_span (a real golden-
    pocket + confirmed RSI divergence entry, momentum_entry.py) must log
    outcome="bought_direct" so the operator can later correlate divergence
    "steepness" against real performance."""
    from aria_core import rsi_divergence_log

    await pt.reset_portfolio(1_000_000.0)

    calls = []

    async def _fake_record(contract, chain, **kw):
        calls.append({"contract": contract, "chain": chain, **kw})

    monkeypatch.setattr(rsi_divergence_log, "record_divergence", _fake_record)

    async def signal(contract):
        return {
            "action": "BUY", "symbol": "AAA", "price": 0.7, "rr": 2.5, "align_score": 3,
            "rsi_gap": 11.0, "rsi_span": 12,
        }

    async def price_lookup(contract):
        return 0.7

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert len(act["opened"]) == 1
    assert len(calls) == 1
    assert calls[0]["outcome"] == "bought_direct"
    assert calls[0]["gap"] == pytest.approx(11.0)
    assert calls[0]["span"] == 12


@pytest.mark.asyncio
async def test_run_cycle_never_logs_divergence_for_buy_without_rsi_fields(tmp_db, monkeypatch):
    """A BUY from an analyzer that never sets rsi_gap/rsi_span (e.g. a
    bonding/VC-thesis entry) must never be logged into the divergence log --
    there is no real divergence "steepness" to measure for it."""
    from aria_core import rsi_divergence_log

    await pt.reset_portfolio(1_000_000.0)

    calls = []

    async def _fake_record(contract, chain, **kw):
        calls.append({"contract": contract, "chain": chain, **kw})

    monkeypatch.setattr(rsi_divergence_log, "record_divergence", _fake_record)

    async def signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 0.7, "rr": 2.5, "align_score": 3}

    async def price_lookup(contract):
        return 0.7

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert len(act["opened"]) == 1
    assert calls == []


@pytest.mark.asyncio
async def test_reentry_allowed_again_after_a_win_breaks_the_streak(tmp_db):
    """Non-régression : un gain entre deux pertes remet le compteur à zéro -- la
    garde ne se déclenche pas si la dernière perte est isolée."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    await pt.close_position(A, 0.8, reason="stop suiveur")  # perte
    await pt.open_position(A, "AAA", 0.8, alloc_usd=50_000, wallet="swing")
    await pt.close_position(A, 1.2, reason="cible")  # gain -- casse la série
    await pt.open_position(A, "AAA", 1.2, alloc_usd=50_000, wallet="swing")
    await pt.close_position(A, 1.0, reason="stop suiveur")  # perte isolée (1 seule)

    async def normal_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 1.0, "rr": 2.5, "align_score": 3}

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=normal_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert len(act["opened"]) == 1


@pytest.mark.asyncio
async def test_loss_streak_gate_scoped_to_one_contract_only(tmp_db):
    """Chirurgical : le blocage de A (2 pertes d'affilée) ne doit jamais affecter B,
    un contrat totalement différent, jamais touché."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    await pt.close_position(A, 0.8, reason="stop suiveur")
    await pt.open_position(A, "AAA", 0.8, alloc_usd=50_000, wallet="swing")
    await pt.close_position(A, 0.6, reason="stop suiveur")

    async def both_signals(contract):
        if contract == A:
            return {"action": "BUY", "symbol": "AAA", "price": 0.7, "rr": 2.5, "align_score": 3}
        return {"action": "BUY", "symbol": "BBB", "price": 1.0, "rr": 2.5, "align_score": 3}

    async def price_lookup(contract):
        return 0.7 if contract == A else 1.0

    act = await pt.run_paper_cycle(
        candidates=[A, B], analyzer=both_signals, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert [o["contract"] for o in act["opened"]] == [B]
    assert not await pt.has_open(A)
    assert await pt.has_open(B)


# ── garde-fou re-achat après vente sur invalidation, conviction 2x (07/24,
# observation opérateur directe sur une vraie position AERO : "vente puis achat
# suspect sauf si elle y croit deux fois plus") ─────────────────────────────

class TestLastInvalidationExitRR:
    """Tests unitaires purs -- même patron que TestConsecutiveLossesForContract."""

    @pytest.mark.asyncio
    async def test_no_positions_returns_none(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        assert await pt._last_invalidation_exit_rr(A) is None

    @pytest.mark.asyncio
    async def test_most_recent_close_was_invalidation_returns_its_rr(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, rr=1.2, wallet="swing")
        await pt.close_position(A, 0.8, reason="invalidation")
        assert await pt._last_invalidation_exit_rr(A) == 1.2

    @pytest.mark.asyncio
    async def test_most_recent_close_was_not_invalidation_returns_none(self, tmp_db):
        """Le garde-fou ne cible QUE le motif "vente-puis-rachat immédiat" -- une
        clôture récente pour une autre raison (stop suiveur, cible) neutralise
        le signal, même si une invalidation existe plus loin dans l'historique."""
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, rr=1.2, wallet="swing")
        await pt.close_position(A, 0.8, reason="invalidation")
        await pt.open_position(A, "AAA", 0.8, alloc_usd=50_000, rr=2.0, wallet="swing")
        await pt.close_position(A, 1.0, reason="stop suiveur")
        assert await pt._last_invalidation_exit_rr(A) is None

    @pytest.mark.asyncio
    async def test_open_position_ignored_never_counted(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, rr=1.2, wallet="swing")
        await pt.close_position(A, 0.8, reason="invalidation")
        await pt.open_position(A, "AAA", 0.8, alloc_usd=50_000, rr=2.0, wallet="swing")  # encore ouverte
        assert await pt._last_invalidation_exit_rr(A) == 1.2


@pytest.mark.asyncio
async def test_reentry_after_invalidation_blocked_without_double_conviction(tmp_db):
    """Vente sur invalidation (rr=1.2), rachat immédiat avec un rr à peine plus
    haut (1.5, < 2x) -- doit être rejeté."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, rr=1.2, wallet="swing")
    await pt.close_position(A, 0.8, reason="invalidation")

    async def weak_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 0.8, "rr": 1.5, "align_score": 2}

    async def price_lookup(contract):
        return 0.8

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=weak_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert act["opened"] == []
    assert not await pt.has_open(A)


@pytest.mark.asyncio
async def test_reentry_after_invalidation_allowed_with_double_conviction(tmp_db):
    """Même scénario, mais le nouveau signal a EXACTEMENT 2x le rr invalidé
    (1.2 -> 2.4) -- autorisé (le seuil est inclusif, pas strictement supérieur)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, rr=1.2, wallet="swing")
    await pt.close_position(A, 0.8, reason="invalidation")

    async def strong_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 0.8, "rr": 2.4, "align_score": 3}

    async def price_lookup(contract):
        return 0.8

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=strong_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert len(act["opened"]) == 1


@pytest.mark.asyncio
async def test_reentry_after_invalidation_blocked_when_new_rr_missing(tmp_db):
    """Un signal sans rr du tout ne peut jamais prouver une conviction double --
    dégradation sûre, jamais un OK par défaut."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, rr=1.2, wallet="swing")
    await pt.close_position(A, 0.8, reason="invalidation")

    async def no_rr_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 0.8, "align_score": 3}

    async def price_lookup(contract):
        return 0.8

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=no_rr_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert act["opened"] == []


@pytest.mark.asyncio
async def test_reentry_allowed_when_most_recent_close_was_not_invalidation(tmp_db):
    """Non-régression : une clôture sur "cible" (jamais une invalidation) ne
    déclenche jamais ce garde-fou, même avec un rr faible sur le nouveau signal."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, rr=1.2, wallet="swing")
    await pt.close_position(A, 1.5, reason="cible")

    async def weak_signal(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 1.5, "rr": 1.3, "align_score": 2}

    async def price_lookup(contract):
        return 1.5

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=weak_signal, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert len(act["opened"]) == 1


@pytest.mark.asyncio
async def test_invalidation_reentry_gate_scoped_to_one_contract_only(tmp_db):
    """Chirurgical : le blocage de A (invalidation récente, conviction insuffisante)
    ne doit jamais affecter B, un contrat totalement différent."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, rr=1.2, wallet="swing")
    await pt.close_position(A, 0.8, reason="invalidation")

    async def both_signals(contract):
        if contract == A:
            return {"action": "BUY", "symbol": "AAA", "price": 0.8, "rr": 1.5, "align_score": 2}
        return {"action": "BUY", "symbol": "BBB", "price": 1.0, "rr": 2.5, "align_score": 3}

    async def price_lookup(contract):
        return 0.8 if contract == A else 1.0

    act = await pt.run_paper_cycle(
        candidates=[A, B], analyzer=both_signals, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    assert [o["contract"] for o in act["opened"]] == [B]
    assert not await pt.has_open(A)
    assert await pt.has_open(B)


@pytest.mark.asyncio
async def test_all_tp_stages_hit_in_one_jump_closes_fully(tmp_db):
    """Un bond de prix qui dépasse TOUS les paliers d'un coup ne laisse jamais une
    position résiduelle ouverte -- le dernier palier clôture ce qui reste."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

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
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.9, alloc_usd=90_000, wallet="swing")

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
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

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


# ── timeout de stagnation scalping (08/01, bug réel trouvé en direct : 19/21 ────────
# positions scalping ouvertes gelées à 0% de mouvement depuis l'entrée, certaines
# depuis 19h+, rien ne les fermait jamais -- le capital restait bloqué sur des setups
# morts au lieu de tourner vers de nouveaux candidats) ──────────────────────────────

@pytest.mark.asyncio
async def test_scalping_stagnant_position_closed_after_timeout(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="scalping", mode="scalping",
    )
    await _backdate_opened_at(D, pt.SCALPING_STAGNATION_TIMEOUT_HOURS + 0.5)

    async def price_lookup(contract):
        return 1.0  # jamais bougé depuis l'entrée

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "timeout stagnation (scalping)"
    assert not await pt.has_open(D)
    assert "Timeout de stagnation" in act["closed"][0]["close_notes"]


@pytest.mark.asyncio
async def test_scalping_position_not_closed_before_timeout_elapsed(tmp_db):
    """Moins de SCALPING_STAGNATION_TIMEOUT_HOURS écoulées -- reste ouverte même
    si le prix n'a jamais bougé (pas encore assez de temps pour conclure)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="scalping", mode="scalping",
    )
    await _backdate_opened_at(D, pt.SCALPING_STAGNATION_TIMEOUT_HOURS - 0.5)

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    assert await pt.has_open(D)


@pytest.mark.asyncio
async def test_scalping_position_with_real_movement_never_times_out(tmp_db):
    """Le timeout ne concerne QUE les positions genuinely stagnantes -- une position
    scalping qui a réellement dépassé le seuil de mouvement minimum, même après le
    délai, ne doit jamais être fermée par ce mécanisme."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="scalping", mode="scalping",
    )
    await _backdate_opened_at(D, pt.SCALPING_STAGNATION_TIMEOUT_HOURS + 0.5)

    # +10% -- généreusement au-dessus de SCALPING_STAGNATION_MIN_MOVE_PCT (1%),
    # marge large pour absorber l'impact de prix simulé à l'entrée/sortie
    # (open_position/close_position dégradent le prix "spot" demandé, cf.
    # simulated_fill_price()) sans faire dépendre le test d'un calcul exact.
    prices = {"v": 1.10}

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert await pt.has_open(D)

    # Laisse le temps à la confirmation temporelle du stop suiveur (même
    # mécanique anti-mèche que le reste du fichier, cf. HIGH_WATER_
    # CONFIRMATION_SECONDS) -- sans ça, un retour de prix AVANT confirmation
    # abandonne la candidature pending (comportement voulu du stop suiveur,
    # anti-mèche), un cas distinct de celui testé ici (un vrai mouvement
    # SOUTENU qui a eu le temps d'être confirmé).
    await _backdate_pending_since(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)  # confirme le ratchet
    pos = await pt._get_open(D)
    assert pos["high_water_price"] > pos["entry_price"] * 1.05  # ratché, largement au-dessus de l'entrée

    prices["v"] = 1.0  # retombe à l'entrée -- toujours pas fermé (le plus haut CONFIRMÉ a dépassé le seuil)
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    assert await pt.has_open(D)


@pytest.mark.asyncio
async def test_swing_stagnant_position_never_times_out(tmp_db):
    """Le timeout est scalping-only -- une position swing/standard stagnante depuis
    largement plus longtemps que le seuil scalping reste ouverte indéfiniment
    (tolérance de durée différente par design)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="swing", mode="standard",
    )
    await _backdate_opened_at(D, pt.SCALPING_STAGNATION_TIMEOUT_HOURS * 10)

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    assert await pt.has_open(D)


@pytest.mark.asyncio
async def test_scalping_generic_stop_takes_priority_over_stagnation_timeout(tmp_db):
    """Si le stop générique (invalidation) se déclenche sur la même position, elle se
    ferme via ce chemin normal -- jamais un double traitement ni un conflit."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.9, alloc_usd=90_000, wallet="scalping", mode="scalping",
    )
    await _backdate_opened_at(D, pt.SCALPING_STAGNATION_TIMEOUT_HOURS + 0.5)

    async def price_lookup(contract):
        return 0.8  # sous l'invalidation

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "invalidation"
    assert not await pt.has_open(D)


@pytest.mark.asyncio
async def test_scalping_stagnation_timeout_never_fires_on_a_real_drawdown(tmp_db):
    """08/02 -- real bug found live (diagnostic workflow: 7/7 closed scalping
    trades lost, ALL via this timeout closing blindly at whatever the current
    price was, none via the ATR trailing stop): a position already down
    beyond the same threshold that defines "no significant move" on the
    upside isn't stagnant, it's a real drawdown -- must be left for the
    (now-repaired) trailing stop to handle, never force-closed here."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="scalping", mode="scalping",
    )
    await _backdate_opened_at(D, pt.SCALPING_STAGNATION_TIMEOUT_HOURS + 0.5)

    # -3% -- never moved up (peak_gain_pct stays under the 1% floor, the OLD
    # sole condition), but already down well past the SAME 1% threshold on
    # the downside -- must NOT be treated as "stagnant".
    async def price_lookup(contract):
        return 0.97

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    assert await pt.has_open(D)


@pytest.mark.asyncio
async def test_scalping_stagnation_timeout_still_fires_on_a_genuinely_flat_position(tmp_db):
    """A LIGHT move within the symmetric tolerance band (here -0.5%, under the
    1% floor on both sides) is still genuine stagnation -- the timeout must
    keep firing for the case it was actually built for (08/01 incident:
    positions frozen at ~0% for 19h+, capital stuck on dead setups)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="scalping", mode="scalping",
    )
    await _backdate_opened_at(D, pt.SCALPING_STAGNATION_TIMEOUT_HOURS + 0.5)
    # The real stored entry_price differs from the nominal 1.0 requested
    # (simulated scalping swap fee applied on buy) -- compute the test price
    # relative to what was ACTUALLY stored, not the nominal ask.
    real_entry_price = (await pt._get_open(D))["entry_price"]

    async def price_lookup(contract):
        return real_entry_price * 0.995  # -0.5% off the REAL entry, inside the tolerance band

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "timeout stagnation (scalping)"
    assert not await pt.has_open(D)


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

    # ── mode="scalping" (03/08, 9-pocket diagnostic, docs/HANDOFF_LLM.md) ──
    # les bornes partagées ci-dessus etaient pensees pour le swing/standard --
    # non atteintes par les pertes reelles de scalping (1,7%-3,6%, toutes sous
    # l'ancien plancher 5%).

    def test_scalping_mode_none_still_falls_back_to_fixed_default(self):
        assert pt._effective_trail_pct(None, mode="scalping") == pt.TRAIL_STOP_PCT

    def test_scalping_mode_mid_range_uses_dedicated_multiplier(self):
        # 3 % d'ATR * 2.5 = 7.5 %, dans les bornes scalping [1.5 %, 10 %].
        assert pt._effective_trail_pct(0.03, mode="scalping") == pytest.approx(0.075)

    def test_scalping_mode_low_atr_clamped_to_scalping_floor(self):
        # 0.5 % d'ATR * 2.5 = 1.25 %, sous le plancher scalping 1.5 % -> clampé.
        assert pt._effective_trail_pct(0.005, mode="scalping") == pt.MIN_ATR_TRAIL_PCT_SCALPING

    def test_scalping_mode_high_atr_clamped_to_scalping_ceiling(self):
        # 50 % d'ATR * 2.5 = 125 %, largement au-dessus du plafond scalping 10 % -> clampé.
        assert pt._effective_trail_pct(0.50, mode="scalping") == pt.MAX_ATR_TRAIL_PCT_SCALPING

    def test_scalping_floor_is_stricter_than_standard_floor(self):
        # meme entry_atr_pct, le mode scalping doit produire un stop PLUS SERRE
        # que le mode standard -- exactement le point du diagnostic (le plancher
        # standard 5% ne se declenchait jamais sur des mouvements de 1,7%-3,6%).
        atr = 0.01
        assert pt._effective_trail_pct(atr, mode="scalping") < pt._effective_trail_pct(atr, mode=None)

    def test_non_scalping_mode_string_keeps_standard_bounds(self):
        # toute valeur de mode autre que "scalping" (y compris "standard")
        # garde le comportement historique partage -- jamais un changement
        # de comportement pour un appelant qui passe explicitement "standard".
        assert pt._effective_trail_pct(0.01, mode="standard") == pt.MIN_ATR_TRAIL_PCT
        assert pt._effective_trail_pct(0.50, mode="standard") == pt.MAX_ATR_TRAIL_PCT


class TestSatellitePocketEligibleEntryAtrPct:
    """Item #253 (08/02) -- _satellite_pocket_eligible reads entry_atr_pct via
    _compute_active_stop; a missing value (the bug this chantier fixes) falls
    back to the fixed 15% trail instead of a real ATR-calibrated one, which
    can flip the eligibility verdict itself, not just the stop width shown
    elsewhere. Both positions otherwise identical: entry_price=1.0,
    high_water_price=1.0 (no latent gain), invalidation_price=0.5 (never
    dominates the max()), target_price=2.0, strategy=momentum, entry/current
    regime both Euphoria (gate #4 satisfied either way), price=0.90."""

    def _position(self, entry_atr_pct):
        return {
            "strategy": "momentum", "entry_price": 1.0, "high_water_price": 1.0,
            "invalidation_price": 0.5, "target_price": 2.0,
            "entry_regime": "euphorie", "entry_atr_pct": entry_atr_pct,
            "breakeven_locked": False,
        }

    def test_none_uses_fixed_stop_stays_eligible(self):
        # trail=15% (fallback) -> active_stop=0.85, price 0.90 > 0.85 (not
        # touched) -> remaining_rr=(2.0-0.90)/(0.90-0.85)=22.0 >= 1.5.
        eligible, remaining_rr = pt._satellite_pocket_eligible(
            self._position(None), price=0.90, current_regime="euphorie",
        )
        assert eligible is True
        assert remaining_rr == pytest.approx(22.0)

    def test_real_low_atr_tighter_stop_flips_to_ineligible(self):
        # entry_atr_pct=0.01 -> trail clamped to MIN_ATR_TRAIL_PCT (5%) ->
        # active_stop=0.95, price 0.90 <= 0.95 (already touched) -> never
        # eligible -- same position, opposite verdict, purely because
        # entry_atr_pct is now populated instead of None.
        eligible, remaining_rr = pt._satellite_pocket_eligible(
            self._position(0.01), price=0.90, current_regime="euphorie",
        )
        assert eligible is False
        assert remaining_rr is None


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
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

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
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

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


def test_breakeven_floor_threshold_scalping_mode_uses_lower_floor():
    """08/04, real gap found live: a typical scalping TP1 (target 1.03, +3%,
    consistent with the now-corrected ATR invalidation floor's tighter range)
    gives 50%*3%=1.5%, clamped by the 8% swing floor without mode -- the
    breakeven safety net this mechanism exists to provide almost never
    engages on scalping as a result. With mode="scalping", the dedicated 2%
    floor takes over instead, still above the raw 1.5% but far below 8%."""
    swing_threshold = pt._breakeven_floor_threshold(1.03, 1.0)
    scalping_threshold = pt._breakeven_floor_threshold(1.03, 1.0, mode="scalping")
    assert swing_threshold == pytest.approx(pt.BREAKEVEN_FLOOR_MIN_PCT)
    assert scalping_threshold == pytest.approx(pt.BREAKEVEN_FLOOR_MIN_PCT_SCALPING)
    assert scalping_threshold < swing_threshold


def test_breakeven_floor_threshold_scalping_mode_still_uses_ratio_when_above_its_floor():
    """Un TP1 large (+40%, cas rare mais possible en scalping) doit toujours
    utiliser 50% de la distance réelle même en mode scalping -- le plancher
    dédié n'écrase jamais un seuil déjà légitimement au-dessus de lui."""
    threshold = pt._breakeven_floor_threshold(1.4, 1.0, mode="scalping")
    assert threshold == pytest.approx(0.20)  # 50% * 40%, identique au cas swing


def test_breakeven_floor_threshold_unknown_mode_falls_back_to_swing_floor():
    for mode in (None, "standard", "vc"):
        assert pt._breakeven_floor_threshold(1.03, 1.0, mode=mode) == pytest.approx(pt.BREAKEVEN_FLOOR_MIN_PCT)


@pytest.mark.asyncio
async def test_breakeven_floor_single_touch_starts_candidacy_not_yet_locked(tmp_db):
    """20/07 -- confirmation temporelle (revue croisée externe, corrige l'asymétrie
    face au ratchet high_water) : une SEULE lecture qui touche le seuil flash démarre
    seulement la candidature, ne verrouille plus l'instant d'après."""
    await pt.reset_portfolio(1_000_000.0)
    # TP1 = +40% -> seuil flash = 50%*40% = +20% (prix 1.20), au-dessus du plancher 8%.
    await pt.open_position(D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

    prices = {"v": 1.25}  # touche le seuil flash (+20%)

    async def price_lookup(contract):
        return prices["v"]

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    pos = await pt._get_open(D)
    assert pos["breakeven_locked"] == 0  # pas encore -- candidature seulement
    assert pos["breakeven_pending_since"] is not None


@pytest.mark.asyncio
async def test_breakeven_floor_resets_if_price_drops_before_confirmation(tmp_db):
    """20/07 -- exactement le scénario que la revue croisée visait à corriger : un
    pump-puis-dump rapide qui retombe AVANT la confirmation ne doit PLUS verrouiller
    le point mort (candidature abandonnée, preuve qu'elle n'était pas soutenue)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

    prices = {"v": 1.25}

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["breakeven_pending_since"] is not None

    prices["v"] = 1.05  # retombé sous le seuil flash (+20%), AVANT confirmation
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["breakeven_locked"] == 0
    assert pos["breakeven_pending_since"] is None  # candidature abandonnée


@pytest.mark.asyncio
async def test_breakeven_floor_locks_after_sustained_touch_and_protects_against_deeper_crash(tmp_db):
    """Cas central (Gemini, Point 2, Piste B), version confirmée (20/07) : une fois le
    seuil flash tenu au moins HIGH_WATER_CONFIRMATION_SECONDS, le point mort se
    verrouille pour de bon et protège malgré un crash qui suit."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

    prices = {"v": 1.25}

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    await _backdate_breakeven_pending(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    pos = await pt._get_open(D)
    assert pos["breakeven_locked"] == 1
    assert pos["breakeven_pending_since"] is None  # effacée une fois verrouillée

    prices["v"] = 0.95  # crash -- SOUS le point mort (1.0), au-dessus de l'ancien stop (0.85)
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    closed = act["closed"][0]
    assert closed["close_reason"] == "breakeven hard floor"
    assert closed["exit_price"] == pytest.approx(0.95)
    assert "Point mort verrouillé" in closed["close_notes"]
    assert "+20%" in closed["close_notes"]


@pytest.mark.asyncio
async def test_breakeven_floor_scalping_mode_starts_candidacy_below_swing_floor(tmp_db):
    """08/04, real gap found live: TP1 ~= +3% from the REAL entry (target
    1.0403, entry_price becomes 1.01 after open_position's scalping-mode 1%
    DEX buy fee -- see risk_guard.DEX_SWAP_FEE_PCT) -> raw threshold
    50%*3%=1.5%. Without mode, the 8% swing floor dominates -- this setup
    would never start candidacy at all. With mode="scalping" (2% floor), a
    price of 1.031 (~+2.08% from the real 1.01 entry) DOES touch the flash
    threshold (1.01*1.02=1.0302) and starts candidacy -- proves the safety
    net actually engages on a realistic scalping setup instead of being
    structurally unreachable."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, target_price=1.0403, invalidation_price=0.97, alloc_usd=500.0,
        wallet="scalping_v6", mode="scalping",
    )

    prices = {"v": 1.031}  # touche le seuil flash scalping (1.01*1.02=1.0302)

    async def price_lookup(contract):
        return prices["v"]

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    pos = await pt._get_open(D)
    assert pos["breakeven_locked"] == 0  # candidature seulement, pas encore verrouillé
    assert pos["breakeven_pending_since"] is not None


@pytest.mark.asyncio
async def test_breakeven_floor_same_price_never_starts_candidacy_without_scalping_mode(tmp_db):
    """Même target_price (1.0403), même prix touché (1.031, ~+3.1% -- swing
    n'applique aucun frais d'achat, entry_price reste exactement 1.0), mode
    "standard" -- le plancher swing (8%, seuil 1.08) domine, la candidature ne
    démarre JAMAIS à ce prix. Preuve que le fix précédent est bien scopé au
    mode, jamais un changement de comportement global."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, target_price=1.0403, invalidation_price=0.97, alloc_usd=90_000, wallet="swing",
    )

    prices = {"v": 1.031}

    async def price_lookup(contract):
        return prices["v"]

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    pos = await pt._get_open(D)
    assert pos["breakeven_locked"] == 0
    assert pos["breakeven_pending_since"] is None  # jamais entré en candidature


@pytest.mark.asyncio
async def test_breakeven_floor_never_triggers_when_price_never_touches_flash_threshold(tmp_db):
    """Non-régression : une position dont le prix ne dépasse jamais le seuil flash se
    comporte exactement comme avant ce correctif (stop suiveur classique)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

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
    await pt.open_position(D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

    prices = {"v": 1.30}  # +30% -- au-dessus du seuil flash (+20%), sous TP1 (+40%)

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["breakeven_locked"] == 0  # candidature seulement (20/07, confirmation temporelle)

    # Recule les DEUX candidatures (high_water ET breakeven) démarrées par le cycle
    # ci-dessus -- même durée de confirmation, aucune raison de les décaler l'une de
    # l'autre pour ce test.
    await _backdate_pending_since(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    await _backdate_breakeven_pending(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    pos = await pt._get_open(D)
    assert pos["high_water_price"] == pytest.approx(1.30)  # confirmé
    assert pos["breakeven_locked"] == 1  # confirmé aussi

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
    await pt.open_position(D, "DDD", 1.0, target_price=1.4, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

    prices = {"v": 1.25}

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    await _backdate_breakeven_pending(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
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
        entry_regime="peur", wallet="swing",
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
        entry_regime="euphorie", wallet="swing",
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
        entry_regime="peur", wallet="swing",
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
    await pt.open_position(D, "DDD", 1.0, target_price=1.2, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

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
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

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
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, entry_atr_pct=0.10, wallet="swing",
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
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, entry_atr_pct=0.01, wallet="swing",
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
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, entry_atr_pct=0.08, wallet="swing")
    assert pos["entry_atr_pct"] == pytest.approx(0.08)


@pytest.mark.asyncio
async def test_open_position_persists_golden_pocket_bounds(tmp_db):
    """Item #101 (26/07), demande operateur ("aria doit pouvoir connaitre en
    temps reel toute les valeurs de son golden pocket d'entree et de
    sortie") -- gp_low/gp_high doivent survivre la persistance, pas seulement
    exister dans le dict signal ephemere."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, gp_low=0.95, gp_high=1.05, wallet="swing")
    assert pos["gp_low"] == pytest.approx(0.95)
    assert pos["gp_high"] == pytest.approx(1.05)


@pytest.mark.asyncio
async def test_open_position_golden_pocket_bounds_default_to_none(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    assert pos["gp_low"] is None
    assert pos["gp_high"] is None


@pytest.mark.asyncio
async def test_open_position_removes_contract_from_manual_queue(tmp_db):
    """Item #236, 30/07: a contract queued via /add is drained from
    manual_candidates once actually bought -- no longer needs re-discovery
    every cycle."""
    from aria_core import manual_candidates

    await manual_candidates.add_manual_candidate(A, "base")
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")

    assert await manual_candidates.list_pending_manual_candidates() == []


@pytest.mark.asyncio
async def test_open_position_never_open_never_manually_queued_is_a_noop(tmp_db):
    """The vast majority of buys never went through /add -- cleanup must be a
    harmless no-op, not an error."""
    from aria_core import manual_candidates

    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")

    assert pos is not None
    assert await manual_candidates.list_pending_manual_candidates() == []


@pytest.mark.asyncio
async def test_open_position_entry_atr_pct_defaults_to_none(tmp_db):
    """Non-régression : positions ouvertes sans ATR (ex. ancien pilote VC-thesis) --
    ``entry_atr_pct`` reste ``None``, comportement de stop suiveur inchangé."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    assert pos["entry_atr_pct"] is None


from aria_core.risk_guard import DEX_SWAP_FEE_PCT


@pytest.mark.asyncio
async def test_scalping_mode_applies_real_dex_swap_fee_on_buy(tmp_db):
    """Item #101 (26/07), operator request ("verifie [les frais/slippage]") :
    en mode scalping, le prix de remplissage a l'achat integre le frais de
    swap DEX reel (1%, Uniswap v3 tier standard pour paire volatile) -- jamais
    applique en mode standard (comportement historique inchange)."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, mode="scalping", wallet="swing")
    assert pos["entry_price"] == pytest.approx(1.0 * (1.0 + DEX_SWAP_FEE_PCT))


@pytest.mark.asyncio
async def test_standard_mode_never_applies_dex_swap_fee_on_buy(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    assert pos["entry_price"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_scalping_mode_applies_real_dex_swap_fee_on_sell(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, mode="scalping", wallet="swing")
    closed = await pt.close_position(A, 1.5)
    assert closed["exit_price"] == pytest.approx(1.5 * (1.0 - DEX_SWAP_FEE_PCT))


@pytest.mark.asyncio
async def test_standard_mode_never_applies_dex_swap_fee_on_sell(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    closed = await pt.close_position(A, 1.5)
    assert closed["exit_price"] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_scalping_mode_applies_real_dex_swap_fee_on_partial_sell(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, mode="scalping", wallet="swing")
    partial = await pt.reduce_position(A, 1.5, 10_000.0, stage=1)
    assert partial["exit_price"] == pytest.approx(1.5 * (1.0 - DEX_SWAP_FEE_PCT))


# ── Item #105 (26/07) -- vente sur divergence RSI baissière confirmée (scalping) ──

def _bearish_divergence_candles():
    """Même série calibrée que test_entry_signals.py::_bearish_setup_series() --
    RSI au sommet récent ~69 (dans [60,80]), divergence confirmée."""
    from aria_core.skills.ta_levels import Candle

    lead_in = [100.0] * 15
    euphoria = [100, 110, 118, 123]
    pullback = [115, 107, 102, 99, 97]
    retest = [104, 112, 121, 125]
    tail = [120]
    closes = lead_in + euphoria + pullback + retest + tail
    return [Candle(ts=i, open=c, high=c, low=c, close=c, volume=1_000.0) for i, c in enumerate(closes)]


def _scalping_exit_pair_lookup(*, price):
    async def fake_pair_lookup(contract, *, chain="base"):
        from aria_core.services.dexscreener import PairSnapshot

        return PairSnapshot(
            pair_address="0xpool", price_usd=price, liquidity_usd=200_000.0,
            volume_24h_usd=10_000.0, base_symbol="AAA",
        )

    return fake_pair_lookup


@pytest.mark.asyncio
async def test_scalping_position_closes_on_bearish_divergence_when_target_not_reached(tmp_db, monkeypatch):
    await pt.reset_portfolio(1_000_000.0)
    await pt.set_trading_mode("scalping")
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, mode="scalping", wallet="swing")
    monkeypatch.setattr(pt, "_default_pair_lookup", _scalping_exit_pair_lookup(price=1.2))

    async def fake_fetch_candles(*args, **kwargs):
        return _bearish_divergence_candles()

    monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch_candles)

    act = await pt.run_paper_cycle(candidates=[])

    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "divergence RSI baissière (scalping)"
    assert not await pt.has_open(A)


@pytest.mark.asyncio
async def test_scalping_position_stays_open_on_bearish_divergence_when_target_already_reached(
    tmp_db, monkeypatch,
):
    """Objectif de profit 24h déjà atteint -- la divergence baissière ne force
    plus rien, le comportement normal (stop suiveur/paliers TP) continue de
    gouverner seul."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.set_trading_mode("scalping")
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, mode="scalping", wallet="swing")
    monkeypatch.setattr(pt, "_default_pair_lookup", _scalping_exit_pair_lookup(price=1.01))

    async def fake_fetch_candles(*args, **kwargs):
        return _bearish_divergence_candles()

    monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch_candles)

    async def fake_summary(*args, **kwargs):
        return {"equity": 1_000_000.0 + pt.DAILY_FLOOR_TARGET_PROFIT_USD + 1.0}

    monkeypatch.setattr(pt, "portfolio_summary", fake_summary)

    act = await pt.run_paper_cycle(candidates=[])

    assert act["closed"] == []
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_standard_mode_never_checks_bearish_divergence(tmp_db, monkeypatch):
    """Non-régression : mode standard (défaut) ne déclenche jamais ce check,
    même face à une divergence baissière confirmée."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")  # mode="standard" implicite
    monkeypatch.setattr(pt, "_default_pair_lookup", _scalping_exit_pair_lookup(price=1.2))

    called = False

    async def fake_fetch_candles(*args, **kwargs):
        nonlocal called
        called = True
        return _bearish_divergence_candles()

    monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch_candles)

    act = await pt.run_paper_cycle(candidates=[])

    assert called is False
    assert act["closed"] == []
    assert await pt.has_open(A)


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
    await pt.open_position(C, "CCC", 1.0, alloc_usd=90_000, wallet="swing")  # qty = 90_000

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
    await pt.open_position(E, "EEE", 1.0, alloc_usd=90_000, wallet="swing")  # qty = 90_000

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


# ── multi-position-per-contract prerequisite (27/07, multi-pocket plan) ──────
# ``open_position`` still refuses a 2nd position on the same contract today
# (has_open(contract) with no strategy/wallet filter, Phase 2 will relax
# this) -- these tests insert a 2nd open row DIRECTLY via SQL to simulate the
# future multi-pocket state before Phase 2 lands, proving close_position/
# reduce_position already behave correctly once that state exists.

async def _duplicate_open_position_row(contract: str) -> int:
    """Test-only helper: clones the sole open row for ``contract`` into a
    second open row (different id, same contract) -- simulates 2 pockets
    holding the same token simultaneously, ahead of Phase 2's real sourcing
    change."""
    import aiosqlite

    async with aiosqlite.connect(pt.DB_PATH) as db:
        cols = ", ".join(c for c in pt._POS_FIELDS if c != "id")
        async with db.execute(
            f"SELECT {cols} FROM paper_position WHERE LOWER(contract) = ? AND status = 'open'",
            (contract.lower(),),
        ) as cur:
            row = await cur.fetchone()
        placeholders = ", ".join("?" for _ in row)
        cur2 = await db.execute(
            f"INSERT INTO paper_position ({cols}) VALUES ({placeholders})", row,
        )
        await db.commit()
        return cur2.lastrowid


@pytest.mark.asyncio
async def test_get_open_raises_on_ambiguous_contract_without_position_id(tmp_db):
    """Real bug class this closes: once 2 positions can legally share a
    contract (one per pocket), a caller that forgets to pass ``position_id``
    must fail LOUDLY, never silently resolve to an arbitrary row."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=10_000, wallet="swing")
    second_id = await _duplicate_open_position_row(A)

    with pytest.raises(RuntimeError, match="ambiguity"):
        await pt._get_open(A)

    # sanity: the duplicated row is genuinely open, resolvable by id
    duplicated = await pt._get_open(A, position_id=second_id)
    assert duplicated is not None
    assert duplicated["status"] == "open"


@pytest.mark.asyncio
async def test_close_position_by_id_closes_only_the_targeted_row(tmp_db):
    """The core fix: close_position(contract, ..., position_id=X) must close
    EXACTLY row X, leaving a second open position on the SAME contract
    completely untouched."""
    await pt.reset_portfolio(1_000_000.0)
    opened = await pt.open_position(A, "AAA", 1.0, alloc_usd=10_000, wallet="swing")
    first_id = opened["id"]
    second_id = await _duplicate_open_position_row(A)
    assert first_id != second_id

    closed = await pt.close_position(A, 2.0, reason="cible", position_id=first_id)
    assert closed is not None
    assert closed["id"] == first_id

    # the first row is now closed...
    async with __import__("aiosqlite").connect(pt.DB_PATH) as db:
        async with db.execute(
            "SELECT status FROM paper_position WHERE id = ?", (first_id,),
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == "closed"

    # ...but the SECOND row on the same contract is untouched (still open)
    still_open = await pt._get_open(A, position_id=second_id)
    assert still_open is not None
    assert still_open["status"] == "open"


@pytest.mark.asyncio
async def test_reduce_position_by_id_reduces_only_the_targeted_row(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    opened = await pt.open_position(A, "AAA", 1.0, alloc_usd=90_000, wallet="swing")
    first_id = opened["id"]
    second_id = await _duplicate_open_position_row(A)

    partial = await pt.reduce_position(A, 1.5, 30_000, stage=1, position_id=first_id)
    assert partial is not None

    reduced = await pt._get_open(A, position_id=first_id)
    assert reduced["qty"] == 60_000  # 90_000 - 30_000

    untouched = await pt._get_open(A, position_id=second_id)
    assert untouched["qty"] == 90_000  # the OTHER position never touched


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
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=1_000, wallet="swing") is not None
    # au-delà du plafond, refus
    assert await pt.open_position("0x" + "f" * 40, "OVER", 1.0, alloc_usd=1_000, wallet="swing") is None


@pytest.mark.asyncio
async def test_max_positions_never_capped_in_scalping_mode(tmp_db):
    """Item #101 (26/07), décision opérateur explicite ("laisse libre, voyons
    comment ARIA trade sans la force") -- aucun plafond de nombre de positions
    en mode scalping, contrairement au mode standard."""
    await pt.reset_portfolio(1_000_000.0)
    for i in range(pt.MAX_POSITIONS + 5):
        c = "0x" + f"{i:040x}"
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=1_000, mode="scalping", wallet="swing") is not None


@pytest.mark.asyncio
async def test_trading_mode_defaults_to_standard(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    assert await pt.get_trading_mode() == "standard"


@pytest.mark.asyncio
async def test_trading_mode_can_be_switched_to_scalping(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.set_trading_mode("scalping")
    assert await pt.get_trading_mode() == "scalping"


@pytest.mark.asyncio
async def test_trading_mode_rejects_unknown_value(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    with pytest.raises(ValueError):
        await pt.set_trading_mode("swing")


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
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, category="clanker", wallet="swing")
    assert pos["category"] == "clanker"


@pytest.mark.asyncio
async def test_concentration_cap_shrinks_alloc_near_the_limit(tmp_db):
    """Plafond 40% de 1M = 400k. 7 positions de 50k (350k) déjà ouvertes dans la même
    catégorie -- une 8e demandant 50k ne doit tenir que 50k (350k+50k=400k, pile le
    plafond), pas être refusée pour autant."""
    await pt.reset_portfolio(1_000_000.0)
    for i in range(7):
        c = "0x" + f"{i:040x}"
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=50_000, category="clanker", wallet="swing") is not None

    pos = await pt.open_position("0x" + "7" * 40, "T7", 1.0, alloc_usd=50_000, category="clanker", wallet="swing")
    assert pos is not None
    assert pos["cost_usd"] == 50_000


@pytest.mark.asyncio
async def test_concentration_cap_skips_when_room_too_small(tmp_db):
    """8 positions de 50k (400k, pile le plafond) -- une 9e n'a plus AUCUNE place :
    skip (None), pas une position poussière."""
    await pt.reset_portfolio(1_000_000.0)
    for i in range(8):
        c = "0x" + f"{i:040x}"
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=50_000, category="clanker", wallet="swing") is not None

    over = await pt.open_position("0x" + "8" * 40, "T8", 1.0, alloc_usd=50_000, category="clanker", wallet="swing")
    assert over is None


@pytest.mark.asyncio
async def test_concentration_cap_does_not_affect_other_categories(tmp_db):
    """Le plafond est PAR catégorie -- une catégorie saturée ne bloque pas les autres."""
    await pt.reset_portfolio(1_000_000.0)
    for i in range(8):
        c = "0x" + f"{i:040x}"
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=50_000, category="clanker", wallet="swing") is not None


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

    other = await pt.open_position("0x" + "9" * 40, "OTHER", 1.0, alloc_usd=50_000, category="virtuals_bonding", wallet="swing")
    assert other is not None
    assert other["cost_usd"] == 50_000


@pytest.mark.asyncio
async def test_run_cycle_closes_position_on_new_security_signal(tmp_db, monkeypatch):
    from aria_core import paper_trader_risk as risk

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000,
        entry_security_json=risk.EntrySecuritySnapshot(is_honeypot=False).to_json(), wallet="swing",
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
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")  # sans entry_security_json

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
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")  # pas d'entry_security_json

    async def price_lookup(contract):
        return 1.0  # ni stop ni TP déclenché

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["closed"] == []
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_run_cycle_position_management_stops_when_paused(tmp_db, monkeypatch):
    """05/08, real gap found live (operator: "/off doit couper toute la
    chaine achat ET vente, donc les API ne sont plus sollicitees non plus"):
    the position-management loop (security re-scan + price lookup on every
    OPEN position) never checked paper_pause.is_paused() at all -- a cycle
    already in flight when /off fires kept re-soliciting GoPlus/Blockscout/
    DexScreener for every open position regardless. Must stop immediately,
    same as the 04/08 buy-side fix -- no position touched, no rescan/price
    call made, once paused."""
    from aria_core import paper_pause, paper_trader_risk as risk

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000,
        entry_security_json=risk.EntrySecuritySnapshot(is_honeypot=False).to_json(), wallet="swing",
    )

    monkeypatch.setattr(paper_pause, "is_paused", lambda: True)

    calls = {"n": 0}

    async def fake_rescan(position, *, pair=None):
        calls["n"] += 1
        return {"contract": position["contract"], "reasons": ["honeypot détecté (absent à l'entrée)"]}

    monkeypatch.setattr(risk, "rescan_open_position", fake_rescan)

    async def price_lookup(contract):
        calls["n"] += 1
        return 1.2

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert act["checked"] == 0
    assert act["closed"] == []
    assert calls["n"] == 0
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
    await pt.open_position(B, "BBB", 1.0, invalidation_price=0.9, alloc_usd=50_000, wallet="swing")

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
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, chain="solana", wallet="swing")
    assert pos["chain"] == "solana"


@pytest.mark.asyncio
async def test_open_position_defaults_chain_to_base(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
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
    pos = await pt.open_position(SOL_MIXED_CASE, "SOL", 1.0, alloc_usd=50_000, chain="solana", wallet="swing")
    assert pos["contract"] == SOL_MIXED_CASE  # jamais lowercased


@pytest.mark.asyncio
async def test_open_position_still_lowercases_base_contract(tmp_db):
    """Comportement EVM inchangé -- seul Solana est exempté du lowercase."""
    await pt.reset_portfolio(1_000_000.0)
    mixed = "0x" + "A" * 40
    pos = await pt.open_position(mixed, "AAA", 1.0, alloc_usd=50_000, chain="base", wallet="swing")
    assert pos["contract"] == mixed.lower()


@pytest.mark.asyncio
async def test_has_open_finds_solana_position_case_insensitively(tmp_db):
    """_get_open (via has_open) n'a pas de paramètre chain -- doit retrouver une
    position Solana stockée en casse mixte même en cherchant en minuscules."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(SOL_MIXED_CASE, "SOL", 1.0, alloc_usd=50_000, chain="solana", wallet="swing")
    assert await pt.has_open(SOL_MIXED_CASE) is True
    assert await pt.has_open(SOL_MIXED_CASE.lower()) is True


@pytest.mark.asyncio
async def test_list_positions_for_contract_finds_solana_case_insensitively(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(SOL_MIXED_CASE, "SOL", 1.0, alloc_usd=50_000, chain="solana", wallet="swing")
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
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, chain="solana", wallet="swing")

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
    await pt.open_position(C, "WIN", 1.0, alloc_usd=500_000.0, wallet="swing")
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
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, thesis="Bon momentum, holders sains.", wallet="swing")
    assert pos["thesis"] == "Bon momentum, holders sains."


@pytest.mark.asyncio
async def test_open_position_thesis_defaults_to_none(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    assert pos["thesis"] is None


# ── pool_liquidity_usd -> risk_guard.cap_alloc_to_price_impact (19/07, revue Gemini) ──


@pytest.mark.asyncio
async def test_open_position_shrinks_alloc_on_thin_pool(tmp_db):
    """50k$ demandés sur un pool à 100k$ (la moitié du pool). Deux plafonds
    s'appliquent en cascade -- le R/R-based (``cap_alloc_to_price_impact``,
    same case as TestCapAllocToPriceImpact.test_shrinks_on_thin_pool_matches_
    hand_computed_breakeven in test_risk_guard.py) reduit d'abord a 10 000$,
    PUIS le plafond de part de pool (Item #233, 30/07 -- 1% de 100 000$ = 1000$)
    reduit davantage -- le plus strict des deux gagne, jamais l'inverse."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(
        A, "AAA", 1.0, target_price=1.5, invalidation_price=0.9,
        alloc_usd=50_000, pool_liquidity_usd=100_000.0, wallet="swing",
    )
    assert pos["cost_usd"] == pytest.approx(1_000.0, rel=1e-6)


@pytest.mark.asyncio
async def test_open_position_scalping_mode_uses_the_lower_price_impact_floor(tmp_db):
    """08/02 -- real problem found live (audit + adversarial verify
    workflow): scalping's tight ATR stops leave so little margin above
    risk_guard.PRICE_IMPACT_MIN_RR (1.0) that the mandatory 1% swap fee alone
    crushed most signals -- scalping_v2 never opened a single position in
    17h30+ despite 4 real signals. mode="scalping" must route through
    risk_guard.PRICE_IMPACT_MIN_RR_SCALPING (0.5), giving a strictly larger
    allocation than the same setup under swing's unchanged default floor."""
    await pt.reset_portfolio(1_000_000.0, wallet="swing")
    await pt.reset_portfolio(1_000_000.0, wallet="scalping")
    pos_standard = await pt.open_position(
        A, "AAA", 1.0, target_price=1.06, invalidation_price=0.97,
        alloc_usd=1_000, pool_liquidity_usd=50_000.0, wallet="swing", mode="standard",
    )
    pos_scalping = await pt.open_position(
        B, "BBB", 1.0, target_price=1.06, invalidation_price=0.97,
        alloc_usd=1_000, pool_liquidity_usd=50_000.0, wallet="scalping", mode="scalping",
    )
    # Values hand-verified in test_risk_guard.py's own
    # TestCapAllocToPriceImpact tests -- kept in sync deliberately, not
    # re-derived independently here. mode="standard" never applies the
    # scalping swap fee (apply_swap_fee=(mode=="scalping")), so it lands on
    # the no-fee/default-floor value (375.0), not the with-fee one -- the
    # comparison that matters is scalping's lower floor giving MORE room
    # than standard's default floor on the SAME nominal setup.
    assert pos_standard["cost_usd"] == pytest.approx(375.0, rel=1e-6)
    # 08/05 -- fee 1% -> 0.3%: the impact cap now allows ~$673 on this setup,
    # so the OTHER sizing bound ($500 on this fixture) becomes the binding
    # one -- cost lands exactly on it (was 495.05 = the impact cap when the
    # 1% fee made it the tighter of the two).
    assert pos_scalping["cost_usd"] == pytest.approx(500.0, rel=1e-5)
    assert pos_scalping["cost_usd"] > pos_standard["cost_usd"]


@pytest.mark.asyncio
async def test_open_position_pool_liquidity_none_unchanged(tmp_db):
    """Non-régression : ``pool_liquidity_usd`` non fourni (défaut ``None``, ex. l'ancien
    pilote VC-thesis) -- comportement inchangé, aucun rétrécissement lié à l'impact de
    prix."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(
        A, "AAA", 1.0, target_price=1.5, invalidation_price=0.9, alloc_usd=50_000, wallet="swing",
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
    # Item #233 (30/07): cap_alloc_to_pool_share applies after the R/R-based
    # cap -- 1% of 100 000$ = 1000$, stricter than the 10 000$ R/R cap.
    assert opens[0]["cost_usd"] == pytest.approx(1_000.0, rel=1e-6)


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


def test_format_holder_concentration_unverifiable_alert_mentions_symbol_and_reason():
    alert = pt.format_holder_concentration_unverifiable_alert(contract=A, symbol="AAA", chain="base")
    assert "AAA" in alert
    assert "invérifiable" in alert
    assert "Aucun argent réel." in alert


def test_format_holder_concentration_unverifiable_alert_falls_back_to_contract_prefix():
    alert = pt.format_holder_concentration_unverifiable_alert(contract=A, symbol="", chain="base")
    assert A[:10] in alert


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


def test_format_buy_alert_bolds_the_title_line():
    """29/07 -- operator request: highlight buy/sell/limit-order alerts so
    they stand out in a busy feed."""
    buy = pt.format_buy_alert({"symbol": "AAA", "contract": A, "entry_price": 2.0, "cost_usd": 50_000})
    assert "<b>ACHAT FICTIF AAA</b>" in buy


def test_format_buy_alert_escapes_html_special_chars_in_symbol_and_thesis():
    """A token symbol is on-chain metadata an attacker can set freely -- an
    unescaped ``<``/``>``/``&`` would break Telegram's HTML parser for the
    WHOLE message once this alert opts into parse_mode="HTML" (via its own
    ``<b>`` title)."""
    buy = pt.format_buy_alert({
        "symbol": "<script>", "contract": A, "entry_price": 2.0, "cost_usd": 50_000,
        "thesis": "R/R > 2 & setup < risky",
    })
    assert "<script>" not in buy
    assert "&lt;script&gt;" in buy
    assert "R/R &gt; 2 &amp; setup &lt; risky" in buy


def test_format_sell_alert_bolds_the_title_line():
    sell = pt.format_sell_alert(
        {"symbol": "AAA", "contract": A, "exit_price": 1.5, "pnl_usd": -500, "pnl_pct": -2.0,
         "close_reason": "invalidation"}
    )
    assert "<b>VENTE FICTIVE AAA (invalidation)</b>" in sell


def test_format_sell_alert_escapes_html_special_chars():
    sell = pt.format_sell_alert({
        "symbol": "<script>", "contract": A, "exit_price": 1.5, "pnl_usd": -500, "pnl_pct": -2.0,
        "close_reason": "invalidation", "close_notes": "prix < seuil & invalidé",
    })
    assert "<script>" not in sell
    assert "&lt;script&gt;" in sell
    assert "prix &lt; seuil &amp; invalidé" in sell


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


def test_format_position_tracking_alert_labels_combined_pockets():
    """29/07 -- real operator confusion ("pourquoi il y a que 1 wallet...
    il vaut 1400000 alors qu'il y a quelques heures il valait 995k"): the
    header must say explicitly this is a combined-pockets total, not a
    single ~$1M portfolio, whenever the caller passes combined_pockets=True.

    08/02 -- pocket_count is now REQUIRED to say a specific number (real bug:
    the header used to hardcode "3 poches combinées" long after the
    architecture grew past 3 pockets -- see the dedicated regression test
    below for the exact incident)."""
    msg = pt.format_position_tracking_alert(
        [{"contract": A, "symbol": "AAA", "entry_price": 1.0, "price": 1.5, "qty": 1000.0, "cost_usd": 1000.0}],
        cash=2_000_000.0, equity=2_500_000.0, combined_pockets=True, pocket_count=8,
    )
    assert "8 poches combinées" in msg
    assert "2,500,000" in msg


def test_format_position_tracking_alert_never_hardcodes_stale_pocket_count():
    """08/02 -- real bug found live (operator: "pourquoi je vois 3047000
    alors qu'on est en perte normalement ?"): the header used to say "3
    poches combinées" as a fixed literal, regardless of how many pockets the
    caller actually summed cash across -- stale the moment the architecture
    grew past 3 (scalping_v1..v6 + swing + vc = 8). The label must reflect
    whatever pocket_count the caller passes, never a hardcoded "3"."""
    msg = pt.format_position_tracking_alert(
        [{"contract": A, "symbol": "AAA", "entry_price": 1.0, "price": 1.5, "qty": 1000.0, "cost_usd": 1000.0}],
        cash=2_000_000.0, equity=2_500_000.0, combined_pockets=True, pocket_count=8,
    )
    assert "3 poches combinées" not in msg
    assert "8 poches combinées" in msg


def test_format_position_tracking_alert_combined_pockets_without_count_degrades_generic():
    """Missing pocket_count (caller failed to compute it) -- degrade to the
    generic label rather than rendering a broken "None poches combinées"."""
    msg = pt.format_position_tracking_alert(
        [{"contract": A, "symbol": "AAA", "entry_price": 1.0, "price": 1.5, "qty": 1000.0, "cost_usd": 1000.0}],
        cash=2_000_000.0, equity=2_500_000.0, combined_pockets=True, pocket_count=None,
    )
    assert "poches combinées" not in msg
    assert "portefeuille papier" in msg


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


# ── _strategy_label (26/07, demande opérateur explicite : le header disait
#    toujours "(mode trading)" quel que soit scalping/standard/vc_thesis) ──────

def test_strategy_label_scalping():
    assert pt._strategy_label({"mode": "scalping", "strategy": "momentum"}) == "scalping"


def test_strategy_label_standard_momentum_is_swing_trading():
    assert pt._strategy_label({"mode": "standard", "strategy": "momentum"}) == "swing trading"


def test_strategy_label_vc_thesis_wins_regardless_of_mode():
    """vc_thesis est un pipeline totalement séparé (safety_screen/vc_analysis,
    jamais le switch scalping/standard) -- il prime toujours."""
    assert pt._strategy_label({"mode": "scalping", "strategy": "vc_thesis"}) == "venture capital"
    assert pt._strategy_label({"mode": "standard", "strategy": "vc_thesis"}) == "venture capital"


def test_strategy_label_missing_fields_defaults_to_swing_trading():
    assert pt._strategy_label({}) == "swing trading"


def test_strategy_label_scalping_variant_shows_the_real_pocket():
    """08/02 -- real UX gap found live (operator: "je vois beaucoup de
    scalping mais je vois pas si c v1 v2 v3") -- this label predates
    scalping_v1..v6 (26/07, before the 08/01 variants split) and always
    said the same generic "scalping" regardless of which of the 6
    independent comparison-arm engines produced the position, making the
    side-by-side comparison invisible in every Telegram alert."""
    for wallet in ("scalping_v1", "scalping_v2", "scalping_v3", "scalping_v4", "scalping_v5", "scalping_v6"):
        assert pt._strategy_label({"mode": "scalping", "strategy": "momentum", "wallet": wallet}) == wallet


def test_strategy_label_legacy_scalping_wallet_unchanged():
    """Gate OFF (scalping_variants_enabled() off): the single pocket is
    still named exactly "scalping" -- must keep the old generic label, not
    a redundant "scalping" duplicate of itself, and never crash on a wallet
    that doesn't match the "scalping_v*" pattern."""
    assert pt._strategy_label({"mode": "scalping", "strategy": "momentum", "wallet": "scalping"}) == "scalping"


def test_strategy_label_megacap_pocket_shows_megacap_not_swing_trading():
    """02/08 -- same UX gap as the scalping_v1..v6 fix above, found the same
    day for the new "megacap" pocket: it shares mode="standard"/
    strategy="momentum" with swing, so without this fix every megacap alert
    would silently say "swing trading"."""
    assert pt._strategy_label({"mode": "standard", "strategy": "momentum", "wallet": "megacap"}) == "megacap"


def test_strategy_label_swing_still_swing_trading_not_confused_with_megacap():
    assert pt._strategy_label({"mode": "standard", "strategy": "momentum", "wallet": "swing"}) == "swing trading"


def test_strategy_label_scalping_v9_shows_v9_not_swing_trading():
    """07/08 -- real bug found live (operator: "tout passe dans swing au
    lieu de v9"): scalping_v9.py persists mode="standard"/strategy="momentum"
    on its own positions (unlike scalping_variants.py's v8/v1..v6, which use
    mode="scalping") -- the wallet-prefix check used to only run under
    mode=="scalping", so every v9 alert silently fell through to "swing
    trading"."""
    assert pt._strategy_label(
        {"mode": "standard", "strategy": "momentum", "wallet": "scalping_v9"}
    ) == "scalping_v9"


def test_format_buy_alert_shows_scalping_label():
    buy = pt.format_buy_alert(
        {"symbol": "AAA", "contract": A, "entry_price": 2.0, "cost_usd": 25_000, "mode": "scalping"}
    )
    assert "(scalping)" in buy
    assert "mode trading" not in buy


def test_format_sell_alert_shows_venture_capital_label():
    sell = pt.format_sell_alert(
        {"symbol": "AAA", "contract": A, "exit_price": 2.0, "pnl_usd": 100.0, "pnl_pct": 1.0, "strategy": "vc_thesis"}
    )
    assert "(venture capital)" in sell


def test_format_partial_exit_alert_shows_swing_trading_label():
    partial = pt.format_partial_exit_alert(
        {"symbol": "AAA", "contract": A, "exit_price": 2.0, "pnl_usd": 100.0, "pnl_pct": 1.0, "remaining_qty": 5.0}
    )
    assert "(swing trading)" in partial


def test_format_position_tracking_alert_labels_each_position_individually():
    """Deux positions ouvertes de modes DIFFÉRENTS en même temps (un vrai cas
    rencontré : une position standard/swing encore ouverte pendant que le
    switch portefeuille-entier est déjà passé en scalping) -- chacune doit
    porter SON PROPRE label, jamais un seul label partagé dans le header."""
    msg = pt.format_position_tracking_alert([
        {"contract": A, "symbol": "AAA", "entry_price": 1.0, "price": 1.5, "qty": 1000.0,
         "cost_usd": 1000.0, "mode": "standard", "strategy": "momentum"},
        {"contract": D, "symbol": "DDD", "entry_price": 1.0, "price": 1.1, "qty": 1000.0,
         "cost_usd": 1000.0, "mode": "scalping", "strategy": "momentum"},
    ])
    assert "AAA (swing trading)" in msg
    assert "DDD (scalping)" in msg


def test_format_position_tracking_alert_shows_the_real_scalping_variant():
    """08/02 -- real UX gap found live (operator: "je vois beaucoup de
    scalping mais je vois pas si c v1 v2 v3"): two open positions from
    DIFFERENT scalping engines (v1 vs v3) used to both show the same
    generic "(scalping)" label, making the 6-way side-by-side comparison
    invisible at a glance in every Telegram tracking alert."""
    msg = pt.format_position_tracking_alert([
        {"contract": A, "symbol": "AAA", "entry_price": 1.0, "price": 1.5, "qty": 1000.0,
         "cost_usd": 1000.0, "mode": "scalping", "strategy": "momentum", "wallet": "scalping_v1"},
        {"contract": D, "symbol": "DDD", "entry_price": 1.0, "price": 1.1, "qty": 1000.0,
         "cost_usd": 1000.0, "mode": "scalping", "strategy": "momentum", "wallet": "scalping_v3"},
    ])
    assert "AAA (scalping_v1)" in msg
    assert "DDD (scalping_v3)" in msg
    assert "(scalping)" not in msg


@pytest.mark.asyncio
async def test_run_cycle_notifies_position_tracking_for_still_open_positions(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=10_000, wallet="swing")

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
    # Item #223 (30/07), operator observation ("je vois pas le temps de détention
    # dans feedback min"): _format_tracked_position_line already reads opened_at
    # (Item #137, 27/07) via _format_hold_duration -- but the dict built inside
    # this cycle's own tracking loop never carried that key, silently dropping
    # the "· détenue ..." segment on every real periodic tracking alert.
    assert any("détenue" in a for a in alerts)


@pytest.mark.asyncio
async def test_run_cycle_tracking_alert_includes_open_v9_positions(tmp_db):
    """08/07 -- real bug found live (operator: "les position v9 apparaisse
    pas ici" on the periodic Telegram tracking alert): the early `continue`
    that keeps v9 positions out of the generic ATR-trail/security-rescan
    machinery (legitimate -- v9 manages its own exits) ALSO skipped the
    `tracked.append` further down the same loop, so v9's open positions
    silently never showed up here even though their cash was correctly
    counted elsewhere (visible_reporting_wallets)."""
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=10_000, wallet=pt.V9_WALLET,
    )

    async def price_lookup(contract):
        return 1.05

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)

    assert act["closed"] == []
    assert len(act["tracked"]) == 1
    assert act["tracked"][0]["wallet"] == pt.V9_WALLET
    assert any("suivi positions ouvertes" in a for a in alerts)
    assert any("DDD" in a for a in alerts)


@pytest.mark.asyncio
async def test_run_cycle_tracking_alert_shows_the_real_scalping_variant_end_to_end(tmp_db):
    """08/02 -- real UX gap found live (operator screenshot: 8 open scalping
    positions all shown as generic "(scalping)", no way to tell v1 from v3
    from v6 apart). End-to-end: the wallet column already on the real DB
    row must survive all the way to the rendered Telegram line, not just
    the direct-dict-construction test above."""
    await pt.reset_portfolio(1_000_000.0, wallet="scalping_v3")
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=10_000, wallet="scalping_v3", mode="scalping",
    )

    async def price_lookup(contract):
        return 1.1

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)

    tracking_alerts = [a for a in alerts if "suivi positions ouvertes" in a]
    assert len(tracking_alerts) == 1
    assert "DDD (scalping_v3)" in tracking_alerts[0]
    assert "(scalping)" not in tracking_alerts[0]


@pytest.mark.asyncio
async def test_run_cycle_tracking_alert_shows_real_equity_not_generic_1m(tmp_db):
    """Non-régression du bug réel (17/07) : le cycle doit calculer et transmettre le
    cash/equity RÉELS à l'alerte de suivi, jamais le libellé générique "1 M$"."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=10_000, wallet="swing")

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
async def test_run_cycle_tracking_alert_never_double_counts_a_pocket_with_no_paper_state_row(tmp_db, monkeypatch):
    """08/02 -- real bug found live (operator: "pourquoi je vois 3047000
    alors qu'on est en perte normalement ?"): the tracking alert's cash sum
    used to hardcode ("scalping", "swing", "vc") -- after this same day's
    migration folded "scalping" into "scalping_v6", cash_available("scalping")
    silently failed open to a full untouched $1M (no paper_state row left)
    while the REAL open position under scalping_v1 was only ever added on
    the position-VALUE side of the equity sum, never subtracted from cash
    anywhere -- a pure double-count that made a portfolio at a real loss
    display as if it had GAINED money. Now sums cash across the REAL,
    current pocket list (all_reporting_wallets()) -- proven here by opening
    a position under scalping_v1 specifically (never "scalping", which no
    longer exists) and checking the displayed pocket count/equity reflect
    the real 8-pocket architecture, not a stale hardcoded 3."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    await pt.reset_portfolio(1_000_000.0, wallet="scalping_v1")
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=10_000, wallet="scalping_v1", mode="scalping",
    )

    async def price_lookup(contract, **kw):
        return 1.0  # unchanged -- isolates the double-count from any real PnL

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)

    tracking_alerts = [a for a in alerts if "suivi positions ouvertes" in a]
    assert len(tracking_alerts) == 1
    msg = tracking_alerts[0]
    # 06/08, operator order: retired pockets (scalping_v1 here) never
    # surface in this tracking -- only the active trio (scalping_v8 + swing
    # + vc) is counted/displayed (visible_reporting_wallets). The retired
    # pocket's OPEN position still shows in the tracked list (real money in
    # play) but its flat $1M cash stays out of the displayed equity.
    assert "3 poches combinées" in msg
    assert "4 poches combinées" not in msg
    # No double-count: 3 active pockets at $1M cash + the ~$10,000 open
    # position's value -- never the retired wallet's $1M on top.
    equity_str = msg.split("équité ")[1].split(" $")[0].replace(",", "")
    assert 3_000_000.0 < float(equity_str) < 3_020_000.0


@pytest.mark.asyncio
async def test_run_cycle_tracking_alert_excludes_positions_closed_this_cycle(tmp_db):
    """Une position fermée CE tour ne doit JAMAIS apparaître aussi dans le suivi
    périodique -- déjà couverte par l'alerte de vente, pas de doublon."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.9, alloc_usd=90_000, wallet="swing")

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
async def test_tracked_snapshot_reflects_partial_exit_from_the_same_cycle(tmp_db):
    """27/07, real bug found (operator screenshot): a partial profit-take
    (TP stage 1/3) and the periodic tracking alert can both happen within the
    SAME cycle -- the tracking alert must show the POST-reduction cost_usd
    (the real remaining capital), never the stale pre-reduction snapshot
    taken earlier in this same cycle's management loop. A real incident
    showed a tracking alert displaying the FULL pre-reduction cost (100% of
    the position) right after its own partial-exit alert had already sold a
    third of it moments earlier."""
    await pt.reset_portfolio(1_000_000.0)
    # alloc_usd=30_000 (not the requested 90_000): with invalidation_price=0.5
    # (50% risk distance) and the 2%-of-capital risk cap already enforced by
    # open_position -> size_position_by_risk, any alloc above 40_000 here
    # gets silently clamped down to 40_000 -- picking a value already under
    # that cap keeps this test's arithmetic exact and independent from that
    # unrelated guardrail.
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=30_000, wallet="swing")

    async def price_lookup(contract):
        return 1.6  # +60% -- crosses TP stage 1 (+50%) but not stage 2 (+100%)

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)

    assert len(act["partial"]) == 1
    assert act["closed"] == []
    assert len(act["tracked"]) == 1
    # Real bug: this used to still be 30_000 (the full pre-reduction cost),
    # even though the partial exit had already reduced it moments earlier in
    # the very same cycle.
    assert act["tracked"][0]["cost_usd"] == pytest.approx(30_000 * 2 / 3)
    assert act["tracked"][0]["qty"] == pytest.approx(act["partial"][0]["remaining_qty"])
    tracking_alerts = [a for a in alerts if "suivi positions ouvertes" in a]
    assert len(tracking_alerts) == 1
    # The stale pre-reduction cost (30 000) must never appear in the
    # displayed alert -- only the fresh post-reduction figure (20 000).
    assert "30 000" not in tracking_alerts[0]
    assert "30000" not in tracking_alerts[0]


@pytest.mark.asyncio
async def test_run_cycle_keeps_position_in_tracking_when_price_unavailable(tmp_db):
    """03/08, real bug found live (operator: "il y a un beug sur lequité il y
    a juste une centaine de perte pas autant") -- a position whose price
    lookup fails (delisted/illiquid pool, real case: RAGE) used to vanish
    from `tracked` entirely (bare `continue`) even though its cost stayed
    deducted from cash, silently under-reporting combined equity by its full
    cost basis with zero indication anything was wrong. It must now stay
    visible, mark-to-last-known (entry_price), flagged `price_unavailable`."""
    await pt.reset_portfolio(1_000_000.0)
    # invalidation_price=0.5 (50% risk distance) + the 2%-of-capital risk cap
    # (open_position -> size_position_by_risk) clamps the requested 50_000
    # down to 40_000 -- same arithmetic as the sibling partial-exit test
    # above, unrelated to what's actually under test here.
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=50_000, wallet="swing")

    async def price_lookup(contract):
        return None  # pool delisted/illiquid -- exactly the real RAGE symptom

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)

    assert len(act["tracked"]) == 1
    tracked = act["tracked"][0]
    assert tracked["price_unavailable"] is True
    assert tracked["price"] == pytest.approx(1.0)  # entry_price fallback, never dropped/zeroed
    assert tracked["cost_usd"] == pytest.approx(40_000)

    tracking_alerts = [a for a in alerts if "suivi positions ouvertes" in a]
    assert len(tracking_alerts) == 1
    assert "prix indisponible" in tracking_alerts[0]
    assert "40,000" in tracking_alerts[0]
    # never a fabricated +0.0% P&L on a position whose real price is unknown
    assert "P&L latent" not in tracking_alerts[0].split("prix indisponible")[1].split("\n")[0]


@pytest.mark.asyncio
async def test_run_cycle_tracking_alert_throttled_to_every_other_cycle(tmp_db):
    """17/07, demande opérateur explicite : réduire de moitié le bruit Telegram de
    l'alerte de suivi -- un cycle qui suit de trop près le précédent (même position
    ouverte, rien d'autre ne change) ne renvoie pas l'alerte."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=10_000, wallet="swing")

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
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.9, alloc_usd=90_000, wallet="swing")

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
async def test_skip_new_entries_never_opens_new_positions(tmp_db):
    """22/07 -- avec skip_new_entries=True (le cooldown heartbeat classique,
    découplé de momentum_discovery_cycle), un candidat BUY parfaitement valide
    n'ouvre JAMAIS de position -- réservé au cycle de découverte dédié (1h)."""
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 1.0, "target": 2.0, "invalidation": 0.5}

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
        skip_new_entries=True,
    )

    assert act["opened"] == []
    assert not await pt.has_open(A)
    assert "risk_state" in act  # 1ter (#186) reste exécutée même en mode skip


@pytest.mark.asyncio
async def test_skip_new_entries_still_manages_open_positions(tmp_db):
    """22/07 -- skip_new_entries=True saute UNIQUEMENT l'étape 2 (nouvelles
    entrées) -- l'étape 1 (gestion des positions déjà ouvertes, stop suiveur/
    invalidation) continue de s'appliquer normalement, jamais ralentie sans
    décision explicite séparée."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.9, alloc_usd=90_000, wallet="swing")

    async def price_lookup(contract):
        return 0.5  # bien sous l'invalidation -- doit se fermer ce tour

    act = await pt.run_paper_cycle(
        candidates=[], price_lookup=price_lookup, depeg_check=_no_depeg,
        skip_new_entries=True,
    )

    assert len(act["closed"]) == 1
    assert act["closed"][0]["contract"] == D
    assert not await pt.has_open(D)


@pytest.mark.asyncio
async def test_skip_new_entries_and_skip_position_management_together_is_a_noop(tmp_db):
    """22/07 -- les deux flags à True en même temps (jamais fait par un appelant
    réel, mais un contrat d'appel doit rester sûr) : ni gestion de position, ni
    nouvelle entrée -- un cycle qui ne fait rien, jamais une erreur."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.9, alloc_usd=90_000, wallet="swing")

    async def analyzer(contract):
        return {"action": "BUY", "symbol": "AAA", "price": 1.0, "target": 2.0, "invalidation": 0.5}

    async def price_lookup(contract):
        return 0.5

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
        skip_position_management=True, skip_new_entries=True,
    )

    assert act["opened"] == []
    assert act["closed"] == []
    assert await pt.has_open(D)  # toujours ouverte, rien touché


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
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    assert pos["strategy"] == "momentum"


@pytest.mark.asyncio
async def test_open_position_persists_vc_thesis_strategy_and_entry_liquidity(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=80_000.0, wallet="swing",
    )
    assert pos["strategy"] == "vc_thesis"
    assert pos["entry_liquidity_usd"] == 80_000.0


@pytest.mark.asyncio
async def test_open_position_persists_entry_market_cap(tmp_db):
    """08/01 -- purely observational field (operator request: measure which
    market-cap tranche performs best in scalping before ever gating on it)."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000, entry_market_cap_usd=12_000_000.0, wallet="swing",
    )
    assert pos["entry_market_cap_usd"] == 12_000_000.0


@pytest.mark.asyncio
async def test_open_position_entry_market_cap_none_when_unprovided(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000, wallet="swing")
    assert pos["entry_market_cap_usd"] is None


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
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=80_000.0, wallet="swing",
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
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=200_000.0, wallet="swing",
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
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=200_000.0, wallet="swing",
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
        strategy="vc_thesis", pool_liquidity_usd=200_000.0, wallet="swing",
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=3.2, liquidity_usd=200_000.0),
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "cible thèse VC"


# ── Tâche #4 (22/07) : monitoring post-entrée VC -- vente récente du déployeur ────────

@pytest.mark.asyncio
async def test_vc_thesis_position_closes_on_dev_wallet_recent_selling(tmp_db, monkeypatch):
    """Une vente récente et significative du wallet déployeur (delta >=
    VC_DEV_SOLD_DELTA_ALERT_PCT depuis l'instantané d'entrée) déclenche une sortie
    d'urgence -- indépendamment de la liquidité, ici parfaitement saine."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=200_000.0,
        entry_dev_sold_pct=5.0, wallet="swing",
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=1.1, liquidity_usd=200_000.0),  # liquidité saine
    )

    async def fake_dev_check(contract, chain, entry_sold_pct):
        assert entry_sold_pct == 5.0
        return True, "dev wallet a vendu 20.0 points de % supplémentaires depuis l'entrée (5.0%->25.0%)"

    monkeypatch.setattr(pt, "_check_vc_dev_wallet_recent_selling", fake_dev_check)
    act = await pt.run_paper_cycle(candidates=[])
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "vente déployeur détectée"


@pytest.mark.asyncio
async def test_vc_thesis_position_survives_without_entry_dev_snapshot(tmp_db, monkeypatch):
    """Non-régression : sans instantané d'entrée (entry_dev_sold_pct=None, défaut
    historique pour tout appelant qui ne le fournit pas), le check reste fail-open --
    jamais de fausse alerte, jamais un appel réseau tenté (vérifié via un mock qui
    lèverait si jamais atteint au-delà du garde `entry_sold_pct is None`)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=200_000.0, wallet="swing",
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=1.05, liquidity_usd=195_000.0),
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert act["closed"] == []
    assert await pt.has_open(A)


# ── Tâche #4 (22/07) : monitoring post-entrée VC -- chute de liquidité SOUDAINE ───────

@pytest.mark.asyncio
async def test_vc_thesis_position_closes_on_sudden_liquidity_drop_between_cycles(tmp_db, monkeypatch):
    """Une chute de 30%+ entre deux cycles CONSÉCUTIFS déclenche l'invalidation même si
    le cumulé depuis l'entrée reste sous 50% -- un retrait de LP étalé en petites
    tranches doit être détecté cycle par cycle, pas seulement en cumulé depuis l'entrée."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=200_000.0, wallet="swing",
    )
    # Cycle 1 : 190k$ (-5% depuis l'entrée) -- établit last_liquidity_usd=190k$, aucune
    # invalidation (ni absolue, ni cumulée, ni soudaine -- 1er cycle de gestion).
    monkeypatch.setattr(
        pt, "_default_pair_lookup", _vc_position_pair_lookup(price=1.0, liquidity_usd=190_000.0),
    )
    act1 = await pt.run_paper_cycle(candidates=[])
    assert act1["closed"] == []

    # Cycle 2 : 120k$ -- chute de 36,8% depuis le DERNIER cycle (190k$->120k$, >30%),
    # mais seulement 40% depuis l'ENTRÉE (200k$->120k$, <50%) -- le check cumulé seul ne
    # suffirait pas, seul le nouveau check "soudain entre deux cycles" le détecte.
    monkeypatch.setattr(
        pt, "_default_pair_lookup", _vc_position_pair_lookup(price=0.95, liquidity_usd=120_000.0),
    )
    act2 = await pt.run_paper_cycle(candidates=[])
    assert len(act2["closed"]) == 1
    assert act2["closed"][0]["close_reason"] == "invalidation fondamentale (liquidité)"
    assert "SOUDAINE" in act2["closed"][0]["close_notes"]


@pytest.mark.asyncio
async def test_vc_thesis_position_survives_gradual_drop_no_sudden_trigger(tmp_db, monkeypatch):
    """Non-régression : une baisse progressive, jamais >30% d'un cycle à l'autre ET
    jamais >50% cumulé depuis l'entrée, ne déclenche aucune invalidation."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=50_000, strategy="vc_thesis", pool_liquidity_usd=200_000.0, wallet="swing",
    )
    for liq in (185_000.0, 170_000.0, 155_000.0):
        monkeypatch.setattr(
            pt, "_default_pair_lookup", _vc_position_pair_lookup(price=1.0, liquidity_usd=liq),
        )
        act = await pt.run_paper_cycle(candidates=[])
        assert act["closed"] == [], f"liquidité {liq} n'aurait pas dû déclencher une clôture"
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_vc_thesis_position_never_stopped_out_on_a_deep_pullback(tmp_db, monkeypatch):
    """Le point central de la Formule B (Gemini) : une correction de -50% depuis le plus
    haut, normale pour une thèse VC moyen terme, ne doit JAMAIS déclencher de sortie --
    contrairement à la discipline momentum (stop suiveur ATR)."""
    await pt.reset_portfolio(1_000_000.0)
    # #175 (20/07) -- pool_liquidity_usd VOLONTAIREMENT omis : ce test isole le
    # comportement Take Seed/pullback (gain_mult = price/entry_price), qui n'a rien à
    # voir avec le prix de remplissage simulé (risk_guard.simulated_fill_price) -- le
    # fournir aurait dégradé entry_price loin de 1.0 et cassé le narratif "3x"/"-50%
    # depuis l'entrée" ci-dessous sans rien apporter à ce qui est réellement testé ici.
    await pt.open_position(
        A, "AAA", 1.0, target_price=10.0, alloc_usd=50_000, strategy="vc_thesis", wallet="swing",
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
    # #175 -- pool_liquidity_usd omis, cf. commentaire de test_vc_thesis_position_never_
    # stopped_out_on_a_deep_pullback (le "exactement 2x" ci-dessous exige entry_price
    # non dégradé).
    await pt.open_position(
        A, "AAA", 1.0, target_price=10.0, alloc_usd=50_000, strategy="vc_thesis", wallet="swing",
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
    # #175 -- pool_liquidity_usd omis, même raison que ci-dessus.
    await pt.open_position(
        A, "AAA", 1.0, target_price=10.0, alloc_usd=50_000, strategy="vc_thesis", wallet="swing",
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
    # #175 -- pool_liquidity_usd omis, même raison que ci-dessus.
    await pt.open_position(
        A, "AAA", 1.0, target_price=10.0, alloc_usd=50_000, strategy="vc_thesis", wallet="swing",
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
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=50_000, wallet="swing")

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
    assert pt._fresh_rr(1.0, 3.0, None) is None
    assert pt._fresh_rr(0.0, 3.0, 0.5) is None


def test_fresh_rr_no_target_returns_infinite_when_above_invalidation():
    """08/02 -- real bug found live: scalping_v5 ("no fixed TP, pure trailing
    stop" by design) had this treated as missing data -> None ->
    _execution_rr_still_valid fail-closed -> 100% of its BUY signals silently
    killed at this recheck, regardless of price movement. A missing target
    means "no upper bound", not "can't judge" -- float("inf") clears any
    finite bar while the invalidation check (structural break) still applies."""
    assert pt._fresh_rr(1.0, None, 0.5) == float("inf")


def test_fresh_rr_no_target_still_rejects_broken_structure():
    assert pt._fresh_rr(0.4, None, 0.5) is None  # at/below invalidation
    assert pt._fresh_rr(0.5, None, 0.5) is None  # exactly at invalidation


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
    """Le vrai cas à rejeter en ACHAT DIRECT : le prix a couru si près de la cible
    que le R/R structurel ne tient plus la barre franchie à l'origine -- ARIA
    n'achète pas un setup dégradé, contrairement à ce qu'un simple seuil % aurait
    pu laisser passer ou rejeter arbitrairement. 07/23 -- depuis le mécanisme
    d'ordre limite, ce n'est plus un rejet MUET pour autant : le prix a dérivé
    vers le HAUT (structure toujours intacte, au-dessus de l'invalidation) donc un
    ordre limite est posé au prix original du signal plutôt que de perdre
    l'opportunité (voir test_limit_orders.py pour le mécanisme lui-même)."""
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
    from aria_core import limit_orders

    assert await limit_orders.has_active_order(A, "base") is True


@pytest.mark.asyncio
async def test_run_cycle_places_limit_order_on_small_upward_drift_check_case(tmp_db, monkeypatch):
    """Le cas réel qui a motivé ce mécanisme (CHECK, 07/23) : le prix n'a dérivé
    QUE légèrement vers le haut pendant l'analyse (0.038 signal -> 0.044
    exécution) -- structure toujours intacte, loin de la cible. Un ordre limite
    est posé au prix ORIGINAL du signal (0.038), jamais au prix dégradé."""
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        # R/R au signal (0.038) = (0.06-0.038)/(0.038-0.03) = 2.75 -- achat direct
        return {
            "action": "BUY", "symbol": "CHECK", "price": 0.038, "target": 0.06,
            "invalidation": 0.03, "rr": 2.75, "align_score": 3, "chain": "base",
        }

    async def price_lookup(contract):
        # dérive légère -- R/R frais = (0.06-0.044)/(0.044-0.03) = 1.14, sous la
        # barre d'achat direct (2.0) -- rejeté en direct, mais éligible ordre limite.
        return 0.044

    act = await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer, price_lookup=price_lookup)
    assert act["opened"] == []
    assert not await pt.has_open(A)

    from aria_core import limit_orders

    active = await limit_orders.get_active_orders()
    assert len(active) == 1
    assert active[0]["contract"] == A
    assert active[0]["target_price"] == pytest.approx(0.038)  # prix du SIGNAL, jamais le prix dégradé

    # 29/07 -- operator feedback ("ordre limite ne montre pas la taille de la
    # future position"): an estimate is computed and persisted at placement
    # time, using the same compute_entry_alloc formula a real buy would use.
    import json as _json

    order_sig = _json.loads(active[0]["signal_json"])
    assert order_sig["estimated_alloc_usd"] > 0
    assert order_sig["estimated_alloc_pct"] > 0


@pytest.mark.asyncio
async def test_run_cycle_price_drift_limit_order_uses_scalping_expiry(tmp_db, monkeypatch):
    """08/04, real gap found live: unlike its golden-pocket/RSI-watch sibling
    (already mode-aware), this price-drift path never forwarded expiry_hours
    at all -- silently falling back to the flat 3h swing-calibrated default
    for scalping too. Same setup as the CHECK case above, just mode="scalping"
    -- the resulting order's expires_at must reflect the dedicated ~1h
    scalping window, never the swing 3h."""
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "symbol": "CHECK", "price": 0.038, "target": 0.06,
            "invalidation": 0.03, "rr": 2.75, "align_score": 3, "chain": "base",
            "mode": "scalping",
        }

    async def price_lookup(contract):
        return 0.044

    before = datetime.now(timezone.utc)
    act = await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer, price_lookup=price_lookup)
    assert act["opened"] == []

    from aria_core import limit_orders

    active = await limit_orders.get_active_orders()
    assert len(active) == 1
    expires_at = datetime.fromisoformat(active[0]["expires_at"])
    delta_hours = (expires_at - before).total_seconds() / 3600.0
    assert delta_hours == pytest.approx(limit_orders.LIMIT_ORDER_EXPIRY_HOURS_SCALPING, abs=0.05)
    assert delta_hours < limit_orders.LIMIT_ORDER_EXPIRY_HOURS  # jamais le flat 3h swing


@pytest.mark.asyncio
async def test_run_cycle_price_drift_limit_order_keeps_swing_expiry_unchanged(tmp_db, monkeypatch):
    """Comportement INCHANGÉ pour tout mode autre que scalping (None/"standard") --
    même setup, sans mode="scalping", doit garder le flat 3h historique."""
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "symbol": "CHECK", "price": 0.038, "target": 0.06,
            "invalidation": 0.03, "rr": 2.75, "align_score": 3, "chain": "base",
        }

    async def price_lookup(contract):
        return 0.044

    before = datetime.now(timezone.utc)
    act = await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer, price_lookup=price_lookup)
    assert act["opened"] == []

    from aria_core import limit_orders

    active = await limit_orders.get_active_orders()
    assert len(active) == 1
    expires_at = datetime.fromisoformat(active[0]["expires_at"])
    delta_hours = (expires_at - before).total_seconds() / 3600.0
    assert delta_hours == pytest.approx(limit_orders.LIMIT_ORDER_EXPIRY_HOURS, abs=0.05)


@pytest.mark.asyncio
async def test_run_cycle_never_places_limit_order_when_structure_already_broken(tmp_db, monkeypatch):
    """Cas (a) du design : le prix est tombé À OU SOUS l'invalidation pendant
    l'analyse -- le setup est mort, jamais un ordre limite sur un setup mort,
    rejet pur exactement comme avant ce mécanisme."""
    monkeypatch.setattr(pt, "_execution_rr_still_valid", _REAL_EXECUTION_RR_STILL_VALID)
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "symbol": "DEAD", "price": 1.0, "target": 3.0,
            "invalidation": 0.5, "rr": 4.0, "align_score": 3, "chain": "base",
        }

    async def price_lookup(contract):
        return 0.4  # sous l'invalidation -- structure cassée

    act = await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer, price_lookup=price_lookup)
    assert act["opened"] == []
    assert not await pt.has_open(A)

    from aria_core import limit_orders

    assert await limit_orders.get_active_orders() == []


@pytest.mark.asyncio
async def test_run_cycle_places_limit_order_on_golden_pocket_watch_candidate(tmp_db, monkeypatch):
    """Item #182, 28/07, golden-pocket liberation: a HOLD/no_entry_signal
    result alongside a limit_order_candidate (momentum_entry.py, price still
    above a computable golden-pocket zone but the DEX composite score already
    confirms high quality) places a watch-and-wait limit order -- distinct
    origin from the price-drift case above (a HOLD, never an already-decided
    BUY), same underlying mechanism."""
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "HOLD", "symbol": "WATCH", "price": 1.5, "chain": "base",
            "reasons": ["pas de setup golden pocket + divergence RSI avec R/R positif"],
            "hold_reason": "no_entry_signal",
            "limit_order_candidate": {
                "target_price": 1.382, "target": 2.5, "invalidation": 1.18972,
                "rr": 2.5, "symbol": "WATCH", "dex_security_score": 75.0,
                "dex_security_breakdown": {}, "reason": "score DEX fort, golden pocket pas encore formé",
            },
        }

    async def price_lookup(contract):
        return 1.5

    notified = []

    async def notifier(msg):
        notified.append(msg)

    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=fake_analyzer, price_lookup=price_lookup, notifier=notifier,
    )
    assert act["opened"] == []
    assert not await pt.has_open(A)

    import json

    from aria_core import limit_orders

    active = await limit_orders.get_active_orders()
    assert len(active) == 1
    assert active[0]["contract"] == A
    assert active[0]["target_price"] == pytest.approx(1.382)
    sig = json.loads(active[0]["signal_json"])
    assert sig["limit_order_reason"] == "golden_pocket_pending"
    assert sig["invalidation"] == pytest.approx(1.18972)
    assert sig["dex_security_score"] == 75.0
    assert any("score DEX fort" in r for r in sig["reasons"])
    assert len(notified) == 1


@pytest.mark.asyncio
async def test_run_cycle_persists_entry_atr_pct_on_watch_candidate_limit_order(tmp_db, monkeypatch):
    """Item #253 (08/02): the watch-candidate's entry_atr_pct (set by
    momentum_entry.py's builders, §1) must survive the order_sig = {**sig,
    **watch, ...} merge (paper_trader.py's create_pending_order call site) --
    same scenario as test_run_cycle_places_limit_order_on_golden_pocket_
    watch_candidate above, with entry_atr_pct added to the fake watch dict."""
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "HOLD", "symbol": "WATCH", "price": 1.5, "chain": "base",
            "reasons": ["pas de setup golden pocket + divergence RSI avec R/R positif"],
            "hold_reason": "no_entry_signal",
            "limit_order_candidate": {
                "target_price": 1.382, "target": 2.5, "invalidation": 1.18972,
                "rr": 2.5, "symbol": "WATCH", "dex_security_score": 75.0,
                "dex_security_breakdown": {}, "reason": "score DEX fort, golden pocket pas encore formé",
                "entry_atr_pct": 0.18,
            },
        }

    async def price_lookup(contract):
        return 1.5

    await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer, price_lookup=price_lookup)

    import json

    from aria_core import limit_orders

    active = await limit_orders.get_active_orders()
    assert len(active) == 1
    sig = json.loads(active[0]["signal_json"])
    assert sig["entry_atr_pct"] == pytest.approx(0.18)


@pytest.mark.asyncio
async def test_run_cycle_no_limit_order_on_plain_no_entry_signal_without_watch_candidate(tmp_db, monkeypatch):
    """The vast majority of no_entry_signal HOLDs never carry a
    limit_order_candidate (score unresolved/below threshold, retracement
    insufficient, etc.) -- must behave exactly as before this chantier, no
    limit order placed."""
    await pt.reset_portfolio(1_000_000.0)

    async def fake_analyzer(contract):
        return {
            "action": "HOLD", "symbol": "PLAIN", "price": 1.5, "chain": "base",
            "reasons": ["pas de setup golden pocket + divergence RSI avec R/R positif"],
            "hold_reason": "no_entry_signal",
        }

    async def price_lookup(contract):
        return 1.5

    act = await pt.run_paper_cycle(candidates=[A], analyzer=fake_analyzer, price_lookup=price_lookup)
    assert act["opened"] == []

    from aria_core import limit_orders

    assert await limit_orders.get_active_orders() == []


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


# ── Daily trade FLOOR (07/23, diagnostic) ────────────────────────────────────

from datetime import datetime as _dt, timezone as _tz


def test_daily_floor_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_DAILY_TRADE_FLOOR_ENABLED", raising=False)
    assert pt.daily_trade_floor_enabled() is False


def test_daily_floor_gate_on(monkeypatch):
    monkeypatch.setenv("ARIA_DAILY_TRADE_FLOOR_ENABLED", "true")
    assert pt.daily_trade_floor_enabled() is True


def test_daily_floor_target_paces_across_the_day():
    midnight = _dt(2026, 7, 23, 0, 0, 0, tzinfo=_tz.utc)
    assert pt._daily_floor_target(midnight) == 0
    # start of day -> becomes 1 as soon as any time elapsed (nudge to act early)
    assert pt._daily_floor_target(midnight.replace(minute=30)) == 1
    # midday -> ~half
    assert pt._daily_floor_target(midnight.replace(hour=12)) == math.ceil(pt.DAILY_TRADE_FLOOR * 0.5)
    # end of day -> the full floor
    assert pt._daily_floor_target(midnight.replace(hour=23, minute=59)) == pt.DAILY_TRADE_FLOOR


@pytest.mark.asyncio
async def test_count_positions_opened_today(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "A", 1.0, target_price=2.0, invalidation_price=0.5, wallet="swing")
    now = _dt.now(_tz.utc)
    assert await pt.count_positions_opened_today(now=now) == 1


@pytest.mark.asyncio
async def test_count_positions_opened_today_scopes_to_swing_by_default(tmp_db):
    """27/07 -- 3-pocket architecture plan: this diagnostic floor only ever
    books into "swing" -- positions opened the SAME day in a different pocket
    (scalping/vc) must never inflate this count, or the floor would believe
    today's target is already met while "swing" itself had zero trades."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "A", 1.0, target_price=2.0, invalidation_price=0.5, wallet="swing")
    await pt.open_position(B, "B", 1.0, target_price=2.0, invalidation_price=0.5, wallet="scalping")
    await pt.open_position(C, "C", 1.0, target_price=2.0, invalidation_price=0.5, wallet="vc")
    now = _dt.now(_tz.utc)
    assert await pt.count_positions_opened_today(now=now) == 1
    assert await pt.count_positions_opened_today(now=now, wallet="scalping") == 1
    assert await pt.count_positions_opened_today(now=now, wallet="vc") == 1


def _floor_buy_sig(symbol="FL", price=1.0):
    return {
        "action": "BUY", "chain": "base", "symbol": symbol, "price": price,
        "target": 2.0, "invalidation": 0.5, "rr": 1.2, "align_score": 1,
        "floor_trade": True, "strategy": "momentum", "regime": "neutre",
        "reasons": ["mode plancher (diagnostic)"],
        # 25/07 -- generous liquidity so cap_alloc_to_price_impact (applied
        # inside open_position) never becomes the binding constraint here --
        # this fixture isolates the dynamic risk/ATR sizing itself, a
        # separate concern already covered by its own dedicated tests.
        "liquidity_usd": 10_000_000.0,
        "rvol_multiple": 1.5,
    }


async def _not_blocked(wallet="swing", *, price_lookup=None):
    from aria_core import risk_guard
    return risk_guard.PortfolioRiskState(
        wallet=wallet, equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )


@pytest.mark.asyncio
async def test_daily_floor_skipped_when_gate_off(tmp_db, monkeypatch):
    monkeypatch.delenv("ARIA_DAILY_TRADE_FLOOR_ENABLED", raising=False)
    await pt.reset_portfolio(1_000_000.0)
    res = await pt.run_daily_trade_floor_cycle()
    assert res["outcome"] == "skipped" and res["reason"] == "gate_off"


@pytest.mark.asyncio
async def test_daily_floor_skipped_when_circuit_breaker_armed(tmp_db, monkeypatch):
    monkeypatch.setenv("ARIA_DAILY_TRADE_FLOOR_ENABLED", "true")
    from aria_core import risk_guard

    async def _blocked(wallet="swing", *, price_lookup=None):
        return risk_guard.PortfolioRiskState(
            wallet=wallet, equity=800_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.2,
            consecutive_losses=5, alloc_multiplier=1.0, blocked=True,
        )

    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _blocked)
    await pt.reset_portfolio(1_000_000.0)
    res = await pt.run_daily_trade_floor_cycle(now=_dt(2026, 7, 23, 20, 0, 0, tzinfo=_tz.utc))
    assert res["outcome"] == "skipped" and res["reason"] == "risk_circuit_breaker"


@pytest.mark.asyncio
async def test_daily_floor_opens_small_tagged_trades_when_behind(tmp_db, monkeypatch):
    monkeypatch.setenv("ARIA_DAILY_TRADE_FLOOR_ENABLED", "true")
    from aria_core import momentum_entry, risk_guard
    from aria_core.skills import market_sentiment

    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _not_blocked)

    async def _neutral():
        return market_sentiment.META_REGIME_NEUTRAL

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", _neutral)

    # 25/07 -- FLOOR_MAX_OPENS_PER_CYCLE raised to 5, needs at least that many
    # distinct candidates for this test to actually exercise the per-cycle cap
    # rather than just running out of candidates first.
    async def _fake_sources(*, limit=20):
        addrs = [A, B, C, D, E]
        return addrs, {addr: "base" for addr in addrs}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        assert relaxed is True  # the floor must always evaluate in relaxed mode
        return _floor_buy_sig(symbol=contract[:4])

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)

    await pt.reset_portfolio(1_000_000.0)
    # late in the day -> target = DAILY_TRADE_FLOOR, none opened yet, so it
    # should open up to FLOOR_MAX_OPENS_PER_CYCLE this cycle.
    res = await pt.run_daily_trade_floor_cycle(now=_dt(2026, 7, 23, 23, 0, 0, tzinfo=_tz.utc))
    assert res["outcome"] == "ok"
    assert len(res["opened"]) == pt.FLOOR_MAX_OPENS_PER_CYCLE
    for pos in res["opened"]:
        assert pos["discovery_channel"] == "floor"
        # 25/07, operator request ("enleve le truc qui force les positions
        # avec 1% du capital"): sizing is now the SAME dynamic risk/ATR
        # formula as a normal conviction pick (compute_entry_alloc), never a
        # fixed 1% ceiling. This weak signal (rr=1.2, align=1, no ATR) lands
        # on the WEAK tier -- ALLOC_PCT * MIN_ALLOC_MULTIPLIER * start.
        assert pos["conviction_tier"] == "weak"
        assert pos["cost_usd"] == pytest.approx(0.05 * 0.4 * 1_000_000.0, rel=0.01)


@pytest.mark.asyncio
async def test_daily_floor_feeds_a_real_pacing_context_to_the_analyzer(tmp_db, monkeypatch):
    """25/07, operator request ("je veux qu'elle sache combien elle a dans
    son portfolio et combien de benefice ou perte elle a realise pour s'auto
    mettre un stress"): the floor cycle must pass a REAL weekly_context (this
    window's own $75k target, day 1/1) to the analyzer -- never None, which
    would leave ARIA blind to her own equity/target during this 24h test."""
    monkeypatch.setenv("ARIA_DAILY_TRADE_FLOOR_ENABLED", "true")
    from aria_core import momentum_entry, risk_guard
    from aria_core.skills import market_sentiment

    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _not_blocked)

    async def _neutral():
        return market_sentiment.META_REGIME_NEUTRAL

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", _neutral)

    async def _fake_sources(*, limit=20):
        return [A], {A: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    captured = {}

    async def _fake_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        captured["weekly_context"] = weekly_context
        return _floor_buy_sig(symbol=contract[:4])

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)

    await pt.reset_portfolio(1_000_000.0)
    await pt.run_daily_trade_floor_cycle(now=_dt(2026, 7, 23, 23, 0, 0, tzinfo=_tz.utc))

    ctx = captured["weekly_context"]
    assert ctx is not None
    assert ctx["day"] == 1 and ctx["days_total"] == 1
    assert ctx["equity"] == pytest.approx(1_000_000.0)
    assert ctx["target_equity"] == pytest.approx(1_000_000.0 + pt.DAILY_FLOOR_TARGET_PROFIT_USD)


@pytest.mark.asyncio
async def test_daily_floor_forwards_the_portfolio_wide_trading_mode(tmp_db, monkeypatch):
    """26/07 -- real bug found via an operator-reported Telegram screenshot: a
    real position (AERO) was opened by this cycle with mode="standard" even
    though the portfolio-wide switch was set to "scalping" (get_trading_mode()
    was never called here) -- its thesis showed the full conviction_research
    diligence that scalping mode is specifically supposed to skip (Item #101).
    This test locks in the fix: set_trading_mode("scalping") must be reflected
    in the mode= kwarg the floor cycle forwards to evaluate_momentum_entry."""
    monkeypatch.setenv("ARIA_DAILY_TRADE_FLOOR_ENABLED", "true")
    from aria_core import momentum_entry, risk_guard

    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _not_blocked)

    async def _fake_sources(*, limit=20):
        return [A], {A: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    captured = {}

    async def _fake_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        captured["mode"] = mode
        return _floor_buy_sig(symbol=contract[:4])

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)

    await pt.reset_portfolio(1_000_000.0)
    await pt.set_trading_mode("scalping")
    await pt.run_daily_trade_floor_cycle(now=_dt(2026, 7, 23, 23, 0, 0, tzinfo=_tz.utc))

    assert captured["mode"] == "scalping"


@pytest.mark.asyncio
async def test_daily_floor_on_pace_opens_nothing(tmp_db, monkeypatch):
    monkeypatch.setenv("ARIA_DAILY_TRADE_FLOOR_ENABLED", "true")
    from aria_core import risk_guard

    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _not_blocked)
    await pt.reset_portfolio(1_000_000.0)
    # Already opened the FULL day's floor -- robust to whatever DAILY_TRADE_FLOOR
    # is set to, unlike a hardcoded count that would drift out of sync with it.
    # Small explicit alloc_usd so DAILY_TRADE_FLOOR positions always fit
    # within the $1M cash budget (the default 5% flat alloc would exhaust
    # cash well before reaching a raised floor target, silently opening
    # fewer positions than intended).
    for i in range(pt.DAILY_TRADE_FLOOR):
        addr = f"0x{i:040x}"
        await pt.open_position(
            addr, f"T{i}", 1.0, target_price=2.0, invalidation_price=0.5, alloc_usd=1_000.0, wallet="swing",
        )
    res = await pt.run_daily_trade_floor_cycle(now=_dt(2026, 7, 23, 23, 59, 0, tzinfo=_tz.utc))
    assert res["outcome"] == "on_pace"
    assert res["opened"] == []


@pytest.mark.asyncio
async def test_daily_floor_never_crashes_on_candidate_already_open_in_another_pocket(tmp_db, monkeypatch):
    """27/07 -- 3-pocket architecture plan: this diagnostic floor always books
    into "swing" (see wallet="swing" on its own open_position call) -- a
    candidate that's ALREADY open in a DIFFERENT pocket (vc/scalping, entirely
    plausible once multi-pocket sourcing is on) must never reach the
    unscoped ``has_open``/``get_open_positions`` multi-pocket ambiguity guard
    in ``_get_open`` (RuntimeError) -- both calls inside this loop are scoped
    to wallet="swing" specifically for this reason. The candidate must still
    be correctly skipped as a duplicate within "swing" once actually opened
    there, and a genuinely fresh candidate must still open normally."""
    monkeypatch.setenv("ARIA_DAILY_TRADE_FLOOR_ENABLED", "true")
    from aria_core import momentum_entry, risk_guard
    from aria_core.skills import market_sentiment

    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _not_blocked)

    async def _neutral():
        return market_sentiment.META_REGIME_NEUTRAL

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", _neutral)

    async def _fake_sources(*, limit=20):
        return [A, B], {A: "base", B: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        return _floor_buy_sig(symbol=contract[:4])

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)

    await pt.reset_portfolio(1_000_000.0)
    # A is already open in a DIFFERENT pocket -- 2 distinct open rows on this
    # contract portfolio-wide (vc + whatever "swing" opens below), the exact
    # condition that makes an UNSCOPED has_open(contract) raise.
    await pt.open_position(A, "AAA", 1.0, target_price=2.0, invalidation_price=0.5, wallet="vc")

    res = await pt.run_daily_trade_floor_cycle(now=_dt(2026, 7, 23, 23, 0, 0, tzinfo=_tz.utc))
    assert res["outcome"] == "ok"
    # Never crashed on the pre-existing "vc" position sharing contract A -- A
    # is fresh for "swing" (this pocket never held it), so it's legitimately
    # opened there too, same as B (fresh everywhere).
    opened_contracts = {p["contract"] for p in res["opened"]}
    assert opened_contracts == {A, B}
    assert await pt.has_open(A, wallet="vc") is True
    assert await pt.has_open(A, wallet="swing") is True


@pytest.mark.asyncio
async def test_daily_floor_never_forces_a_non_floor_or_hold_signal(tmp_db, monkeypatch):
    """Safety: if the relaxed eval returns HOLD (or a BUY without floor_trade),
    the floor never opens it -- it only ever acts on an explicit relaxed
    floor_trade BUY (guarantees the safety guards ran)."""
    monkeypatch.setenv("ARIA_DAILY_TRADE_FLOOR_ENABLED", "true")
    from aria_core import momentum_entry, risk_guard
    from aria_core.skills import market_sentiment

    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _not_blocked)

    async def _neutral():
        return market_sentiment.META_REGIME_NEUTRAL

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", _neutral)

    async def _fake_sources(*, limit=20):
        return [A, B], {A: "base", B: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _hold_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False):
        return {"action": "HOLD", "chain": "base", "symbol": "X", "hold_reason": "honeypot_rejected"}

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _hold_eval)

    await pt.reset_portfolio(1_000_000.0)
    res = await pt.run_daily_trade_floor_cycle(now=_dt(2026, 7, 23, 23, 0, 0, tzinfo=_tz.utc))
    assert res["opened"] == []
    assert res["outcome"] == "no_safe_candidate"


# ── 24/07, bonding-entry chantier: sourcing/routing/sizing/price-lookup ─────

@pytest.mark.asyncio
async def test_bonding_candidates_sources_from_launchpad_discovery(monkeypatch):
    from aria_core.services import launchpad_discovery

    # This test exercises _bonding_candidates ITSELF -- undo the file-wide
    # autouse mock (see _bypass_bonding_candidates_network_call) for this
    # test only, same restore-the-real-thing pattern as
    # _REAL_EXECUTION_RR_STILL_VALID above.
    monkeypatch.setattr(pt, "_bonding_candidates", _REAL_BONDING_CANDIDATES)

    async def fake_discover(*, limit_per_launchpad=50):
        return {"virtuals_bonding": [A, B], "clanker": [C]}

    monkeypatch.setattr(launchpad_discovery, "discover_bonding_candidates", fake_discover)

    result = await pt._bonding_candidates(limit=20)

    assert result == [A, B]  # only the virtuals_bonding bucket, "clanker" ignored


@pytest.mark.asyncio
async def test_bonding_candidates_degrades_to_empty_on_error(monkeypatch):
    from aria_core.services import launchpad_discovery

    monkeypatch.setattr(pt, "_bonding_candidates", _REAL_BONDING_CANDIDATES)

    async def fake_discover(*, limit_per_launchpad=50):
        raise RuntimeError("Virtuals down")

    monkeypatch.setattr(launchpad_discovery, "discover_bonding_candidates", fake_discover)

    assert await pt._bonding_candidates(limit=20) == []


@pytest.mark.asyncio
async def test_momentum_candidates_appends_bonding_without_overwriting(monkeypatch):
    from aria_core import momentum_entry
    from aria_core.bonding_entry import CHAIN_MARKER

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": A, "chain": "base"}]

    async def fake_bonding(*, limit=20):
        return [B, A]  # A already sourced by standard discovery -- must NOT be overwritten

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(pt, "_bonding_candidates", fake_bonding)

    contracts, chain_map = await pt._momentum_candidates_and_chain_map(limit=20)

    assert contracts == [A, B]
    assert chain_map[A] == "base"  # kept the real chain, not overwritten by bonding
    assert chain_map[B] == CHAIN_MARKER


@pytest.mark.asyncio
async def test_momentum_candidates_prioritizes_never_scanned_over_stale(monkeypatch):
    """31/07, operator request ("toujours les derniers scannés en dernier
    pour être sûr que tous les token passe au scan"): when discovery
    surfaces MORE candidates than the per-cycle limit, truncating on raw
    discovery order could starve the same never-reached candidates forever.
    A/B/C already scanned (B most recently, A a while ago), D never scanned
    -- with limit=2, D (never scanned) and A (oldest real scan) must win over
    B/C (scanned more recently), even though D/A are LAST in the raw
    discovery order."""
    from aria_core import momentum_entry, momentum_scan_log

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        # Raw discovery order deliberately puts the recently-scanned ones
        # FIRST -- a naive found[:limit] would pick B/C, never D/A.
        return [
            {"contract": B, "chain": "base"},
            {"contract": C, "chain": "base"},
            {"contract": A, "chain": "base"},
            {"contract": D, "chain": "base"},
        ]

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)

    await momentum_scan_log.record_scan(A, "base", "no_entry_signal")
    old_scan = (await momentum_scan_log.last_scan_for(A, "base"))["scanned_at"]
    await momentum_scan_log.record_scan(B, "base", "no_entry_signal")
    await momentum_scan_log.record_scan(C, "base", "no_entry_signal")
    # D never scanned.

    contracts, _ = await pt._momentum_candidates_and_chain_map(limit=2)

    assert set(contracts) == {D, A}  # never-scanned + oldest-scanned, not the 2 most recent
    assert old_scan  # sanity: A really has a real, older timestamp on record


@pytest.mark.asyncio
async def test_momentum_candidates_falls_back_to_discovery_order_on_scan_log_failure(monkeypatch):
    """Best-effort: a last_scan_map failure must never block discovery --
    degrades to the raw discovery order, same doctrine as every other
    optional signal in this pipeline."""
    from aria_core import momentum_entry, momentum_scan_log

    async def fake_discover(*, chains=momentum_entry.DEFAULT_CHAINS, limit_per_chain=30):
        return [{"contract": A, "chain": "base"}, {"contract": B, "chain": "base"}]

    async def failing_last_scan_map(pairs):
        raise RuntimeError("DB unavailable")

    monkeypatch.setattr(momentum_entry, "discover_momentum_candidates", fake_discover)
    monkeypatch.setattr(momentum_scan_log, "last_scan_map", failing_last_scan_map)

    contracts, _ = await pt._momentum_candidates_and_chain_map(limit=2)
    assert set(contracts) == {A, B}


@pytest.mark.asyncio
async def test_default_momentum_analyzer_routes_bonding_chain_to_bonding_entry(monkeypatch):
    from aria_core import bonding_entry, momentum_entry
    from aria_core.bonding_entry import CHAIN_MARKER

    bonding_called_with = {}
    momentum_called_with = {}

    async def fake_bonding_eval(contract, *, weekly_context=None, current_regime=None):
        bonding_called_with["contract"] = contract
        return {"action": "HOLD", "chain": CHAIN_MARKER, "hold_reason": "test"}

    async def fake_momentum_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        momentum_called_with["contract"] = contract
        momentum_called_with["chain"] = chain
        return {"action": "HOLD", "chain": chain, "hold_reason": "test"}

    monkeypatch.setattr(bonding_entry, "evaluate_bonding_entry", fake_bonding_eval)
    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_momentum_eval)

    analyzer = pt._default_momentum_analyzer({A: CHAIN_MARKER, B: "base"})

    await analyzer(A)
    await analyzer(B)

    assert bonding_called_with["contract"] == A
    assert momentum_called_with == {"contract": B, "chain": "base"}


@pytest.mark.asyncio
async def test_vc_analyzer_with_bonding_routes_bonding_chain_to_bonding_entry(monkeypatch):
    """Item #157, 28/07: the VC pocket's own analyzer must route a bonding-
    tagged contract to evaluate_bonding_entry (never _default_analyzer, which
    depends on DexScreener/GoPlus and can't process a bonding-curve token),
    while a plain Base contract still goes through the historical VC-thesis
    path unchanged."""
    from aria_core import bonding_entry
    from aria_core.bonding_entry import CHAIN_MARKER

    bonding_called_with = {}
    vc_called_with = {}

    async def fake_bonding_eval(contract):
        bonding_called_with["contract"] = contract
        return {"action": "HOLD", "chain": CHAIN_MARKER, "hold_reason": "test"}

    async def fake_default_analyzer(contract):
        vc_called_with["contract"] = contract
        return {"action": "HOLD", "chain": "base", "hold_reason": "test"}

    monkeypatch.setattr(bonding_entry, "evaluate_bonding_entry", fake_bonding_eval)
    monkeypatch.setattr(pt, "_default_analyzer", fake_default_analyzer)

    analyzer = pt._vc_analyzer_with_bonding({A: CHAIN_MARKER, B: "base"})

    await analyzer(A)
    await analyzer(B)

    assert bonding_called_with["contract"] == A
    assert vc_called_with["contract"] == B


@pytest.mark.asyncio
async def test_multi_pocket_gate_on_vc_pocket_sources_bonding_candidates(tmp_db, monkeypatch):
    """Item #157: a bonding-tagged contract found by the shared momentum
    discovery is ALSO appended to the VC pocket's own candidate list and
    analyzed via evaluate_bonding_entry, opening a wallet="vc" position on it
    -- the SAME contract can legitimately end up open in scalping/swing (via
    momentum discovery) AND vc (via this new wiring) at once, same accepted
    cross-pocket overlap already proven for D/E above. strategy stays
    "momentum" (bonding_entry.py's own dict), never "vc_thesis" -- only the
    capital pool/reset eligibility changes with the wallet, never the exit
    discipline."""
    # 08/05 -- sourcing pause neutralized: this test validates the pocket
    # ARCHITECTURE, the pause behavior has its own dedicated tests.
    monkeypatch.setattr(pt, "SOURCING_PAUSED_WALLETS", frozenset())
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setenv("ARIA_VC_POCKET_SOURCING_ENABLED", "true")
    from aria_core import bonding_entry
    from aria_core.bonding_entry import CHAIN_MARKER
    from aria_core.skills import candidate_ranking

    async def _fake_sources(*, limit=20):
        return [], {F: CHAIN_MARKER}  # no scalping/swing candidates this cycle, F is bonding-only

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_bonding_eval(contract):
        return {
            "action": "BUY", "chain": CHAIN_MARKER, "symbol": "BOND", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "strategy": "momentum",
        }

    monkeypatch.setattr(bonding_entry, "evaluate_bonding_entry", _fake_bonding_eval)

    class _FakeRankedCandidate:
        def __init__(self, contract: str) -> None:
            self.contract = contract

    async def _fake_top_candidates(limit):
        return []  # the classic VC-thesis source is empty this cycle

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    async def _price_lookup(contract, chain="base"):
        return 1.0

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(price_lookup=_price_lookup, depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    assert act["opened"][0]["contract"] == F
    assert act["opened"][0]["wallet"] == "vc"
    assert act["opened"][0]["strategy"] == "momentum"  # never vc_thesis for a bonding position


@pytest.mark.asyncio
async def test_multi_pocket_gate_on_megacap_pocket_sources_from_fixed_watchlist(tmp_db, monkeypatch):
    """02/08 -- the "megacap" pocket does NOT go through
    build_scalping_pocket_entries() (structurally scalping-only) -- it is a
    3rd statically-added tuple entry, built the same way as "vc" just above
    it in the real loop. Candidates come from fixed_watchlist.
    list_watchlist_candidates(), never momentum_candidates -- confirmed here
    by mocking a single fixed-watchlist contract distinct from the shared
    momentum discovery (empty this cycle)."""
    # 08/05 -- sourcing pause neutralized: this test validates the pocket
    # ARCHITECTURE, the pause behavior has its own dedicated tests.
    monkeypatch.setattr(pt, "SOURCING_PAUSED_WALLETS", frozenset())
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setenv("ARIA_FIXED_WATCHLIST_POCKET_ENABLED", "true")
    from aria_core import fixed_watchlist
    from aria_core.skills import candidate_ranking

    async def _fake_sources(*, limit=20):
        return [], {}  # no scalping/swing candidates this cycle

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_top_candidates(limit):
        return []  # the vc pocket sources nothing this cycle (gate off anyway)

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    async def _fake_list_watchlist_candidates():
        return [{"contract": G, "chain": "base", "symbol": "MEGA"}]

    monkeypatch.setattr(fixed_watchlist, "list_watchlist_candidates", _fake_list_watchlist_candidates)

    async def _fake_default_momentum_analyzer_factory(*args, **kwargs):
        async def _analyzer(contract):
            return {
                "action": "BUY", "chain": "base", "symbol": "MEGA", "price": 1.0,
                "target": 2.0, "invalidation": 0.9, "strategy": "momentum",
            }
        return _analyzer

    async def _fake_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "MEGA", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "strategy": "momentum",
        }

    def _fake_default_momentum_analyzer(chain_by_contract, **kwargs):
        # Called for scalping/swing/vc/megacap alike (build_scalping_pocket_entries
        # also goes through this same factory with mode="scalping") -- the
        # mode="standard" assertion belongs on the opened position below, not here.
        return _fake_analyzer

    monkeypatch.setattr(pt, "_default_momentum_analyzer", _fake_default_momentum_analyzer)

    async def _price_lookup(contract, chain="base"):
        return 1.0

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(price_lookup=_price_lookup, depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    assert act["opened"][0]["contract"] == G
    assert act["opened"][0]["wallet"] == "megacap"
    assert act["opened"][0]["mode"] == "standard"


@pytest.mark.asyncio
async def test_multi_pocket_gate_off_megacap_never_sourced(tmp_db, monkeypatch):
    """fixed_watchlist_pocket_enabled() OFF -- no "megacap" entry reaches
    _open_new_entries_for_wallet at all, byte-for-byte the pre-existing
    2-pocket (scalping/swing) + vc behavior."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.delenv("ARIA_FIXED_WATCHLIST_POCKET_ENABLED", raising=False)
    from aria_core import fixed_watchlist
    from aria_core.skills import candidate_ranking

    async def _fake_sources(*, limit=20):
        return [], {}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_top_candidates(limit):
        return []

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    called = {"hit": False}

    async def _fake_list_watchlist_candidates():
        called["hit"] = True
        return [{"contract": G, "chain": "base", "symbol": "MEGA"}]

    monkeypatch.setattr(fixed_watchlist, "list_watchlist_candidates", _fake_list_watchlist_candidates)

    async def _price_lookup(contract, chain="base"):
        return 1.0

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(price_lookup=_price_lookup, depeg_check=_no_depeg)

    assert act["opened"] == []
    # fixed_watchlist IS still queried (unconditional construction, mirrors
    # vc_candidates) -- only the OPEN attempt is gated, never the fetch.
    assert called["hit"] is True


@pytest.mark.asyncio
async def test_multi_pocket_tracking_alert_sums_cash_across_all_3_pockets(tmp_db, monkeypatch):
    """29/07 -- real operator confusion ("pourquoi il y a que 1 wallet...
    il vaut 1400000 alors qu'il y a quelques heures il valait 995k"): the
    periodic tracking alert's ``tracked`` list already spans every pocket
    (position management is a single unified loop), but ``cash_available()``
    used to default to "swing" alone -- mixing one pocket's cash with all 3
    pockets' position value into a number that was neither a real
    single-pocket total nor a real combined one. Must now sum all 3."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    from aria_core.skills import candidate_ranking

    async def _no_new_candidates(*, limit=20):
        return [], {}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _no_new_candidates)

    async def _fake_top_candidates(limit):
        return []

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    # 06/08 -- migrated the historical "scalping" wallet to "scalping_v8"
    # (v1-v7 retired, hidden from every operator-facing pocket count).
    for wallet in ("scalping_v8", "swing", "vc"):
        await pt.reset_portfolio(1_000_000.0, wallet=wallet)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000.0, wallet="scalping_v8")
    await pt.open_position(B, "BBB", 1.0, alloc_usd=50_000.0, wallet="swing")
    # vc pocket left empty -- its full $1M cash must still count toward the total.

    async def _price_lookup(contract, chain="base"):
        return 1.0  # unchanged from entry -- keeps the arithmetic exact

    notified = []

    async def _notifier(msg):
        notified.append(msg)

    await pt.run_paper_cycle(price_lookup=_price_lookup, notifier=_notifier, depeg_check=_no_depeg)

    tracking_msgs = [m for m in notified if "suivi positions ouvertes" in m]
    assert len(tracking_msgs) == 1
    msg = tracking_msgs[0]
    assert "3 poches combinées" in msg
    # cash: (1M-50k) + (1M-50k) + 1M = 2,900,000 ; equity: cash + 100,000 open value = 3,000,000
    assert "2,900,000" in msg
    assert "3,000,000" in msg


@pytest.mark.asyncio
async def test_default_momentum_analyzer_records_verdict_for_cross_path_dedup(monkeypatch):
    """Item #128, 28/07: this closure is the ONE place both the periodic
    heartbeat cycle and the WebSocket drain evaluate a candidate -- recording
    here (rather than inside evaluate_momentum_entry's many internal early
    returns) covers both callers with a single write site."""
    from aria_core import momentum_entry, momentum_timing

    momentum_timing._recent_evaluations.clear()

    async def fake_momentum_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        return {"action": "HOLD", "chain": chain, "hold_reason": "test"}

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_momentum_eval)

    analyzer = pt._default_momentum_analyzer({B: "base"})
    await analyzer(B)

    assert momentum_timing.recently_evaluated_action(B, "base") == "HOLD"


@pytest.mark.asyncio
async def test_default_momentum_analyzer_records_none_verdict_without_crashing(monkeypatch):
    """``evaluate_momentum_entry`` returns ``None`` on no usable price data --
    the analyzer must still record (a None verdict, not a crash) rather than
    calling ``.get`` on ``None``."""
    from aria_core import momentum_entry, momentum_timing

    momentum_timing._recent_evaluations.clear()

    async def fake_momentum_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        return None

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", fake_momentum_eval)

    analyzer = pt._default_momentum_analyzer({B: "base"})
    result = await analyzer(B)

    assert result is None
    assert (B.lower(), "base") in momentum_timing._recent_evaluations


@pytest.mark.asyncio
async def test_bonding_pair_lookup_converts_price_to_usd(monkeypatch):
    from aria_core.services.virtuals import VirtualToken, VirtualTrade

    token = VirtualToken(
        symbol="BOND", status="UNDERGRAD", token_address=None,
        pre_token_address=A, liquidity_usd=12_000.0,
    )

    class _FakeClient:
        async def fetch_by_address(self, contract, chain="BASE"):
            return token

        async def fetch_recent_trades(self, contract, *, limit=1, chain_id=0):
            return [VirtualTrade(timestamp=1, price=0.002, is_buy=True)]

    monkeypatch.setattr("aria_core.services.virtuals.virtuals_client", _FakeClient())

    async def fake_rate():
        return 0.6

    monkeypatch.setattr("aria_core.services.virtuals.virtual_usd_rate", fake_rate)

    pair = await pt._bonding_pair_lookup(A)

    assert pair is not None
    assert pair.price_usd == pytest.approx(0.002 * 0.6)
    assert pair.liquidity_usd == pytest.approx(12_000.0)
    assert pair.pair_address == ""  # never fabricated -- forces _robust_close_price to spot


@pytest.mark.asyncio
async def test_bonding_pair_lookup_none_when_rate_unavailable(monkeypatch):
    from aria_core.services.virtuals import VirtualToken, VirtualTrade

    token = VirtualToken(symbol="BOND", status="UNDERGRAD", pre_token_address=A)

    class _FakeClient:
        async def fetch_by_address(self, contract, chain="BASE"):
            return token

        async def fetch_recent_trades(self, contract, *, limit=1, chain_id=0):
            return [VirtualTrade(timestamp=1, price=0.002, is_buy=True)]

    monkeypatch.setattr("aria_core.services.virtuals.virtuals_client", _FakeClient())

    async def fake_rate_unavailable():
        return None

    monkeypatch.setattr("aria_core.services.virtuals.virtual_usd_rate", fake_rate_unavailable)

    assert await pt._bonding_pair_lookup(A) is None


@pytest.mark.asyncio
async def test_bonding_pair_lookup_hands_off_to_dexscreener_after_graduation(monkeypatch):
    """Once a bonding token graduates, is_in_bonding turns False -- price
    lookup must hand off to the SAME DexScreener path a standard momentum
    position already uses, rather than keep reading a bonding-only source."""
    from aria_core.services.dexscreener import PairSnapshot
    from aria_core.services.virtuals import VirtualToken

    graduated = VirtualToken(symbol="BOND", status="AVAILABLE", token_address=A)
    real_pair = PairSnapshot(pair_address="real_pool", price_usd=3.14, liquidity_usd=90_000.0, base_address=A)

    class _FakeClient:
        async def fetch_by_address(self, contract, chain="BASE"):
            return graduated

        async def fetch_recent_trades(self, contract, *, limit=1, chain_id=0):
            raise AssertionError("must not query bonding trades once graduated")

    monkeypatch.setattr("aria_core.services.virtuals.virtuals_client", _FakeClient())

    async def fake_fetch_token_pairs(contract, *, chain="base"):
        assert chain == "base"
        return [real_pair]

    monkeypatch.setattr("aria_core.services.dexscreener.fetch_token_pairs", fake_fetch_token_pairs)

    pair = await pt._bonding_pair_lookup(A)

    assert pair.pair_address == "real_pool"
    assert pair.price_usd == pytest.approx(3.14)


@pytest.mark.asyncio
async def test_bonding_pair_lookup_none_when_token_unresolved(monkeypatch):
    class _FakeClient:
        async def fetch_by_address(self, contract, chain="BASE"):
            return None

    monkeypatch.setattr("aria_core.services.virtuals.virtuals_client", _FakeClient())

    assert await pt._bonding_pair_lookup(A) is None


@pytest.mark.asyncio
async def test_default_pair_lookup_routes_bonding_chain_marker(monkeypatch):
    from aria_core.bonding_entry import CHAIN_MARKER
    from aria_core.services.dexscreener import PairSnapshot

    fake_pair = PairSnapshot(pair_address="", price_usd=1.23, liquidity_usd=5_000.0, base_address=A)

    async def fake_bonding_lookup(contract):
        assert contract == A
        return fake_pair

    monkeypatch.setattr(pt, "_bonding_pair_lookup", fake_bonding_lookup)

    result = await pt._default_pair_lookup(A, chain=CHAIN_MARKER)

    assert result is fake_pair


@pytest.mark.asyncio
async def test_doppler_pair_lookup_converts_price(monkeypatch):
    """24/07 -- same gap as _bonding_pair_lookup, for a Bankr/Doppler token
    instead of a Virtuals bonding one: no DexScreener entry, no on-chain
    price read -> the position's trailing stop/TP could never fire."""
    async def fake_get_token_price_usd(contract, *, token_decimals=18, w3=None):
        assert contract == A
        return 1.1e-07

    monkeypatch.setattr("aria_core.services.doppler.get_token_price_usd", fake_get_token_price_usd)

    pair = await pt._doppler_pair_lookup(A)

    assert pair is not None
    assert pair.price_usd == pytest.approx(1.1e-07)
    assert pair.liquidity_usd == 0.0
    assert pair.pair_address == ""  # never fabricated -- forces _robust_close_price to spot


@pytest.mark.asyncio
async def test_doppler_pair_lookup_none_when_price_unavailable(monkeypatch):
    async def fake_get_token_price_usd(contract, *, token_decimals=18, w3=None):
        return None

    monkeypatch.setattr("aria_core.services.doppler.get_token_price_usd", fake_get_token_price_usd)
    assert await pt._doppler_pair_lookup(A) is None


@pytest.mark.asyncio
async def test_default_pair_lookup_routes_doppler_chain_marker(monkeypatch):
    from aria_core.services import doppler
    from aria_core.services.dexscreener import PairSnapshot

    fake_pair = PairSnapshot(pair_address="", price_usd=1.1e-07, liquidity_usd=0.0, base_address=A)

    async def fake_doppler_lookup(contract):
        assert contract == A
        return fake_pair

    monkeypatch.setattr(pt, "_doppler_pair_lookup", fake_doppler_lookup)

    result = await pt._default_pair_lookup(A, chain=doppler.CHAIN_MARKER)

    assert result is fake_pair


@pytest.mark.asyncio
async def test_bonding_signal_gets_extra_size_reduction(tmp_db, monkeypatch):
    """The core fix of task #69: a bonding-tagged BUY signal's allocation is
    the STANDARD risk/ATR sizing (compute_entry_alloc) multiplied by
    BONDING_SIZE_REDUCTION -- never the standard sizing alone."""
    from aria_core import bonding_entry

    captured: dict = {}
    real_compute_entry_alloc = pt.compute_entry_alloc

    def spy_compute_entry_alloc(sig, start, weekly_context, risk_state):
        alloc, tier = real_compute_entry_alloc(sig, start, weekly_context, risk_state)
        captured["pre_reduction_alloc"] = alloc
        return alloc, tier

    monkeypatch.setattr(pt, "compute_entry_alloc", spy_compute_entry_alloc)

    real_open_position = pt.open_position

    async def spy_open_position(*args, **kwargs):
        captured["alloc_usd"] = kwargs.get("alloc_usd")
        return await real_open_position(*args, **kwargs)

    monkeypatch.setattr(pt, "open_position", spy_open_position)

    async def fake_price_lookup(contract, *, chain: str = "base"):
        return 1.0  # fully synthetic, no DexScreener/Virtuals/CoinGecko call

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "chain": bonding_entry.CHAIN_MARKER, "symbol": "BOND",
            "price": 1.0, "target": 2.0, "invalidation": 0.5, "rr": 3.0,
            "align_score": 2, "entry_atr_pct": 0.2, "strategy": "momentum",
            "reasons": ["setup bonding"],
        }

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=fake_analyzer, price_lookup=fake_price_lookup, depeg_check=_no_depeg,
    )

    assert len(act["opened"]) == 1
    assert "pre_reduction_alloc" in captured and "alloc_usd" in captured
    assert captured["alloc_usd"] == pytest.approx(
        captured["pre_reduction_alloc"] * bonding_entry.BONDING_SIZE_REDUCTION
    )
    assert act["opened"][0]["chain"] == bonding_entry.CHAIN_MARKER


@pytest.mark.asyncio
async def test_bonding_signal_reduced_further_in_late_btc_cycle(tmp_db, monkeypatch):
    """Item #165: a late-cycle BTC macro backdrop ("distribution") applies an
    ADDITIONAL tighten-only reduction on top of BONDING_SIZE_REDUCTION."""
    from aria_core import bonding_entry
    from aria_core.skills import btc_cycles

    async def _fake_late_cycle(*, client=None, force_refresh=False):
        return {"label": "distribution", "since": "2026-01-01", "change_pct": -5.0, "cycle_name": "test"}

    monkeypatch.setattr(btc_cycles, "fetch_current_macro_phase", _fake_late_cycle)

    captured: dict = {}
    real_compute_entry_alloc = pt.compute_entry_alloc

    def spy_compute_entry_alloc(sig, start, weekly_context, risk_state):
        alloc, tier = real_compute_entry_alloc(sig, start, weekly_context, risk_state)
        captured["pre_reduction_alloc"] = alloc
        return alloc, tier

    monkeypatch.setattr(pt, "compute_entry_alloc", spy_compute_entry_alloc)

    real_open_position = pt.open_position

    async def spy_open_position(*args, **kwargs):
        captured["alloc_usd"] = kwargs.get("alloc_usd")
        return await real_open_position(*args, **kwargs)

    monkeypatch.setattr(pt, "open_position", spy_open_position)

    async def fake_price_lookup(contract, *, chain: str = "base"):
        return 1.0

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "chain": bonding_entry.CHAIN_MARKER, "symbol": "BOND",
            "price": 1.0, "target": 2.0, "invalidation": 0.5, "rr": 3.0,
            "align_score": 2, "entry_atr_pct": 0.2, "strategy": "momentum",
            "reasons": ["setup bonding"],
        }

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=fake_analyzer, price_lookup=fake_price_lookup, depeg_check=_no_depeg,
    )

    assert len(act["opened"]) == 1
    expected = (
        captured["pre_reduction_alloc"]
        * bonding_entry.BONDING_SIZE_REDUCTION
        * bonding_entry._BTC_LATE_CYCLE_SIZE_MULTIPLIER
    )
    assert captured["alloc_usd"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_non_bonding_signal_unaffected_by_bonding_reduction(tmp_db, monkeypatch):
    """Regression guard: a standard momentum ("base") BUY must NOT be
    affected by the bonding-only reduction."""
    captured: dict = {}
    real_compute_entry_alloc = pt.compute_entry_alloc

    def spy_compute_entry_alloc(sig, start, weekly_context, risk_state):
        alloc, tier = real_compute_entry_alloc(sig, start, weekly_context, risk_state)
        captured["pre_reduction_alloc"] = alloc
        return alloc, tier

    monkeypatch.setattr(pt, "compute_entry_alloc", spy_compute_entry_alloc)

    real_open_position = pt.open_position

    async def spy_open_position(*args, **kwargs):
        captured["alloc_usd"] = kwargs.get("alloc_usd")
        return await real_open_position(*args, **kwargs)

    monkeypatch.setattr(pt, "open_position", spy_open_position)

    async def fake_price_lookup(contract, *, chain: str = "base"):
        return 1.0

    async def fake_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "BASE",
            "price": 1.0, "target": 2.0, "invalidation": 0.5, "rr": 3.0,
            "align_score": 2, "entry_atr_pct": 0.2, "strategy": "momentum",
            "reasons": ["setup momentum standard"],
        }

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=fake_analyzer, price_lookup=fake_price_lookup, depeg_check=_no_depeg,
    )

    assert len(act["opened"]) == 1
    assert captured["alloc_usd"] == pytest.approx(captured["pre_reduction_alloc"])


# ── #154, 28/07 -- bonding 4-tier exit design (Take-Seed/Tier2/Tier3/moonbag) ──

@pytest.mark.asyncio
async def test_bonding_position_take_seed_tier_sells_the_bonding_fraction(tmp_db):
    """A bonding position must use BONDING_TP_STAGE_FRACTIONS (front-loaded,
    unequal), never the generic equal-thirds TP_STAGE_FRACTION."""
    await pt.reset_portfolio(1_000_000.0)

    from aria_core import bonding_entry

    async def analyzer(contract):
        # rr=None matches the real bonding_entry.py fallback (#152): no
        # technical signal -> honestly reports rr=None, which routes the
        # fresh-price re-check to the ambiguous floor (1.0) rather than the
        # stricter direct-buy floor (2.0) a declared rr>=2.0 would demand.
        return {
            "action": "BUY", "chain": bonding_entry.CHAIN_MARKER, "symbol": "BOND",
            "price": 1.0, "target": 2.0, "invalidation": 0.35, "rr": None,
            "align_score": 0, "entry_atr_pct": 0.2, "strategy": "momentum",
            "reasons": ["setup bonding"],
        }

    prices = {"v": 1.0}

    async def price_lookup(contract, *, chain: str = "base"):
        return prices["v"]

    await pt.run_paper_cycle(
        candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    pos_before = await pt._get_open(A)
    initial_qty = pos_before["qty"]

    prices["v"] = 2.0  # +100% -- BONDING_TP_STAGES[0], the Take-Seed tier
    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
    )

    assert act["closed"] == []
    assert len(act["partial"]) == 1
    pos_after = await pt._get_open(A)
    assert pos_after["tp_stage_hit"] == 1
    sold = initial_qty - pos_after["qty"]
    assert sold == pytest.approx(initial_qty * pt.BONDING_TP_STAGE_FRACTIONS[0])


@pytest.mark.asyncio
async def test_bonding_position_survives_tier3_as_a_real_moonbag(tmp_db):
    """The core behavior change of #154: unlike the generic system (whose
    last configured stage always fully closes), bonding's 3rd tier is STILL
    a partial sell -- ~10% of the initial qty survives as a moonbag, managed
    by the trailing stop alone, never force-closed by the TP mechanism."""
    await pt.reset_portfolio(1_000_000.0)

    from aria_core import bonding_entry

    async def analyzer(contract):
        # rr=None matches the real bonding_entry.py fallback (#152): no
        # technical signal -> honestly reports rr=None, which routes the
        # fresh-price re-check to the ambiguous floor (1.0) rather than the
        # stricter direct-buy floor (2.0) a declared rr>=2.0 would demand.
        return {
            "action": "BUY", "chain": bonding_entry.CHAIN_MARKER, "symbol": "BOND",
            "price": 1.0, "target": 2.0, "invalidation": 0.35, "rr": None,
            "align_score": 0, "entry_atr_pct": 0.2, "strategy": "momentum",
            "reasons": ["setup bonding"],
        }

    prices = {"v": 1.0}

    async def price_lookup(contract, *, chain: str = "base"):
        return prices["v"]

    await pt.run_paper_cycle(
        candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    pos_before = await pt._get_open(A)
    initial_qty = pos_before["qty"]

    # Jump straight past all 3 bonding tiers in one cycle (12.5x price).
    prices["v"] = 12.5
    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
    )

    assert act["closed"] == []  # NOT fully closed -- the moonbag survives
    pos_after = await pt._get_open(A)
    assert pos_after is not None
    assert pos_after["tp_stage_hit"] == 3
    expected_remaining_fraction = 1.0 - sum(pt.BONDING_TP_STAGE_FRACTIONS)
    assert pos_after["qty"] == pytest.approx(initial_qty * expected_remaining_fraction, rel=1e-6)


# ── #155, 28/07 -- bonding 3-volet stop-loss (velocity + liquidity floor) ──

def test_advance_velocity_window_triggers_on_fast_crash():
    """A drop >= BONDING_VELOCITY_DROP_PCT from the still-active reference
    triggers immediately, even well inside the window."""
    from datetime import datetime, timezone

    ref_since = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)  # 10 min later
    new_ref_price, new_ref_since, triggered = pt._advance_velocity_window(1.0, ref_since, 0.55, now)
    assert triggered is True
    assert new_ref_price == 1.0  # reference preserved, never rolled forward on a trigger
    assert new_ref_since == ref_since


def test_advance_velocity_window_survives_a_minor_dip():
    from datetime import datetime, timezone

    ref_since = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)
    new_ref_price, new_ref_since, triggered = pt._advance_velocity_window(1.0, ref_since, 0.9, now)
    assert triggered is False
    assert new_ref_price == 1.0
    assert new_ref_since == ref_since


def test_advance_velocity_window_rolls_forward_after_expiry_without_trigger():
    from datetime import datetime, timezone

    ref_since = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    now = datetime(2026, 1, 1, 0, 31, tzinfo=timezone.utc)  # past the 30-min window
    new_ref_price, new_ref_since, triggered = pt._advance_velocity_window(1.0, ref_since, 0.9, now)
    assert triggered is False
    assert new_ref_price == 0.9  # fresh anchor
    assert new_ref_since == now.isoformat()


def test_advance_velocity_window_initializes_with_no_prior_reference():
    from datetime import datetime, timezone

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new_ref_price, new_ref_since, triggered = pt._advance_velocity_window(None, None, 1.0, now)
    assert triggered is False
    assert new_ref_price == 1.0
    assert new_ref_since == now.isoformat()


@pytest.mark.asyncio
async def test_bonding_position_closes_on_velocity_crash(tmp_db, monkeypatch):
    """A fast crash (>= BONDING_VELOCITY_DROP_PCT within the rolling window)
    must close a bonding position outright, independent of the ATR trailing
    stop/invalidation. Requires 2 management cycles: the first anchors the
    velocity reference (never triggers on a brand-new reference), the second
    measures the drop against it."""
    from aria_core import bonding_entry

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "BOND", 1.0, alloc_usd=100, strategy="momentum",
        chain=bonding_entry.CHAIN_MARKER, pool_liquidity_usd=50_000.0, wallet="swing",
    )
    monkeypatch.setattr(pt, "_default_pair_lookup", _vc_position_pair_lookup(price=1.0, liquidity_usd=50_000.0))
    act1 = await pt.run_paper_cycle(candidates=[])
    assert act1["closed"] == []  # anchors the reference, no trigger yet
    assert await pt.has_open(A)

    monkeypatch.setattr(pt, "_default_pair_lookup", _vc_position_pair_lookup(price=0.55, liquidity_usd=50_000.0))
    act2 = await pt.run_paper_cycle(candidates=[])
    assert len(act2["closed"]) == 1
    assert act2["closed"][0]["close_reason"] == "stop bonding (vélocité)"
    assert not await pt.has_open(A)


@pytest.mark.asyncio
async def test_bonding_position_survives_a_minor_velocity_dip(tmp_db, monkeypatch):
    from aria_core import bonding_entry

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "BOND", 1.0, alloc_usd=100, strategy="momentum",
        chain=bonding_entry.CHAIN_MARKER, pool_liquidity_usd=50_000.0, wallet="swing",
    )
    monkeypatch.setattr(pt, "_default_pair_lookup", _vc_position_pair_lookup(price=1.0, liquidity_usd=50_000.0))
    await pt.run_paper_cycle(candidates=[])

    # 10% dip -- below BONDING_VELOCITY_DROP_PCT (40%) and comfortably above
    # the generic ATR-less trailing stop's own boundary (TRAIL_STOP_PCT=15%,
    # active_stop=0.85 here), so this position must survive on BOTH counts.
    monkeypatch.setattr(pt, "_default_pair_lookup", _vc_position_pair_lookup(price=0.90, liquidity_usd=50_000.0))
    act2 = await pt.run_paper_cycle(candidates=[])
    assert act2["closed"] == []
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_bonding_position_closes_on_absolute_liquidity_floor(tmp_db, monkeypatch):
    from aria_core import bonding_entry

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "BOND", 1.0, alloc_usd=100, strategy="momentum",
        chain=bonding_entry.CHAIN_MARKER, pool_liquidity_usd=50_000.0, wallet="swing",
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=1.05, liquidity_usd=8_000.0),  # < BONDING_LIQUIDITY_FLOOR_USD
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "stop bonding (liquidité)"
    assert not await pt.has_open(A)


@pytest.mark.asyncio
async def test_bonding_position_closes_on_relative_liquidity_drop(tmp_db, monkeypatch):
    from aria_core import bonding_entry

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "BOND", 1.0, alloc_usd=100, strategy="momentum",
        chain=bonding_entry.CHAIN_MARKER, pool_liquidity_usd=100_000.0, wallet="swing",
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=1.05, liquidity_usd=40_000.0),  # 60% de chute, > plancher absolu
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "stop bonding (liquidité)"


@pytest.mark.asyncio
async def test_bonding_position_closes_on_sudden_liquidity_drop_between_cycles(tmp_db, monkeypatch):
    """Two cycles, each dip alone stays under BOTH the absolute floor and the
    cumulative-since-entry threshold, but the SUDDEN drop between them (30%)
    must still invalidate."""
    from aria_core import bonding_entry

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "BOND", 1.0, alloc_usd=100, strategy="momentum",
        chain=bonding_entry.CHAIN_MARKER, pool_liquidity_usd=100_000.0, wallet="swing",
    )
    monkeypatch.setattr(pt, "_default_pair_lookup", _vc_position_pair_lookup(price=1.0, liquidity_usd=80_000.0))
    act1 = await pt.run_paper_cycle(candidates=[])
    assert act1["closed"] == []

    monkeypatch.setattr(pt, "_default_pair_lookup", _vc_position_pair_lookup(price=1.0, liquidity_usd=55_000.0))
    act2 = await pt.run_paper_cycle(candidates=[])
    assert len(act2["closed"]) == 1
    assert act2["closed"][0]["close_reason"] == "stop bonding (liquidité)"


@pytest.mark.asyncio
async def test_bonding_position_survives_a_minor_liquidity_dip(tmp_db, monkeypatch):
    """Regression guard: a modest liquidity dip (normal noise) must never
    trigger the bonding volet-3 stop."""
    from aria_core import bonding_entry

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "BOND", 1.0, alloc_usd=100, strategy="momentum",
        chain=bonding_entry.CHAIN_MARKER, pool_liquidity_usd=100_000.0, wallet="swing",
    )
    monkeypatch.setattr(
        pt, "_default_pair_lookup",
        _vc_position_pair_lookup(price=1.05, liquidity_usd=85_000.0),  # -15%, bruit normal
    )
    act = await pt.run_paper_cycle(candidates=[])
    assert act["closed"] == []
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_non_bonding_position_never_touched_by_bonding_volet23(tmp_db, monkeypatch):
    """Regression guard: a regular (non-bonding) momentum position must never
    be affected by the volet-3 liquidity floor, even at a liquidity profile
    that WOULD trigger it on a bonding position (BONDING_LIQUIDITY_FLOOR_USD).
    Price held constant across cycles so the (unrelated) generic ATR trailing
    stop never fires and confounds the assertion."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, alloc_usd=100, strategy="momentum",
        chain="base", pool_liquidity_usd=100_000.0, wallet="swing",
    )
    monkeypatch.setattr(pt, "_default_pair_lookup", _vc_position_pair_lookup(price=1.0, liquidity_usd=100_000.0))
    await pt.run_paper_cycle(candidates=[])
    monkeypatch.setattr(pt, "_default_pair_lookup", _vc_position_pair_lookup(price=1.0, liquidity_usd=5_000.0))
    act = await pt.run_paper_cycle(candidates=[])
    assert act["closed"] == []
    assert await pt.has_open(A)


@pytest.mark.asyncio
async def test_non_bonding_position_still_fully_closes_on_last_stage(tmp_db):
    """Regression guard: the generic momentum system's existing behavior
    (last stage = full close) must stay untouched for non-bonding positions."""
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {"action": "BUY", "chain": "base", "symbol": "BASE", "price": 1.0, "target": 2.0, "invalidation": 0.5}

    prices = {"v": 1.0}

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(
        candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
    )
    prices["v"] = 100.0  # far past every generic TP stage in one jump
    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=analyzer, price_lookup=price_lookup, depeg_check=_no_depeg,
    )

    assert len(act["closed"]) == 1
    assert not await pt.has_open(A)


# ── 27/07 -- 3-pocket architecture plan, Phase 2 (multi_pocket_sourcing_enabled) ──
# Phase 1 (commit 1d6ba7c1) migrated the schema only (zero behavior change) --
# these tests cover the REAL concurrent-sourcing loop this phase adds:
# has_open/open_position/MAX_POSITIONS wallet-scoping, the gate itself (OFF
# preserves today's single-"swing"-pocket behavior byte for byte, ON sources
# scalping/swing/vc independently every cycle).

@pytest.mark.asyncio
async def test_open_position_requires_wallet_kwarg():
    """``wallet`` is MANDATORY (no default) -- a caller that forgets it must get
    a loud TypeError, never a silent fallback to some pocket."""
    with pytest.raises(TypeError):
        await pt.open_position(A, "AAA", 1.0, alloc_usd=10_000)  # no wallet=


@pytest.mark.asyncio
async def test_same_contract_open_in_two_different_wallets_simultaneously(tmp_db):
    """The whole point of 3 concurrent pockets: has_open/open_position scoped
    per wallet let the SAME contract be legitimately held by 2 (or 3) pockets
    at once -- neither blocks nor counts against the other."""
    await pt.reset_portfolio(1_000_000.0)
    pos_swing = await pt.open_position(A, "AAA", 1.0, alloc_usd=10_000, wallet="swing")
    pos_scalping = await pt.open_position(A, "AAA", 1.0, alloc_usd=10_000, wallet="scalping")
    pos_vc = await pt.open_position(A, "AAA", 1.0, alloc_usd=10_000, wallet="vc")

    assert pos_swing is not None and pos_scalping is not None and pos_vc is not None
    assert len({pos_swing["id"], pos_scalping["id"], pos_vc["id"]}) == 3  # 3 distinct rows

    assert await pt.has_open(A, wallet="swing") is True
    assert await pt.has_open(A, wallet="scalping") is True
    assert await pt.has_open(A, wallet="vc") is True
    # A 4th attempt on an ALREADY-occupied pocket is still refused -- per-wallet
    # scoping doesn't weaken the existing single-position-per-pocket guard.
    assert await pt.open_position(A, "AAA", 1.0, alloc_usd=10_000, wallet="swing") is None

    # has_open(contract) with NO wallet filter (legacy, unscoped) now hits a
    # GENUINE multi-pocket ambiguity (3 distinct open rows on this contract) --
    # by design, already true since Phase 1 (see _get_open's own docstring),
    # this raises loudly rather than silently picking one pocket arbitrarily.
    # Any caller that must stay pocket-agnostic once multi-pocket sourcing is
    # active has to pass wallet= explicitly, exactly as open_position and
    # _open_new_entries_for_wallet both already do.
    with pytest.raises(RuntimeError):
        await pt.has_open(A)


@pytest.mark.asyncio
async def test_get_open_positions_wallet_filter_isolates_pockets(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=10_000, wallet="swing")
    await pt.open_position(B, "BBB", 1.0, alloc_usd=10_000, wallet="scalping")
    await pt.open_position(C, "CCC", 1.0, alloc_usd=10_000, wallet="vc")

    assert [p["contract"] for p in await pt.get_open_positions(wallet="swing")] == [A]
    assert [p["contract"] for p in await pt.get_open_positions(wallet="scalping")] == [B]
    assert [p["contract"] for p in await pt.get_open_positions(wallet="vc")] == [C]
    assert len(await pt.get_open_positions()) == 3  # unfiltered -- all pockets, unchanged


@pytest.mark.asyncio
async def test_open_position_max_positions_cap_stays_legacy_value_regardless_of_wallet(tmp_db):
    """27/07 -- ``open_position``'s OWN inner defense-in-depth cap intentionally
    stays the legacy portfolio-wide ``MAX_POSITIONS`` (30) for ANY wallet (a
    safety net for any caller, e.g. a manual command) -- the REAL per-pocket
    caps (5/15/unlimited) are enforced one level up, per cycle, by
    ``_open_new_entries_for_wallet`` (see the dedicated tests below). Exercises
    the "vc" pocket specifically (real cap would be 5) to prove this inner net
    does NOT itself apply the tighter per-pocket number."""
    await pt.reset_portfolio(1_000_000.0)
    for i in range(pt.MAX_POSITIONS_VC + 2):  # well past the real per-pocket VC cap (5)
        c = "0x" + f"{i:040x}"
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=1_000, wallet="vc") is not None
    assert len(await pt.get_open_positions(wallet="vc")) == pt.MAX_POSITIONS_VC + 2


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_enforces_per_wallet_cap(tmp_db):
    """``_open_new_entries_for_wallet`` (the real per-cycle chokepoint) stops at
    ``max_positions_cap`` regardless of how many MORE candidates remain -- and
    scopes its count to THIS pocket only (a full "scalping"/"swing" pocket
    elsewhere must never block "vc")."""
    from aria_core import risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _buy_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "T", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
        }

    async def _price_lookup(contract):
        return 1.0

    await pt.reset_portfolio(1_000_000.0)
    # Fill "swing" to its own cap (15) FIRST -- must have zero effect on "vc"'s
    # own cap (5) below (independent pockets, independent counts).
    swing_candidates = ["0x" + f"9{i:039x}" for i in range(pt.MAX_POSITIONS_SWING)]
    swing_opened, swing_count = await pt._open_new_entries_for_wallet(
        "swing", swing_candidates, _buy_analyzer,
        price_lookup=_price_lookup, notifier=None, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=pt.MAX_POSITIONS_SWING,
        funnel={},
    )
    assert swing_count == pt.MAX_POSITIONS_SWING == 15

    vc_candidates = ["0x" + f"1{i:039x}" for i in range(10)]  # more than the VC cap (5)
    funnel: dict = {}
    vc_opened, vc_count = await pt._open_new_entries_for_wallet(
        "vc", vc_candidates, _buy_analyzer,
        price_lookup=_price_lookup, notifier=None, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=pt.MAX_POSITIONS_VC,
        funnel=funnel,
    )
    assert vc_count == pt.MAX_POSITIONS_VC == 5
    assert len(vc_opened) == 5
    assert len(await pt.get_open_positions(wallet="vc")) == 5
    assert len(await pt.get_open_positions(wallet="swing")) == pt.MAX_POSITIONS_SWING


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_stops_immediately_when_paused_mid_batch(tmp_db):
    """04/08, real bug found live (operator: "je vien de faire /off et sa sa
    tourne encore"): every CALLER of this function only checks paper_pause.
    is_paused() once, before starting a cycle -- a cycle already past that
    check when /off flips kept creating positions for its entire candidate
    batch (confirmed live: 3 scalping_v6 limit orders created 2m44s-4m20s
    after /off was recorded, during a slow provider-circuit-breaker window).
    Re-checked HERE, inside the per-candidate loop -- must stop the batch
    immediately once paused, regardless of how many candidates remain."""
    from aria_core import paper_pause, risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    calls = {"n": 0}

    async def _buy_analyzer(contract):
        calls["n"] += 1
        return {
            "action": "BUY", "chain": "base", "symbol": "T", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
        }

    async def _price_lookup(contract):
        return 1.0

    paused_state = {"paused": False}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(paper_pause, "is_paused", lambda: paused_state["paused"])

    async def _pause_after_first(contract):
        # Flips /off DURING the batch -- simulates the operator pausing
        # mid-cycle, exactly what the live incident showed.
        paused_state["paused"] = True
        return {
            "action": "BUY", "chain": "base", "symbol": "T", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
        }

    try:
        await pt.reset_portfolio(1_000_000.0)
        candidates = ["0x" + f"9{i:039x}" for i in range(5)]

        opened, count = await pt._open_new_entries_for_wallet(
            "swing", candidates, _pause_after_first,
            price_lookup=_price_lookup, notifier=None, max_new=99,
            using_default_price_lookup=False, closed_this_cycle=set(),
            weekly_context=None, risk_state=risk_state, discovery_channel=None,
            trading_mode="standard", max_positions_cap=None, funnel={},
        )
    finally:
        monkeypatch.undo()

    # Only the FIRST candidate (which itself flipped the pause) was opened --
    # the loop must never reach the remaining 4.
    assert count == 1
    assert len(opened) == 1


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_skips_entire_batch_when_already_paused(tmp_db):
    """Simpler companion to the mid-batch test above: if /off is ALREADY
    active before this function is even called (the common case -- most
    callers already check paper_pause.is_paused() themselves), the loop must
    open nothing at all, not just stop late."""
    from aria_core import paper_pause, risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _buy_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "T", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
        }

    async def _price_lookup(contract):
        return 1.0

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(paper_pause, "is_paused", lambda: True)
    try:
        await pt.reset_portfolio(1_000_000.0)
        candidates = ["0x" + f"9{i:039x}" for i in range(5)]

        opened, count = await pt._open_new_entries_for_wallet(
            "swing", candidates, _buy_analyzer,
            price_lookup=_price_lookup, notifier=None, max_new=99,
            using_default_price_lookup=False, closed_this_cycle=set(),
            weekly_context=None, risk_state=risk_state, discovery_channel=None,
            trading_mode="standard", max_positions_cap=None, funnel={},
        )
    finally:
        monkeypatch.undo()

    assert count == 0
    assert opened == []


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_unlimited_cap_never_breaks(tmp_db):
    """``max_positions_cap=None`` (the scalping doctrine) never stops the loop on
    a count check -- same unlimited philosophy as today's existing
    "trading_mode == scalping" MAX_POSITIONS bypass. 10 candidates, comfortably
    past ``MAX_POSITIONS_VC``(5) -- if the cap were mistakenly applied here too,
    this would stop well short of 10. NOTE: alloc is a FIXED % of a wallet's
    OWN starting capital (``ALLOC_PCT``/conviction tier), so inflating
    ``reset_portfolio``'s amount does NOT buy room for more positions (it
    scales both sides identically) -- real cash availability is the ONLY
    other brake here, by design (never a numeric position-count ceiling in
    scalping), so the candidate count is kept well within what ~5% positions
    of the DEFAULT $1M naturally afford (well under 20)."""
    from aria_core import risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _buy_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "T", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
            # "scalping" mode -- otherwise ``open_position``'s OWN inner
            # defense-in-depth check (legacy MAX_POSITIONS=30, mode-gated,
            # see its docstring) would apply too, muddying which cap this
            # test is actually exercising.
            "mode": "scalping",
        }

    async def _price_lookup(contract):
        return 1.0

    await pt.reset_portfolio(1_000_000.0, wallet="scalping")
    candidates = ["0x" + f"2{i:039x}" for i in range(10)]
    opened, count = await pt._open_new_entries_for_wallet(
        "scalping", candidates, _buy_analyzer,
        price_lookup=_price_lookup, notifier=None, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="scalping", max_positions_cap=pt.MAX_POSITIONS_SCALPING,
        funnel={},
    )
    assert pt.MAX_POSITIONS_SCALPING is None
    assert count == 10  # all 10 opened -- never capped by count, past MAX_POSITIONS_VC(5)


# ── Item #193 (28/07): momentum_scan_log records every evaluation, not just
#    what momentum_funnel_log's own aggregate counters see ──────────────────


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_records_scan_on_hold(tmp_db):
    from aria_core import momentum_scan_log, risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _hold_analyzer(contract):
        return {
            "action": "HOLD", "chain": "base", "symbol": "TOK", "price": 1.5,
            "hold_reason": "volume_too_low", "mode": "standard",
        }

    async def _price_lookup(contract):
        return 1.5

    await pt.reset_portfolio(1_000_000.0)
    await pt._open_new_entries_for_wallet(
        "swing", [A], _hold_analyzer,
        price_lookup=_price_lookup, notifier=None, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )
    row = await momentum_scan_log.last_scan_for(A, "base")
    assert row is not None
    assert row["hold_reason"] == "volume_too_low"
    assert row["symbol"] == "TOK"
    assert row["price"] == pytest.approx(1.5)
    assert row["mode"] == "standard"
    assert row["wallet"] == "swing"


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_notifies_on_holder_concentration_unverifiable(tmp_db):
    """03/08 -- dedicated alert for the new fail-closed HOLD (distinct from
    the silent per-cycle funnel counter used for every other HOLD reason)."""
    from aria_core import risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _hold_analyzer(contract):
        return {
            "action": "HOLD", "chain": "base", "symbol": "TOK", "price": 1.5,
            "hold_reason": "holder_concentration_unverifiable", "mode": "standard",
        }

    async def _price_lookup(contract):
        return 1.5

    notified = []

    async def _notifier(message):
        notified.append(message)

    await pt.reset_portfolio(1_000_000.0)
    await pt._open_new_entries_for_wallet(
        "swing", [A], _hold_analyzer,
        price_lookup=_price_lookup, notifier=_notifier, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )
    assert len(notified) == 1
    assert "TOK" in notified[0]
    assert "invérifiable" in notified[0]


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_no_notification_on_normal_hold(tmp_db):
    """Non-regression: a normal HOLD (any other reason) must never fire this
    new alert -- only the specific unverifiable-security reason does."""
    from aria_core import risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _hold_analyzer(contract):
        return {
            "action": "HOLD", "chain": "base", "symbol": "TOK", "price": 1.5,
            "hold_reason": "holder_concentration", "mode": "standard",
        }

    async def _price_lookup(contract):
        return 1.5

    notified = []

    async def _notifier(message):
        notified.append(message)

    await pt.reset_portfolio(1_000_000.0)
    await pt._open_new_entries_for_wallet(
        "swing", [A], _hold_analyzer,
        price_lookup=_price_lookup, notifier=_notifier, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )
    assert notified == []


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_records_scan_on_analyzer_error(tmp_db):
    from aria_core import momentum_scan_log, risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _boom_analyzer(contract):
        raise RuntimeError("network exploded")

    await pt.reset_portfolio(1_000_000.0)
    await pt._open_new_entries_for_wallet(
        "swing", [A], _boom_analyzer,
        price_lookup=lambda c: None, notifier=None, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )
    row = await momentum_scan_log.last_scan_for(A, "base")
    assert row is not None
    assert row["hold_reason"] == "analyzer_error"


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_records_scan_on_no_price_data(tmp_db):
    from aria_core import momentum_scan_log, risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _none_analyzer(contract):
        return None

    await pt.reset_portfolio(1_000_000.0)
    await pt._open_new_entries_for_wallet(
        "swing", [A], _none_analyzer,
        price_lookup=lambda c: None, notifier=None, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )
    row = await momentum_scan_log.last_scan_for(A, "base")
    assert row is not None
    assert row["hold_reason"] == "no_price_data"


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_records_scan_on_buy(tmp_db):
    from aria_core import momentum_scan_log, risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _buy_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "TOK", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
        }

    async def _price_lookup(contract):
        return 1.0

    await pt.reset_portfolio(1_000_000.0)
    opened_positions, opened = await pt._open_new_entries_for_wallet(
        "swing", [A], _buy_analyzer,
        price_lookup=_price_lookup, notifier=None, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )
    assert opened == 1
    row = await momentum_scan_log.last_scan_for(A, "base")
    assert row is not None
    assert row["hold_reason"] is None  # confirmed BUY, never confused with a HOLD


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_logs_chasing_filter_shadow_check(tmp_db, monkeypatch):
    """Item #65 (08/03), anti-chasing shadow filter: logged right before
    open_position, on the FRESH execution price -- never blocking anything."""
    from aria_core import chasing_filter_shadow, risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _buy_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "TOK", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
            "recent_low": 0.95, "recent_low_window": 25,
            "reasons": ["V3 Stochastique ultra-réactif"],
        }

    async def _price_lookup(contract):
        return 1.01  # slightly fresher than the signal's own 1.0

    calls = []

    async def _fake_record_check(contract, chain, **kw):
        calls.append({"contract": contract, "chain": chain, **kw})

    monkeypatch.setattr(chasing_filter_shadow, "record_check", _fake_record_check)

    await pt.reset_portfolio(1_000_000.0)
    opened_positions, opened = await pt._open_new_entries_for_wallet(
        "swing", [A], _buy_analyzer,
        price_lookup=_price_lookup, notifier=None, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )
    assert opened == 1
    assert len(calls) == 1
    assert calls[0]["contract"] == A
    assert calls[0]["wallet"] == "swing"
    assert calls[0]["source"] == "direct_buy"
    assert calls[0]["recent_low"] == 0.95
    assert calls[0]["recent_low_window"] == 25
    assert calls[0]["execution_price"] == pytest.approx(1.01)


@pytest.mark.asyncio
async def test_open_new_entries_for_wallet_records_scan_on_buy_refused(tmp_db, monkeypatch):
    """A portfolio-level constraint refuses the buy (open_position itself
    returns None -- e.g. insufficient cash, NOT a signal quality issue,
    NOT the position-count cap already checked before the analyzer even
    runs) -- still a real evaluation of this contract, tagged distinctly
    from a confirmed BUY so it's never mistaken for one downstream."""
    from aria_core import momentum_scan_log, risk_guard

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def _buy_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "TOK", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
        }

    async def _price_lookup(contract):
        return 1.0

    async def _refused_open_position(*args, **kwargs):
        return None  # simulates a portfolio-level refusal (e.g. cash short)

    await pt.reset_portfolio(1_000_000.0)
    monkeypatch.setattr(pt, "open_position", _refused_open_position)
    opened_positions, opened = await pt._open_new_entries_for_wallet(
        "swing", [A], _buy_analyzer,
        price_lookup=_price_lookup, notifier=None, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )
    assert opened == 0
    row = await momentum_scan_log.last_scan_for(A, "base")
    assert row is not None
    assert row["hold_reason"] == "buy_refused"


@pytest.mark.asyncio
async def test_multi_pocket_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", raising=False)
    assert pt.multi_pocket_sourcing_enabled() is False


@pytest.mark.asyncio
async def test_multi_pocket_gate_reads_env_var(monkeypatch):
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    assert pt.multi_pocket_sourcing_enabled() is True
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "false")
    assert pt.multi_pocket_sourcing_enabled() is False


@pytest.mark.asyncio
async def test_multi_pocket_gate_off_default_sourcing_matches_pre_chantier_behavior(tmp_db, monkeypatch):
    """Gate OFF (default, no env var) -- the REAL default-sourcing heartbeat path
    (candidates=None, analyzer=None) must open positions ONLY into the "swing"
    pocket, exactly as before this whole chantier. ``_momentum_candidates_and_
    chain_map`` is stubbed WHOLESALE (not just its inner ``discover_momentum_
    candidates`` dependency) so this test never touches the real (network-bound)
    bonding-candidates discovery -- same safe pattern already used by the other
    default-sourcing tests in this file (see ``test_run_cycle_fear_regime_
    halves_new_entry_allocation`` above)."""
    monkeypatch.delenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", raising=False)
    from aria_core import momentum_entry

    async def _fake_sources(*, limit=20):
        return [D], {D: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
        }

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)

    async def _price_lookup(contract):
        return 1.0

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(price_lookup=_price_lookup, depeg_check=_no_depeg)

    assert len(act["opened"]) == 1
    assert act["opened"][0]["wallet"] == "swing"
    assert len(await pt.get_open_positions(wallet="swing")) == 1
    assert await pt.get_open_positions(wallet="scalping") == []
    assert await pt.get_open_positions(wallet="vc") == []


@pytest.mark.asyncio
async def test_multi_pocket_gate_on_sources_three_pockets_independently(tmp_db, monkeypatch):
    """Gate ON: the SAME default-sourcing heartbeat call now opens into ALL 3
    pockets independently in one cycle -- scalping_v8+swing share the same
    momentum discovery/contract (different analyzer ``mode``), vc sources from
    the separate dormant ``candidate_ranking.top_candidates``/``_default_
    analyzer`` path. The SAME contract (D) legitimately ends up open in BOTH
    scalping_v8 and swing at once -- proof #1 of this chantier's whole point."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    monkeypatch.setenv("ARIA_VC_POCKET_SOURCING_ENABLED", "true")
    from aria_core.skills import scalping_variants as _sv

    async def _fake_v8_buy(contract, chain):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "mode": "scalping",
        }

    monkeypatch.setitem(_sv.VARIANT_ANALYZERS, "scalping_v8", _fake_v8_buy)
    # 08/05 -- sourcing pause neutralized: this test validates the 3-pocket
    # architecture itself, the pause has its own dedicated tests.
    monkeypatch.setattr(pt, "SOURCING_PAUSED_WALLETS", frozenset())
    from aria_core import momentum_entry
    from aria_core.skills import candidate_ranking

    async def _fake_sources(*, limit=20):
        return [D], {D: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_momentum_eval(
        contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None,
    ):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3, "mode": mode,
        }

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_momentum_eval)

    class _FakeRankedCandidate:
        def __init__(self, contract: str) -> None:
            self.contract = contract

    async def _fake_top_candidates(limit):
        return [_FakeRankedCandidate(E)]

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    async def _fake_vc_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "EEE", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "strategy": "vc_thesis",
        }

    monkeypatch.setattr(pt, "_default_analyzer", _fake_vc_analyzer)

    async def _price_lookup(contract):
        return 1.0

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(price_lookup=_price_lookup, depeg_check=_no_depeg)

    wallets_opened = {p["wallet"] for p in act["opened"]}
    assert wallets_opened == {"scalping_v8", "swing", "vc"}
    assert len(act["opened"]) == 3

    scalping_positions = await pt.get_open_positions(wallet="scalping_v8")
    swing_positions = await pt.get_open_positions(wallet="swing")
    vc_positions = await pt.get_open_positions(wallet="vc")
    assert len(scalping_positions) == 1 and scalping_positions[0]["contract"] == D
    assert len(swing_positions) == 1 and swing_positions[0]["contract"] == D
    assert len(vc_positions) == 1 and vc_positions[0]["contract"] == E
    # Same contract (D), 2 legitimately distinct open positions (one per pocket).
    assert scalping_positions[0]["id"] != swing_positions[0]["id"]
    assert scalping_positions[0]["mode"] == "scalping"
    assert swing_positions[0]["mode"] == "standard"


@pytest.mark.asyncio
async def test_scalping_only_sourcing_skips_swing_and_vc_new_entries(tmp_db, monkeypatch):
    """08/01 -- operator's temporary call while the scalping stagnation-timeout
    fix is being validated: with ARIA_SCALPING_ONLY_SOURCING_ENABLED on, only
    the scalping pocket opens new positions -- swing/vc are skipped entirely
    this cycle, even though the multi-pocket gate itself is on and their
    candidates/analyzers would otherwise have produced a BUY."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    monkeypatch.setenv("ARIA_SCALPING_ONLY_SOURCING_ENABLED", "true")
    from aria_core.skills import scalping_variants as _sv

    async def _fake_v8_buy(contract, chain):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "mode": "scalping",
        }

    monkeypatch.setitem(_sv.VARIANT_ANALYZERS, "scalping_v8", _fake_v8_buy)
    from aria_core import momentum_entry
    from aria_core.skills import candidate_ranking

    async def _fake_sources(*, limit=20):
        return [D], {D: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_momentum_eval(
        contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None,
    ):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3, "mode": mode,
        }

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_momentum_eval)

    class _FakeRankedCandidate:
        def __init__(self, contract: str) -> None:
            self.contract = contract

    async def _fake_top_candidates(limit):
        return [_FakeRankedCandidate(E)]

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    async def _fake_vc_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "EEE", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "strategy": "vc_thesis",
        }

    monkeypatch.setattr(pt, "_default_analyzer", _fake_vc_analyzer)

    async def _price_lookup(contract):
        return 1.0

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(price_lookup=_price_lookup, depeg_check=_no_depeg)

    wallets_opened = {p["wallet"] for p in act["opened"]}
    assert wallets_opened == {"scalping_v8"}
    assert len(act["opened"]) == 1
    assert await pt.get_open_positions(wallet="swing") == []
    assert await pt.get_open_positions(wallet="vc") == []
    assert len(await pt.get_open_positions(wallet="scalping_v8")) == 1


@pytest.mark.asyncio
async def test_scalping_only_sourcing_off_by_default(tmp_db, monkeypatch):
    monkeypatch.delenv("ARIA_SCALPING_ONLY_SOURCING_ENABLED", raising=False)
    assert pt.scalping_only_sourcing_enabled() is False


# 08/02 -- real gap found live (audit + adversarial verify workflow): the
# "vc" pocket had NO mechanical guardrail enforcing its intended dormancy
# (decided 15/07) -- it was actively sourced every cycle, its dormancy
# resting entirely on no candidate clearing safety_screen's score>=70 bar
# by chance.

@pytest.mark.asyncio
async def test_vc_pocket_sourcing_off_by_default(tmp_db, monkeypatch):
    monkeypatch.delenv("ARIA_VC_POCKET_SOURCING_ENABLED", raising=False)
    assert pt.vc_pocket_sourcing_enabled() is False


@pytest.mark.asyncio
async def test_vc_pocket_sourcing_gate_off_skips_vc_but_not_swing(tmp_db, monkeypatch):
    """Deliberately narrower than scalping_only_sourcing_enabled(): this gate
    only ever skips "vc" -- swing must keep sourcing normally even while vc
    is gated off."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.delenv("ARIA_VC_POCKET_SOURCING_ENABLED", raising=False)
    from aria_core import momentum_entry
    from aria_core.skills import candidate_ranking

    async def _fake_sources(*, limit=20):
        return [D], {D: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_momentum_eval(
        contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None,
    ):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3, "mode": mode,
        }

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_momentum_eval)

    class _FakeRankedCandidate:
        def __init__(self, contract: str) -> None:
            self.contract = contract

    async def _fake_top_candidates(limit):
        return [_FakeRankedCandidate(E)]

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    async def _fake_vc_analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "EEE", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "strategy": "vc_thesis",
        }

    monkeypatch.setattr(pt, "_default_analyzer", _fake_vc_analyzer)

    async def _price_lookup(contract):
        return 1.0

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(price_lookup=_price_lookup, depeg_check=_no_depeg)

    wallets_opened = {p["wallet"] for p in act["opened"]}
    assert "vc" not in wallets_opened
    assert "swing" in wallets_opened
    assert await pt.get_open_positions(wallet="vc") == []


@pytest.mark.asyncio
async def test_scalping_only_sourcing_never_closes_an_already_open_swing_position(tmp_db, monkeypatch):
    """The gate only skips NEW entries -- an already-open swing position keeps
    being managed exactly as before (still eligible for its trailing stop/TP),
    never force-closed just because sourcing is paused."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setenv("ARIA_SCALPING_ONLY_SOURCING_ENABLED", "true")

    async def _fake_sources(*, limit=20):
        return [], {}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    from aria_core.skills import candidate_ranking

    async def _fake_top_candidates(limit):
        return []

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="swing")

    async def _price_lookup(contract):
        return 1.0  # unchanged -- neither stop nor TP triggers

    await pt.run_paper_cycle(price_lookup=_price_lookup, depeg_check=_no_depeg)
    assert await pt.has_open(D)


# ── 5-variant scalping architecture (08/01, scalping_variants_enabled) ──────

def test_scalping_variants_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_SCALPING_VARIANTS_ENABLED", raising=False)
    assert pt.scalping_variants_enabled() is False


def test_all_pocket_wallets_gate_off_returns_swing_vc():
    # 06/08 -- v1-v7 retired: gate OFF no longer resurrects the legacy
    # "scalping" pocket (its engine arm was removed with v6).
    assert pt.all_pocket_wallets() == ("swing", "vc")


def test_all_pocket_wallets_gate_on_returns_v8_plus_two(monkeypatch):
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    assert pt.all_pocket_wallets() == ("scalping_v8", "swing", "vc")


# 08/02 -- real bug found live (adversarial cross-review workflow): several
# callers outside this module tested wallet == "scalping" literally, which
# stopped matching once scalping_variants_enabled() migrated that pocket's
# history to "scalping_v6" alongside 5 new scalping_v1..v5 pockets (real
# impact: limit_orders.py's trigger/watch-mode/position-cap logic and
# heartbeat.py's weekly review loop, see docs/HANDOFF_PIPELINE_MOMENTUM.md).
# is_scalping_pocket() is the single source of truth any future caller
# should use instead of a hardcoded comparison.

def test_is_scalping_pocket_matches_legacy_name():
    assert pt.is_scalping_pocket("scalping") is True


def test_is_scalping_pocket_matches_all_7_variant_wallets():
    for wallet in (
        "scalping_v1", "scalping_v2", "scalping_v3", "scalping_v4", "scalping_v5", "scalping_v6",
        "scalping_v7",
    ):
        assert pt.is_scalping_pocket(wallet) is True


def test_is_scalping_pocket_rejects_non_scalping_wallets():
    assert pt.is_scalping_pocket("swing") is False
    assert pt.is_scalping_pocket("vc") is False
    assert pt.is_scalping_pocket("") is False


# ── "megacap" pocket (02/08, fixed_watchlist.py, 10 established tokens) ────

def test_fixed_watchlist_pocket_enabled_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_FIXED_WATCHLIST_POCKET_ENABLED", raising=False)
    assert pt.fixed_watchlist_pocket_enabled() is False


def test_fixed_watchlist_pocket_enabled_on(monkeypatch):
    monkeypatch.setenv("ARIA_FIXED_WATCHLIST_POCKET_ENABLED", "true")
    assert pt.fixed_watchlist_pocket_enabled() is True


def test_all_pocket_wallets_gate_off_with_megacap_enabled(monkeypatch):
    """Additive to BOTH scalping_variants_enabled() branches -- flipping this
    gate never touches the 6 existing pockets or their sourcing."""
    monkeypatch.delenv("ARIA_SCALPING_VARIANTS_ENABLED", raising=False)
    monkeypatch.setenv("ARIA_FIXED_WATCHLIST_POCKET_ENABLED", "true")
    assert pt.all_pocket_wallets() == ("swing", "vc", "megacap")


def test_all_pocket_wallets_gate_on_with_megacap_enabled(monkeypatch):
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    monkeypatch.setenv("ARIA_FIXED_WATCHLIST_POCKET_ENABLED", "true")
    assert pt.all_pocket_wallets() == ("scalping_v8", "swing", "vc", "megacap")


def test_all_pocket_wallets_megacap_gate_off_no_change():
    """Both gates OFF -- never an extra entry by accident."""
    assert pt.all_pocket_wallets() == ("swing", "vc")


def test_uses_fine_rsi_confirmation_true_for_swing_and_megacap():
    assert pt.uses_fine_rsi_confirmation("swing") is True
    assert pt.uses_fine_rsi_confirmation("megacap") is True


def test_uses_fine_rsi_confirmation_false_for_vc():
    assert pt.uses_fine_rsi_confirmation("vc") is False


def test_uses_fine_rsi_confirmation_false_for_scalping_pockets():
    for wallet in ("scalping", "scalping_v1", "scalping_v2", "scalping_v3", "scalping_v4", "scalping_v5", "scalping_v6", "scalping_v8"):
        assert pt.uses_fine_rsi_confirmation(wallet) is False


@pytest.mark.asyncio
async def test_all_reporting_wallets_matches_pocket_wallets_when_no_legacy_row(tmp_db):
    """No extra paper_state row beyond the active pockets -- same tuple as
    all_pocket_wallets(), no phantom pocket invented."""
    await pt._ensure_tables()
    assert await pt.all_reporting_wallets() == pt.all_pocket_wallets()


@pytest.mark.asyncio
async def test_all_reporting_wallets_never_crashes_on_a_genuinely_virgin_db(tmp_db):
    """08/02 -- real bug found live (public paper-wallet endpoint fix): unlike
    every other public function in this module, this one never called
    _ensure_tables() first -- invisible in every OTHER test in this file
    because they all call _ensure_tables()/reset_portfolio() beforehand, but
    a genuinely empty DB (no paper_trader function ever called yet, the
    real first-request-ever scenario) raised "no such table: paper_state"
    outright. Deliberately does NOT call _ensure_tables() itself first --
    that's the whole point of this test."""
    assert await pt.all_reporting_wallets() == pt.all_pocket_wallets()


@pytest.mark.asyncio
async def test_all_reporting_wallets_includes_retired_pocket_with_history(tmp_db, monkeypatch):
    """08/01 real bug (operator screenshot, "je le voit pas"): once
    scalping_variants_enabled() replaces "scalping" in all_pocket_wallets(),
    a legacy "scalping" paper_state row (real history, e.g. still-open
    positions) must not vanish from reporting/risk views."""
    import aiosqlite

    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    await pt._ensure_tables()
    async with aiosqlite.connect(pt.DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO paper_state (wallet, starting_capital, created_at) "
            "VALUES ('scalping', 1000000.0, '2026-07-30T00:00:00+00:00')",
        )
        await db.commit()

    wallets = await pt.all_reporting_wallets()
    active = pt.all_pocket_wallets()
    assert "scalping" in wallets
    assert set(active).issubset(set(wallets))
    # Active pockets stay first, in the same order as all_pocket_wallets();
    # the retired one is appended after, never reordering the active set.
    assert wallets[: len(active)] == active
    assert wallets[-1] == "scalping"


@pytest.mark.asyncio
async def test_scalping_variants_enabled_sources_v8_only(tmp_db, monkeypatch):
    """06/08 -- v1-v7 retired: gate ON sources scalping_v8 alone, sharing
    the SAME momentum discovery pass as the other pockets (never a
    duplicated network call)."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    # 08/05 -- sourcing pause (operator focus decision) is a SEPARATE concern,
    # neutralized here so this test keeps validating the FULL 8-pocket
    # architecture (dedicated tests below cover the pause behavior itself).
    monkeypatch.setattr(pt, "SOURCING_PAUSED_WALLETS", frozenset())
    from aria_core import momentum_entry
    from aria_core.skills import candidate_ranking, scalping_variants

    async def _fake_sources(*, limit=20):
        return [D], {D: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_top_candidates(limit):
        return []

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    async def _fake_buy(contract, chain):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "mode": "scalping",
        }

    for key in scalping_variants.VARIANT_ANALYZERS:
        monkeypatch.setitem(scalping_variants.VARIANT_ANALYZERS, key, _fake_buy)

    # scalping_v6's own analyzer is the legacy _default_momentum_analyzer,
    # which calls momentum_entry.evaluate_momentum_entry -- a REAL code path
    # in this test (unlike the 5 variants above, faked via VARIANT_ANALYZERS),
    # so it needs its own fake too. swing/vc go through this SAME function
    # (mode="standard") -- gated on mode here so this scalping-only test
    # doesn't accidentally also open a swing/vc position via the same mock.
    async def _fake_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        if mode != "scalping":
            return {"action": "HOLD", "chain": chain, "symbol": "DDD", "price": 1.0, "hold_reason": "test_stub"}
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3, "mode": mode,
        }

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(price_lookup=_price_lookup_one, depeg_check=_no_depeg)

    wallets_opened = {p["wallet"] for p in act["opened"]}
    assert wallets_opened == {"scalping_v8"}
    assert await pt.get_open_positions(wallet="scalping") == []  # legacy slot never sourced
    positions = await pt.get_open_positions(wallet="scalping_v8")
    assert len(positions) == 1 and positions[0]["mode"] == "scalping"


# ── build_scalping_pocket_entries (08/01) ───────────────────────────────────
# Real bug found live: scalping_v1 alone consumed 245-283s of the shared 300s
# momentum_discovery_cycle budget on every tick (production DB evidence,
# 6 bursts measured) -- v2..v5 never got a chance to evaluate even one
# candidate. Fix: all scalping-variant pockets now share the SAME small
# candidate slice (MAX_SCALPING_VARIANT_CANDIDATES_PER_CYCLE) instead of each
# getting the full up-to-50 list independently.


def test_build_scalping_pocket_entries_gate_off_sources_nothing():
    # 06/08 -- v1-v7 retired: the gate is now v8's kill-switch, OFF means
    # no scalping pocket sources at all (the legacy "scalping" fallback is
    # gone, its engine arm was removed with v6).
    assert pt.build_scalping_pocket_entries([f"0xC{i}" for i in range(30)], {}) == ()


def test_build_scalping_pocket_entries_gate_on_returns_v8_only(monkeypatch):
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    entries = pt.build_scalping_pocket_entries([f"0xC{i}" for i in range(30)], {})
    assert [e[0] for e in entries] == ["scalping_v8"]
    assert all(e[3] == "scalping" for e in entries)
    assert all(e[4] == pt.MAX_POSITIONS_SCALPING for e in entries)


def test_build_scalping_pocket_entries_gate_on_truncates_shared_candidate_slice(monkeypatch):
    """The real fix: more than MAX_SCALPING_VARIANT_CANDIDATES_PER_CYCLE
    candidates exist -- every one of the 6 pockets gets the SAME truncated
    slice (identical input, preserving the "compared side by side" design
    intent), never the full list independently."""
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    candidates = [f"0xC{i}" for i in range(30)]
    entries = pt.build_scalping_pocket_entries(candidates, {})
    expected = candidates[: pt.MAX_SCALPING_VARIANT_CANDIDATES_PER_CYCLE]
    for _wallet, pocket_candidates, _analyzer, _mode, _cap in entries:
        assert pocket_candidates == expected
        assert len(pocket_candidates) == pt.MAX_SCALPING_VARIANT_CANDIDATES_PER_CYCLE


def test_build_scalping_pocket_entries_gate_on_under_cap_never_padded(monkeypatch):
    """Fewer candidates than the cap -- no truncation artifact, every pocket
    just sees the (short) full list."""
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    candidates = ["0xC1", "0xC2"]
    entries = pt.build_scalping_pocket_entries(candidates, {})
    for _wallet, pocket_candidates, _analyzer, _mode, _cap in entries:
        assert pocket_candidates == candidates


@pytest.mark.asyncio
async def test_scalping_variants_disabled_no_scalping_pocket_sources(tmp_db, monkeypatch):
    """Gate OFF (v8 kill-switch since the 06/08 v1-v7 retirement) -- no
    scalping pocket sources at all; swing keeps sourcing normally."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.delenv("ARIA_SCALPING_VARIANTS_ENABLED", raising=False)
    from aria_core import momentum_entry
    from aria_core.skills import candidate_ranking

    async def _fake_sources(*, limit=20):
        return [D], {D: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3, "mode": mode,
        }

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)

    async def _fake_top_candidates(limit):
        return []

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(price_lookup=_price_lookup_one, depeg_check=_no_depeg)

    wallets_opened = {p["wallet"] for p in act["opened"]}
    assert not any(pt.is_scalping_pocket(w) for w in wallets_opened)
    assert await pt.get_open_positions(wallet="scalping_v8") == []


@pytest.mark.asyncio
async def test_scalping_variants_stagnation_timeout_applies_to_variant_pockets(tmp_db, monkeypatch):
    """The 08/01 stagnation timeout checks mode == "scalping", not the wallet
    name -- a variant pocket's position (wallet="scalping_v3", mode="scalping")
    must be just as eligible for force-close as the classic "scalping" pocket's."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    from aria_core.skills import candidate_ranking

    async def _fake_sources(*, limit=20):
        return [], {}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_top_candidates(limit):
        return []

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)

    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000, wallet="scalping_v3", mode="scalping",
    )
    await _backdate_opened_at(D, pt.SCALPING_STAGNATION_TIMEOUT_HOURS + 0.5)

    act = await pt.run_paper_cycle(price_lookup=_price_lookup_one, depeg_check=_no_depeg)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "timeout stagnation (scalping)"


@pytest.mark.asyncio
async def test_multi_pocket_gate_on_never_splits_an_explicit_caller(tmp_db, monkeypatch):
    """Gate ON, but the caller provides its OWN candidates/analyzer (e.g.
    momentum_websocket.py's real-time drain, or any test) -- multi-pocket
    sourcing must NEVER override an explicit caller's choice, same scoping
    precedent as the #194 momentum pivot itself. Always books into "swing"."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")

    async def _analyzer(contract):
        return {
            "action": "BUY", "chain": "base", "symbol": "AAA", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3,
        }

    async def _price_lookup(contract):
        return 1.0

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(
        candidates=[A], analyzer=_analyzer, price_lookup=_price_lookup, depeg_check=_no_depeg,
    )

    assert len(act["opened"]) == 1
    assert act["opened"][0]["wallet"] == "swing"
    assert await pt.get_open_positions(wallet="scalping") == []
    assert await pt.get_open_positions(wallet="vc") == []


@pytest.mark.asyncio
async def test_open_new_entries_suppresses_sibling_duplicate_notification(tmp_db):
    """04/08 real case, operator-reported live ("j'ai que des graphiques"):
    scalping_v6/v7 independently detect the identical golden-pocket/RSI-
    divergence setup (byte-identical target/invalidation -- they share
    momentum_entry's 120s candle cache) -- only the FIRST pocket to place
    its order should also notify; the second pocket's order is still
    created (each pocket's own trigger/exit logic must run independently)."""
    from aria_core import risk_guard, limit_orders

    risk_state = risk_guard.PortfolioRiskState(
        wallet="scalping_v6", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    async def fake_analyzer(contract):
        return {
            "action": "HOLD", "symbol": "MAG7", "price": 0.4218, "chain": "base",
            "reasons": ["golden pocket atteinte, divergence RSI pas encore confirmee"],
            "hold_reason": "no_entry_signal",
            "limit_order_candidate": {
                "target_price": 0.4218, "target": 0.4234, "invalidation": 0.4103,
                "rr": 0.14, "symbol": "MAG7", "limit_order_reason": "rsi_divergence_pending",
            },
        }

    async def price_lookup(contract):
        return 0.4218

    notified_v6 = []
    notified_v7 = []

    async def notifier_v6(msg):
        notified_v6.append(msg)

    async def notifier_v7(msg):
        notified_v7.append(msg)

    await pt.reset_portfolio(1_000_000.0)
    await pt._open_new_entries_for_wallet(
        "scalping_v6", [A], fake_analyzer,
        price_lookup=price_lookup, notifier=notifier_v6, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )
    await pt._open_new_entries_for_wallet(
        "scalping_v7", [A], fake_analyzer,
        price_lookup=price_lookup, notifier=notifier_v7, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )

    active = await limit_orders.get_active_orders()
    assert len(active) == 2  # both pockets still get their own order
    assert len(notified_v6) == 1  # first pocket notifies normally
    assert len(notified_v7) == 0  # second pocket's duplicate suppressed


@pytest.mark.asyncio
async def test_open_new_entries_suppresses_repeat_failure_notification(tmp_db):
    """04/08 real case (MAG7.ssi, 6 consecutive cancellations since 08/03):
    once a contract+reason has failed REPEAT_FAILURE_NOTIFY_SUPPRESS_THRESHOLD
    times in a row, a fresh watch order still gets CREATED (never gated --
    the operator explicitly removed any R/R-based creation gate on 31/07,
    Item #252) but no longer spams a new Telegram notification."""
    from aria_core import risk_guard, limit_orders

    risk_state = risk_guard.PortfolioRiskState(
        wallet="swing", equity=1_000_000.0, high_water_mark=1_000_000.0, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=1.0, blocked=False,
    )

    await pt.reset_portfolio(1_000_000.0)
    for _ in range(limit_orders.REPEAT_FAILURE_NOTIFY_SUPPRESS_THRESHOLD):
        order = await limit_orders.create_pending_order(
            A, "base", "MAG7", 0.42,
            {"limit_order_reason": "rsi_divergence_pending"}, wallet="swing",
        )
        await limit_orders.mark_cancelled(order["id"], "expired")

    async def fake_analyzer(contract):
        return {
            "action": "HOLD", "symbol": "MAG7", "price": 0.4218, "chain": "base",
            "reasons": ["golden pocket atteinte, divergence RSI pas encore confirmee"],
            "hold_reason": "no_entry_signal",
            "limit_order_candidate": {
                "target_price": 0.4218, "target": 0.4234, "invalidation": 0.4103,
                "rr": 0.14, "symbol": "MAG7", "limit_order_reason": "rsi_divergence_pending",
            },
        }

    async def price_lookup(contract):
        return 0.4218

    notified = []

    async def notifier(msg):
        notified.append(msg)

    await pt._open_new_entries_for_wallet(
        "swing", [A], fake_analyzer,
        price_lookup=price_lookup, notifier=notifier, max_new=99,
        using_default_price_lookup=False, closed_this_cycle=set(),
        weekly_context=None, risk_state=risk_state, discovery_channel=None,
        trading_mode="standard", max_positions_cap=None, funnel={},
    )

    active = await limit_orders.get_active_orders()
    assert len(active) == 1  # the new order IS created, never gated
    assert notified == []  # but the repeat-failure notification is suppressed


def test_scalping_stagnation_override_v8_shorter_timeout():
    """08/05 (scalping_v8) -- per-wallet stagnation seam: v8 gets 1.5h, every
    other wallet (and a missing wallet) keeps the generic constants
    byte-for-byte."""
    from aria_core import paper_trader as pt

    assert pt._scalping_stagnation_params_for_wallet("scalping_v8") == (
        1.5, pt.SCALPING_STAGNATION_MIN_MOVE_PCT,
    )
    generic = (pt.SCALPING_STAGNATION_TIMEOUT_HOURS, pt.SCALPING_STAGNATION_MIN_MOVE_PCT)
    assert pt._scalping_stagnation_params_for_wallet("scalping_v6") == generic
    assert pt._scalping_stagnation_params_for_wallet(None) == generic


def test_scalping_max_hold_hours_v8_only():
    """06/08 -- v8 has an absolute 2h hold cap, every other wallet (and a
    missing wallet) stays uncapped -- byte-for-byte unchanged."""
    from aria_core import paper_trader as pt

    assert pt._scalping_max_hold_hours_for_wallet("scalping_v8") == 2.0
    assert pt._scalping_max_hold_hours_for_wallet("scalping_v6") is None
    assert pt._scalping_max_hold_hours_for_wallet(None) is None


@pytest.mark.asyncio
async def test_scalping_v8_max_hold_duration_closes_a_drifting_position(tmp_db):
    """06/08 -- real gap observed live (LMTS 3h+, SOL 5h+, operator: "un
    scalping doit etre court sinon c'est du swing mal regle"): a v8 position
    with a small CONFIRMED peak above the stagnation threshold (exempt from
    that timeout) that then drifts back to entry without ever tripping the
    trail must still close past the absolute hold cap (2h), regardless of
    movement."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        D, "DDD", 1.0, invalidation_price=0.5, alloc_usd=90_000,
        wallet="scalping_v8", mode="scalping",
    )

    prices = {"v": 1.10}

    async def price_lookup(contract):
        return prices["v"]

    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    await _backdate_pending_since(D, pt.HIGH_WATER_CONFIRMATION_SECONDS + 5)
    await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)  # confirme le ratchet
    pos = await pt._get_open(D)
    assert pos["high_water_price"] > pos["entry_price"] * 1.05

    # Retombe a l'entree -- exempte du stagnation timeout (pic confirme
    # au-dessus du seuil) ET pas de drawdown suffisant pour le trail suiveur.
    prices["v"] = 1.0
    await _backdate_opened_at(D, 2.5)  # au-dela du plafond absolu v8 (2h)
    act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
    assert len(act["closed"]) == 1
    assert act["closed"][0]["close_reason"] == "duree max scalping"
    assert not await pt.has_open(D)


# ── sourcing pause (08/05; 06/08: v1-v7 retired, vc reactivated) ────────────

def test_sourcing_paused_default_state():
    """Only megacap stays paused since the 06/08 retirement -- the active
    trio (v8, swing, vc) never paused."""
    assert pt.sourcing_paused("megacap") is True
    for w in ("scalping_v8", "swing", "vc", None):
        assert pt.sourcing_paused(w) is not True


@pytest.mark.asyncio
async def test_paused_pockets_source_nothing_but_focus_pockets_do(tmp_db, monkeypatch):
    """End-to-end through run_paper_cycle with the DEFAULT pause state (no
    neutralization): only the focus arms open -- paused pockets are skipped
    before their analyzer even runs (that's the point: no network cost).
    Same full setup as test_scalping_variants_enabled_sources_v8_only
    above, minus the pause neutralization."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    from aria_core import momentum_entry
    from aria_core.skills import candidate_ranking, scalping_variants

    async def _fake_sources(*, limit=20):
        return [D], {D: "base"}

    monkeypatch.setattr(pt, "_momentum_candidates_and_chain_map", _fake_sources)

    async def _fake_top_candidates(limit):
        return []

    monkeypatch.setattr(candidate_ranking, "top_candidates", _fake_top_candidates)
    analyzer_called_for: list[str] = []

    def _tracking_fake_variant_analyzer(evaluate_fn, chain_by_contract):
        async def analyzer(contract):
            analyzer_called_for.append(getattr(evaluate_fn, "__name__", "?"))
            return {
                "action": "BUY", "chain": "base", "symbol": "DDD", "price": 1.0,
                "target": 2.0, "invalidation": 0.9, "mode": "scalping",
            }
        return analyzer

    monkeypatch.setattr(pt, "_scalping_variant_analyzer", _tracking_fake_variant_analyzer)

    async def _fake_eval(contract, chain, *, weekly_context=None, current_regime=None, relaxed=False, mode="standard", waive_holder_concentration=False, rsi_watch_span=None):
        if mode != "scalping":
            return {"action": "HOLD", "chain": chain, "symbol": "DDD", "price": 1.0, "hold_reason": "test_stub"}
        return {
            "action": "BUY", "chain": chain, "symbol": "DDD", "price": 1.0,
            "target": 2.0, "invalidation": 0.9, "rr": 3.0, "align_score": 3, "mode": mode,
        }

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)

    await pt.reset_portfolio(1_000_000.0)
    act = await pt.run_paper_cycle(price_lookup=_price_lookup_one, depeg_check=_no_depeg)

    wallets_opened = {p["wallet"] for p in act["opened"]}
    assert "scalping_v8" in wallets_opened
    assert "megacap" not in wallets_opened
    # v8 is the only variant analyzer left after the 06/08 retirement.
    assert analyzer_called_for == ["evaluate_v8_wick_reversal"]


@pytest.mark.asyncio
async def test_high_water_starts_at_spot_not_degraded_fill(tmp_db):
    """08/05 -- real bug caught by v8's first 4 live positions (all
    trail-stopped in minutes at ~-3.9%, "high +0.0% vs entry"): seeding
    high_water with the degraded fill (spot + simulated fee/impact) consumed
    the whole trail width at t=0. Market levels live on SPOT; the fill only
    prices what we paid."""
    pos = await pt.open_position(
        A, "AAA", 1.0, invalidation_price=0.9, alloc_usd=10_000,
        pool_liquidity_usd=100_000.0, wallet="scalping", mode="scalping",
    )
    # fill is degraded above spot (fee + impact on 10% of the pool)
    assert pos["entry_price"] > 1.0
    # ...but the trailing high-water mark starts at the SPOT entry
    assert pos["high_water_price"] == pytest.approx(1.0)


# ── pocket_state_text() -- 06/08, LLM context freshness ──────────────────────
# Operator finding, live (screenshot): a free-text question about v9 fell
# through to an unrelated skill match because nothing in aria_brain's context
# knew v9 existed. pocket_state_text() closes that gap -- these tests lock
# its two contracts: (1) always reflects the REAL gate state, never stale,
# (2) never leaks a retired pocket (same boundary as the Telegram bilan).

@pytest.mark.asyncio
async def test_pocket_state_text_lists_active_trio_by_default(monkeypatch, tmp_db):
    monkeypatch.delenv("ARIA_SCALPING_V9_ENABLED", raising=False)
    monkeypatch.delenv("ARIA_FIXED_WATCHLIST_POCKET_ENABLED", raising=False)
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    await pt.reset_portfolio(1_000_000.0, wallet="scalping_v8")
    await pt.reset_portfolio(1_000_000.0, wallet="swing")
    await pt.reset_portfolio(1_000_000.0, wallet="vc")
    text = await pt.pocket_state_text()
    assert "Scalping V8" in text
    assert "Swing" in text
    assert "VC" in text
    assert "scalping_v9" not in text.lower()


@pytest.mark.asyncio
async def test_pocket_state_text_never_shows_retired_pockets(monkeypatch, tmp_db):
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    # a retired pocket with real history in the DB (migrated rows, e.g.)
    await pt.reset_portfolio(1_000_000.0, wallet="scalping_v6")
    await pt.reset_portfolio(1_000_000.0, wallet="scalping_v8")
    text = await pt.pocket_state_text()
    assert "scalping_v6" not in text
    assert "Scalping V6" not in text


@pytest.mark.asyncio
async def test_pocket_state_text_reflects_v9_when_gate_on(monkeypatch, tmp_db):
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    await pt.reset_portfolio(1_000_000.0, wallet="scalping_v9")
    text = await pt.pocket_state_text()
    assert "Scalping V9" in text
    assert "scalping_v9" in text


@pytest.mark.asyncio
async def test_pocket_state_text_includes_v9_watchlist_entries(monkeypatch, tmp_db):
    """The watchlist table self-seeds SPX on first read (see scalping_v9.py's
    module docstring) -- no network call needed to exercise this block."""
    from aria_core import scalping_v9

    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    await pt.reset_portfolio(1_000_000.0, wallet="scalping_v9")
    seeded = await scalping_v9.get_watchlist()
    assert seeded  # sanity: the seed contract is really there
    text = await pt.pocket_state_text()
    assert seeded[0]["contract"].lower() in text.lower()
    assert "RSI(" in text and "MFI(" in text


def test_pocket_labels_has_entry_for_every_active_wallet_family():
    """Locks the exact class of bug Devil's Advocate report 786c7483 named
    ("v6/v7/v8 displayed as raw wallet ids since their creation") -- any
    wallet name all_pocket_wallets()/visible_reporting_wallets() can ever
    return needs a POCKET_LABELS entry, checked here independently of any
    gate's current on/off state."""
    for wallet in ("scalping_v8", "scalping_v9", "swing", "vc", "megacap"):
        assert wallet in pt.POCKET_LABELS
