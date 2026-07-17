"""Plafond de dépense x402 — décision opérateur explicite (16/07) : 5$ maximum par
semaine, dépensés STRATÉGIQUEMENT ("ne jamais être à court, mais en dépenser assez
pour optimiser la vitesse à accumuler des données").

Traduction concrète de cette consigne :
  - Plafond dur, jamais dépassé (`can_spend`/`record_spend` — fail-closed : en cas de
    doute sur le solde déjà consommé, on refuse plutôt que de risquer un dépassement).
  - AUCUN throttle artificiel en dessous du plafond : la vitesse d'accumulation de
    connaissance durable est justement l'objectif ("optimiser la vitesse") — le seul
    frein légitime est la discipline "un fait, une fois" (dédoublonnage), pas un
    goutte-à-goutte quotidien imposé par ce module.
  - Semaine glissante calendaire (lundi 00:00 UTC), pas un cumul depuis toujours.

Structurellement séparé de `wallet_guard.py`/`agent_wallet_log.py` — même doctrine que
`sepolia_autonomous.py`/`bonding_trade_log.py` : ce plafond ne modifie ni ne contourne
le garde-fou partagé qui protège tout capital réel à plus grande échelle. Portée
strictement limitée aux micropaiements de données/API x402 (centimes) — ne touche
JAMAIS le trading avec capital réel (swaps, positions), qui reste sur son propre
chemin séparé (CLAUDE.md, 16/07).

Append-only (même patron que `agent_directive_log`/`agent_wallet_log`) : aucune
fonction UPDATE/DELETE ici, chaque tentative (`status` in {"ok", "blocked", "failed"})
reste tracée pour toujours, jamais silencieuse.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

WEEKLY_CAP_USD = 5.0

_COLUMNS = [
    "id",
    "resource",
    "provider",
    "amount_usd",
    "status",
    "reason",
    "created_at",
    "pay_to",
]

# 17/07 -- ajouté après un vrai faux positif de agent_wallet_monitor.py (une alerte
# "SORTIE NON INITIÉE PAR ARIA" sur le tout premier paiement x402 réel, jamais
# reconnu comme "known" car x402_cdp_signer.py ne passe pas par agent_wallet_log).
# `pay_to` (adresse de règlement du 402, déjà connue au moment de record_spend --
# jamais un nouvel appel réseau) permet au moniteur de corréler un mouvement
# on-chain détecté à une dépense x402 déjà journalisée, sans dépendre d'un
# éventuel header X-PAYMENT-RESPONSE (optionnel dans le protocole, jamais garanti).
_ADDED_COLUMNS = [
    ("pay_to", "TEXT NOT NULL DEFAULT ''"),
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS x402_spend_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                amount_usd REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                pay_to TEXT NOT NULL DEFAULT ''
            )
            """
        )
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(x402_spend_log)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE x402_spend_log ADD COLUMN {name} {ddl}")
        await db.commit()


def week_start(now: datetime | None = None) -> datetime:
    """Début de la semaine calendaire courante (lundi 00:00 UTC)."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    monday = ref - timedelta(days=ref.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


async def spent_this_week(now: datetime | None = None) -> float:
    """Somme des dépenses RÉELLEMENT effectuées (status='ok') depuis le début de la
    semaine calendaire courante. Les tentatives 'blocked'/'failed' ne comptent jamais
    contre le plafond -- seul un paiement réellement réglé consomme le budget."""
    await _ensure_table()
    start = week_start(now).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) FROM x402_spend_log "
                "WHERE status = 'ok' AND created_at >= ?",
                (start,),
            )
        ).fetchone()
    return float(row[0]) if row else 0.0


async def remaining_budget(now: datetime | None = None) -> float:
    spent = await spent_this_week(now)
    return max(0.0, WEEKLY_CAP_USD - spent)


async def can_spend(amount_usd: float, now: datetime | None = None) -> bool:
    """Fail-closed : un montant négatif/nul est toujours refusé (rien à payer), et si le
    solde restant ne couvre pas le montant demandé, on refuse plutôt que d'approcher le
    plafond au plus près."""
    if amount_usd <= 0:
        return False
    remaining = await remaining_budget(now)
    return amount_usd <= remaining


async def record_spend(
    *,
    resource: str,
    provider: str = "",
    amount_usd: float,
    status: str,
    reason: str = "",
    pay_to: str = "",
) -> None:
    """Enregistre une tentative de paiement x402 (``status`` in {"ok", "blocked",
    "failed"}) -- jamais seulement les succès, un refus de plafond doit rester tracé.
    ``pay_to`` (17/07) : adresse de règlement déclarée par le 402, pour corrélation
    par ``agent_wallet_monitor.py`` (cf. commentaire sur ``_ADDED_COLUMNS``)."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO x402_spend_log (resource, provider, amount_usd, status, reason, created_at, pay_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (resource, provider, amount_usd, status, reason, now, pay_to),
        )
        await db.commit()


async def weekly_status(now: datetime | None = None) -> dict:
    """Diagnostic (même doctrine que l'endpoint agent-wallet-ledger, #158/#159) --
    lisible pour vérifier le rythme de dépense sans avoir à lire la base directement."""
    spent = await spent_this_week(now)
    return {
        "cap_usd": WEEKLY_CAP_USD,
        "spent_usd": round(spent, 4),
        "remaining_usd": round(max(0.0, WEEKLY_CAP_USD - spent), 4),
        "week_started_at": week_start(now).isoformat(),
    }


async def list_spends(limit: int = 200) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM x402_spend_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]
