"""Pool de tokens « screenés » — le vivier dans lequel la boucle tire ses 20.

Un token qui **passe le filtre** (`skills/safety_screen.py`) entre ici. Chaque
lundi, la boucle d'entraînement tire **20 candidats au sort** dans le pool actif
(loterie) → échantillon **non biaisé** (pas de cherry-pick) ET **screené** (pas un
scam technique). Un token peut être re-vérifié et **retiré** (`dropped`) s'il se
dégrade (liquidité qui fuit, LP délocké) — un contrat propre aujourd'hui peut ne
plus l'être demain.

Stockage local SQLite `aria.db`, table `screened_token` (clé = contrat).
Aucune action financière : c'est un annuaire de candidats.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_COLUMNS = [
    "contract",
    "symbol",
    "liquidity_usd",
    "security_score",
    "top_holder_pct",
    "verdict",
    "pool_address",
    "network",
    "status",
    "first_screened_at",
    "last_checked_at",
    "screen_reason",
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS screened_token (
                contract TEXT PRIMARY KEY,
                symbol TEXT,
                liquidity_usd REAL,
                security_score INTEGER,
                top_holder_pct REAL,
                verdict TEXT,
                pool_address TEXT,
                network TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                first_screened_at TEXT NOT NULL,
                last_checked_at TEXT NOT NULL,
                screen_reason TEXT
            )
            """
        )
        await db.commit()


async def upsert_screened(
    *,
    contract: str,
    symbol: str = "",
    liquidity_usd: float = 0.0,
    security_score: int = 0,
    top_holder_pct: float | None = None,
    verdict: str = "",
    pool_address: str = "",
    network: str = "base",
    screen_reason: str = "",
) -> None:
    """Ajoute/rafraîchit un token screené (status ``active``).

    Upsert : ``first_screened_at`` est préservé au ré-enregistrement (on garde la
    date de première entrée), ``last_checked_at`` est toujours mis à jour. Ré-activer
    (`active`) un token qui repasse le filtre est volontaire.
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO screened_token
              (contract, symbol, liquidity_usd, security_score, top_holder_pct,
               verdict, pool_address, network, status, first_screened_at,
               last_checked_at, screen_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            ON CONFLICT(contract) DO UPDATE SET
              symbol=excluded.symbol,
              liquidity_usd=excluded.liquidity_usd,
              security_score=excluded.security_score,
              top_holder_pct=excluded.top_holder_pct,
              verdict=excluded.verdict,
              pool_address=excluded.pool_address,
              network=excluded.network,
              status='active',
              last_checked_at=excluded.last_checked_at,
              screen_reason=excluded.screen_reason
            """,
            (
                contract, symbol, liquidity_usd, security_score, top_holder_pct,
                verdict, pool_address, network, now, now, screen_reason,
            ),
        )
        await db.commit()


async def record_rejected(
    *, contract: str, reason: str = "", symbol: str = "", network: str = "base"
) -> None:
    """Marque un contrat comme rejeté (« jeté pour toujours »), avec sa raison.

    On le garde EN BASE (status ``rejected``) plutôt que de l'ignorer : ça évite de
    le re-scanner sans fin (intransigeance = efficace), et ça permet une
    **résurrection** ciblée si un bruit réapparaît (cf. ``reconsider``). Upsert :
    ``first_screened_at`` préservé.
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO screened_token
              (contract, symbol, liquidity_usd, security_score, top_holder_pct,
               verdict, pool_address, network, status, first_screened_at,
               last_checked_at, screen_reason)
            VALUES (?, ?, 0, 0, NULL, '', '', ?, 'rejected', ?, ?, ?)
            ON CONFLICT(contract) DO UPDATE SET
              status='rejected', last_checked_at=excluded.last_checked_at,
              screen_reason=excluded.screen_reason
            """,
            (contract, symbol, network, now, now, reason),
        )
        await db.commit()


async def record_pending(
    *, contract: str, reason: str = "", symbol: str = "", network: str = "base"
) -> None:
    """Marque un contrat comme « à revoir » (échec MOU, donnée indisponible), avec sa
    raison — jamais un rejet définitif.

    Contrairement à ``record_rejected``, ``status='pending'`` NE court-circuite PAS le
    re-scan (``get_status`` ne bloque que sur 'rejected'/'active') : le contrat sera
    retenté au prochain cycle. Objectif : que la raison d'un échec mou (holders non
    renvoyés, contrat non vérifié, etc.) laisse une trace consultable plutôt que de
    disparaître sans aucune donnée, en base ou ailleurs (cf. audit #77).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO screened_token
              (contract, symbol, liquidity_usd, security_score, top_holder_pct,
               verdict, pool_address, network, status, first_screened_at,
               last_checked_at, screen_reason)
            VALUES (?, ?, 0, 0, NULL, '', '', ?, 'pending', ?, ?, ?)
            ON CONFLICT(contract) DO UPDATE SET
              status='pending', last_checked_at=excluded.last_checked_at,
              screen_reason=excluded.screen_reason
            """,
            (contract, symbol, network, now, now, reason),
        )
        await db.commit()


async def get_status(contract: str) -> str | None:
    """Statut connu d'un contrat (active / rejected / dropped), ou None si jamais vu."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute("SELECT status FROM screened_token WHERE contract=?", (contract,))
        ).fetchone()
    return row[0] if row else None


async def reconsider(contract: str) -> bool:
    """Un bruit a réapparu : rouvre un rejeté pour réévaluation. True si applicable.

    Ne fait que LEVER le « jeté pour toujours » (statut -> pending) ; la vraie
    décision revient au re-scan on-chain (le bruit filtre/réveille, il ne décide pas).
    Retourne False si le contrat est inconnu ou déjà actif.
    """
    status = await get_status(contract)
    if status not in ("rejected", "dropped"):
        return False
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE screened_token SET status='pending', last_checked_at=? WHERE contract=?",
            (now, contract),
        )
        await db.commit()
    return True


async def drop_token(contract: str, *, reason: str = "") -> None:
    """Retire un token du pool actif (dégradé). Reste en base (status ``dropped``)."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE screened_token SET status='dropped', last_checked_at=? WHERE contract=?",
            (now, contract),
        )
        await db.commit()


async def list_stale_pending(
    *, older_than_hours: int = 24, limit: int = 20, network: str = "base"
) -> list[dict]:
    """Candidats ``pending`` dont le dernier check date d'au moins ``older_than_hours``.

    'pending' == échec MOU (donnée pas encore mûre : contrat pas encore vérifié,
    holders pas encore lisibles, liquidité pas encore montée...) — jamais un rejet
    définitif (cf. ``record_pending``), mais rien ne le retente PROACTIVEMENT
    aujourd'hui : seule une redécouverte fortuite (même contrat qui réapparaît dans
    ``discover_top_pools``/``discover_direct_candidates``) le fait rescanner. Cette
    liste sert de file d'attente pour un retry délibéré (cf.
    ``base_crawler.retry_stale_pending``), pas un nouveau mécanisme de filtrage —
    ``token_absorber.absorb`` (déjà appelé sans court-circuit sur 'pending') fait
    tout le travail de réévaluation.
    """
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM screened_token WHERE status='pending' AND network=? "
                "AND last_checked_at <= ? ORDER BY last_checked_at ASC LIMIT ?",
                (network, cutoff, limit),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def list_pool(status: str = "active", limit: int = 1000, *, network: str = "base") -> list[dict]:
    """``network="base"`` par défaut préserve EXACTEMENT le comportement historique
    (le pool VC 85% n'a jamais écrit autre chose que ``network="base"``). Le pool
    bonding (niche 15%, cf. ``bonding_absorber.py``) vit sous ``network="base-bonding"``
    — jamais mélangé sans un appel explicite, pour ne pas contaminer le tirage
    hebdomadaire (``weekly_training.draw_lottery`` reste 100% pool VC, inchangé)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM screened_token WHERE status=? AND network=? "
                "ORDER BY last_checked_at DESC LIMIT ?",
                (status, network, limit),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def count_pool(status: str = "active", *, network: str = "base") -> int:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM screened_token WHERE status=? AND network=?",
                (status, network),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def draw_lottery(n: int = 20, *, status: str = "active", network: str = "base") -> list[dict]:
    """Tire ``n`` tokens AU SORT dans le pool actif (échantillon non biaisé).

    Si le pool contient moins de ``n`` tokens, retourne tout le pool (mélangé).
    Le tirage aléatoire est ce qui empêche le cherry-pick : ARIA ne choisit pas
    « ceux qui l'arrangent », le hasard décide dans un vivier déjà screené.
    """
    pool = await list_pool(status=status, limit=100_000, network=network)
    if n <= 0 or not pool:
        return []
    if len(pool) <= n:
        random.shuffle(pool)
        return pool
    return random.sample(pool, n)
