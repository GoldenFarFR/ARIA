"""Portefeuille papier 1 M$ (mode TRADING) — le banc d'essai de la preuve.

ARIA applique ses VRAIS rapports à un portefeuille FICTIF de 1 000 000 $ : elle ouvre et
ferme des positions imaginaires au prix RÉEL du marché, émet des alertes d'achat et de
vente CLAIREMENT FICTIVES, et mesure sa performance dans le temps. Objectif : prouver la
performance sur ~20 jours AVANT tout argent réel (pacte docs/protocole-argent-reel.md).

Mode TRADING (pas VC) : horizon court, sortie sur cible/invalidation dérivées de l'analyse
réelle. AUCUNE exécution on-chain, AUCUNE signature, AUCUN argent réel — de la simulation
persistée en local (aria.db). Le prix de marché est réel ; les ordres sont fictifs.
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

_POS_FIELDS = (
    "id", "contract", "symbol", "cost_usd", "entry_price", "qty",
    "target_price", "invalidation_price", "opened_at", "status",
    "exit_price", "closed_at", "pnl_usd", "pnl_pct", "close_reason",
)


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
                close_reason TEXT
            )
            """
        )
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
    """Cash = capital de départ − coût des positions ouvertes + P&L réalisé des clôturées."""
    start = await starting_capital()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM paper_position WHERE status = 'open'"
        ) as cur:
            open_cost = (await cur.fetchone())[0] or 0.0
        async with db.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) FROM paper_position WHERE status = 'closed'"
        ) as cur:
            realized = (await cur.fetchone())[0] or 0.0
    return float(start) - float(open_cost) + float(realized)


async def open_position(
    contract: str,
    symbol: str,
    entry_price: float,
    *,
    target_price: float | None = None,
    invalidation_price: float | None = None,
    alloc_usd: float | None = None,
) -> dict | None:
    """Ouvre une position FICTIVE au prix d'entrée réel. Refuse si déjà ouverte, plafond de
    positions atteint, prix invalide ou cash insuffisant. Retourne la position ou None."""
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

    qty = alloc / entry_price
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO paper_position
              (contract, symbol, cost_usd, entry_price, qty, target_price,
               invalidation_price, opened_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (contract, symbol or "", alloc, entry_price, qty, target_price, invalidation_price, _now()),
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


async def portfolio_summary(*, price_lookup=None) -> dict:
    """Photo du portefeuille : cash, valeur totale (marquée au marché si price_lookup),
    rendement %, P&L réalisé/latent, taux de réussite. ``price_lookup(contract)`` async → prix."""
    start = await starting_capital()
    opens = await get_open_positions()
    closed = await get_closed_positions(limit=100_000)
    realized = sum((p["pnl_usd"] or 0.0) for p in closed)
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

    result, ctx = await analyze_vc_with_context(contract)
    action = "BUY" if getattr(result, "recommandation", "") == "BUY" else "HOLD"
    price = ctx.best_pair.price_usd if ctx.best_pair else None
    target = _num(getattr(result, "cible", None)) or (ctx.ta_entry.cible if ctx.ta_entry else None)
    inval = _num(getattr(result, "invalidation", None)) or (
        ctx.ta_entry.invalidation if ctx.ta_entry else None
    )
    return {
        "action": action,
        "symbol": ctx.best_pair.base_symbol if ctx.best_pair else "",
        "price": price,
        "target": target,
        "invalidation": inval,
    }


async def run_paper_cycle(
    *,
    candidates=None,
    analyzer=None,
    price_lookup=None,
    notifier=None,
    max_new: int = 3,
) -> dict:
    """Un tour de simulation, appliquant les VRAIS rapports :
      1. positions ouvertes : au prix réel, ferme celles dont la cible OU l'invalidation
         est atteinte (mode trading) et émet une alerte de vente fictive ;
      2. nouveaux achats : sur les candidats classés avec un signal d'ACHAT réel, ouvre une
         position fictive et émet une alerte d'achat fictive.
    Tout est injectable (candidates/analyzer/price_lookup/notifier) → testable hors-ligne.
    Aucune exécution réelle, jamais un ordre : de la simulation.
    """
    await _ensure_tables()
    price_lookup = price_lookup or _default_price_lookup
    actions: dict = {"opened": [], "closed": [], "checked": 0}

    # 1) Gérer les positions ouvertes (sortie sur cible / invalidation).
    for p in await get_open_positions():
        actions["checked"] += 1
        try:
            price = await price_lookup(p["contract"])
        except Exception:  # noqa: BLE001
            price = None
        if not price or price <= 0:
            continue
        reason = None
        if p.get("target_price") and price >= p["target_price"]:
            reason = "cible atteinte"
        elif p.get("invalidation_price") and price <= p["invalidation_price"]:
            reason = "invalidation"
        if reason:
            closed = await close_position(p["contract"], price, reason=reason)
            if closed:
                actions["closed"].append(closed)
                if notifier:
                    try:
                        await notifier(format_sell_alert(closed))
                    except Exception:  # noqa: BLE001 — l'alerte ne casse pas le cycle
                        pass

    # 2) Ouvrir de nouvelles positions depuis les candidats classés (signal d'achat réel).
    if candidates is None:
        from aria_core.skills.candidate_ranking import top_candidates

        candidates = [c.contract for c in await top_candidates(20)]
    analyzer = analyzer or _default_analyzer
    # On ne re-rentre pas un nom qu'on vient de SORTIR ce tour (évite le churn : une sortie
    # sur cible/invalidation exige un nouveau signal au tour suivant, pas un rachat immédiat).
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
