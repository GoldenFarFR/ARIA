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
    "retry_count",
    "source",
]

# Colonnes ajoutées après coup : (nom, définition SQL) pour la migration ALTER
# (même patron que `vc_predictions.py`/`exam.py` — SQLite ne les crée pas sur une
# table préexistante, seulement `CREATE TABLE IF NOT EXISTS`).
_ADDED_COLUMNS = [
    ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
    # Pipeline de découverte d'origine ('top_pools' / 'radar_x' / ...) : chaîne vide
    # sur les lignes historiques (jamais NULL, jamais un rejet opaque). Suite audit
    # #77 diversification (12/07) : sans ça, impossible de mesurer objectivement quel
    # pipeline contribue le bruit (échecs durs) vs le signal.
    ("source", "TEXT NOT NULL DEFAULT ''"),
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
                screen_reason TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # Migration à chaud : ajoute les colonnes manquantes aux DB existantes
        # (SQLite ne les crée pas si la table préexiste). Idempotent, non destructif.
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(screened_token)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE screened_token ADD COLUMN {name} {ddl}")
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
    source: str = "",
) -> None:
    """Ajoute/rafraîchit un token screené (status ``active``).

    Upsert : ``first_screened_at`` est préservé au ré-enregistrement (on garde la
    date de première entrée), ``last_checked_at`` est toujours mis à jour. Ré-activer
    (`active`) un token qui repasse le filtre est volontaire. ``retry_count`` est
    remis à zéro : une fois actif, le compteur de tentatives « pending » n'a plus
    de sens — s'il redégrade plus tard, il repart sur un budget de tentatives frais.
    ``source`` (optionnel, ex. ``'top_pools'``/``'radar_x'``) : pipeline de découverte
    d'origine, préservé au ré-enregistrement comme ``first_screened_at`` (n'écrase pas
    une source déjà connue si l'appelant ne la précise pas).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO screened_token
              (contract, symbol, liquidity_usd, security_score, top_holder_pct,
               verdict, pool_address, network, status, first_screened_at,
               last_checked_at, screen_reason, retry_count, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, 0, ?)
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
              screen_reason=excluded.screen_reason,
              retry_count=0,
              source=CASE WHEN excluded.source != '' THEN excluded.source ELSE screened_token.source END
            """,
            (
                contract, symbol, liquidity_usd, security_score, top_holder_pct,
                verdict, pool_address, network, now, now, screen_reason, source,
            ),
        )
        await db.commit()


async def record_rejected(
    *, contract: str, reason: str = "", symbol: str = "", network: str = "base",
    source: str = "", liquidity_usd: float = 0.0, security_score: int = 0,
    verdict: str = "", top_holder_pct: float | None = None,
) -> None:
    """Marque un contrat comme rejeté (« jeté pour toujours »), avec sa raison.

    On le garde EN BASE (status ``rejected``) plutôt que de l'ignorer : ça évite de
    le re-scanner sans fin (intransigeance = efficace), et ça permet une
    **résurrection** ciblée si un bruit réapparaît (cf. ``reconsider``). Upsert :
    ``first_screened_at`` préservé. ``source`` : même logique que ``upsert_screened``
    (préservé si non précisé au ré-enregistrement).

    ``liquidity_usd``/``security_score``/``verdict``/``top_holder_pct`` (optionnels,
    15/07, même correctif que ``record_pending``) : transmettre les vraies valeurs du
    scan quand l'appelant les a déjà (rejet APRÈS un scan complet), plutôt que les
    laisser à 0/''/NULL — sinon un rejet dur (honeypot, score catastrophique) est
    indiscernable d'un rejet dont on n'a jamais su le score. Défauts préservés pour
    l'appelant sans scan.
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO screened_token
              (contract, symbol, liquidity_usd, security_score, top_holder_pct,
               verdict, pool_address, network, status, first_screened_at,
               last_checked_at, screen_reason, source)
            VALUES (?, ?, ?, ?, ?, ?, '', ?, 'rejected', ?, ?, ?, ?)
            ON CONFLICT(contract) DO UPDATE SET
              status='rejected', last_checked_at=excluded.last_checked_at,
              screen_reason=excluded.screen_reason,
              liquidity_usd=excluded.liquidity_usd,
              security_score=excluded.security_score,
              verdict=excluded.verdict,
              top_holder_pct=excluded.top_holder_pct,
              source=CASE WHEN excluded.source != '' THEN excluded.source ELSE screened_token.source END
            """,
            (
                contract, symbol, liquidity_usd, security_score, top_holder_pct,
                verdict, network, now, now, reason, source,
            ),
        )
        await db.commit()


async def record_pending(
    *, contract: str, reason: str = "", symbol: str = "", network: str = "base",
    source: str = "", liquidity_usd: float = 0.0, security_score: int = 0,
    verdict: str = "", top_holder_pct: float | None = None,
) -> None:
    """Marque un contrat comme « à revoir » (échec MOU, donnée indisponible), avec sa
    raison — jamais un rejet définitif.

    Contrairement à ``record_rejected``, ``status='pending'`` NE court-circuite PAS le
    re-scan (``get_status`` ne bloque que sur 'rejected'/'active') : le contrat sera
    retenté au prochain cycle. Objectif : que la raison d'un échec mou (holders non
    renvoyés, contrat non vérifié, etc.) laisse une trace consultable plutôt que de
    disparaître sans aucune donnée, en base ou ailleurs (cf. audit #77).

    ``liquidity_usd``/``security_score``/``verdict`` (optionnels, 15/07) : quand
    l'appelant a déjà un scan complet en main (échec mou APRÈS le scan, ex.
    ``token_absorber.absorb`` sur holders inconnus), transmettre les vraies valeurs
    calculées plutôt que de les laisser à 0 — avant ce correctif, un candidat pending
    prometteur (score/liquidité corrects, juste une donnée annexe manquante) était
    indiscernable d'un candidat pending sans aucun signal, empêchant tout classement
    par proximité du seuil (cf. ``list_closest_to_passing``). Défaut 0/'' préservé
    pour l'appelant qui n'a PAS encore de scan (ex. pré-filtre Volet C) — jamais une
    donnée inventée.

    ``retry_count`` s'incrémente à chaque appel (1 au premier échec mou, +1 à chaque
    repassage — que ce soit une redécouverte fortuite ou un retry délibéré, même
    fonction pour les deux, cf. ``token_absorber.absorb``) : c'est ce compteur que
    ``abandon_stale_pending`` lit pour arrêter d'insister sur un signal qui ne mûrit
    jamais (cf. suite audit #77/#105). ``source`` : même logique que
    ``upsert_screened`` (préservé si non précisé au ré-enregistrement).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO screened_token
              (contract, symbol, liquidity_usd, security_score, top_holder_pct,
               verdict, pool_address, network, status, first_screened_at,
               last_checked_at, screen_reason, retry_count, source)
            VALUES (?, ?, ?, ?, ?, ?, '', ?, 'pending', ?, ?, ?, 1, ?)
            ON CONFLICT(contract) DO UPDATE SET
              status='pending', last_checked_at=excluded.last_checked_at,
              screen_reason=excluded.screen_reason,
              liquidity_usd=excluded.liquidity_usd,
              security_score=excluded.security_score,
              verdict=excluded.verdict,
              top_holder_pct=excluded.top_holder_pct,
              retry_count=screened_token.retry_count + 1,
              source=CASE WHEN excluded.source != '' THEN excluded.source ELSE screened_token.source END
            """,
            (
                contract, symbol, liquidity_usd, security_score, top_holder_pct,
                verdict, network, now, now, reason, source,
            ),
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
    Retourne False si le contrat est inconnu ou déjà actif. ``retry_count`` repart à
    zéro : un signal externe qui justifie la résurrection mérite un budget de
    tentatives frais, pas la suite d'un compteur d'une vie précédente (y compris pour
    un contrat déjà abandonné par ``abandon_stale_pending``).
    """
    status = await get_status(contract)
    if status not in ("rejected", "dropped"):
        return False
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE screened_token SET status='pending', last_checked_at=?, "
            "retry_count=0 WHERE contract=?",
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


async def abandon_stale_pending(
    contract: str, *, max_retries: int = 5, max_age_days: int = 7
) -> bool:
    """Bascule un ``pending`` qui n'en finit plus vers un état terminal (``rejected``).

    Un candidat en échec MOU indéfiniment (jamais actif, jamais un vrai
    ``hard_fail`` malveillant confirmé) resterait sinon ``pending`` pour toujours :
    retenté à chaque cycle ``retry_stale_pending`` (audit #77), un scan API toutes
    les 24h sans fin pour un signal qui ne mûrit jamais. **Ce n'est PAS un nouveau
    critère de sécurité** — aucun filtre dupliqué, ``safety_screen``/
    ``token_absorber`` inchangés, le seuil `passed` reste identique — uniquement
    une limite sur le NOMBRE DE PASSAGES : au-delà de ``max_retries`` tentatives
    OU ``max_age_days`` jours depuis ``first_screened_at``, on arrête d'insister et
    on classe définitivement, en gardant la dernière raison molle connue en trace
    (jamais une case vide, même doctrine que ``record_pending``/``record_rejected``).

    Retourne False (no-op) si le contrat est inconnu, n'est plus ``pending``, ou n'a
    pas encore dépassé les seuils — appelé par ``base_crawler.retry_stale_pending``
    uniquement après un nouvel échec mou confirmé (``token_absorber.absorb`` a déjà
    tranché : encore MOU, ni mûri en ``active`` ni un vrai rejet malveillant).
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT status, first_screened_at, retry_count, screen_reason "
                "FROM screened_token WHERE contract=?",
                (contract,),
            )
        ).fetchone()
        if row is None or row[0] != "pending":
            return False
        _status, first_screened_at, retry_count, last_reason = row
        age_days = (
            datetime.now(timezone.utc) - datetime.fromisoformat(first_screened_at)
        ).total_seconds() / 86_400
        if retry_count < max_retries and age_days < max_age_days:
            return False
        now = datetime.now(timezone.utc).isoformat()
        reason = (
            f"abandonné après {retry_count} tentatives ({age_days:.1f}j) — signal "
            f"faible persistant : {last_reason or 'raison indisponible'}"
        )
        await db.execute(
            "UPDATE screened_token SET status='rejected', last_checked_at=?, "
            "screen_reason=? WHERE contract=?",
            (now, reason, contract),
        )
        await db.commit()
    return True


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


_LIQUIDITY_TARGET_USD = 30_000.0


async def list_closest_to_passing(*, network: str = "base", limit: int = 3) -> list[dict]:
    """Classe les candidats ``pending`` par proximité du seuil de sécurité — de vrais
    points d'entrée à surveiller plutôt qu'un simple comptage binaire actif/pas actif
    (demande opérateur 14/07, cf. CLAUDE.md). Heuristique informationnelle, pas un
    score officiel : score de sécurité le plus haut d'abord (le plus proche de passer
    le seuil ``safety_screen`` par en-dessous), puis liquidité la plus proche de
    30 000$ (le plancher usuel) en cas d'égalité. Une valeur manquante (``None``) est
    reléguée en fin de classement plutôt que de fausser le tri.
    """
    pool = await list_pool(status="pending", limit=100_000, network=network)

    def _rank(entry: dict) -> tuple[float, float]:
        score = entry.get("security_score")
        score_component = -float(score) if isinstance(score, (int, float)) else 0.0
        liquidity = entry.get("liquidity_usd")
        liquidity_gap = (
            abs(_LIQUIDITY_TARGET_USD - float(liquidity))
            if isinstance(liquidity, (int, float))
            else float("inf")
        )
        return (score_component, liquidity_gap)

    return sorted(pool, key=_rank)[:limit]
