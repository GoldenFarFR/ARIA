"""X handle registry — short aliases, operator persistence, no manual rewriting."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from aria_core.paths import data_dir

REGISTRY_PATH = data_dir() / "x_handle_registry.json"

# Built-in aliases — type @holding @veille instead of the full handles
_BUILTIN_ALIASES: dict[str, list[str]] = {
    "holding": ["GoldenFarFR"],
    "operateur": ["GoldenFarFR"],
    "operator": ["GoldenFarFR"],
    "aria": ["Aria_ZHC"],
    "peers": ["solvrbot", "grok", "aixbt_agent"],
    "veille": ["solvrbot", "grok", "aixbt_agent"],
    "pairs": ["solvrbot", "grok", "aixbt_agent"],
    "dex": ["Dexscreener"],
    "base": ["base"],
}

_ALIAS_TOKEN = re.compile(r"@([A-Za-z0-9_]+)")
_PLUS_PACK = re.compile(r"\s*\+([A-Za-z0-9_]+)\s*$")


def _default_overlay() -> dict[str, Any]:
    return {
        "added_handles": [],
        "aliases": {},
        "default_tweet_pack": "veille",
        "updated_at": None,
    }


def _load_overlay() -> dict[str, Any]:
    if not REGISTRY_PATH.is_file():
        return _default_overlay()
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        base = _default_overlay()
        base.update(data)
        return base
    except Exception:
        return _default_overlay()


def _save_overlay(data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _merged_handles.cache_clear()
    _all_aliases.cache_clear()


@lru_cache(maxsize=1)
def _merged_handles() -> tuple[dict[str, str], ...]:
    """Known handles: yaml SSOT + operator additions."""
    from aria_core.knowledge.x_watchlist import operator_watch_handles

    overlay = _load_overlay()
    entries: list[dict[str, str]] = []
    seen: set[str] = set()

    for handle in operator_watch_handles():
        key = handle.lower()
        if key not in seen:
            seen.add(key)
            entries.append({"handle": handle, "source": "yaml", "role": ""})

    for item in overlay.get("added_handles") or []:
        if isinstance(item, str):
            h = item.lstrip("@")
        elif isinstance(item, dict):
            h = str(item.get("handle", "")).lstrip("@")
        else:
            continue
        if h and h.lower() not in seen:
            seen.add(h.lower())
            role = item.get("role", "custom") if isinstance(item, dict) else "custom"
            entries.append({"handle": h, "source": "operator", "role": role})

    return tuple(entries)


@lru_cache(maxsize=1)
def _all_aliases() -> dict[str, list[str]]:
    overlay = _load_overlay()
    merged: dict[str, list[str]] = {k: list(v) for k, v in _BUILTIN_ALIASES.items()}
    for name, handles in (overlay.get("aliases") or {}).items():
        if not name or not handles:
            continue
        clean = [str(h).lstrip("@") for h in handles if str(h).strip()]
        if clean:
            merged[name.lower()] = clean
    return merged


def known_handles() -> list[str]:
    return [e["handle"] for e in _merged_handles()]


def format_mentions(handles: list[str]) -> str:
    return " ".join(f"@{h.lstrip('@')}" for h in handles if h.strip())


def mentions_for_pack(pack: str | None = None) -> str:
    overlay = _load_overlay()
    name = (pack or overlay.get("default_tweet_pack") or "veille").lower()
    aliases = _all_aliases()
    handles = aliases.get(name) or known_handles()[:4]
    return format_mentions(handles)


def resolve_handles_in_text(text: str) -> str:
    """
    Replaces @alias with the real @handles.
    E.g.: "Question du jour @veille" -> "... @solvrbot @grok @aixbt_agent"
    Suffix +veille -> mentions appended at the end of the tweet.
    """
    if not text.strip():
        return text

    aliases = dict(_all_aliases())
    known = {h.lower() for h in known_handles()}
    for h in known_handles():
        aliases.setdefault(h.lower(), [h])

    def _expand(match: re.Match[str]) -> str:
        token = match.group(1)
        key = token.lower()
        if key in aliases:
            return format_mentions(aliases[key])
        if key in known:
            return f"@{token}"
        return match.group(0)

    out = _ALIAS_TOKEN.sub(_expand, text)

    pack_match = _PLUS_PACK.search(out)
    if pack_match:
        pack = pack_match.group(1).lower()
        body = out[: pack_match.start()].rstrip()
        extra = mentions_for_pack(pack)
        if extra and extra not in body:
            out = f"{body} {extra}".strip()
        else:
            out = body

    from aria_core.x_text import fit_x_tweet

    return fit_x_tweet(out, ellipsis="...")


def add_handle(handle: str, *, role: str = "custom") -> str:
    h = handle.lstrip("@").strip()
    if not h or not re.match(r"^[A-Za-z0-9_]{1,50}$", h):
        raise ValueError(f"Handle invalide : {handle}")
    overlay = _load_overlay()
    items = overlay.get("added_handles") or []
    for item in items:
        existing = item if isinstance(item, str) else item.get("handle", "")
        if str(existing).lstrip("@").lower() == h.lower():
            return f"@{h} déjà dans le registre."
    items.append({"handle": h, "role": role[:64]})
    overlay["added_handles"] = items
    _save_overlay(overlay)
    return f"Handle @{h} ajouté (rôle : {role or 'custom'})."


def remove_handle(handle: str) -> str:
    h = handle.lstrip("@").strip().lower()
    overlay = _load_overlay()
    items = overlay.get("added_handles") or []
    new_items = []
    removed = False
    for item in items:
        raw = item if isinstance(item, str) else item.get("handle", "")
        if str(raw).lstrip("@").lower() == h:
            removed = True
            continue
        new_items.append(item)
    if not removed:
        return f"@{handle} introuvable dans les ajouts opérateur (le YAML SSOT reste inchangé)."
    overlay["added_handles"] = new_items
    _save_overlay(overlay)
    return f"Handle @{handle} retiré des ajouts opérateur."


def set_alias(name: str, handles: list[str]) -> str:
    key = name.lstrip("@").strip().lower()
    if not key or not re.match(r"^[a-z0-9_]{1,32}$", key):
        raise ValueError(f"Alias invalide : {name}")
    clean = [h.lstrip("@") for h in handles if h.strip()]
    if not clean:
        raise ValueError("Liste de handles vide.")
    overlay = _load_overlay()
    aliases = overlay.get("aliases") or {}
    aliases[key] = clean
    overlay["aliases"] = aliases
    _save_overlay(overlay)
    return f"Alias @{key} → {format_mentions(clean)}"


def set_default_pack(pack: str) -> str:
    key = pack.lstrip("@").strip().lower()
    if key not in _all_aliases():
        raise ValueError(f"Pack inconnu : {pack} (alias existant requis)")
    overlay = _load_overlay()
    overlay["default_tweet_pack"] = key
    _save_overlay(overlay)
    return f"Pack tweet par défaut : +{key} ({mentions_for_pack(key)})"


def registry_status() -> dict[str, Any]:
    overlay = _load_overlay()
    return {
        "handles": list(_merged_handles()),
        "aliases": _all_aliases(),
        "default_tweet_pack": overlay.get("default_tweet_pack", "veille"),
        "builtin_aliases": list(_BUILTIN_ALIASES.keys()),
    }


def format_registry_short() -> str:
    """Summary shown in /x status — aliases at a glance, no lookup needed."""
    aliases = _all_aliases()
    pack = registry_status()["default_tweet_pack"]
    lines = ["Alias handles (tape dans un tweet — expansion auto) :"]
    for key in ("holding", "veille", "peers", "aria", "dex"):
        if key in aliases:
            lines.append(f"  @{key} → {format_mentions(aliases[key])}")
    lines.append(f"Suffixe : « … +{pack} » ajoute le pack par défaut.")
    lines.append("Liste complète : /handles ou /x handles")
    return "\n".join(lines)


def format_registry_help() -> str:
    aliases = _all_aliases()
    lines = [
        "Handles X — registre & alias",
        "",
        "Alias (dans un tweet, /x post ou /x compose) :",
    ]
    for key in sorted(aliases.keys()):
        if key in _BUILTIN_ALIASES or key in ("holding", "veille", "peers", "aria", "dex"):
            lines.append(f"  @{key} → {format_mentions(aliases[key])}")
    lines.extend(
        [
            "",
            "Exemple : /x post Question du jour @veille",
            "Exemple : Mon tweet ici +holding",
            "",
            "Commandes :",
            "/handles — cette liste",
            "/x handles add <handle> [rôle]",
            "/x handles remove <handle>",
            "/x handles alias <nom> h1 h2 …",
            "/x handles pack veille — pack par défaut",
            "",
            f"Pack actuel : +{registry_status()['default_tweet_pack']}",
        ]
    )
    handles = known_handles()
    if handles:
        lines.append(f"Registre : {', '.join('@' + h for h in handles[:15])}")
    return "\n".join(lines)