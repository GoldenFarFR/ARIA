"""Préférence de langue de sortie des analyses VC — persistée localement.

`/vc` est admin-gated : il s'agit de la langue dans laquelle l'opérateur veut
recevoir l'analyse (pour la relayer à un client FR ou EN). Choisie une fois via
`/langue`, mémorisée entre les redémarrages (table clé/valeur dans `aria.db`).
Défaut : français. Aucune action financière — simple réglage d'affichage.
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
    """Langue de sortie mémorisée (FR par défaut, jamais d'erreur)."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(str(aria_db_path())) as db:
            async with db.execute(
                "SELECT value FROM aria_setting WHERE key = ?", (_KEY,)
            ) as cur:
                row = await cur.fetchone()
    except Exception:  # noqa: BLE001 — un réglage ne doit jamais casser le flux
        return _DEFAULT
    if row and row[0] in SUPPORTED_VC_LANGS:
        return row[0]
    return _DEFAULT


async def set_output_lang(lang: str) -> str:
    """Enregistre la langue de sortie. Retourne la langue normalisée effective.

    Lève ``ValueError`` si la langue n'est pas supportée (pour un message clair
    à l'utilisateur ; le caller gère l'erreur).
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
