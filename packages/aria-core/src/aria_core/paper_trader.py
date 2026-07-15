"""Portefeuille papier 1 M$ (mode TRADING) — le banc d'essai de la preuve.

ARIA applique ses VRAIS rapports à un portefeuille FICTIF de 1 000 000 $ : elle ouvre et
ferme des positions imaginaires au prix RÉEL du marché, émet des alertes d'achat et de
vente CLAIREMENT FICTIVES, et mesure sa performance dans le temps. Objectif : prouver la
performance sur ~20 jours AVANT tout argent réel (pacte docs/protocole-argent-reel.md).

Mode TRADING (pas VC) : horizon court, niveaux dérivés de l'analyse réelle. Gestion de
position par STOP SUIVEUR (se resserre avec le plus haut atteint, ne se relâche jamais en
dessous de l'invalidation d'origine) + PRISE DE PROFIT ÉCHELONNÉE (vend par tiers à +50 %,
+100 %, +200 % de gain plutôt qu'un tout-ou-rien à la cible) — protège les gains acquis
sans couper le potentiel restant. AUCUNE exécution on-chain, AUCUNE signature, AUCUN
argent réel — de la simulation persistée en local (aria.db). Le prix de marché est réel ;
les ordres sont fictifs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

STARTING_CAPITAL_USD = 1_000_000.0
ALLOC_PCT = 0.05          # 5 % du capital de départ par position (~50 000 $) — mode trading
MAX_POSITIONS = 15        # coussin de cash + diversification
MODE = "trading"

# Gestion de position (stop suiveur + prise de profit échelonnée) — remplace la sortie
# binaire (100 % à la cible OU à l'invalidation) par une gestion qui protège les gains
# ACQUIS sans couper le potentiel restant.
TRAIL_STOP_PCT = 0.15         # stop suiveur : 15 % sous le plus haut atteint depuis l'entrée
TP_STAGES = (0.5, 1.0, 2.0)   # paliers de gain vs entrée (+50 %, +100 %, +200 %)
TP_STAGE_FRACTION = 1.0 / 3.0  # fraction de la quantité INITIALE vendue à chaque palier
TP_QTY_EPSILON = 1e-9         # reliquat négligeable après le dernier palier -> clôture complète

_POS_FIELDS = (
    "id", "contract", "symbol", "cost_usd", "entry_price", "qty",
    "target_price", "invalidation_price", "opened_at", "status",
    "exit_price", "closed_at", "pnl_usd", "pnl_pct", "close_reason",
    "high_water_price", "tp_stage_hit", "initial_qty", "realized_pnl_partial",
    "category", "entry_security_json",
)

_ADDED_COLUMNS = [
    ("high_water_price", "REAL"),
    ("tp_stage_hit", "INTEGER NOT NULL DEFAULT 0"),
    ("initial_qty", "REAL"),
    ("realized_pnl_partial", "REAL NOT NULL DEFAULT 0"),
    # #187 -- surveillance continue + plafond de concentration (voir paper_trader_risk.py)
    ("category", "TEXT NOT NULL DEFAULT ''"),
    ("entry_security_json", "TEXT"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(v) -> float | None:
    """Parse défensif d'un prix éventuellement '$1,234.5' → float, ou None."""
    try:
        if v is None:
            return None
        s = str(v).replace("$", "").replace(",", "").strip().split()[0]
        return float(s)
    except (ValueError, IndexError, TypeError):
        return None


def _row_to_pos(row: tuple) -> dict:
    return dict(zip(_POS_FIELDS, row))


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_position (
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
                close_reason TEXT,
                high_water_price REAL,
                tp_stage_hit INTEGER NOT NULL DEFAULT 0,
                initial_qty REAL,
                realized_pnl_partial REAL NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT '',
                entry_security_json TEXT
            )
            """
        )
        # Migration à chaud : ajoute les colonnes de gestion de position aux DB existantes
        # (SQLite ne les crée pas si la table préexiste). Idempotent, non destructif.
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(paper_position)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE paper_position ADD COLUMN {name} {ddl}")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                starting_capital REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO paper_state (id, starting_capital, created_at) VALUES (1, ?, ?)",
            (STARTING_CAPITAL_USD, _now()),
        )
        await db.commit()


async def starting_capital() -> float:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT starting_capital FROM paper_state WHERE id = 1") as cur:
            row = await cur.fetchone()
    return float(row[0]) if row else STARTING_CAPITAL_USD


async def reset_portfolio(starting: float = STARTING_CAPITAL_USD, *, created_at: str | None = None) -> None:
    """Repart à neuf (nouveau run de preuve). DESTRUCTIF : à déclencher explicitement par
    l'opérateur, jamais par une boucle automatique."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DROP TABLE IF EXISTS paper_position")
        await db.execute("DROP TABLE IF EXISTS paper_state")
        await db.commit()
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET starting_capital = ?, created_at = ? WHERE id = 1",
            (starting, created_at or _now()),
        )
        await db.commit()


async def get_open_positions() -> list[dict]:
    await _ensure_tables()
    cols = ", ".join(_POS_FIELDS)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {cols} FROM paper_position WHERE status = 'open' ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_pos(r) for r in rows]


async def get_closed_positions(limit: int = 500) -> list[dict]:
    await _ensure_tables()
    cols = ", ".join(_POS_FIELDS)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {cols} FROM paper_position WHERE status = 'closed' ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_pos(r) for r in rows]


async def list_positions_for_contract(contract: str, limit: int = 100) -> list[dict]:
    """Toutes les positions papier (ouvertes + clôturées) d'un contrat, récentes d'abord.

    Alimente le « dossier par token ». La clé contrat est stockée en minuscules
    (cf. open_position) — on normalise donc la recherche de la même façon.
    """
    await _ensure_tables()
    contract = (contract or "").lower()
    cols = ", ".join(_POS_FIELDS)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {cols} FROM paper_position WHERE contract = ? ORDER BY id DESC LIMIT ?",
            (contract, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_pos(r) for r in rows]


async def _get_open(contract: str) -> dict | None:
    contract = (contract or "").lower()
    cols = ", ".join(_POS_FIELDS)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {cols} FROM paper_position WHERE contract = ? AND status = 'open' LIMIT 1",
            (contract,),
        ) as cur:
            row = await cur.fetchone()
    return _row_to_pos(row) if row else None


async def has_open(contract: str) -> bool:
    return (await _get_open(contract)) is not None


async def cash_available() -> float:
    """Cash = capital de départ − coût des positions ouvertes + P&L réalisé des clôturées
    + P&L réalisé des prises de profit PARTIELLES sur des positions encore ouvertes (le
    coût restant de ``cost_usd`` est déjà réduit proportionnellement par ``reduce_position``,
    donc seul le profit au-delà de la base de coût doit être rajouté ici)."""
    start = await starting_capital()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COALESCE(SUM(realized_pnl_partial), 0) "
            "FROM paper_position WHERE status = 'open'"
        ) as cur:
            open_cost, open_partial = await cur.fetchone()
        async with db.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) FROM paper_position WHERE status = 'closed'"
        ) as cur:
            realized = (await cur.fetchone())[0] or 0.0
    return float(start) - float(open_cost or 0.0) + float(realized) + float(open_partial or 0.0)


async def open_position(
    contract: str,
    symbol: str,
    entry_price: float,
    *,
    target_price: float | None = None,
    invalidation_price: float | None = None,
    alloc_usd: float | None = None,
    category: str = "",
    entry_security_json: str = "",
) -> dict | None:
    """Ouvre une position FICTIVE au prix d'entrée réel. Refuse si déjà ouverte, plafond de
    positions atteint, prix invalide, cash insuffisant, ou plafond de concentration de
    ``category`` dépassé sans place suffisante (#187, voir paper_trader_risk.py -- l'alloc
    est RÉDUITE pour tenir sous le plafond quand la place restante est significative,
    sinon la position est skippée). Retourne la position ou None."""
    await _ensure_tables()
    contract = (contract or "").lower()
    if not contract or not entry_price or entry_price <= 0:
        return None
    if await has_open(contract):
        return None
    if len(await get_open_positions()) >= MAX_POSITIONS:
        return None

    start = await starting_capital()
    cash = await cash_available()
    alloc = alloc_usd if alloc_usd is not None else ALLOC_PCT * start
    alloc = min(alloc, cash)
    if alloc <= 0:
        return None

    if category:
        from aria_core import paper_trader_risk as risk

        opens = await get_open_positions()
        already = risk.category_exposure_usd(category, opens)
        alloc = risk.fit_alloc_to_concentration_cap(
            category=category,
            alloc=alloc,
            already_deployed_usd=already,
            starting_capital=start,
            min_alloc=ALLOC_PCT * start * risk.MIN_CONCENTRATION_ALLOC_FRACTION,
        )
        if alloc <= 0:
            return None

    qty = alloc / entry_price
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO paper_position
              (contract, symbol, cost_usd, entry_price, qty, target_price,
               invalidation_price, opened_at, status, high_water_price, initial_qty,
               category, entry_security_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
            """,
            (contract, symbol or "", alloc, entry_price, qty, target_price, invalidation_price,
             _now(), entry_price, qty, category or "", entry_security_json or None),
        )
        await db.commit()
        pid = cur.lastrowid
    return await _get_open(contract) or {"id": pid, "contract": contract}


async def close_position(contract: str, exit_price: float, *, reason: str = "manuel") -> dict | None:
    """Ferme une position FICTIVE au prix de sortie réel et enregistre le P&L."""
    await _ensure_tables()
    pos = await _get_open(contract)
    if not pos or not exit_price or exit_price <= 0:
        return None
    proceeds = pos["qty"] * exit_price
    pnl_usd = proceeds - pos["cost_usd"]
    pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0 if pos["entry_price"] else 0.0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE paper_position
               SET status = 'closed', exit_price = ?, closed_at = ?, pnl_usd = ?,
                   pnl_pct = ?, close_reason = ?
             WHERE id = ?
            """,
            (exit_price, _now(), pnl_usd, pnl_pct, reason, pos["id"]),
        )
        await db.commit()
    return {**pos, "status": "closed", "exit_price": exit_price, "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct, "close_reason": reason}


async def reduce_position(
    contract: str, exit_price: float, sell_qty: float, *, stage: int, reason: str = "prise de profit",
) -> dict | None:
    """Prise de profit PARTIELLE : vend une fraction de la position et garde le reste
    ouvert avec une base de coût réduite proportionnellement (même ``entry_price``, moins
    de ``qty``/``cost_usd``). Le P&L de la tranche vendue est accumulé dans
    ``realized_pnl_partial`` -- il reste visible dans ``cash_available``/``portfolio_summary``
    sans attendre la clôture complète de la position."""
    await _ensure_tables()
    pos = await _get_open(contract)
    if not pos or not exit_price or exit_price <= 0 or sell_qty <= 0:
        return None
    sell_qty = min(sell_qty, pos["qty"])
    frac = sell_qty / pos["qty"] if pos["qty"] else 0.0
    sold_cost = pos["cost_usd"] * frac
    proceeds = sell_qty * exit_price
    pnl_usd = proceeds - sold_cost
    new_qty = pos["qty"] - sell_qty
    new_cost = pos["cost_usd"] - sold_cost
    new_realized_partial = (pos.get("realized_pnl_partial") or 0.0) + pnl_usd
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE paper_position
               SET qty = ?, cost_usd = ?, realized_pnl_partial = ?, tp_stage_hit = ?
             WHERE id = ?
            """,
            (new_qty, new_cost, new_realized_partial, stage, pos["id"]),
        )
        await db.commit()
    pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0 if pos["entry_price"] else 0.0
    return {
        **pos, "sold_qty": sell_qty, "exit_price": exit_price, "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct, "close_reason": reason, "remaining_qty": new_qty,
        "tp_stage_hit": stage,
    }


async def _update_high_water(position_id: int, price: float) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_position SET high_water_price = ? WHERE id = ?", (price, position_id),
        )
        await db.commit()


async def portfolio_summary(*, price_lookup=None) -> dict:
    """Photo du portefeuille : cash, valeur totale (marquée au marché si price_lookup),
    rendement %, P&L réalisé/latent, taux de réussite. ``price_lookup(contract)`` async → prix."""
    start = await starting_capital()
    opens = await get_open_positions()
    closed = await get_closed_positions(limit=100_000)
    realized = (
        sum((p["pnl_usd"] or 0.0) for p in closed)
        + sum((p.get("realized_pnl_partial") or 0.0) for p in opens)
    )
    cash = start - sum(p["cost_usd"] for p in opens) + realized

    open_value = 0.0
    unrealized = 0.0
    for p in opens:
        price = None
        if price_lookup is not None:
            try:
                price = await price_lookup(p["contract"])
            except Exception:  # noqa: BLE001 — un prix indispo n'arrête pas la photo
                price = None
        value = p["qty"] * price if (price and price > 0) else p["cost_usd"]
        open_value += value
        unrealized += value - p["cost_usd"]

    equity = cash + open_value
    ret_pct = (equity / start - 1.0) * 100.0 if start else 0.0
    wins = [p for p in closed if (p["pnl_usd"] or 0.0) > 0]
    win_rate = (len(wins) / len(closed) * 100.0) if closed else None
    return {
        "starting": start,
        "cash": cash,
        "equity": equity,
        "return_pct": ret_pct,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "open_positions": len(opens),
        "closed_trades": len(closed),
        "win_rate": win_rate,
    }


# ── Alertes FICTIVES (opérateur) — toujours estampillées SIMULATION ──────────────────

def format_buy_alert(pos: dict) -> str:
    name = pos.get("symbol") or (pos.get("contract") or "")[:10]
    lines = [
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"ACHAT FICTIF {name}",
        f"Entrée {pos['entry_price']:.6g} · taille {pos['cost_usd']:,.0f} $",
    ]
    if pos.get("target_price"):
        lines.append(f"Cible {pos['target_price']:.6g}")
    if pos.get("invalidation_price"):
        lines.append(f"Invalidation {pos['invalidation_price']:.6g}")
    lines.append("Aucun argent réel — preuve de performance en cours.")
    return "\n".join(lines)


def format_sell_alert(closed: dict) -> str:
    name = closed.get("symbol") or (closed.get("contract") or "")[:10]
    pnl = closed.get("pnl_usd") or 0.0
    pct = closed.get("pnl_pct") or 0.0
    sign = "+" if pnl >= 0 else ""
    return "\n".join([
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"VENTE FICTIVE {name} ({closed.get('close_reason', '')})",
        f"Sortie {closed['exit_price']:.6g} · P&L {sign}{pnl:,.0f} $ ({sign}{pct:.1f}%)",
        "Aucun argent réel.",
    ])


def format_partial_exit_alert(partial: dict) -> str:
    name = partial.get("symbol") or (partial.get("contract") or "")[:10]
    pnl = partial.get("pnl_usd") or 0.0
    pct = partial.get("pnl_pct") or 0.0
    sign = "+" if pnl >= 0 else ""
    return "\n".join([
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"PRISE DE PROFIT PARTIELLE FICTIVE {name} ({partial.get('close_reason', '')})",
        f"Sortie {partial['exit_price']:.6g} · {sign}{pnl:,.0f} $ ({sign}{pct:.1f}%) sur la tranche vendue",
        f"Position restante : {partial.get('remaining_qty', 0):.6g} unités",
        "Aucun argent réel.",
    ])


def format_summary(summary: dict) -> str:
    wr = summary.get("win_rate")
    wr_str = f"{wr:.0f}%" if wr is not None else "n/a"
    return "\n".join([
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"Valeur totale : {summary['equity']:,.0f} $ ({summary['return_pct']:+.2f}%)",
        f"Cash {summary['cash']:,.0f} $ · {summary['open_positions']} positions ouvertes",
        f"Réalisé {summary['realized_pnl']:+,.0f} $ · latent {summary['unrealized_pnl']:+,.0f} $",
        f"Trades clôturés {summary['closed_trades']} · réussite {wr_str}",
        "Aucun argent réel — track record de preuve.",
    ])


# ── Défauts prod (réseau/LLM), injectables en test ───────────────────────────────────

async def _default_price_lookup(contract: str) -> float | None:
    from aria_core.skills.acp_onchain_scan import scan_base_token

    ctx = await scan_base_token(contract)
    return ctx.best_pair.price_usd if ctx.best_pair else None


async def _default_analyzer(contract: str) -> dict | None:
    """Signal d'un contrat à partir de la VRAIE analyse VC. Retourne action + niveaux."""
    from aria_core.skills.vc_analysis import analyze_vc_with_context
    from aria_core import paper_trader_risk as risk

    result, ctx = await analyze_vc_with_context(contract)
    action = "BUY" if getattr(result, "recommandation", "") == "BUY" else "HOLD"
    price = ctx.best_pair.price_usd if ctx.best_pair else None
    target = _num(getattr(result, "cible", None)) or (ctx.ta_entry.cible if ctx.ta_entry else None)
    inval = _num(getattr(result, "invalidation", None)) or (
        ctx.ta_entry.invalidation if ctx.ta_entry else None
    )
    category = risk.derive_category(ctx.launchpad, bonding_phase=ctx.bonding_phase)
    entry_snapshot = await risk.capture_entry_snapshot(contract, ctx)
    return {
        "action": action,
        "symbol": ctx.best_pair.base_symbol if ctx.best_pair else "",
        "price": price,
        "target": target,
        "invalidation": inval,
        "category": category,
        "entry_security_json": entry_snapshot.to_json(),
    }


async def run_paper_cycle(
    *,
    candidates=None,
    analyzer=None,
    price_lookup=None,
    notifier=None,
    max_new: int = 3,
    depeg_check=None,
) -> dict:
    """Un tour de simulation, appliquant les VRAIS rapports :
      1. positions ouvertes : surveillance de sécurité continue (#187) puis gestion par
         stop suiveur + prise de profit échelonnée (voir ``TRAIL_STOP_PCT``/``TP_STAGES``)
         — protège les gains acquis sans couper le potentiel restant, au lieu d'une sortie
         binaire 100 % cible OU 100 % invalidation ;
      2. nouveaux achats : sur les candidats classés avec un signal d'ACHAT réel (bloqué si
         USDC est dépeg, #187), ouvre une position fictive et émet une alerte d'achat fictive.
    Tout est injectable (candidates/analyzer/price_lookup/notifier/depeg_check) → testable
    hors-ligne, sans appel réseau caché.
    Aucune exécution réelle, jamais un ordre : de la simulation.
    """
    await _ensure_tables()
    price_lookup = price_lookup or _default_price_lookup
    actions: dict = {"opened": [], "closed": [], "partial": [], "checked": 0}

    # 1) Gérer les positions ouvertes : d'abord une surveillance continue de SÉCURITÉ
    #    (#187 -- honeypot/ownership apparus après l'entrée, jamais vérifiés qu'une seule
    #    fois avant), qui prime sur toute gestion par prix ; puis stop suiveur (ne se
    #    relâche jamais) et prise de profit échelonnée sur ce qui reste ouvert.
    from aria_core import paper_trader_risk as risk

    for p in await get_open_positions():
        actions["checked"] += 1
        try:
            price = await price_lookup(p["contract"])
        except Exception:  # noqa: BLE001
            price = None

        try:
            security_flag = await risk.rescan_open_position(p)
        except Exception as exc:  # noqa: BLE001 — la surveillance ne doit jamais casser le cycle
            logger.info("paper_cycle: re-scan sécurité %s échoué (%s)", p["contract"], exc)
            security_flag = None
        if security_flag:
            # Position paper -> fermeture automatique sans risque, ça teste la RÉACTION.
            # Avec du capital RÉEL ceci deviendrait une ALERTE seule (doctrine
            # wallet_guard -- jamais de vente automatique sans confirmation opérateur),
            # voir paper_trader_risk.py.
            exit_price = price if (price and price > 0) else p["entry_price"]
            closed = await close_position(p["contract"], exit_price, reason="sécurité re-scan")
            if closed:
                actions["closed"].append(closed)
                actions.setdefault("security_alerts", []).append(security_flag)
                if notifier:
                    try:
                        alert = format_sell_alert(closed) + "\n⚠️ " + "; ".join(security_flag["reasons"])
                        await notifier(alert)
                    except Exception:  # noqa: BLE001
                        pass
            continue

        if not price or price <= 0:
            continue

        high_water = max(p.get("high_water_price") or p["entry_price"], price)
        if high_water != (p.get("high_water_price") or p["entry_price"]):
            await _update_high_water(p["id"], high_water)
        trailing_stop = high_water * (1 - TRAIL_STOP_PCT)
        invalidation = p.get("invalidation_price")
        active_stop = max(trailing_stop, invalidation) if invalidation else trailing_stop
        stop_is_trailing = not invalidation or trailing_stop > invalidation

        if active_stop and price <= active_stop:
            closed = await close_position(
                p["contract"], price, reason="stop suiveur" if stop_is_trailing else "invalidation",
            )
            if closed:
                actions["closed"].append(closed)
                if notifier:
                    try:
                        await notifier(format_sell_alert(closed))
                    except Exception:  # noqa: BLE001 — l'alerte ne casse pas le cycle
                        pass
            continue  # position fermée, rien d'autre à évaluer ce tour

        # Prise de profit échelonnée : vend une fraction de la quantité INITIALE à chaque
        # palier de gain franchi. Dernier palier (ou reliquat négligeable) -> clôture complète.
        initial_qty = p.get("initial_qty") or p["qty"]
        stage_hit = int(p.get("tp_stage_hit") or 0)
        remaining_qty = p["qty"]
        entry_price = p["entry_price"]
        gain_pct = (price / entry_price - 1.0) if entry_price else 0.0

        while stage_hit < len(TP_STAGES) and gain_pct >= TP_STAGES[stage_hit]:
            stage_hit += 1
            sell_qty = min(initial_qty * TP_STAGE_FRACTION, remaining_qty)
            is_last_stage = stage_hit >= len(TP_STAGES) or remaining_qty - sell_qty <= TP_QTY_EPSILON
            if is_last_stage:
                closed = await close_position(
                    p["contract"], price, reason=f"palier {stage_hit}/{len(TP_STAGES)} (clôture)",
                )
                if closed:
                    actions["closed"].append(closed)
                    if notifier:
                        try:
                            await notifier(format_sell_alert(closed))
                        except Exception:  # noqa: BLE001
                            pass
                break

            partial = await reduce_position(
                p["contract"], price, sell_qty, stage=stage_hit,
                reason=f"palier {stage_hit}/{len(TP_STAGES)}",
            )
            if partial:
                actions["partial"].append(partial)
                remaining_qty = partial["remaining_qty"]
                if notifier:
                    try:
                        await notifier(format_partial_exit_alert(partial))
                    except Exception:  # noqa: BLE001
                        pass

    # 2) Ouvrir de nouvelles positions depuis les candidats classés (signal d'achat réel) --
    #    sauf si USDC est dépeg (#187) : le pricing de tout ce portefeuille suppose un USD
    #    stable, on bloque les NOUVELLES entrées (les positions déjà ouvertes ne sont pas
    #    touchées) tant que le dépeg n'est pas résorbé.
    if candidates is None:
        from aria_core.skills.candidate_ranking import top_candidates

        candidates = [c.contract for c in await top_candidates(20)]

    # Rien à acheter -> pas la peine de vérifier le dépeg (évite un appel réseau inutile
    # à chaque cycle, y compris quand aucun candidat n'est proposé ce tour).
    depeg_pct = None
    depegged = False
    if candidates:
        depeg_check = depeg_check or risk.usdc_depeg_pct
        try:
            depeg_pct = await depeg_check()
        except Exception as exc:  # noqa: BLE001
            logger.info("paper_cycle: vérif dépeg USDC échouée (%s)", exc)
            depeg_pct = None
        depegged = depeg_pct is not None and depeg_pct > risk.USDC_DEPEG_THRESHOLD_PCT
    actions["usdc_depeg_pct"] = depeg_pct
    actions["depeg_blocked"] = depegged

    if depegged:
        logger.warning(
            "paper_cycle: USDC dépeg %.2f%% (> seuil %.2f%%) -- nouvelles entrées bloquées ce cycle",
            (depeg_pct or 0.0) * 100, risk.USDC_DEPEG_THRESHOLD_PCT * 100,
        )
        return actions

    analyzer = analyzer or _default_analyzer
    # On ne re-rentre pas un nom qu'on vient de SORTIR ce tour (évite le churn : une sortie
    # sur stop suiveur/dernier palier exige un nouveau signal au tour suivant, pas un rachat
    # immédiat).
    closed_this_cycle = {c["contract"] for c in actions["closed"]}

    opened = 0
    for contract in candidates:
        if opened >= max_new:
            break
        if len(await get_open_positions()) >= MAX_POSITIONS:
            break
        if contract in closed_this_cycle:
            continue
        if await has_open(contract):
            continue
        try:
            sig = await analyzer(contract)
        except Exception as exc:  # noqa: BLE001 — une analyse qui plante n'arrête pas le cycle
            logger.info("paper_cycle: analyse %s échouée (%s)", contract, exc)
            continue
        if not sig or sig.get("action") != "BUY":
            continue
        price = sig.get("price")
        if not price:
            try:
                price = await price_lookup(contract)
            except Exception:  # noqa: BLE001
                price = None
        if not price or price <= 0:
            continue
        pos = await open_position(
            contract,
            sig.get("symbol", ""),
            price,
            target_price=sig.get("target"),
            invalidation_price=sig.get("invalidation"),
            category=sig.get("category", ""),
            entry_security_json=sig.get("entry_security_json", ""),
        )
        if pos:
            opened += 1
            actions["opened"].append(pos)
            if notifier:
                try:
                    await notifier(format_buy_alert(pos))
                except Exception:  # noqa: BLE001
                    pass

    return actions
