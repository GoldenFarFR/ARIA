"""Output language preference for VC analyses — persisted locally.

`/vc` is admin-gated: this is the language the operator wants to receive the
analysis in (to relay it to an FR or EN client). Chosen once via `/langue`,
remembered across restarts (key/value table in `aria.db`). Default: French.
No financial action — a simple display setting.
"""
from __future__ import annotations

import aiosqlite

from aria_core.locale import LANG_FR
from aria_core.paths import aria_db_path
from aria_core.skills.vc_i18n import SUPPORTED_VC_LANGS, norm_lang

_KEY = "vc_output_lang"
_DEFAULT = LANG_FR


async def _ensure_table() -> None:
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS aria_setting ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        await db.commit()


async def get_output_lang() -> str:
    """Remembered output language (FR by default, never an error)."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(str(aria_db_path())) as db:
            async with db.execute(
                "SELECT value FROM aria_setting WHERE key = ?", (_KEY,)
            ) as cur:
                row = await cur.fetchone()
    except Exception:  # noqa: BLE001 — a setting must never break the flow
        return _DEFAULT
    if row and row[0] in SUPPORTED_VC_LANGS:
        return row[0]
    return _DEFAULT


async def set_output_lang(lang: str) -> str:
    """Records the output language. Returns the effective normalized language.

    Raises ``ValueError`` if the language isn't supported (for a clear
    message to the user; the caller handles the error).
    """
    value = (lang or "").strip().lower()
    if value not in SUPPORTED_VC_LANGS:
        raise ValueError(f"langue non supportée : {value!r}")
    await _ensure_table()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            "INSERT INTO aria_setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_KEY, norm_lang(value)),
        )
        await db.commit()
    return norm_lang(value)
