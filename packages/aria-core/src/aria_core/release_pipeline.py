"""Pipeline de sorties d'ARIA — munitions marketing + synchro automatique du site.

Chaque feature déjà construite attend son tour de diffusion (statut built -> announced
-> live). ARIA annonce (X/Telegram) et bascule le statut ; le SITE lit ce pipeline et
reflète le statut automatiquement — la roadmap de la vitrine reste synchro avec les
annonces, sans mise à jour manuelle.

Source : ``knowledge/release_pipeline.yaml`` (éditable). Le statut runtime est persisté
en SQLite (``release_status``) pour survivre aux redéploiements — le YAML donne le
contenu (titre, pitch, blurb) et le statut INITIAL ; la base garde les transitions.
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

# VERROU OPÉRATEUR (dôme) : la campagne est OUTWARD-FACING -> jamais autonome. Rien
# n'est diffusé tant que l'opérateur n'a pas ARMÉ la campagne (feu vert donné SEULEMENT
# quand le produit est parfait ET la roadmap construite). Par défaut : dormant.
_ARM_KEY = "__campaign_armed__"


async def is_campaign_armed() -> bool:
    """La campagne est-elle armée par l'opérateur ? (défaut : non — tout reste dormant)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT status FROM release_status WHERE id = ?", (_ARM_KEY,))
        row = await cur.fetchone()
    return bool(row and row[0] == "armed")


async def arm_campaign(*, armed: bool = True) -> None:
    """Feu vert opérateur : arme (ou désarme) la campagne. SEUL geste qui autorise la diffusion."""
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
    """Les posts de teaser (phase 0 FOMO). Contenu prêt, diffusion gatée par l'opérateur."""
    return list(_teasers())


async def next_teaser(*, index: int = 0) -> str | None:
    """Le prochain teaser à diffuser — SEULEMENT si la campagne est armée (sinon None)."""
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
    """Toutes les sorties, statut runtime appliqué (base > YAML). Ordre du manifeste."""
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
    """Vue publique pour la vitrine : id, titre, statut, blurb (PAS le pitch interne)."""
    return [
        {"id": r.id, "title": r.title, "status": r.status, "blurb": r.blurb,
         "announced_at": r.announced_at}
        for r in await list_releases()
    ]


async def set_status(release_id: str, status: str) -> bool:
    """Bascule le statut d'une sortie (built/announced/live). Retourne False si inconnu."""
    if status not in _STATUSES:
        raise ValueError(f"statut invalide : {status}")
    if release_id not in {str(i.get('id')) for i in _manifest()}:
        return False
    await _ensure_table()
    ts = datetime.now(timezone.utc).isoformat() if status == "announced" else None
    async with aiosqlite.connect(DB_PATH) as db:
        # announced_at fixé à la première annonce ; conservé ensuite.
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
    """Prochaine munition à diffuser (première 'built' dans l'ordre du manifeste)."""
    for r in await list_releases():
        if r.status == "built":
            return r
    return None


async def announce_next() -> dict | None:
    """Bascule la prochaine 'built' en 'announced' et renvoie son pitch prêt à diffuser.

    C'est le geste que fait ARIA pendant la campagne : elle sort UNE munition, publie son
    pitch (X/Telegram), et le site la montrera comme 'announced'. Retourne None si épuisé.
    """
    nxt = await next_to_announce()
    if nxt is None:
        return None
    await set_status(nxt.id, "announced")
    return {"id": nxt.id, "title": nxt.title, "pitch": nxt.pitch}


# Canaux de diffusion. X est câblable aujourd'hui ; TikTok est un SEAM posé (vidéo à
# venir, cf. tâche vidéos marketing). Un publisher = coroutine async(text, release)->bool.
_SITE_URL = "https://ariavanguardzhc.com"


async def publish_release(
    release_id: str | None = None,
    *,
    x_publisher=None,
    tiktok_publisher=None,
    go_live: bool = True,
) -> dict | None:
    """Diffuse UNE sortie sur X + TikTok ET synchronise le site — dans le MÊME geste.

    Anticipe la boucle complète de campagne :
      1. choisit la prochaine munition 'built' (ou ``release_id`` donné) ;
      2. publie son pitch sur chaque canal configuré (X, TikTok) — publishers injectables,
         best-effort (un canal qui échoue n'annule pas les autres) ;
      3. bascule le statut -> le site (qui lit ce pipeline) l'affiche automatiquement
         comme annoncée puis 'live' (``go_live``). Aucune mise à jour manuelle du site.

    Retourne {id, title, pitch, published_to:[...], status} ou None si plus rien à sortir.
    TikTok sans publisher configuré est simplement listé comme 'pending' (seam, jamais bloquant).
    """
    # Verrou opérateur : sans feu vert, rien ne sort (dormant).
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
    # X (câblable maintenant : brancher le publisher X existant).
    if x_publisher is not None:
        try:
            if await x_publisher(text, target):
                published.append("x")
            else:
                # Echec explicite (False, pas d'exception) -- meme sort qu'un canal sans
                # publisher configure : jamais silencieusement absent des deux listes (#127).
                pending.append("x")
        except Exception:  # noqa: BLE001 — un canal qui plante n'annule pas les autres
            pending.append("x")
    else:
        pending.append("x")
    # TikTok (seam : vidéo générée plus tard).
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

    # Synchro site : annoncée, puis live (le site reflète le statut au prochain chargement).
    await set_status(target.id, "announced")
    if go_live:
        await set_status(target.id, "live")

    return {
        "id": target.id, "title": target.title, "pitch": target.pitch,
        "link": link, "published_to": published, "pending_channels": pending,
        "status": "live" if go_live else "announced",
    }
