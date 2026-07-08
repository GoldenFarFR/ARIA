"""Carnet de bord d'ARIA — journal de chaque analyse + suivi de la thèse dans le temps.

Deux besoins de l'opérateur réunis :

1. **Journal interne** : pour CHAQUE analyse/investissement, ARIA consigne le pourquoi
   et le comment — thèse de départ, faits, décision, points d'entrée/sortie, référence
   du graphique, et chaque révision ultérieure. Registre append-only, horodaté,
   exportable en .txt lisible.

2. **Suivi de la thèse** : périodiquement, ARIA re-vérifie si la thèse TIENT ENCORE —
   le prix vs la cible/invalidation, ET l'activité du projet (livre-t-il du contenu,
   commits, posts... ou stagne-t-il ?). Une thèse qui se dégrade est signalée.

Sert trois choses : PREUVE (registre inviolable pour le pacte argent réel),
TRANSPARENCE (règle « transparence totale »), PRODUIT (cœur du cockpit abonné).

Stockage local SQLite `aria.db` : tables ``journal_entry`` et ``thesis_checkpoint``.
Aucune action financière : c'est un cahier, pas un exécuteur.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)
DB_PATH = str(aria_db_path())

# Fenêtres d'activité projet (jours) : au-delà, on parle de stagnation.
_ACTIVE_WITHIN_DAYS = 14
_STAGNANT_AFTER_DAYS = 30


@dataclass(frozen=True)
class ActivityVerdict:
    """Le projet livre-t-il encore, ou stagne-t-il ?"""

    status: str  # shipping / slowing / stagnating / unknown
    note: str = ""


def assess_project_activity(
    *, github_last_commit_days: int | None = None, social_last_post_days: int | None = None
) -> ActivityVerdict:
    """Juge l'activité récente d'un projet à partir des délais depuis le dernier signe de vie.

    Prend le signal le PLUS récent (commit OU post) : un projet peut communiquer sans
    committer, ou l'inverse. ``None`` partout -> unknown (jamais une accusation gratuite).
    """
    signals = [d for d in (github_last_commit_days, social_last_post_days) if d is not None]
    if not signals:
        return ActivityVerdict(status="unknown", note="aucun signal d'activité accessible")
    freshest = min(signals)
    if freshest <= _ACTIVE_WITHIN_DAYS:
        return ActivityVerdict(status="shipping", note=f"activité il y a {freshest} j (livre)")
    if freshest <= _STAGNANT_AFTER_DAYS:
        return ActivityVerdict(status="slowing", note=f"dernier signe il y a {freshest} j (ralentit)")
    return ActivityVerdict(status="stagnating", note=f"rien depuis {freshest} j (stagne)")


def judge_thesis(
    *,
    price_vs_entry_pct: float | None,
    invalidation_hit: bool,
    activity: ActivityVerdict,
) -> tuple[str, str]:
    """Verdict global du suivi de thèse : (statut, note). Faits d'abord, jamais un rejet aveugle.

    - invalidation touchée -> 'invalidated' (le niveau de sortie a parlé) ;
    - projet qui stagne -> 'stagnating' (la thèse « builder actif » ne tient plus) ;
    - projet qui livre + prix qui tient/monte -> 'delivering' ;
    - sinon 'on_track'.
    """
    if invalidation_hit:
        return "invalidated", "niveau d'invalidation atteint : thèse cassée"
    if activity.status == "stagnating":
        return "stagnating", f"projet en stagnation : {activity.note}"
    if activity.status == "shipping" and (price_vs_entry_pct or 0) >= 0:
        return "delivering", f"projet actif et prix qui tient ({activity.note})"
    return "on_track", f"thèse maintenue ({activity.note})"


@dataclass
class JournalEntry:
    contract: str
    symbol: str = ""
    decision: str = ""          # BUY / WATCH / AVOID
    thesis: str = ""
    reasoning: str = ""
    facts: list[str] = field(default_factory=list)
    entry_price: float | None = None
    target_price: float | None = None
    invalidation_price: float | None = None
    chart_ref: str = ""         # chemin/URI du graphique annoté (entrée/sortie)
    created_at: str = ""


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_entry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                symbol TEXT,
                decision TEXT,
                thesis TEXT,
                reasoning TEXT,
                facts TEXT,
                entry_price REAL,
                target_price REAL,
                invalidation_price REAL,
                chart_ref TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS thesis_checkpoint (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                price REAL,
                price_vs_entry_pct REAL,
                activity_status TEXT,
                verdict TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _charts_dir() -> str:
    import os

    d = os.path.join(os.path.dirname(DB_PATH) or ".", "journal_charts")
    os.makedirs(d, exist_ok=True)
    return d


def save_entry_screenshot(
    contract: str,
    candles,
    *,
    entry=None,
    invalidation=None,
    target=None,
    markers=None,
    tag: str = "entry",
    horizon_weeks: int = 4,
) -> str:
    """Génère et SAUVE le screenshot du graphique (réel + simulation) pour une entrée.

    Retourne le chemin du PNG (utilisable comme ``chart_ref``). ARIA « fait » donc
    bien le screenshot : historique réel annoté du point d'entrée + simulation des
    prochaines semaines (scénario, pas une prévision). Jamais bloquant : sur erreur,
    renvoie une chaîne vide (le carnet reste texte).
    """
    try:
        from aria_core.skills.chart_render import render_scenario_png, save_png_data_uri

        uri = render_scenario_png(
            candles, entry=entry, invalidation=invalidation, target=target,
            markers=markers, horizon_weeks=horizon_weeks,
        )
        safe = "".join(ch for ch in contract.lower() if ch.isalnum())[:20]
        path = f"{_charts_dir()}/{safe}_{tag}.png"
        return save_png_data_uri(uri, path)
    except Exception:  # noqa: BLE001 — le screenshot est un bonus, jamais bloquant
        return ""


async def record_entry(entry: JournalEntry) -> int:
    """Consigne une analyse dans le journal (append-only). Retourne l'id de l'entrée."""
    await _ensure_tables()
    created = entry.created_at or _now()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO journal_entry
                (contract, symbol, decision, thesis, reasoning, facts, entry_price,
                 target_price, invalidation_price, chart_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.contract, entry.symbol, entry.decision, entry.thesis, entry.reasoning,
                json.dumps(entry.facts, ensure_ascii=False), entry.entry_price,
                entry.target_price, entry.invalidation_price, entry.chart_ref, created,
            ),
        )
        await db.commit()
        return int(cur.lastrowid)


async def record_checkpoint(
    contract: str, *, price: float | None, price_vs_entry_pct: float | None,
    activity_status: str, verdict: str, note: str = "",
) -> None:
    """Consigne un point de contrôle du suivi de thèse (append-only)."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO thesis_checkpoint
                (contract, price, price_vs_entry_pct, activity_status, verdict, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (contract, price, price_vs_entry_pct, activity_status, verdict, note, _now()),
        )
        await db.commit()


async def list_entries(contract: str | None = None, limit: int = 50) -> list[dict]:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if contract:
            cur = await db.execute(
                "SELECT * FROM journal_entry WHERE LOWER(contract) = LOWER(?) "
                "ORDER BY id DESC LIMIT ?",
                (contract, limit),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM journal_entry ORDER BY id DESC LIMIT ?", (limit,)
            )
        rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["facts"] = json.loads(d.get("facts") or "[]")
        out.append(d)
    return out


async def list_checkpoints(contract: str, limit: int = 50) -> list[dict]:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM thesis_checkpoint WHERE LOWER(contract) = LOWER(?) "
            "ORDER BY id DESC LIMIT ?",
            (contract, limit),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def review_open_theses(
    positions: list[dict],
    *,
    price_fn=None,
    activity_fn=None,
) -> list[dict]:
    """Repasse sur chaque position ouverte : re-vérifie prix + activité, consigne un checkpoint.

    ``positions`` : liste de dicts {contract, entry_price, invalidation_price, github_url}.
    ``price_fn(contract) -> prix|None`` et ``activity_fn(github_url) -> jours|None`` sont
    injectables (offline). Retourne la liste des ALERTES (thèses stagnantes ou invalidées),
    pour que l'appelant (heartbeat) notifie l'opérateur. Jamais bloquant par position.
    """
    if price_fn is None:
        async def price_fn(_c):  # pragma: no cover - défaut réseau
            return None
    if activity_fn is None:
        from aria_core.services.project_activity import github_days_since_commit as activity_fn

    alerts: list[dict] = []
    for pos in positions or []:
        contract = pos.get("contract")
        if not contract:
            continue
        try:
            price = await price_fn(contract)
            entry = pos.get("entry_price")
            inval = pos.get("invalidation_price")
            pct = None
            if price and entry:
                pct = round(100.0 * (price - entry) / entry, 1)
            inval_hit = bool(price and inval and price <= inval)
            gh_days = None
            try:
                gh_days = await activity_fn(pos.get("github_url"))
            except Exception:  # noqa: BLE001 — l'activité est un bonus
                gh_days = None
            activity = assess_project_activity(github_last_commit_days=gh_days)
            verdict, note = judge_thesis(
                price_vs_entry_pct=pct, invalidation_hit=inval_hit, activity=activity
            )
            await record_checkpoint(
                contract, price=price, price_vs_entry_pct=pct,
                activity_status=activity.status, verdict=verdict, note=note,
            )
            if verdict in ("invalidated", "stagnating"):
                alerts.append({"contract": contract, "verdict": verdict, "note": note})
        except Exception as exc:  # noqa: BLE001 — une position qui plante n'arrête pas le tour
            logger.info("review_open_theses: %s échoué (%s)", contract, exc)
    return alerts


async def export_txt(limit: int = 100) -> str:
    """Exporte le carnet en texte lisible (.txt) — pour l'opérateur et la preuve."""
    entries = await list_entries(limit=limit)
    lines: list[str] = ["CARNET DE BORD ARIA", "=" * 60, ""]
    for e in entries:
        lines.append(f"[{e['created_at']}] {e['symbol'] or e['contract'][:10]} — {e['decision']}")
        lines.append(f"  Contrat : {e['contract']}")
        if e.get("chart_ref"):
            lines.append(f"  Graphique : {e['chart_ref']}")
        if e.get("thesis"):
            lines.append(f"  These : {e['thesis']}")
        if e.get("entry_price") is not None:
            lines.append(
                f"  Entree {e['entry_price']} / cible {e.get('target_price')} / "
                f"invalidation {e.get('invalidation_price')}"
            )
        for f in e.get("facts", [])[:8]:
            lines.append(f"  - {f}")
        checks = await list_checkpoints(e["contract"], limit=10)
        for c in reversed(checks):
            lines.append(
                f"    > suivi [{c['created_at'][:10]}] {c['verdict']} : {c.get('note', '')}"
            )
        lines.append("")
    return "\n".join(lines)
