"""Poll hebdomadaire — 3 idées d'apps (Kelly model, web + Play Store Android)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from aria_core.llm import chat_with_context, is_llm_configured
from aria_core.memory import append_memory, build_llm_context
from aria_core.narrative import llm_system_block
from aria_core.paths import data_dir
from aria_core.runtime import settings

_FALLBACK_IDEAS: list[dict[str, str]] = [
    {
        "title": "Watchlist crypto minimaliste",
        "pitch": "Alertes prix + favoris, 1 écran, freemium Play Store.",
        "stack": "React Native ou Kotlin",
        "revenue": "IAP alertes illimitées ~2,99 $",
    },
    {
        "title": "Calculateur frais DEX",
        "pitch": "Estime slippage + gas sur swap, outil utilitaire.",
        "stack": "PWA web + wrapper Android TWA",
        "revenue": "Ads ou version Pro 4,99 $",
    },
    {
        "title": "Bot signaux Telegram white-label",
        "pitch": "Micro-SaaS : client configure 3 paires, reçoit alertes.",
        "stack": "Python + Telegram API",
        "revenue": "Setup 49 $ + 9 $/mois",
    },
]

_STATE_PATH = data_dir() / "app_idea_poll_state.json"


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {"last_poll_at": None, "ideas": [], "selected_index": None}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_poll_at": None, "ideas": [], "selected_index": None}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_llm_ideas(raw: str) -> list[dict[str, str]]:
    ideas: list[dict[str, str]] = []
    blocks = re.split(r"\n(?=\d+[\.\)])", raw.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        title_m = re.search(r"title\s*:\s*(.+)", block, re.I)
        pitch_m = re.search(r"pitch\s*:\s*(.+)", block, re.I)
        stack_m = re.search(r"stack\s*:\s*(.+)", block, re.I)
        rev_m = re.search(r"revenue\s*:\s*(.+)", block, re.I)
        if title_m:
            ideas.append(
                {
                    "title": title_m.group(1).strip()[:80],
                    "pitch": (pitch_m.group(1).strip() if pitch_m else "")[:160],
                    "stack": (stack_m.group(1).strip() if stack_m else "")[:80],
                    "revenue": (rev_m.group(1).strip() if rev_m else "")[:80],
                }
            )
        if len(ideas) >= 3:
            break
    return ideas


async def _generate_ideas_llm(lang: str) -> list[dict[str, str]]:
    if not is_llm_configured():
        return []
    lang_key = "fr" if lang.startswith("fr") else "en"
    context = await build_llm_context(public=False)
    lang_hint = "Réponds en français." if lang_key == "fr" else "Reply in English."
    system = (
        f"{context}\n\n{llm_system_block(lang_key)}\n\n"
        "MISSION : génère exactement 3 idées de micro-apps monétisables (modèle Kelly Claude).\n"
        "Au moins 1 idée doit cibler Android Play Store (compte dev 25 $ one-shot).\n"
        "Domaines : crypto utilitaire, outils indie, automation Telegram, ou utilitaire métier simple.\n"
        "Format STRICT par idée (3 blocs) :\n"
        "1.\nTitle: ...\nPitch: ...\nStack: ...\nRevenue: ...\n"
        "2.\nTitle: ...\n...\n"
        "3.\nTitle: ...\n...\n"
        "Pas de token hype. Scope livrable <7 jours pour v0.\n"
        f"{lang_hint}"
    )
    user = (
        "Génère le poll hebdomadaire 3 apps."
        if lang_key == "fr"
        else "Generate the weekly 3-app poll."
    )
    raw = await chat_with_context(user, system, temperature=0.45, max_tokens=700)
    if not raw:
        return []
    parsed = _parse_llm_ideas(raw)
    return parsed if len(parsed) >= 2 else []


def format_poll_message(ideas: list[dict[str, str]], lang: str = "fr") -> str:
    if lang == "en":
        lines = [
            "📱 App factory poll (Kelly model)",
            "Vote: reply app 1 / app 2 / app 3",
            "Play Store: Google dev account = $25 one-time (operator pays).",
            "",
        ]
    else:
        lines = [
            "📱 Poll app factory (modèle Kelly)",
            "Vote : réponds app 1 / app 2 / app 3",
            "Play Store : compte dev Google = 25 $ unique (payé par l'opérateur).",
            "",
        ]
    for i, idea in enumerate(ideas[:3], 1):
        lines.append(f"**App {i} — {idea.get('title', '?')}**")
        if idea.get("pitch"):
            lines.append(f"  {idea['pitch']}")
        if idea.get("stack"):
            lines.append(f"  Stack : {idea['stack']}")
        if idea.get("revenue"):
            lines.append(f"  Revenu : {idea['revenue']}")
        lines.append("")
    if lang == "en":
        lines.append("After vote: I ship v0 in <7 days (web repo or Android AAB).")
    else:
        lines.append("Après vote : je livre v0 en <7 jours (repo web ou AAB Android).")
    return "\n".join(lines).strip()


async def run_app_idea_poll_cycle(lang: str = "fr") -> dict:
    ideas = await _generate_ideas_llm(lang)
    if len(ideas) < 3:
        ideas = list(_FALLBACK_IDEAS)

    state = _load_state()
    state["last_poll_at"] = datetime.now(timezone.utc).isoformat()
    state["ideas"] = ideas[:3]
    state["selected_index"] = None
    _save_state(state)

    msg = format_poll_message(ideas[:3], lang)
    append_memory("entrepreneur", f"[app_poll] {ideas[0].get('title', '')[:60]}")
    return {"status": "ok", "ideas": ideas[:3], "message": msg}


def parse_app_vote(text: str) -> int | None:
    """Retourne 1, 2 ou 3 si vote app, sinon None."""
    clean = re.sub(r"[^\w\s]", "", text.strip().lower())
    m = re.match(r"^app\s*([123])$", clean)
    if m:
        return int(m.group(1))
    m = re.match(r"^([123])$", clean)
    if m and "app" in text.lower():
        return int(m.group(1))
    return None


def record_app_vote(choice: int, lang: str = "fr") -> str:
    state = _load_state()
    ideas = state.get("ideas") or []
    if not ideas:
        if lang == "en":
            return "No active poll — wait for the weekly app factory poll."
        return "Pas de poll actif — attends le poll hebdomadaire app factory."

    idx = choice - 1
    if idx < 0 or idx >= len(ideas):
        if lang == "en":
            return f"Invalid choice — reply app 1 to app {len(ideas)}."
        return f"Choix invalide — réponds app 1 à app {len(ideas)}."

    picked = ideas[idx]
    state["selected_index"] = idx
    state["selected_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    from aria_core.knowledge.cultivation_curriculum import mark_ship_completed

    mark_ship_completed()
    append_memory(
        "entrepreneur",
        f"App vote #{choice}: {picked.get('title', '')} — ship v0 <7j",
    )

    if lang == "en":
        return (
            f"✅ App {choice} selected: {picked.get('title', '?')}\n"
            f"Pitch: {picked.get('pitch', '')}\n"
            f"Next: I open a repo and ship v0 in <7 days "
            f"(web or Play Store AAB — dev account $25 if Android)."
        )
    return (
        f"✅ App {choice} choisie : {picked.get('title', '?')}\n"
        f"Pitch : {picked.get('pitch', '')}\n"
        f"Suite : j'ouvre un repo et livre v0 en <7 jours "
        f"(web ou AAB Play Store — compte dev 25 $ si Android)."
    )