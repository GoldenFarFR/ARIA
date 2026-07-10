"""Canal de directives ARIA -> Claude Code (pilote, audit du 10/07).

ARIA (la tete) depose des directives priorisees dans une file ; une session Claude
Code (cote VPS, lancee par un humain) les lit et les execute. Ce module N'EXECUTE
RIEN et n'ecrit rien a l'exterieur (GitHub/X/email) : c'est une file locale SQLite
plus un journal d'audit inviolable.

Bordage volontaire (lecons de l'incident Cursor/worker-queue, 10/07) :
  - **Perimetre en dur** : ``_DIRECTIVE_CATEGORIES`` limite les directives a la seule
    famille deja deleguee (hygiene repo, docs, backlog). Toute categorie hors liste
    est REFUSEE a l'ecriture. Elargir la liste = un changement de code delibere,
    verrouille par ``test_coherence`` (jamais un glissement silencieux).
  - **Gate OFF par defaut** : ``ARIA_DIRECTIVE_CHANNEL_ENABLED`` ferme la porte cote
    producteur (aucune directive n'entre) tant qu'il n'est pas pose. Fail-closed.
  - **Coupe-circuit dedie** : ``halt_channel()`` pose un marqueur (distinct du /stop
    Telegram et de ``outgoing_pause``) ; le lecteur s'arrete AVANT chaque directive.
  - **Journal append-only** : la table ``aria_directive_log`` ne recoit QUE des INSERT
    (aucune fonction UPDATE/DELETE n'existe dans ce fichier) -> trace consultable meme
    sans validation prealable.

Deux frontieres que ce canal ne franchit JAMAIS (ni maintenant ni une fois elargi) :
capital reel (la validation Telegram d'ARIA reste etanche, hors allowlist pour
toujours) et modification du canal lui-meme ou de ses garde-fous (sinon ARIA pourrait
s'auto-elargir les pouvoirs -- la faille exacte de l'incident Cursor).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path, data_dir

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Perimetre AUTORISE du pilote -- exactement la famille deja deleguee a Claude Code
# ("GitHub propre, automatise et coherent"). Verrouille par test_coherence : l'elargir
# exige un changement de code delibere dans le meme commit.
_DIRECTIVE_CATEGORIES = frozenset({"repo_hygiene", "docs", "backlog"})

_HALT_MARKER = "aria_directive_halt"

_TRUTHY = ("1", "true", "yes", "on")


def channel_enabled() -> bool:
    """Gate producteur OFF par defaut : sans ce flag, aucune directive n'entre dans la file."""
    return os.environ.get("ARIA_DIRECTIVE_CHANNEL_ENABLED", "").strip().lower() in _TRUTHY


def _halt_path():
    return data_dir() / _HALT_MARKER


def is_halted() -> bool:
    """Coupe-circuit dedie (marqueur fichier), independant du /stop Telegram."""
    return _halt_path().exists()


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS aria_directive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                proposed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # Journal append-only : uniquement des INSERT, jamais d'UPDATE/DELETE (aucune
        # fonction de ce module ne les touche -- verrouille par test_coherence).
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS aria_directive_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                directive_id INTEGER,
                actor TEXT NOT NULL,
                event TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def _log(db, *, directive_id: int | None, actor: str, event: str, detail: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO aria_directive_log (directive_id, actor, event, detail, at) "
        "VALUES (?, ?, ?, ?, ?)",
        (directive_id, actor, event, detail[:2000], now),
    )


async def propose_directive(category: str, title: str, detail: str = "") -> dict:
    """Cote PRODUCTEUR (ARIA) : depose une directive. N'execute rien.

    Refuse (sans ecrire dans la file) si le canal est OFF, si le coupe-circuit est
    actif, ou si la categorie est hors du perimetre autorise. Un refus est tout de
    meme journalise (trace de la tentative).
    """
    category = (category or "").strip().lower()
    title = (title or "").strip()
    detail = (detail or "").strip()
    await _ensure_tables()

    if not channel_enabled():
        return {"ok": False, "reason": "canal desactive (ARIA_DIRECTIVE_CHANNEL_ENABLED off)"}
    if is_halted():
        return {"ok": False, "reason": "coupe-circuit actif"}
    if category not in _DIRECTIVE_CATEGORIES:
        async with aiosqlite.connect(DB_PATH) as db:
            await _log(
                db, directive_id=None, actor="aria", event="refused",
                detail=f"categorie hors perimetre: {category!r}",
            )
            await db.commit()
        return {"ok": False, "reason": f"categorie '{category}' hors perimetre autorise"}
    if not title:
        return {"ok": False, "reason": "titre vide"}

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO aria_directive (category, title, detail, status, proposed_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (category, title, detail, now, now),
        )
        directive_id = cur.lastrowid
        await _log(db, directive_id=directive_id, actor="aria", event="proposed", detail=title)
        await db.commit()
    logger.info("aria_directives: directive #%s proposee (%s) %s", directive_id, category, title)
    return {"ok": True, "id": directive_id, "category": category, "title": title}


async def list_directives(status: str | None = None, limit: int = 100) -> list[dict]:
    """Liste les directives (toutes, ou filtrees par statut). Lecture seule."""
    await _ensure_tables()
    cols = ["id", "category", "title", "detail", "status", "proposed_at", "updated_at", "outcome"]
    query = f"SELECT {', '.join(cols)} FROM aria_directive"
    params: tuple = ()
    if status:
        query += " WHERE status=?"
        params = (status,)
    query += " ORDER BY id ASC LIMIT ?"
    params = params + (limit,)
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(query, params)).fetchall()
    return [dict(zip(cols, row)) for row in rows]


async def claim_next_directive() -> dict | None:
    """Cote LECTEUR (session Claude Code sur le VPS) : reserve la plus ancienne directive
    'pending' et la passe en 'executing'.

    Renvoie None si le canal est OFF, si le coupe-circuit est actif, ou si la file est
    vide -- le lecteur s'arrete AVANT toute action. Le classifieur de securite de la
    session reste la derniere ligne de defense sur l'execution reelle.
    """
    await _ensure_tables()
    if not channel_enabled() or is_halted():
        return None
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT id, category, title, detail FROM aria_directive "
                "WHERE status='pending' ORDER BY id ASC LIMIT 1"
            )
        ).fetchone()
        if row is None:
            return None
        directive_id = row[0]
        await db.execute(
            "UPDATE aria_directive SET status='executing', updated_at=? WHERE id=?",
            (now, directive_id),
        )
        await _log(db, directive_id=directive_id, actor="claude", event="claimed", detail=row[2])
        await db.commit()
    return {"id": row[0], "category": row[1], "title": row[2], "detail": row[3]}


async def complete_directive(directive_id: int, outcome: str = "") -> dict:
    """Cote LECTEUR : marque une directive comme executee, avec le compte-rendu."""
    await _ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE aria_directive SET status='done', updated_at=?, outcome=? WHERE id=?",
            (now, (outcome or "").strip()[:2000], directive_id),
        )
        await _log(db, directive_id=directive_id, actor="claude", event="executed", detail=outcome)
        await db.commit()
    return {"ok": True, "id": directive_id, "status": "done"}


async def refuse_directive(directive_id: int, reason: str = "") -> dict:
    """Cote LECTEUR : refuse une directive (hors perimetre juge, ambigue, risquee)."""
    await _ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE aria_directive SET status='refused', updated_at=?, outcome=? WHERE id=?",
            (now, (reason or "").strip()[:2000], directive_id),
        )
        await _log(db, directive_id=directive_id, actor="claude", event="refused", detail=reason)
        await db.commit()
    return {"ok": True, "id": directive_id, "status": "refused"}


async def read_log(limit: int = 200) -> list[dict]:
    """Lit le journal d'audit (append-only), du plus recent au plus ancien. Lecture seule."""
    await _ensure_tables()
    cols = ["id", "directive_id", "actor", "event", "detail", "at"]
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                f"SELECT {', '.join(cols)} FROM aria_directive_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(cols, row)) for row in rows]


async def halt_channel(reason: str = "") -> dict:
    """Coupe-circuit : fige le canal (pose le marqueur). Journalise l'arret."""
    await _ensure_tables()
    _halt_path().write_text(
        (reason or "halt").strip()[:500], encoding="utf-8"
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await _log(db, directive_id=None, actor="operator", event="halted", detail=reason)
        await db.commit()
    logger.warning("aria_directives: CANAL FIGE (%s)", reason or "sans raison")
    return {"ok": True, "halted": True}


async def resume_channel() -> dict:
    """Leve le coupe-circuit (retire le marqueur). Journalise la reprise."""
    await _ensure_tables()
    path = _halt_path()
    if path.exists():
        path.unlink()
    async with aiosqlite.connect(DB_PATH) as db:
        await _log(db, directive_id=None, actor="operator", event="resumed")
        await db.commit()
    return {"ok": True, "halted": False}
