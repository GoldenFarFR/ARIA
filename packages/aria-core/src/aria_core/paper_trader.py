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

import asyncio
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

# #196 -- verrou PARTAGÉ, quel que soit l'appelant (heartbeat paper_trade_cycle OU le
# service websocket momentum #196) : sans lui, deux exécutions concurrentes de
# run_paper_cycle() liraient le capital disponible/le nombre de positions ouvertes AVANT
# que l'une des deux n'écrive -- risque réel de double-allocation ou de dépassement de
# MAX_POSITIONS. Un seul cycle à la fois, jamais deux en parallèle.
_run_cycle_lock = asyncio.Lock()

# Gestion de position (stop suiveur + prise de profit échelonnée) — remplace la sortie
# binaire (100 % à la cible OU à l'invalidation) par une gestion qui protège les gains
# ACQUIS sans couper le potentiel restant.
TRAIL_STOP_PCT = 0.15         # stop suiveur : 15 % sous le plus haut atteint depuis l'entrée
TP_STAGES = (0.5, 1.0, 2.0)   # paliers de gain vs entrée (+50 %, +100 %, +200 %)
TP_STAGE_FRACTION = 1.0 / 3.0  # fraction de la quantité INITIALE vendue à chaque palier
TP_QTY_EPSILON = 1e-9         # reliquat négligeable après le dernier palier -> clôture complète

# 17/07 -- demande opérateur explicite : réduire de moitié le bruit Telegram de l'alerte de
# suivi périodique (#197, une par cycle heartbeat -- ~15 min -- tant qu'une position reste
# ouverte). Fenêtre glissante par le TEMPS écoulé (pas un compteur de cycles) : robuste si la
# cadence heartbeat change un jour sans qu'il faille retoucher cette constante.
TRACKING_ALERT_MIN_INTERVAL_MINUTES = 30

_POS_FIELDS = (
    "id", "contract", "symbol", "cost_usd", "entry_price", "qty",
    "target_price", "invalidation_price", "opened_at", "status",
    "exit_price", "closed_at", "pnl_usd", "pnl_pct", "close_reason",
    "high_water_price", "tp_stage_hit", "initial_qty", "realized_pnl_partial",
    "category", "entry_security_json", "chain", "thesis", "close_notes",
)

_ADDED_COLUMNS = [
    ("high_water_price", "REAL"),
    ("tp_stage_hit", "INTEGER NOT NULL DEFAULT 0"),
    ("initial_qty", "REAL"),
    ("realized_pnl_partial", "REAL NOT NULL DEFAULT 0"),
    # #187 -- surveillance continue + plafond de concentration (voir paper_trader_risk.py)
    ("category", "TEXT NOT NULL DEFAULT ''"),
    ("entry_security_json", "TEXT"),
    # #194 -- pivot momentum multi-chaînes, chaque position se souvient de sa chaîne
    # (Base historiquement implicite -- défaut 'base' pour les positions déjà ouvertes)
    ("chain", "TEXT NOT NULL DEFAULT 'base'"),
    # #197 (15/07) -- VCResult.these (analyse VC complète, déjà calculée par
    # analyze_vc_with_context) persistée à l'ouverture -- avant ce chantier, jamais
    # transmise ni sauvegardée : seuls les niveaux chiffrés (prix/cible/invalidation)
    # survivaient. Objectif opérateur explicite : la session cloud doit pouvoir vérifier
    # après coup, en base, POURQUOI ARIA est entrée -- pas seulement à quel prix.
    ("thesis", "TEXT"),
    # 17/07 -- demande opérateur explicite : chaque VENTE (pas seulement l'achat) doit se
    # justifier avec des chiffres concrets, pour maximiser la donnée exploitable à des fins
    # de calibration -- pas juste un tag court ("stop suiveur"/"invalidation") déjà utilisé
    # par du code/des tests existants (jamais touché ici), un texte séparé qui explique le
    # POURQUOI avec les niveaux réels. Alimenté à chaque clôture totale ET à chaque prise de
    # profit partielle (dans ce dernier cas, sur la ligne encore ouverte -- dernière note en
    # date, pas un historique cumulé).
    ("close_notes", "TEXT"),
]

# Migration à chaud de `paper_state` (#186, 15/07) -- même patron idempotent que
# `_ADDED_COLUMNS` ci-dessus. Plus haut d'équité jamais atteint, utilisé par
# risk_guard.py pour le coupe-circuit de drawdown (jamais NULL après le premier
# appel de `get_equity_high_water_mark` -- initialisé au capital de départ).
_STATE_ADDED_COLUMNS = [
    ("equity_high_water_mark", "REAL"),
    # 17/07 -- horodatage de la dernière alerte de suivi périodique envoyée (voir
    # TRACKING_ALERT_MIN_INTERVAL_MINUTES) -- NULL tant qu'aucune n'a encore été envoyée.
    ("last_tracking_alert_at", "TEXT"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_since(opened_at: str | None) -> float | None:
    """Durée de détention en heures depuis ``opened_at`` (ISO), pour les notes de sortie
    (17/07) -- ``None`` si absent/invalide, jamais une valeur inventée."""
    if not opened_at:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(opened_at)).total_seconds() / 3600.0
    except ValueError:
        return None


def _duration_phrase(opened_at: str | None) -> str:
    hours = _hours_since(opened_at)
    if hours is None:
        return "durée de détention inconnue"
    return f"détenue {hours:.1f}h" if hours < 24 else f"détenue {hours / 24:.1f}j"


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
                entry_security_json TEXT,
                chain TEXT NOT NULL DEFAULT 'base',
                thesis TEXT,
                close_notes TEXT
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
        state_existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(paper_state)")).fetchall()
        }
        for name, ddl in _STATE_ADDED_COLUMNS:
            if name not in state_existing:
                await db.execute(f"ALTER TABLE paper_state ADD COLUMN {name} {ddl}")
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
            "UPDATE paper_state SET starting_capital = ?, created_at = ?, equity_high_water_mark = ? WHERE id = 1",
            (starting, created_at or _now(), starting),
        )
        await db.commit()


async def get_equity_high_water_mark() -> float:
    """Plus haut d'équité jamais atteint (#186, coupe-circuit de drawdown). Initialisé
    au capital de départ tant qu'aucune équité supérieure n'a encore été observée --
    jamais NULL après cet appel (les DB migrées ont la colonne mais pas la valeur)."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT equity_high_water_mark FROM paper_state WHERE id = 1") as cur:
            row = await cur.fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return await starting_capital()


async def set_equity_high_water_mark(value: float) -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET equity_high_water_mark = ? WHERE id = 1", (value,),
        )
        await db.commit()


async def get_last_tracking_alert_at() -> str | None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_tracking_alert_at FROM paper_state WHERE id = 1") as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def set_last_tracking_alert_at(value: str) -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET last_tracking_alert_at = ? WHERE id = 1", (value,),
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
            # `id DESC` en tie-break (#186) : `closed_at` (résolution microseconde) peut
            # coïncider entre deux clôtures rapprochées dans un même tick/test -- l'ordre
            # d'insertion reste le signal fiable de récence dans ce cas, notamment pour le
            # comptage de pertes consécutives de risk_guard.evaluate_portfolio_risk.
            f"SELECT {cols} FROM paper_position WHERE status = 'closed' ORDER BY closed_at DESC, id DESC LIMIT ?",
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
    chain: str = "base",
    thesis: str | None = None,
) -> dict | None:
    """Ouvre une position FICTIVE au prix d'entrée réel. Refuse si déjà ouverte, plafond de
    positions atteint, coupe-circuit de risque armé, prix invalide, cash insuffisant, ou
    plafond de concentration de ``category`` dépassé sans place suffisante (#187, voir
    paper_trader_risk.py -- l'alloc est RÉDUITE pour tenir sous le plafond quand la place
    restante est significative, sinon la position est skippée). ``chain`` (#194, pivot
    momentum multi-chaînes) persiste la chaîne d'origine pour que la gestion ultérieure de
    la position (prix, re-scan) sache quelle chaîne interroger. ``thesis`` (#197, 15/07) :
    raisonnement VC complet (``VCResult.these``) persisté tel quel -- pourquoi ARIA entre,
    pas seulement à quel prix. La persistance prime sur l'affichage Telegram : sauvegardée
    ICI, indépendamment de tout notifier/topic configuré ou non. Retourne la position ou
    None."""
    await _ensure_tables()
    contract = (contract or "").lower()
    if not contract or not entry_price or entry_price <= 0:
        return None
    if await has_open(contract):
        return None
    if len(await get_open_positions()) >= MAX_POSITIONS:
        return None

    # #186 -- chokepoint de sécurité en profondeur : vérifié ICI (pas seulement dans
    # run_paper_cycle) pour couvrir TOUT appelant présent ou futur (ex. commande manuelle,
    # futur pilote de capital réel réutilisant cette même fonction), pas seulement le cycle
    # heartbeat actuel.
    from aria_core import risk_guard

    blocked, reason = risk_guard.blocks_new_entries()
    if blocked:
        logger.info("open_position: refusé par risk_guard (%s)", reason)
        return None

    start = await starting_capital()
    cash = await cash_available()
    alloc = alloc_usd if alloc_usd is not None else ALLOC_PCT * start
    # #186 -- plafond de risque : ne réduit jamais alloc au-delà de sa valeur d'entrée,
    # jamais un bonus. Sans invalidation_price connue, inchangé (stop suiveur seul garde-fou).
    alloc = risk_guard.size_position_by_risk(alloc, entry_price, invalidation_price, start)
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
               category, entry_security_json, chain, thesis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
            """,
            (contract, symbol or "", alloc, entry_price, qty, target_price, invalidation_price,
             _now(), entry_price, qty, category or "", entry_security_json or None,
             (chain or "base").lower(), thesis),
        )
        await db.commit()
        pid = cur.lastrowid
    return await _get_open(contract) or {"id": pid, "contract": contract}


async def close_position(
    contract: str, exit_price: float, *, reason: str = "manuel", notes: str | None = None,
) -> dict | None:
    """Ferme une position FICTIVE au prix de sortie réel et enregistre le P&L. ``reason``
    reste un tag court stable (comparé par égalité ailleurs/dans les tests) ; ``notes``
    (17/07) porte la justification chiffrée complète -- séparés pour ne jamais casser un
    appelant qui dépend du tag exact."""
    await _ensure_tables()
    pos = await _get_open(contract)
    if not pos or not exit_price or exit_price <= 0:
        return None
    proceeds = pos["qty"] * exit_price
    pnl_usd = proceeds - pos["cost_usd"]
    pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0 if pos["entry_price"] else 0.0
    closed_at = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE paper_position
               SET status = 'closed', exit_price = ?, closed_at = ?, pnl_usd = ?,
                   pnl_pct = ?, close_reason = ?, close_notes = ?
             WHERE id = ?
            """,
            (exit_price, closed_at, pnl_usd, pnl_pct, reason, notes, pos["id"]),
        )
        await db.commit()
    return {**pos, "status": "closed", "exit_price": exit_price, "closed_at": closed_at,
            "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "close_reason": reason, "close_notes": notes}


async def reduce_position(
    contract: str, exit_price: float, sell_qty: float, *, stage: int,
    reason: str = "prise de profit", notes: str | None = None,
) -> dict | None:
    """Prise de profit PARTIELLE : vend une fraction de la position et garde le reste
    ouvert avec une base de coût réduite proportionnellement (même ``entry_price``, moins
    de ``qty``/``cost_usd``). Le P&L de la tranche vendue est accumulé dans
    ``realized_pnl_partial`` -- il reste visible dans ``cash_available``/``portfolio_summary``
    sans attendre la clôture complète de la position. ``notes`` (17/07) : justification
    chiffrée de CETTE prise partielle, persistée sur la ligne encore ouverte (remplace la
    précédente -- dernière note en date, pas un historique cumulé)."""
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
               SET qty = ?, cost_usd = ?, realized_pnl_partial = ?, tp_stage_hit = ?, close_notes = ?
             WHERE id = ?
            """,
            (new_qty, new_cost, new_realized_partial, stage, notes, pos["id"]),
        )
        await db.commit()
    pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0 if pos["entry_price"] else 0.0
    return {
        **pos, "sold_qty": sell_qty, "exit_price": exit_price, "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct, "close_reason": reason, "close_notes": notes, "remaining_qty": new_qty,
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
        f"Contrat {pos.get('contract', '')}",
        f"Entrée {pos['entry_price']:.6g} · taille {pos['cost_usd']:,.0f} $",
    ]
    if pos.get("target_price"):
        lines.append(f"Cible {pos['target_price']:.6g}")
    if pos.get("invalidation_price"):
        lines.append(f"Invalidation {pos['invalidation_price']:.6g}")
    # #197 (15/07) -- la thèse VC (pourquoi ARIA entre, pas seulement à quel prix) était
    # calculée mais jamais montrée. Affichée ici tronquée (lisibilité Telegram mobile) --
    # le texte COMPLET, lui, est toujours persisté tel quel en base (thesis, cf.
    # open_position), jamais tronqué là où ça compte pour la vérification après coup.
    thesis = (pos.get("thesis") or "").strip()
    if thesis:
        lines.append(f"Thèse : {thesis[:500]}")
    lines.append("Aucun argent réel — preuve de performance en cours.")
    return "\n".join(lines)


def format_position_tracking_alert(
    tracked: list[dict], *, cash: float | None = None, equity: float | None = None,
) -> str:
    """Suivi PÉRIODIQUE des positions déjà ouvertes (#197, 15/07) -- pas seulement à
    l'achat/la vente. ``tracked`` : liste de dicts {contract, symbol, entry_price, price,
    qty, cost_usd}, une entrée par position ENCORE ouverte à la fin du cycle (les
    positions fermées CE tour sont déjà couvertes par format_sell_alert, pas dupliquées
    ici). Liste vide -> chaîne vide (rien à envoyer, l'appelant ne notifie pas).

    ``cash``/``equity`` (17/07) : trouvé en conditions réelles -- l'en-tête affichait
    "portefeuille papier 1 M$" en dur sur CHAQUE alerte, quelle que soit la valeur RÉELLE
    du moment (déjà 998 415 $ après la première perte) -- l'opérateur ne pouvait pas savoir
    combien il restait sans aller consulter /feedback ou /ledger à part. Optionnels
    (``None`` -> ancien libellé générique, dégradation honnête plutôt qu'un chiffre
    inventé si l'appelant ne les calcule pas)."""
    if not tracked:
        return ""
    if equity is not None and cash is not None:
        header = (
            f"🧪 SIMULATION — suivi positions ouvertes "
            f"(portefeuille papier : équité {equity:,.0f} $, cash {cash:,.0f} $)"
        )
    else:
        header = "🧪 SIMULATION — suivi positions ouvertes (portefeuille papier 1 M$)"
    lines = [header]
    for t in tracked:
        name = t.get("symbol") or (t.get("contract") or "")[:10]
        entry = t.get("entry_price") or 0.0
        price = t.get("price") or 0.0
        qty = t.get("qty") or 0.0
        cost = t.get("cost_usd") or 0.0
        value = qty * price
        pnl = value - cost
        pnl_pct = (price / entry - 1.0) * 100.0 if entry else 0.0
        sign = "+" if pnl >= 0 else ""
        lines.append(f"{name} : {price:.6g} ({sign}{pnl_pct:.1f}%) · P&L latent {sign}{pnl:,.0f} $")
    lines.append("Aucun argent réel.")
    return "\n".join(lines)


def format_sell_alert(closed: dict) -> str:
    name = closed.get("symbol") or (closed.get("contract") or "")[:10]
    pnl = closed.get("pnl_usd") or 0.0
    pct = closed.get("pnl_pct") or 0.0
    sign = "+" if pnl >= 0 else ""
    lines = [
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"VENTE FICTIVE {name} ({closed.get('close_reason', '')})",
        f"Sortie {closed['exit_price']:.6g} · P&L {sign}{pnl:,.0f} $ ({sign}{pct:.1f}%)",
    ]
    notes = (closed.get("close_notes") or "").strip()
    if notes:
        lines.append(f"Pourquoi : {notes}")
    lines.append("Aucun argent réel.")
    return "\n".join(lines)


def format_partial_exit_alert(partial: dict) -> str:
    name = partial.get("symbol") or (partial.get("contract") or "")[:10]
    pnl = partial.get("pnl_usd") or 0.0
    pct = partial.get("pnl_pct") or 0.0
    sign = "+" if pnl >= 0 else ""
    lines = [
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"PRISE DE PROFIT PARTIELLE FICTIVE {name} ({partial.get('close_reason', '')})",
        f"Sortie {partial['exit_price']:.6g} · {sign}{pnl:,.0f} $ ({sign}{pct:.1f}%) sur la tranche vendue",
        f"Position restante : {partial.get('remaining_qty', 0):.6g} unités",
    ]
    notes = (partial.get("close_notes") or "").strip()
    if notes:
        lines.append(f"Pourquoi : {notes}")
    lines.append("Aucun argent réel.")
    return "\n".join(lines)


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

async def _default_price_lookup(contract: str, *, chain: str = "base") -> float | None:
    """Généralisé multi-chaînes (#194) -- DexScreener directement (déjà multi-chaînes,
    services/dexscreener.py) plutôt que scan_base_token (spécifique Base, et surtout
    bien plus lourd : honeypot + TA + mint-authority complets pour juste un prix de
    suivi). ``chain`` par défaut ``"base"`` -- comportement inchangé pour tout appelant
    qui ne le précise pas."""
    from aria_core.services.dexscreener import fetch_token_pairs

    pairs = await fetch_token_pairs(contract, chain=chain)
    if not pairs:
        return None
    best = max(pairs, key=lambda p: p.liquidity_usd)
    return best.price_usd if best.price_usd > 0 else None


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
        # #197 (15/07) -- VCResult.these était déjà calculée ici mais jamais remontée :
        # perdue dès la sortie de cette fonction. Remontée jusqu'à open_position() par
        # run_paper_cycle ci-dessous.
        "these": getattr(result, "these", "") or "",
    }


async def _momentum_candidates_and_chain_map(*, limit: int = 20) -> tuple[list[str], dict[str, str]]:
    """#194, pivot momentum -- source de candidats par défaut pour CE TEST (remplace
    ``candidate_ranking.top_candidates()`` UNIQUEMENT comme défaut de ``run_paper_cycle``
    quand ni ``candidates`` ni ``analyzer`` ne sont fournis par l'appelant -- ``screened_pool``/
    la poche VC 85% ne sont ni modifiés ni moins utilisés ailleurs, décision opérateur
    explicite et réversible). Renvoie la liste de contrats (contrat garde sa forme
    ``list[str]`` historique, inchangée pour le reste de la boucle) + la table
    contrat→chaîne pour l'analyzer momentum ci-dessous."""
    from aria_core import momentum_entry

    found = await momentum_entry.discover_momentum_candidates()
    chain_by_contract = {c["contract"]: c["chain"] for c in found}
    return [c["contract"] for c in found[:limit]], chain_by_contract


def _default_momentum_analyzer(chain_by_contract: dict[str, str]):
    """Ferme sur la table contrat→chaîne construite au sourcing (#194) -- garde la
    signature ``analyzer(contract)`` historique inchangée, aucun appelant existant
    (tests, autres pilotes) n'est affecté."""
    from aria_core import momentum_entry

    async def analyzer(contract: str) -> dict | None:
        chain = chain_by_contract.get(contract, "base")
        return await momentum_entry.evaluate_momentum_entry(contract, chain)

    return analyzer


async def run_paper_cycle(
    *,
    candidates=None,
    analyzer=None,
    price_lookup=None,
    notifier=None,
    max_new: int = 3,
    depeg_check=None,
    skip_position_management: bool = False,
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

    ``skip_position_management`` (#196, défaut ``False`` -- comportement historique
    inchangé) : saute l'étape 1 (re-scan sécurité + stop suiveur/TP sur les positions déjà
    ouvertes) -- réservé au service websocket momentum, déclenché bien plus souvent
    (~30s) que le cycle heartbeat normal (15 min), pour ne pas re-scanner GoPlus/Blockscout
    sur chaque position ouverte à chaque poussée. L'étape 1ter (photo de risque
    portefeuille, #186) reste TOUJOURS exécutée -- l'étape 2 (nouvelles entrées) en dépend
    (plafond/coupe-circuit), quel que soit l'appelant.

    Toute exécution passe par ``_run_cycle_lock`` (#196) -- jamais deux cycles en
    parallèle (heartbeat + websocket), qui liraient sinon le capital/le nombre de
    positions ouvertes avant que l'un des deux n'écrive (double-allocation possible).
    """
    async with _run_cycle_lock:
        return await _run_paper_cycle_locked(
            candidates=candidates,
            analyzer=analyzer,
            price_lookup=price_lookup,
            notifier=notifier,
            max_new=max_new,
            depeg_check=depeg_check,
            skip_position_management=skip_position_management,
        )


async def _run_paper_cycle_locked(
    *,
    candidates=None,
    analyzer=None,
    price_lookup=None,
    notifier=None,
    max_new: int = 3,
    depeg_check=None,
    skip_position_management: bool = False,
) -> dict:
    """Corps réel de ``run_paper_cycle`` -- appelé UNIQUEMENT sous ``_run_cycle_lock``,
    jamais directement (pas de garde-fou de concurrence sinon)."""
    await _ensure_tables()
    price_lookup = price_lookup or _default_price_lookup
    # #194 -- le défaut sait suivre la chaîne persistée d'une position (multi-chaînes) ;
    # tout price_lookup INJECTÉ (tests, ou le pipeline momentum qui fournit le sien via
    # une fermeture propre) garde son contrat d'appel historique à un seul argument.
    using_default_price_lookup = price_lookup is _default_price_lookup
    actions: dict = {"opened": [], "closed": [], "partial": [], "checked": 0, "tracked": []}
    # #197 (15/07) -- suivi périodique : une entrée par position encore ouverte à la fin
    # du cycle (prix courant déjà récupéré ci-dessous, aucun appel réseau supplémentaire).
    tracked: list[dict] = []

    # 1) Gérer les positions ouvertes : d'abord une surveillance continue de SÉCURITÉ
    #    (#187 -- honeypot/ownership apparus après l'entrée, jamais vérifiés qu'une seule
    #    fois avant), qui prime sur toute gestion par prix ; puis stop suiveur (ne se
    #    relâche jamais) et prise de profit échelonnée sur ce qui reste ouvert.
    #    #196 -- sautée si ``skip_position_management`` (service websocket momentum,
    #    déclenché bien plus souvent que le cycle heartbeat normal) : ne re-scanne pas
    #    GoPlus/Blockscout sur chaque position ouverte à chaque poussée de candidat.
    from aria_core import paper_trader_risk as risk

    if not skip_position_management:
        for p in await get_open_positions():
            actions["checked"] += 1
            try:
                if using_default_price_lookup:
                    price = await price_lookup(p["contract"], chain=p.get("chain") or "base")
                else:
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
                sec_notes = (
                    f"Re-scan sécurité déclenché en cours de détention ({_duration_phrase(p.get('opened_at'))}) : "
                    + "; ".join(security_flag["reasons"])
                    + " -- fermeture immédiate (position fictive, teste la réaction)."
                )
                closed = await close_position(
                    p["contract"], exit_price, reason="sécurité re-scan", notes=sec_notes,
                )
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

            # #197 -- provisoire : retiré ci-dessous si la position se clôture (totalement)
            # dans ce même tour, pour ne jamais dupliquer avec format_sell_alert.
            tracked.append({
                "contract": p["contract"], "symbol": p["symbol"], "entry_price": p["entry_price"],
                "qty": p["qty"], "cost_usd": p["cost_usd"], "price": price,
            })

            high_water = max(p.get("high_water_price") or p["entry_price"], price)
            if high_water != (p.get("high_water_price") or p["entry_price"]):
                await _update_high_water(p["id"], high_water)
            trailing_stop = high_water * (1 - TRAIL_STOP_PCT)
            invalidation = p.get("invalidation_price")
            active_stop = max(trailing_stop, invalidation) if invalidation else trailing_stop
            stop_is_trailing = not invalidation or trailing_stop > invalidation

            if active_stop and price <= active_stop:
                exit_gain_pct = (price / p["entry_price"] - 1.0) * 100.0 if p["entry_price"] else 0.0
                if stop_is_trailing:
                    peak_gain_pct = (high_water / p["entry_price"] - 1.0) * 100.0 if p["entry_price"] else 0.0
                    exit_notes = (
                        f"Stop suiveur déclenché : plus haut {high_water:.6g} ({peak_gain_pct:+.1f}% vs entrée), "
                        f"retracement de {TRAIL_STOP_PCT * 100:.0f}% depuis ce sommet a activé la protection -- "
                        f"sortie {price:.6g} ({exit_gain_pct:+.1f}% net vs entrée), {_duration_phrase(p.get('opened_at'))}."
                    )
                else:
                    exit_notes = (
                        f"Invalidation technique atteinte : prix {price:.6g} <= seuil {invalidation:.6g} "
                        f"({exit_gain_pct:+.1f}% vs entrée) -- thèse invalidée, sortie immédiate, "
                        f"{_duration_phrase(p.get('opened_at'))}."
                    )
                closed = await close_position(
                    p["contract"], price,
                    reason="stop suiveur" if stop_is_trailing else "invalidation",
                    notes=exit_notes,
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
                stage_target_pct = TP_STAGES[stage_hit - 1] * 100.0
                if is_last_stage:
                    tp_notes = (
                        f"Dernier palier de profit {stage_hit}/{len(TP_STAGES)} atteint "
                        f"(+{gain_pct * 100:.0f}% vs entrée, seuil visé +{stage_target_pct:.0f}%) -- "
                        f"clôture du reliquat, {_duration_phrase(p.get('opened_at'))}."
                    )
                    closed = await close_position(
                        p["contract"], price,
                        reason=f"palier {stage_hit}/{len(TP_STAGES)} (clôture)", notes=tp_notes,
                    )
                    if closed:
                        actions["closed"].append(closed)
                        if notifier:
                            try:
                                await notifier(format_sell_alert(closed))
                            except Exception:  # noqa: BLE001
                                pass
                    break

                partial_pct = TP_STAGE_FRACTION * 100.0
                remaining_after_pct = max(0.0, 100.0 - stage_hit * TP_STAGE_FRACTION * 100.0)
                partial_notes = (
                    f"Palier de profit {stage_hit}/{len(TP_STAGES)} atteint "
                    f"(+{gain_pct * 100:.0f}% vs entrée, seuil visé +{stage_target_pct:.0f}%) -- "
                    f"prise de {partial_pct:.0f}% de la position initiale, "
                    f"~{remaining_after_pct:.0f}% restant en jeu."
                )
                partial = await reduce_position(
                    p["contract"], price, sell_qty, stage=stage_hit,
                    reason=f"palier {stage_hit}/{len(TP_STAGES)}", notes=partial_notes,
                )
                if partial:
                    actions["partial"].append(partial)
                    remaining_qty = partial["remaining_qty"]
                    if notifier:
                        try:
                            await notifier(format_partial_exit_alert(partial))
                        except Exception:  # noqa: BLE001
                            pass

        # 1bis) Suivi périodique des positions ENCORE ouvertes (#197, 15/07) -- pas seulement
        # à l'achat/la vente. Retire celles fermées CE tour (déjà couvertes par
        # format_sell_alert, jamais dupliquées). Un seul message consolidé, pas un par
        # position (évite le bruit Telegram) -- persistance en base (thesis, prix, contrat)
        # prime de toute façon sur cet affichage, qui reste best-effort.
        closed_contracts_this_cycle = {c["contract"] for c in actions["closed"]}
        tracked = [t for t in tracked if t["contract"] not in closed_contracts_this_cycle]
        actions["tracked"] = tracked
        if tracked and notifier:
            # Équité/cash RÉELS (17/07) -- réutilise le prix déjà récupéré cette boucle pour
            # chaque position (``t["price"]``), aucun nouvel appel réseau ; ``cash_available``
            # est une simple lecture DB (déjà utilisée ailleurs), jamais un doublon de calcul.
            tracking_cash = tracking_equity = None
            try:
                tracking_cash = await cash_available()
                open_value = sum((t.get("qty") or 0.0) * (t.get("price") or 0.0) for t in tracked)
                tracking_equity = tracking_cash + open_value
            except Exception:  # noqa: BLE001 -- l'alerte degrade au libelle generique, jamais fatale
                pass
            # 17/07 -- réduit le bruit Telegram de moitié : n'envoie que si le dernier
            # envoi remonte à au moins TRACKING_ALERT_MIN_INTERVAL_MINUTES. Ne bloque jamais
            # une vraie alerte d'achat/vente (celles-ci ont leur propre notifier plus haut,
            # jamais soumises à cette fenêtre) -- seul ce suivi périodique est throttlé.
            should_notify = True
            try:
                last_at = await get_last_tracking_alert_at()
                if last_at:
                    elapsed_min = (datetime.now(timezone.utc) - datetime.fromisoformat(last_at)).total_seconds() / 60.0
                    should_notify = elapsed_min >= TRACKING_ALERT_MIN_INTERVAL_MINUTES
            except Exception:  # noqa: BLE001 -- en cas de doute, on notifie (dégradation douce)
                should_notify = True
            msg = format_position_tracking_alert(tracked, cash=tracking_cash, equity=tracking_equity)
            if msg and should_notify:
                try:
                    await notifier(msg)
                    await set_last_tracking_alert_at(_now())
                except Exception:  # noqa: BLE001 — l'alerte ne casse pas le cycle
                    pass

    # 1ter) Photo du risque portefeuille (#186) -- une seule fois par cycle, APRÈS la gestion
    # des positions déjà ouvertes (qui doit continuer normalement même coupe-circuit armé) et
    # AVANT toute tentative d'ouverture. Met à jour le plus haut d'équité persisté, arme le
    # coupe-circuit dédié si un palier dur est franchi pour la première fois.
    from aria_core import risk_guard

    risk_state = await risk_guard.evaluate_portfolio_risk(price_lookup=price_lookup)
    actions["risk_state"] = risk_state
    if risk_state.newly_triggered_hard and notifier:
        try:
            await notifier(risk_guard.format_hard_circuit_breaker_alert(risk_state))
        except Exception:  # noqa: BLE001 — l'alerte ne casse pas le cycle
            pass
    elif risk_state.newly_triggered_soft and notifier:
        try:
            await notifier(risk_guard.format_soft_drawdown_alert(risk_state))
        except Exception:  # noqa: BLE001
            pass

    if risk_state.blocked:
        # Palier dur (ou pause globale) : aucune NOUVELLE entrée ce tour -- les positions
        # déjà ouvertes ont déjà été gérées normalement ci-dessus (étape 1).
        return actions

    # 2) Ouvrir de nouvelles positions depuis les candidats classés (signal d'achat réel) --
    #    sauf si USDC est dépeg (#187) : le pricing de tout ce portefeuille suppose un USD
    #    stable, on bloque les NOUVELLES entrées (les positions déjà ouvertes ne sont pas
    #    touchées) tant que le dépeg n'est pas résorbé.
    # #194 -- pivot momentum multi-chaînes : quand NI candidates NI analyzer ne sont
    # fournis (le cas réel du heartbeat, run_paper_cycle(notifier=...) sans arguments),
    # remplace le défaut candidate_ranking.top_candidates()/_default_analyzer (VC-thesis,
    # poche 85%) par le pipeline momentum pour CE TEST -- décision opérateur explicite,
    # réversible, screened_pool/safety_screen non touchés. Tout appelant qui fournit
    # SON PROPRE candidates ou analyzer garde le comportement historique inchangé.
    if candidates is None and analyzer is None:
        candidates, _momentum_chain_by_contract = await _momentum_candidates_and_chain_map(limit=20)
        analyzer = _default_momentum_analyzer(_momentum_chain_by_contract)
    elif candidates is None:
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
    start = await starting_capital()
    # #186 -- palier souple : réduit de moitié l'allocation des NOUVELLES entrées (jamais
    # les positions déjà ouvertes). Passé explicitement à open_position, qui applique ENSUITE
    # son propre plafond de risque par trade (défense en profondeur, cf. size_position_by_risk).
    new_entry_alloc_usd = risk_state.alloc_multiplier * ALLOC_PCT * start

    # Funnel par cycle (mandat #192, 16/07) : agrège POURQUOI chaque candidat évalué
    # n'a pas mené à un achat. Sans ça, une panne prolongée du seul garde-fou dur
    # (GoPlus, aucun repli -- cf. momentum_entry.py) produit exactement le même
    # symptôme observable (zéro nouvelle position) qu'un marché réellement sans
    # candidat valable -- indiscernables sans lire les logs applicatifs un par un,
    # ce qui va à l'encontre de l'objectif diagnostique du test 1M$ (comprendre
    # COMMENT ARIA trade, pas juste SI elle trade). Additif pur : ne change aucun
    # comportement de décision, seulement la visibilité. Le champ ``hold_reason``
    # (momentum_entry.py) alimente ce compteur ; un analyzer qui ne le fournit pas
    # (ex. le pilote VC-thesis historique, ``_default_analyzer``) tombe dans le
    # seau générique "unspecified", sans erreur.
    funnel: dict[str, int] = {}
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
            funnel["analyzer_error"] = funnel.get("analyzer_error", 0) + 1
            continue
        if not sig:
            funnel["no_price_data"] = funnel.get("no_price_data", 0) + 1
            continue
        if sig.get("action") != "BUY":
            reason_code = sig.get("hold_reason") or "unspecified"
            funnel[reason_code] = funnel.get(reason_code, 0) + 1
            continue
        price = sig.get("price")
        if not price:
            try:
                if using_default_price_lookup:
                    price = await price_lookup(contract, chain=sig.get("chain") or "base")
                else:
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
            alloc_usd=new_entry_alloc_usd,
            category=sig.get("category", ""),
            entry_security_json=sig.get("entry_security_json", ""),
            chain=sig.get("chain") or "base",
            # bug trouvé le 17/07 : ``sig.get("these")`` seul ne couvrait que l'ancien
            # analyseur VC-thesis (_default_analyzer, clé "these") -- l'analyseur momentum
            # (#194, evaluate_momentum_entry) construit une vraie liste "reasons" (setup
            # golden pocket/RSI, alignement technique, R/R) mais ne pose jamais "these",
            # donc `thesis` restait silencieusement None sur tous les trades momentum.
            thesis=sig.get("these") or "; ".join(sig.get("reasons") or []) or None,
        )
        if pos:
            opened += 1
            actions["opened"].append(pos)
            if notifier:
                try:
                    await notifier(format_buy_alert(pos))
                except Exception:  # noqa: BLE001
                    pass

    if funnel:
        actions["momentum_funnel"] = funnel
        logger.info("paper_cycle funnel (nouvelles entrées, %d candidats) : %s", len(candidates), funnel)

    return actions
