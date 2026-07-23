"""ARIA's release pipeline — marketing ammunition + automatic site sync.

Each already-built feature waits its turn for release (status built ->
announced -> live). ARIA announces (X/Telegram) and flips the status; the
SITE reads this pipeline and reflects the status automatically — the
showcase's roadmap stays in sync with announcements, no manual update.

Source: ``knowledge/release_pipeline.yaml`` (editable). Runtime status is
persisted in SQLite (``release_status``) to survive redeploys — the YAML
provides the content (title, pitch, blurb) and the INITIAL status; the
database keeps the transitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import aiosqlite
import yaml

from aria_core.paths import aria_db_path

_YAML_PATH = Path(__file__).resolve().parent / "knowledge" / "release_pipeline.yaml"
DB_PATH = str(aria_db_path())

_STATUSES = ("built", "announced", "live")

# OPERATOR LOCK (guardrail): the campaign is OUTWARD-FACING -> never autonomous.
# Nothing is released until the operator has ARMED the campaign (green light
# given ONLY when the product is perfect AND the roadmap is built). Default: dormant.
_ARM_KEY = "__campaign_armed__"


async def is_campaign_armed() -> bool:
    """Is the campaign armed by the operator? (default: no — everything stays dormant)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT status FROM release_status WHERE id = ?", (_ARM_KEY,))
        row = await cur.fetchone()
    return bool(row and row[0] == "armed")


async def arm_campaign(*, armed: bool = True) -> None:
    """Operator green light: arms (or disarms) the campaign. The ONLY action that authorizes release."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO release_status (id, status, announced_at) VALUES (?, ?, NULL) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status",
            (_ARM_KEY, "armed" if armed else "safe"),
        )
        await db.commit()


@dataclass(frozen=True)
class Release:
    id: str
    title: str
    status: str
    blurb: str
    pitch: str
    announced_at: str | None = None


@lru_cache(maxsize=1)
def _teasers() -> list[str]:
    if not _YAML_PATH.is_file():
        return []
    try:
        cfg: dict[str, Any] = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    return [str(t) for t in (cfg.get("teasers") or [])]


def list_teasers() -> list[str]:
    """The teaser posts (phase 0 FOMO). Content ready, release gated by the operator."""
    return list(_teasers())


async def next_teaser(*, index: int = 0) -> str | None:
    """The next teaser to release — ONLY if the campaign is armed (otherwise None)."""
    if not await is_campaign_armed():
        return None
    teasers = _teasers()
    if not teasers or index < 0 or index >= len(teasers):
        return None
    return teasers[index]


@lru_cache(maxsize=1)
def _manifest() -> list[dict]:
    if not _YAML_PATH.is_file():
        return []
    try:
        cfg: dict[str, Any] = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    return list(cfg.get("releases") or [])


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS release_status (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                announced_at TEXT
            )
            """
        )
        await db.commit()


async def _status_overrides() -> dict[str, dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, status, announced_at FROM release_status")
        rows = await cur.fetchall()
    return {r["id"]: dict(r) for r in rows}


async def list_releases() -> list[Release]:
    """All releases, with runtime status applied (DB > YAML). Manifest order."""
    overrides = await _status_overrides()
    out: list[Release] = []
    for item in _manifest():
        rid = str(item.get("id"))
        ov = overrides.get(rid) or {}
        out.append(Release(
            id=rid,
            title=str(item.get("title") or rid),
            status=str(ov.get("status") or item.get("status") or "built"),
            blurb=str(item.get("blurb") or ""),
            pitch=str(item.get("pitch") or ""),
            announced_at=ov.get("announced_at"),
        ))
    return out


async def public_releases() -> list[dict]:
    """Public view for the showcase: id, title, status, blurb (NOT the internal pitch)."""
    return [
        {"id": r.id, "title": r.title, "status": r.status, "blurb": r.blurb,
         "announced_at": r.announced_at}
        for r in await list_releases()
    ]


async def set_status(release_id: str, status: str) -> bool:
    """Flips a release's status (built/announced/live). Returns False if unknown."""
    if status not in _STATUSES:
        raise ValueError(f"invalid status: {status}")
    if release_id not in {str(i.get('id')) for i in _manifest()}:
        return False
    await _ensure_table()
    ts = datetime.now(timezone.utc).isoformat() if status == "announced" else None
    async with aiosqlite.connect(DB_PATH) as db:
        # announced_at set on first announcement; kept afterward.
        await db.execute(
            """
            INSERT INTO release_status (id, status, announced_at) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                announced_at=COALESCE(release_status.announced_at, excluded.announced_at)
            """,
            (release_id, status, ts),
        )
        await db.commit()
    return True


async def next_to_announce() -> Release | None:
    """Next ammunition to release (first 'built' in manifest order)."""
    for r in await list_releases():
        if r.status == "built":
            return r
    return None


async def announce_next() -> dict | None:
    """Flips the next 'built' to 'announced' and returns its ready-to-release pitch.

    This is the move ARIA makes during the campaign: she releases ONE piece of
    ammunition, publishes its pitch (X/Telegram), and the site will show it as
    'announced'. Returns None if exhausted.
    """
    nxt = await next_to_announce()
    if nxt is None:
        return None
    await set_status(nxt.id, "announced")
    return {"id": nxt.id, "title": nxt.title, "pitch": nxt.pitch}


# Release channels. X is wireable today; TikTok is a placed SEAM (video coming
# later, see marketing video task). A publisher = coroutine async(text, release)->bool.
_SITE_URL = "https://ariavanguardzhc.com"


async def publish_release(
    release_id: str | None = None,
    *,
    x_publisher=None,
    tiktok_publisher=None,
    go_live: bool = True,
) -> dict | None:
    """Releases ONE piece on X + TikTok AND syncs the site — in the SAME move.

    Anticipates the full campaign loop:
      1. picks the next 'built' ammunition (or the given ``release_id``);
      2. publishes its pitch on each configured channel (X, TikTok) —
         injectable publishers, best-effort (one failing channel doesn't
         cancel the others);
      3. flips the status -> the site (which reads this pipeline) automatically
         shows it as announced then 'live' (``go_live``). No manual site update.

    Returns {id, title, pitch, published_to:[...], status} or None if nothing left to release.
    TikTok with no configured publisher is simply listed as 'pending' (seam, never blocking).
    """
    # Operator lock: without the green light, nothing goes out (dormant).
    if not await is_campaign_armed():
        return {"blocked": "campagne non armée (feu vert opérateur requis)"}

    target = None
    if release_id:
        for r in await list_releases():
            if r.id == release_id:
                target = r
                break
    else:
        target = await next_to_announce()
    if target is None:
        return None

    link = f"{_SITE_URL}/#{target.id}"
    text = f"{target.pitch}\n\n{link}"

    published: list[str] = []
    pending: list[str] = []
    # X (wireable now: plug in the existing X publisher).
    if x_publisher is not None:
        try:
            if await x_publisher(text, target):
                published.append("x")
            else:
                # Explicit failure (False, not an exception) -- same fate as a
                # channel with no configured publisher: never silently absent
                # from both lists (#127).
                pending.append("x")
        except Exception:  # noqa: BLE001 — a crashing channel doesn't cancel the others
            pending.append("x")
    else:
        pending.append("x")
    # TikTok (seam: video generated later).
    if tiktok_publisher is not None:
        try:
            if await tiktok_publisher(text, target):
                published.append("tiktok")
            else:
                pending.append("tiktok")
        except Exception:  # noqa: BLE001
            pending.append("tiktok")
    else:
        pending.append("tiktok")

    # Site sync: announced, then live (the site reflects the status on next load).
    await set_status(target.id, "announced")
    if go_live:
        await set_status(target.id, "live")

    return {
        "id": target.id, "title": target.title, "pitch": target.pitch,
        "link": link, "published_to": published, "pending_channels": pending,
        "status": "live" if go_live else "announced",
    }
