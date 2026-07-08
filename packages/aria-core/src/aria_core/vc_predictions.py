"""Journal des prédictions VC — mesure la PERTINENCE d'ARIA dans le temps.

Chaque analyse `/vc` est enregistrée comme une **prédiction datée** (même sans
trade réel : « shadow »). Plus tard, l'opérateur attribue un résultat réel
(P&L %) via `/vcresult`. On peut alors mesurer si ARIA est réellement pertinente :

- **hit-rate** : part des recommandations BUY dont le résultat est positif ;
- **P&L moyen** par recommandation ;
- **calibration** : est-ce qu'un « Potentiel 8/10 » surperforme réellement un
  « 5/10 » ? (le vrai test d'un analyste).

Logger *toutes* les analyses (pas seulement les trades) accélère l'accumulation
d'un échantillon statistiquement exploitable — à ~2 trades/mois, se limiter aux
positions réelles serait bien trop lent.

Stockage local SQLite `aria.db`, table `vc_prediction` (ajout pur,
`CREATE TABLE IF NOT EXISTS`). Aucune action financière : c'est un journal.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_COLUMNS = [
    "id",
    "contract",
    "recommandation",
    "potentiel",
    "risque",
    "taille_pct",
    "security_score",
    "llm_used",
    "report_ref",
    "traded",
    "status",
    "outcome_pct",
    "outcome_note",
    "created_at",
    "closed_at",
    # Ajouts « wallet suivi » (voûte 2) — additifs, tous nullable, migrés à chaud
    # pour les DB existantes (aucune ligne ancienne cassée).
    "strategy",           # 'vc' (85% moyen/long) | 'spec' (15% spéculation small-cap)
    "entry_price",        # prix USD au moment du verdict (pour valoriser en live)
    "pool_address",       # pool DEX (pour rafraîchir le prix via OHLCV)
    "network",            # réseau GeckoTerminal (Base au lancement)
    "target_price",       # cible dérivée (facts-only)
    "invalidation_price", # niveau d'invalidation dérivé
]

# Colonnes ajoutées après coup : (nom, définition SQL) pour la migration ALTER.
_ADDED_COLUMNS = [
    ("strategy", "TEXT DEFAULT 'vc'"),
    ("entry_price", "REAL"),
    ("pool_address", "TEXT"),
    ("network", "TEXT"),
    ("target_price", "REAL"),
    ("invalidation_price", "REAL"),
]

# Répartition cible du portefeuille suivi d'ARIA (documentée, jamais un ordre réel).
STRATEGY_ALLOCATION = {"vc": 0.85, "spec": 0.15}

# Buckets de potentiel pour la courbe de calibration.
_CALIB_BUCKETS = [(0, 3, "0-3"), (4, 6, "4-6"), (7, 8, "7-8"), (9, 10, "9-10")]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS vc_prediction (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                recommandation TEXT NOT NULL,
                potentiel INTEGER,
                risque TEXT,
                taille_pct REAL DEFAULT 0,
                security_score INTEGER,
                llm_used INTEGER DEFAULT 0,
                report_ref TEXT,
                traded INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                outcome_pct REAL,
                outcome_note TEXT,
                created_at TEXT NOT NULL,
                closed_at TEXT,
                strategy TEXT DEFAULT 'vc',
                entry_price REAL,
                pool_address TEXT,
                network TEXT,
                target_price REAL,
                invalidation_price REAL
            )
            """
        )
        # Migration à chaud : ajoute les colonnes « wallet suivi » aux DB existantes
        # (SQLite ne les crée pas si la table préexiste). Idempotent, non destructif.
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(vc_prediction)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE vc_prediction ADD COLUMN {name} {ddl}")
        await db.commit()


async def record_prediction(
    *,
    contract: str,
    recommandation: str,
    potentiel: int | None,
    risque: str,
    taille_pct: float,
    security_score: int,
    llm_used: bool,
    report_ref: str = "",
    traded: bool = False,
    strategy: str = "vc",
    entry_price: float | None = None,
    pool_address: str = "",
    network: str = "",
    target_price: float | None = None,
    invalidation_price: float | None = None,
) -> int:
    """Enregistre une prédiction VC ``open`` et retourne son id.

    Les champs « wallet suivi » (strategy, entry_price, pool_address, network,
    target/invalidation) sont optionnels : sans eux, le comportement est identique
    à avant (le verdict est loggé mais ne peut pas être valorisé en live). Avec
    entry_price + pool_address, la position devient valorisable au prix OHLCV réel.
    ``strategy`` ∈ {'vc', 'spec'} — la poche 85/15 (spéculation small-cap).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    strat = strategy if strategy in STRATEGY_ALLOCATION else "vc"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO vc_prediction
            (contract, recommandation, potentiel, risque, taille_pct, security_score,
             llm_used, report_ref, traded, status, created_at,
             strategy, entry_price, pool_address, network, target_price, invalidation_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract,
                recommandation,
                potentiel,
                risque,
                taille_pct,
                security_score,
                1 if llm_used else 0,
                report_ref,
                1 if traded else 0,
                now,
                strat,
                entry_price,
                pool_address or "",
                network or "",
                target_price,
                invalidation_price,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def close_prediction(prediction_id: int, *, outcome_pct: float, note: str = "") -> dict | None:
    """Attribue un résultat réel (P&L %). Transition atomique ``open -> closed``.

    Retourne la ligne close, ou ``None`` si id inconnu / déjà clôturé (on ne
    réécrit jamais un résultat déjà attribué).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE vc_prediction
            SET status = 'closed', outcome_pct = ?, outcome_note = ?, closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (outcome_pct, note, now, prediction_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
        row = await (await db.execute("SELECT * FROM vc_prediction WHERE id = ?", (prediction_id,))).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


async def get_prediction(prediction_id: int) -> dict | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT * FROM vc_prediction WHERE id = ?", (prediction_id,))).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


async def count_predictions_for_contract(contract: str) -> int:
    """Nombre d'analyses déjà enregistrées pour ce contrat (avant la présente).

    Sert à numéroter les rapports (« Rapport n°2 sur ce token ») pour qu'un
    abonné recevant plusieurs analyses suivies du même token puisse s'y
    retrouver. Comparaison insensible à la casse (adresse EVM).
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM vc_prediction WHERE LOWER(contract) = LOWER(?)",
                (contract,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def list_predictions_for_contract(contract: str, limit: int = 50) -> list[dict]:
    """Historique complet des analyses VC pour un contrat, les plus récentes d'abord.

    Alimente le « dossier par token » (chronologie des analyses). Comparaison
    insensible à la casse (adresse EVM stockée telle quelle par l'écrivain).
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM vc_prediction WHERE LOWER(contract) = LOWER(?) "
                "ORDER BY id DESC LIMIT ?",
                (contract, limit),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def total_predictions_count() -> int:
    """Nombre total d'analyses ARIA jamais enregistrées (tous tokens confondus).

    Sert de numéro de série global (« Série 00.047 ») — donne au rapport une
    identité d'édition numérotée, indépendante du suivi par token.
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT COUNT(*) FROM vc_prediction")).fetchone()
    return int(row[0]) if row else 0


async def list_open_predictions(limit: int = 20) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM vc_prediction WHERE status = 'open' ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


def compute_metrics(predictions: list[dict]) -> dict:
    """Métriques de pertinence à partir d'une liste de prédictions (fonction pure, testable).

    Ne considère que les prédictions clôturées (résultat réel attribué) pour les
    taux ; compte les ouvertes séparément.
    """
    closed = [p for p in predictions if p.get("status") == "closed" and p.get("outcome_pct") is not None]
    open_count = sum(1 for p in predictions if p.get("status") == "open")

    buys = [p for p in closed if p.get("recommandation") == "BUY"]
    wins = [p for p in buys if (p.get("outcome_pct") or 0) > 0]
    hit_rate = (len(wins) / len(buys)) if buys else None
    avg_pnl_buy = (sum(p["outcome_pct"] for p in buys) / len(buys)) if buys else None

    # Calibration : P&L moyen par bucket de potentiel (uniquement analyses LLM notées).
    calibration = []
    scored = [p for p in closed if p.get("potentiel") is not None]
    for low, high, label in _CALIB_BUCKETS:
        bucket = [p for p in scored if low <= p["potentiel"] <= high]
        if bucket:
            avg = sum(p["outcome_pct"] for p in bucket) / len(bucket)
            calibration.append({"bucket": label, "count": len(bucket), "avg_pnl": avg})

    # « Wall of NO » : verdicts AVOID (tous statuts) — la preuve publique la plus forte.
    avoid_count = sum(1 for p in predictions if p.get("recommandation") == "AVOID")

    # Ventilation par poche 85/15 (hit-rate BUY par stratégie).
    by_strategy = {}
    for sleeve in ("vc", "spec"):
        s_buys = [p for p in buys if (p.get("strategy") or "vc") == sleeve]
        s_wins = [p for p in s_buys if (p.get("outcome_pct") or 0) > 0]
        by_strategy[sleeve] = {
            "buy_count": len(s_buys),
            "hit_rate": (len(s_wins) / len(s_buys)) if s_buys else None,
            "avg_pnl_buy": (sum(p["outcome_pct"] for p in s_buys) / len(s_buys)) if s_buys else None,
        }

    return {
        "total": len(predictions),
        "closed": len(closed),
        "open": open_count,
        "buy_count": len(buys),
        "hit_rate": hit_rate,
        "avg_pnl_buy": avg_pnl_buy,
        "calibration": calibration,
        "avoid_count": avoid_count,
        "by_strategy": by_strategy,
    }


async def metrics() -> dict:
    """Charge toutes les prédictions et calcule les métriques de pertinence."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute("SELECT * FROM vc_prediction")).fetchall()
    return compute_metrics([dict(zip(_COLUMNS, row)) for row in rows])


def _sleeve_return(holdings: list[dict], current_prices: dict[int, float]) -> tuple[float, int]:
    """Rendement moyen (equal-weight) d'une poche + nombre de positions valorisées."""
    rets = []
    for p in holdings:
        entry = p.get("entry_price")
        cur = current_prices.get(p["id"])
        if entry and cur and entry > 0:
            rets.append((cur - entry) / entry)
    if not rets:
        return 0.0, 0
    return sum(rets) / len(rets), len(rets)


def portfolio_value(
    predictions: list[dict], current_prices: dict[int, float], *, base_index: float = 100.0
) -> dict:
    """Valeur du portefeuille SUIVI d'ARIA (paper, jamais un fonds réel).

    Modèle honnête et documenté : on ne tient que les verdicts **BUY** (ce qu'ARIA
    « détiendrait »), equal-weight dans chaque poche, répartis **85 % VC / 15 %
    spéculation**. Une poche sans position vaut 0 (cash au repos, rendement plat).
    L'indice part de ``base_index`` (100) → la valeur reflète le rendement non
    réalisé, valorisé aux **vrais prix OHLCV** fournis dans ``current_prices``.
    Fonction pure : aucun réseau ici (les prix sont injectés), donc testable.
    """
    buys = [p for p in predictions if p.get("recommandation") == "BUY"]
    vc = [p for p in buys if (p.get("strategy") or "vc") == "vc"]
    spec = [p for p in buys if (p.get("strategy") or "vc") == "spec"]

    vc_ret, n_vc = _sleeve_return(vc, current_prices)
    spec_ret, n_spec = _sleeve_return(spec, current_prices)

    total_ret = (
        STRATEGY_ALLOCATION["vc"] * vc_ret + STRATEGY_ALLOCATION["spec"] * spec_ret
    )
    return {
        "index": round(base_index * (1 + total_ret), 2),
        "total_return_pct": round(total_ret * 100, 2),
        "vc_return_pct": round(vc_ret * 100, 2),
        "spec_return_pct": round(spec_ret * 100, 2),
        "positions_valued": n_vc + n_spec,
        "vc_positions": n_vc,
        "spec_positions": n_spec,
        "allocation": dict(STRATEGY_ALLOCATION),
    }


async def _current_prices_for(predictions: list[dict]) -> dict[int, float]:
    """Récupère le dernier prix OHLCV réel des positions valorisables (BUY + pool).

    Dégradation gracieuse : une position dont le pool est absent ou dont l'OHLCV
    n'est pas disponible est simplement omise (jamais un prix inventé).
    """
    from aria_core.services.ohlcv import ohlcv_client

    prices: dict[int, float] = {}
    for p in predictions:
        if p.get("recommandation") != "BUY":
            continue
        pool = (p.get("pool_address") or "").strip()
        if not pool:
            continue
        res = await ohlcv_client.get_ohlcv(pool, network=(p.get("network") or "base"))
        if res.available and res.candles:
            prices[p["id"]] = res.candles[-1].close
    return prices


async def live_wallet() -> dict:
    """Valeur live du portefeuille suivi d'ARIA (positions BUY ouvertes, prix réels).

    C'est le chiffre du « wallet ARIA » destiné à la page d'accueil (teaser FOMO)
    et à la page abonné. Facts-only : s'il n'y a aucune position valorisable, on
    renvoie un indice neutre (100, +0 %) — jamais un chiffre gonflé.
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute("SELECT * FROM vc_prediction WHERE status = 'open'")
        ).fetchall()
    open_preds = [dict(zip(_COLUMNS, row)) for row in rows]
    prices = await _current_prices_for(open_preds)
    return portfolio_value(open_preds, prices)
