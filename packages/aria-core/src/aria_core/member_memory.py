"""Long-term memory per Privy member on holding sites (Kikou, etc.)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aria_core.integrations.host_hooks import auth_db_path, init_auth_db
from aria_core.integrations.host_hooks import get_game_score

MEMBER_VISITOR_PREFIX = "member:"


def member_visitor_id(privy_did: str) -> str:
    return f"{MEMBER_VISITOR_PREFIX}{privy_did}"


@dataclass
class MemberProfile:
    privy_did: str
    handle: str
    visit_count: int
    first_seen: str
    last_seen: str
    facts: dict[str, Any]

    @property
    def is_returning(self) -> bool:
        return self.visit_count > 1


async def _ensure_table() -> None:
    await init_auth_db()
    async with aiosqlite.connect(str(auth_db_path())) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS member_profiles (
                privy_did TEXT PRIMARY KEY,
                handle TEXT NOT NULL DEFAULT 'member',
                visit_count INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                facts_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        await db.commit()


def _parse_facts(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


async def touch_member(
    *,
    privy_did: str,
    handle: str,
    site_slug: str,
) -> MemberProfile:
    """Record a visit and return the updated profile."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    clean_handle = (handle or "member").lstrip("@")

    async with aiosqlite.connect(str(auth_db_path())) as db:
        cursor = await db.execute(
            "SELECT visit_count, first_seen, facts_json FROM member_profiles WHERE privy_did = ?",
            (privy_did,),
        )
        row = await cursor.fetchone()

        if row:
            visit_count = int(row[0]) + 1
            first_seen = str(row[1])
            facts = _parse_facts(row[2])
            facts["last_site"] = site_slug
            facts["last_touch"] = now
            await db.execute(
                """
                UPDATE member_profiles
                SET handle = ?, visit_count = ?, last_seen = ?, facts_json = ?
                WHERE privy_did = ?
                """,
                (clean_handle, visit_count, now, json.dumps(facts), privy_did),
            )
        else:
            visit_count = 1
            first_seen = now
            facts = {"last_site": site_slug, "last_touch": now, "first_site": site_slug}
            await db.execute(
                """
                INSERT INTO member_profiles
                (privy_did, handle, visit_count, first_seen, last_seen, facts_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (privy_did, clean_handle, visit_count, first_seen, now, json.dumps(facts)),
            )
        await db.commit()

    return MemberProfile(
        privy_did=privy_did,
        handle=clean_handle,
        visit_count=visit_count,
        first_seen=first_seen,
        last_seen=now,
        facts=facts,
    )


async def get_member_profile(privy_did: str) -> MemberProfile | None:
    await _ensure_table()
    async with aiosqlite.connect(str(auth_db_path())) as db:
        cursor = await db.execute(
            """
            SELECT handle, visit_count, first_seen, last_seen, facts_json
            FROM member_profiles WHERE privy_did = ?
            """,
            (privy_did,),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return MemberProfile(
        privy_did=privy_did,
        handle=str(row[0]),
        visit_count=int(row[1]),
        first_seen=str(row[2]),
        last_seen=str(row[3]),
        facts=_parse_facts(row[4]),
    )


async def remember_fact(privy_did: str, key: str, value: Any) -> None:
    await _ensure_table()
    profile = await get_member_profile(privy_did)
    facts = profile.facts if profile else {}
    facts[key] = value
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(str(auth_db_path())) as db:
        if profile:
            await db.execute(
                "UPDATE member_profiles SET facts_json = ?, last_seen = ? WHERE privy_did = ?",
                (json.dumps(facts), now, privy_did),
            )
        else:
            await db.execute(
                """
                INSERT INTO member_profiles
                (privy_did, handle, visit_count, first_seen, last_seen, facts_json)
                VALUES (?, 'member', 0, ?, ?, ?)
                """,
                (privy_did, now, now, json.dumps(facts)),
            )
        await db.commit()


async def build_member_greeting(
    profile: MemberProfile,
    *,
    site_slug: str,
    game_id: str | None = None,
) -> str:
    """Contextual French greeting for holding sites — no LLM required."""
    handle = profile.handle
    score: int | None = None
    if game_id:
        score = await get_game_score(privy_did=profile.privy_did, site_slug=site_slug, game_id=game_id)

    if profile.visit_count <= 1:
        base = (
            f"Salut @{handle} — je suis Aria, ta CAO sur {site_slug}. "
            "Contente de te voir en chair et en pixels pour la première fois."
        )
    else:
        base = (
            f"Re-bonjour @{handle} — visite n°{profile.visit_count} sur {site_slug}. "
            "Je me souviens de toi."
        )

    if score is not None and score > 0:
        base += f" Ton record {game_id or 'jeu'} : {score} points."
    elif game_id:
        base += f" Pas encore de score {game_id} enregistré — c'est le moment."

    nickname = profile.facts.get("nickname")
    if isinstance(nickname, str) and nickname.strip():
        base += f" (Je t'appelle {nickname.strip()}.)"

    return base