"""FAQ + content search — ARIA's public knowledge layer."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_FAQ_PATH = Path(__file__).parent / "faq.yaml"
_FAQ_CACHE: list[dict] | None = None


def _load_faq() -> list[dict]:
    global _FAQ_CACHE
    if _FAQ_CACHE is not None:
        return _FAQ_CACHE
    if not _FAQ_PATH.exists():
        _FAQ_CACHE = []
        return _FAQ_CACHE
    raw = yaml.safe_load(_FAQ_PATH.read_text(encoding="utf-8")) or []
    _FAQ_CACHE = raw if isinstance(raw, list) else []
    return _FAQ_CACHE


def list_faq(tag: str | None = None) -> list[dict]:
    items = _load_faq()
    if not tag:
        return items
    tag_l = tag.lower()
    return [i for i in items if tag_l in [t.lower() for t in i.get("tags", [])]]


def _score_faq(query: str, item: dict) -> int:
    q = query.lower()
    score = 0
    question = (item.get("question") or "").lower()
    answer = (item.get("answer") or "").lower()
    tags = " ".join(item.get("tags", [])).lower()
    for token in re.findall(r"[a-z0-9]{3,}", q):
        if token in question:
            score += 4
        if token in answer:
            score += 2
        if token in tags:
            score += 3
    if q in question:
        score += 10
    return score


def search_faq(query: str, limit: int = 3) -> list[dict]:
    items = _load_faq()
    if not query.strip():
        return items[:limit]
    scored = [(item, _score_faq(query, item)) for item in items]
    scored = [(item, s) for item, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [item for item, _ in scored[:limit]]


def format_faq_reply(matches: list[dict], lang: str = "en") -> str:
    if not matches:
        if lang == "fr":
            return (
                "Je n'ai pas trouvé d'entrée FAQ exacte. Reformule ta question "
                "(holding, DEXPulse, build, marketing, token BASE, launchpads)."
            )
        return (
            "No exact FAQ match. Try rephrasing "
            "(holding, DEXPulse, build, marketing, BASE token, launchpads)."
        )
    parts: list[str] = []
    header = "**FAQ ARIA**\n\n" if lang == "fr" else "**ARIA FAQ**\n\n"
    parts.append(header)
    for item in matches:
        parts.append(f"**{item['question']}**\n{item['answer'].strip()}\n")
    return "\n".join(parts).strip()