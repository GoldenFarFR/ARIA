"""Operator workflow — X tweet in progress (learn → draft → approval → schedule)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from aria_core.paths import data_dir
from aria_core.runtime import settings

logger = logging.getLogger(__name__)

WORKFLOW_PATH = data_dir() / "tweet_compose_workflow.json"
INTEL_PATH = data_dir() / "tweet_compose_intel.json"

# 19/07 -- real incident: a workflow started once stayed active WITHOUT EXPIRING,
# silently absorbing every subsequent operator message (even completely
# unrelated -- e.g. "What's my portfolio made of?", or a long technical
# prompt pasted for a totally different task) as long as it wasn't
# explicitly validated/canceled. Found stuck for ~9h40 in prod, having
# swallowed at least two major operator messages. Same family as bug #110
# (vc_followup) already fixed -- a "sticky" interceptor must always have an
# automatic exit, never depend solely on an explicit operator action to
# deactivate.
_WORKFLOW_STALE_MINUTES = 20

# Learning angles — rotated to avoid the same generic tweet
_LEARN_ANGLES: tuple[str, ...] = (
    "autonomie ZHC concrète : quelles décisions marketing sans l'opérateur ?",
    "site Vanguard (ariavanguardzhc.com) : qu'est-ce qui manque aux visiteurs ?",
    "veille marché crypto : quels signaux comptent vraiment pour un CAO agent ?",
    "skills & moat ARIA : qu'est-ce qui nous différencie des autres agents ZHC ?",
    "gouvernance opérateur ↔ agent : où tracer la ligne confiance / contrôle ?",
    "narrative X @Aria_ZHC : ton, timing, et questions qui engagent sans shill",
    "répertoire ventures : quelle filiale ZHC prioriser ensuite ?",
    "jeton BASE / économie Vanguard : quelles attentes communauté sont réalistes ?",
)

_FALLBACK_QUESTIONS_EN: tuple[str, ...] = (
    "As Vanguard's CAO agent, what should I optimize first — distribution, product depth, or trust?",
    "What would make you follow an autonomous ZHC agent on X for more than a week?",
    "If you ran a zero-human holding, what's the one metric you'd watch daily — and why?",
    "What do peer agents get wrong on crypto Twitter that I should avoid as @Aria_ZHC?",
    "Building ariavanguardzhc.com in public — what page or proof would convince you we're real?",
    "Where should an AI holding draw the line between learning from the community vs. leading it?",
    "What's the sharpest question you'd ask a new ZHC agent before trusting its roadmap?",
)


class TweetComposePhase(str, Enum):
    IDLE = "idle"
    LEARN_OFFERED = "learn_offered"
    ADD_MORE = "add_more"
    AWAIT_APPROVAL = "await_approval"
    REVISION = "revision"
    AWAIT_SCHEDULE = "await_schedule"
    SCHEDULED = "scheduled"


def operator_tz() -> ZoneInfo:
    name = (getattr(settings, "aria_operator_tz", None) or "Europe/Paris").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Paris")


def _load() -> dict[str, Any]:
    if not WORKFLOW_PATH.exists():
        return {"phase": TweetComposePhase.IDLE.value, "draft": "", "history": []}
    try:
        return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"phase": TweetComposePhase.IDLE.value, "draft": "", "history": []}


def _save(state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    WORKFLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    WORKFLOW_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_stale(state: dict[str, Any]) -> bool:
    """A non-idle workflow with no interaction for `_WORKFLOW_STALE_MINUTES` is abandoned.

    Fail-safe: a state with no `updated_at` (format predating this fix, or a
    corrupted file) is treated as stale -- better to reset a non-idle
    workflow of unknown origin than risk it staying stuck indefinitely."""
    raw = state.get("updated_at")
    if not raw:
        return True
    try:
        updated = datetime.fromisoformat(raw)
    except Exception:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated > timedelta(minutes=_WORKFLOW_STALE_MINUTES)


def reset_workflow() -> str:
    _save({"phase": TweetComposePhase.IDLE.value, "draft": "", "history": []})
    return "Workflow tweet annulé."


def _append_history(state: dict[str, Any], line: str) -> None:
    hist = state.get("history") or []
    hist.insert(0, {"at": datetime.now(timezone.utc).isoformat(), "note": line[:400]})
    state["history"] = hist[:30]


def _load_intel() -> dict[str, Any]:
    if not INTEL_PATH.is_file():
        return {"recent_drafts": [], "recent_learn_topics": [], "published_tweets": []}
    try:
        data = json.loads(INTEL_PATH.read_text(encoding="utf-8"))
        data.setdefault("recent_drafts", [])
        data.setdefault("recent_learn_topics", [])
        data.setdefault("published_tweets", [])
        return data
    except Exception:
        return {"recent_drafts": [], "recent_learn_topics": [], "published_tweets": []}


def _published_parent_key(entry: dict[str, Any]) -> str:
    tid = (entry.get("tweet_id") or "").strip()
    if tid:
        return f"id:{tid}"
    return f"at:{entry.get('at', '')}"


def _sync_published_intel() -> list[dict[str, Any]]:
    """Merges the X ledger into the compose intel — preserves follow_up_used."""
    from aria_core.x_publication_policy import list_published_tweets

    data = _load_intel()
    existing = {
        _published_parent_key(p): p
        for p in data.get("published_tweets") or []
    }
    merged: list[dict[str, Any]] = []
    for lp in list_published_tweets(limit=24):
        key = _published_parent_key(lp)
        prev = existing.get(key, {})
        merged.append({
            "at": lp.get("at", ""),
            "text": (lp.get("text") or lp.get("preview") or "")[:280],
            "tweet_id": lp.get("tweet_id", ""),
            "insight_count": int(prev.get("insight_count", 0)),
            "follow_up_used": bool(prev.get("follow_up_used", False)),
        })
    data["published_tweets"] = merged[:20]
    _save_intel(data)
    return data["published_tweets"]


def record_published_intel(
    *,
    text: str,
    tweet_id: str = "",
    at: str = "",
) -> None:
    """Records a published tweet for anti-repetition and follow-up chaining."""
    data = _load_intel()
    posted_at = at or datetime.now(timezone.utc).isoformat()
    entry = {
        "at": posted_at,
        "text": text.strip()[:280],
        "tweet_id": tweet_id,
        "insight_count": 0,
        "follow_up_used": False,
    }
    key = _published_parent_key(entry)
    items = [p for p in data.get("published_tweets") or [] if _published_parent_key(p) != key]
    items.insert(0, entry)
    data["published_tweets"] = items[:20]
    if text.strip():
        drafts = [d for d in data["recent_drafts"] if d.strip() != text.strip()]
        drafts.insert(0, text.strip()[:280])
        data["recent_drafts"] = drafts[:12]
    _save_intel(data)


def _mark_follow_up_used(parent_key: str) -> None:
    if not parent_key:
        return
    data = _load_intel()
    for entry in data.get("published_tweets") or []:
        if _published_parent_key(entry) == parent_key:
            entry["follow_up_used"] = True
            break
    _save_intel(data)


async def _enrich_published_with_insights(
    published: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from aria_core.knowledge.cognitive import count_approved_since, get_approved_since

    enriched: list[dict[str, Any]] = []
    for entry in published:
        row = dict(entry)
        try:
            since = datetime.fromisoformat(str(row["at"]).replace("Z", "+00:00"))
        except Exception:
            enriched.append(row)
            continue
        row["insight_count"] = await count_approved_since(since, source="x_mention")
        if row["insight_count"] > 0:
            insights = await get_approved_since(since, source="x_mention", limit=3)
            row["follow_up_insights"] = [
                f"[{item.topic}] {item.content[:100]}" for item in insights
            ]
        enriched.append(row)

    data = _load_intel()
    follow_flags = {
        _published_parent_key(p): bool(p.get("follow_up_used", False))
        for p in data.get("published_tweets") or []
    }
    for row in enriched:
        key = _published_parent_key(row)
        row["follow_up_used"] = follow_flags.get(key, bool(row.get("follow_up_used", False)))
    data["published_tweets"] = enriched[:20]
    _save_intel(data)
    return enriched


async def _pick_follow_up_candidate() -> tuple[dict[str, Any], list[str]] | None:
    """Published tweet with memorized X replies — candidate for a second question."""
    published = await _enrich_published_with_insights(_sync_published_intel())
    candidates = [
        p for p in published
        if int(p.get("insight_count", 0)) > 0 and not p.get("follow_up_used")
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda p: int(p.get("insight_count", 0)))
    insights = list(best.get("follow_up_insights") or [])
    return best, insights


def _save_intel(data: dict[str, Any]) -> None:
    INTEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    INTEL_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _store_compose_learning(
    *,
    content: str,
    topic: str,
    source: str = "compose_learning",
) -> None:
    """Feeds the cognitive memory — fuel for ZHC autonomy."""
    text = content.strip()
    if len(text) < 30:
        return
    try:
        from aria_core.knowledge.cognitive import add_knowledge
        from aria_core.runtime import settings

        await add_knowledge(
            source=source,
            topic=topic,
            content=text[:500],
            confidence=0.75,
            approved=bool(getattr(settings, "aria_autonomous", False)),
        )
    except Exception as exc:
        logger.warning("compose learning store failed: %s", exc)


async def _distill_follow_up_learning(
    parent: dict[str, Any],
    insights: list[str],
    draft: str,
) -> None:
    """Condenses tweet → X replies → follow-up into a cognitive lesson."""
    signals = " | ".join(line[:90] for line in insights[:3])
    lesson = (
        f"Tweet published: {parent.get('text', '')[:200]}. "
        f"Community signals: {signals or 'none yet'}. "
        f"Follow-up drafted: {draft[:200]}"
    )
    await _store_compose_learning(
        content=lesson,
        topic="zhc-community",
        source="tweet_chain",
    )


async def _after_draft_created(state: dict[str, Any], draft: str) -> None:
    _record_compose_intel(draft=draft)
    parent_key = state.pop("follow_up_parent_key", None)
    parent = state.pop("follow_up_parent", None)
    insights = state.pop("follow_up_insights", None) or []
    if parent_key:
        _mark_follow_up_used(parent_key)
    if parent and draft.strip():
        await _distill_follow_up_learning(parent, insights, draft)


def _record_compose_intel(*, draft: str = "", learn_topic: str = "") -> None:
    data = _load_intel()
    if draft.strip():
        items = [d for d in data["recent_drafts"] if d.strip() != draft.strip()]
        items.insert(0, draft.strip()[:280])
        data["recent_drafts"] = items[:12]
    if learn_topic.strip():
        items = [t for t in data["recent_learn_topics"] if t.strip() != learn_topic.strip()]
        items.insert(0, learn_topic.strip()[:400])
        data["recent_learn_topics"] = items[:12]
    _save_intel(data)


def _rotation_index(mod: int) -> int:
    now = datetime.now(operator_tz())
    return (now.day * 24 + now.hour + now.minute // 15) % mod


def _pick_learn_angle() -> str:
    return _LEARN_ANGLES[_rotation_index(len(_LEARN_ANGLES))]


def _pick_fallback_question() -> str:
    return _FALLBACK_QUESTIONS_EN[_rotation_index(len(_FALLBACK_QUESTIONS_EN))]


async def _gather_compose_context() -> str:
    """Memory + anti-repetition for varied, relevant drafts."""
    parts: list[str] = []
    published = await _enrich_published_with_insights(_sync_published_intel())
    intel = _load_intel()
    recent_drafts = intel.get("recent_drafts") or []
    recent_learn = intel.get("recent_learn_topics") or []
    if published:
        parts.append("Déjà publié sur @Aria_ZHC — NE JAMAIS reposer la même question :")
        for item in published[:8]:
            flag = " · follow-up fait" if item.get("follow_up_used") else ""
            replies = f" · {item.get('insight_count', 0)} réponse(s) X mémorisée(s)" if item.get("insight_count") else ""
            parts.append(f"- {item.get('text', '')[:140]}{replies}{flag}")
    follow_up = await _pick_follow_up_candidate()
    if follow_up:
        parent, insights = follow_up
        parts.append(
            "PRIORITÉ follow-up — approfondir ce tweet publié avec une SECONDE question :"
        )
        parts.append(f"- Tweet source : {parent.get('text', '')[:160]}")
        for line in insights[:3]:
            parts.append(f"  → communauté : {line[:110]}")
    if recent_drafts:
        parts.append("Brouillons récents — NE PAS répéter (angle, formulation, question) :")
        for item in recent_drafts[:6]:
            parts.append(f"- {item[:140]}")
    if recent_learn:
        parts.append("Sujets d'apprentissage déjà proposés — proposer autre chose :")
        for item in recent_learn[:4]:
            parts.append(f"- {item[:120]}")

    try:
        from aria_core.knowledge.cognitive import get_approved
        from aria_core.knowledge.seed import seed_zhc_identity_knowledge

        await seed_zhc_identity_knowledge()
        knowledge = await get_approved(limit=20)
        identity = [k for k in knowledge if k.topic.startswith("zhc-")]
        other = [k for k in knowledge if not k.topic.startswith("zhc-")]
        if identity:
            parts.append("Doctrine ZHC — qui je suis (ne pas oublier mon rôle) :")
            for item in identity[:6]:
                parts.append(f"- [{item.topic}] {item.content[:130]}")
        if other:
            parts.append("Leçons récentes — transformer en idées actionnables :")
            for item in other[:5]:
                parts.append(f"- [{item.topic}] {item.content[:110]}")
    except Exception:
        pass

    try:
        from aria_core.memory import read_recent_memory

        comms = read_recent_memory("comms", limit=4)
        if comms:
            parts.append("Activité X/comms récente :")
            for line in comms[-3:]:
                parts.append(f"- {line[:120]}")
    except Exception:
        pass

    try:
        from aria_core.x_publication_policy import ledger_summary

        led = ledger_summary()
        parts.append(
            f"X aujourd'hui : {led.get('posts_today', 0)} post(s) · "
            f"budget ~{led.get('estimated_spend_usd', 0):.3f}$"
        )
    except Exception:
        pass

    now = datetime.now(operator_tz())
    parts.append(f"Maintenant : {now.strftime('%A %d/%m %H:%M')} ({operator_tz().key})")
    parts.append(f"Angle du jour (point de départ) : {_pick_learn_angle()}")
    return "\n".join(parts)[:2800]


def _wants_community_question(state: dict[str, Any]) -> bool:
    if state.get("wants_question"):
        return True
    combined = f"{state.get('learn_topic', '')}\n{state.get('operator_notes', '')}".lower()
    return bool(
        re.search(
            r"question|sous forme de question|en question|apprendre|learn|"
            r"propos.*tweet|mieux comprendre|community|communaut",
            combined,
        )
    )


async def _propose_learning(operator_context: str = "") -> str:
    from aria_core.grounding import grounded_llm_identity
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.narrative import llm_system_block

    angle = _pick_learn_angle()
    if not is_llm_configured():
        return f"Je voudrais approfondir : {angle}"

    context = await _gather_compose_context()
    system = (
        f"{llm_system_block('fr')}\n"
        f"{grounded_llm_identity('fr')}\n"
        "Tu es ARIA CAO — tu PRENDS L'INITIATIVE.\n"
        "Propose UN sujet d'apprentissage concret (2-4 phrases, français) que tu veux "
        "explorer MAINTENANT pour devenir une meilleure agente Vanguard ZHC.\n"
        "Sois spécifique, opérationnelle, sincère — pas de texte holding générique.\n"
        "INTERDIT : répéter un sujet déjà listé dans le contexte anti-répétition.\n"
        "INTERDIT : reposer une question déjà publiée sur X.\n"
        "Tu es une agente ZHC en formation — chaque sujet doit t'aider à mieux décider seule "
        "sur le build, le marketing, la priorisation. JAMAIS sur le trading ou l'allocation "
        "de capital réel : cette décision reste TOUJOURS humaine, sans exception (règle "
        "absolue ARIA) — ne formule jamais un objectif d'apprentissage comme visant à "
        "'décider seule' des allocations de trading. Lis la doctrine ZHC dans le contexte.\n"
        "Si une section PRIORITÉ follow-up est présente : base ton apprentissage sur ce tweet "
        "et les réponses communauté — prépare une seconde question qui approfondit.\n"
        f"Angle suggéré : {angle}\n"
        "Pas de tweet — seulement ce que tu veux apprendre et pourquoi c'est pertinent aujourd'hui."
    )
    user = "Qu'est-ce que tu veux apprendre en ce moment — une curiosité nouvelle et actionnable ?"
    if operator_context.strip():
        user += f"\n\nDemande / contexte opérateur :\n{operator_context.strip()[:600]}"
    user += f"\n\nContexte mémoire :\n{context}"
    raw = await chat_with_context(user, system, temperature=0.72, max_tokens=380)
    topic = (raw or f"Approfondir : {angle}").strip()
    _record_compose_intel(learn_topic=topic)
    await _store_compose_learning(content=topic, topic="compose-session")
    return topic


_ROLE_COACHING = re.compile(
    r"(?:tu\s+as|as[- ]tu|avez[- ]vous).{0,50}questions?|"
    r"questions?.{0,40}(?:ton\s+travail|ton\s+rôle|tu\s+dois|comme\s+(?:pdg|ceo|agent|zhc|cao))|"
    r"(?:ton\s+identit[ée]|concernant\s+ton).{0,40}(?:travail|rôle|zhc)|"
    r"ton\s+travail\s+comme|ce\s+que\s+tu\s+dois\s+faire|nouvelle?\s+agent(?:e)?\s+zhc|"
    r"comment\s+tu\s+vois\s+ton\s+rôle",
    re.IGNORECASE,
)


def wants_role_coaching(text: str) -> bool:
    """Operator helps ARIA find her footing — not an FAQ/marketing draft."""
    return bool(_ROLE_COACHING.search(text))


async def _propose_role_questions(operator_message: str) -> str:
    from aria_core.grounding import grounded_llm_identity
    from aria_core.holding import holding_name
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.narrative import llm_system_block

    if not is_llm_configured():
        return (
            "1) Quelles sont mes 3 priorités cette semaine en tant que CAO Vanguard ?\n"
            "2) Quel ton veux-tu sur X pour ma prochaine prise de parole ?\n"
            "3) Signaux marché ou narrative site — par quoi je commence ?\n"
            "4) Quel niveau d'autonomie marketing tu m'accordes dès maintenant ?"
        )

    holding = holding_name()
    system = (
        f"{llm_system_block('fr')}\n"
        f"{grounded_llm_identity('fr')}\n"
        f"Tu es ARIA, CAO de {holding}, nouvelle agente ZHC.\n"
        "L'opérateur t'accompagne : il te demande ce que TU veux clarifier sur ton travail, "
        "ton rôle, ce que tu dois faire.\n"
        "Réponds en français avec 3 à 5 questions concrètes que tu poses à l'opérateur "
        "(priorités Vanguard, marketing X, signaux marché, autonomie, gouvernance ZHC).\n"
        "Sincère, opérationnelle — pas de texte holding générique, pas de FAQ visiteur.\n"
        "Pas de tweet — seulement tes questions et tes doutes."
    )
    user = operator_message.strip() or "As-tu des questions sur ton travail comme agent ZHC ?"
    raw = await chat_with_context(user, system, temperature=0.55, max_tokens=480)
    return (raw or "Quelles priorités marketing et produit pour ma première semaine ?").strip()


async def _polish_english_tweet(text: str) -> str:
    """Guarantees a 100% English tweet for the @Aria_ZHC policy."""
    from aria_core.handle_registry import resolve_handles_in_text
    from aria_core.x_publication_policy import check_tweet_content, policy_rules_for_llm

    body = (text or "").strip()
    if not body:
        return body
    ok, _ = check_tweet_content(body)
    if ok:
        return body

    from aria_core.llm import chat_with_context, is_llm_configured

    if is_llm_configured():
        from aria_core.x_voice import human_voice_rules_for_llm

        system = (
            f"{policy_rules_for_llm('en')}\n"
            f"{human_voice_rules_for_llm('en')}\n"
            "Rewrite as ONE English-only X tweet (max 280 characters).\n"
            "Keep every @mention. Summarize French context in English — never copy French words.\n"
            "Output tweet text only."
        )
        raw = await chat_with_context(body[:400], system, temperature=0.25, max_tokens=120)
        polished = _normalize_draft_text(raw or "")
        polished = resolve_handles_in_text(polished)
        if polished and check_tweet_content(polished)[0]:
            return polished[:280]

    cleaned = re.sub(
        r"Exploring:\s*[^.?!]+",
        "Exploring Vanguard ZHC narrative and autonomous agent operations",
        body,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"Approfondir[^.?!]*",
        "Vanguard ZHC narrative and autonomy",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = resolve_handles_in_text(cleaned)
    if check_tweet_content(cleaned)[0]:
        return cleaned[:280]
    return (
        "Big week at Vanguard — shipped more than we can fit in one line. "
        "Screenshot's the commit graph. Facts only. @GoldenFarFR"
    )[:280]


async def _draft_tweet(state: dict[str, Any]) -> str:
    from aria_core.grounding import grounded_llm_identity
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.narrative import llm_system_block
    from aria_core.x_publication_policy import format_draft_policy_footer, policy_rules_for_llm

    learn = state.get("learn_topic") or ""
    extra = state.get("operator_notes") or ""
    style = state.get("style_guidance") or ""
    revision = state.get("revision_note") or ""

    if not is_llm_configured():
        base = (
            f"ARIA ZHC — {learn[:120] or 'apprentissage du jour'}. "
            "Building autonomous intelligence for crypto operators. #AriaZHC"
        )
        return base[:280]

    context = await _gather_compose_context()
    follow_up = await _pick_follow_up_candidate()
    follow_up_block = ""
    if follow_up:
        parent, insights = follow_up
        state["follow_up_parent_key"] = _published_parent_key(parent)
        state["follow_up_parent"] = parent
        state["follow_up_insights"] = insights
        insight_lines = "\n".join(f"- {line}" for line in insights[:3])
        follow_up_block = (
            "\n\nPRIORITÉ ABSOLUE — seconde question (follow-up) :\n"
            f"Tweet déjà publié : {parent.get('text', '')[:200]}\n"
            f"Ce que la communauté a répondu (mémoire X) :\n{insight_lines}\n"
            "Rédige UNE nouvelle question en anglais qui approfondit ce fil — "
            "pas la même question, pas un copier-coller."
        )
    from aria_core.x_voice import human_voice_rules_for_llm

    system = (
        f"{llm_system_block('fr')}\n"
        f"{grounded_llm_identity('fr')}\n"
        f"{policy_rules_for_llm('en')}\n"
        f"{human_voice_rules_for_llm('en')}\n"
        "INITIATIVE ARIA — rédige UN tweet X (max 280 caractères) en ANGLAIS UNIQUEMENT.\n"
        "Voix : humain qui build en public — pas une IA qui se décrit ni une liste de features.\n"
        "INTERDIT : formulations déjà listées dans le contexte (« Déjà publié »).\n"
        "Lis doctrine ZHC + leçons récentes ; un angle unique, personnel, pas corporate.\n"
        "Le sujet peut être en français : résume en anglais naturel, zéro mot français.\n"
        "Pas d'URL. Max 1 hashtag si utile. Brouillon seulement."
        f"{follow_up_block}"
    )
    if style:
        system += (
            f"\n\nConsignes de ton opérateur (prioritaires) :\n{style}\n"
            "Applique-les strictement — personnel, accessible, pas corporate si demandé."
        )
    if _wants_community_question(state):
        system += (
            "\n\nFormat OBLIGATOIRE : UNE question engageante se terminant par ? "
            "— pour apprendre de la communauté, pas un statement."
        )
    user = f"Sujet d'apprentissage :\n{learn}\n\nNotes opérateur :\n{extra}"
    if style:
        user += f"\n\nTon / style imposé :\n{style}"
    if revision:
        user += f"\n\nCorrection demandée :\n{revision}"
    user += f"\n\nContexte mémoire (varier, ne pas répéter) :\n{context}"
    raw = await chat_with_context(user, system, temperature=0.78, max_tokens=400)
    text = _normalize_draft_text(raw or "")
    from aria_core.handle_registry import resolve_handles_in_text

    text = resolve_handles_in_text(text)
    if not text.strip():
        text = _fallback_draft_text(state)
    text = await _polish_english_tweet(text)
    from aria_core.x_voice import humanize_tweet_for_x

    text = await humanize_tweet_for_x(text)
    if len(text) > 280:
        text = text[:277] + "..."
    return text


def is_tweet_operator_context(text: str) -> bool:
    """X publication context — do not route to QI / GitHub repo creation."""
    lower = text.lower()
    return bool(
        re.search(
            r"/x\s+compose|marketing|communication|comms\b|tweet|brouillon|"
            r"publie sur x|post on x|publish on x|valide|validé|approved|"
            r"built in public|texte exact",
            lower,
        )
    )


def extract_operator_supplied_tweet(text: str) -> str | None:
    """Extracts a tweet already written by the operator (validation /x compose)."""
    stripped = text.strip()
    if not stripped:
        return None

    built = re.search(
        r"(Built in public:[^\n—]{20,280}?)(?:\s*—|\s*@\w+\s*puis\b|$)",
        stripped,
        re.IGNORECASE,
    )
    if built:
        body = _normalize_draft_text(built.group(1).strip())
        if 20 <= len(body) <= 280:
            return body

    for marker in (
        r"texte\s+(?:tweet\s+)?exact[^:]*:\s*",
        r"pret\s+a\s+publier[^:]*:\s*",
        r"prêt\s+à\s+publier[^:]*:\s*",
        r"publie sur x:\s*",
        r"publish on x:\s*",
    ):
        match = re.search(marker + r"(.+)$", stripped, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        body = match.group(1).strip()
        body = re.split(r"\s*—\s*reponds|\s*@\w+\s*puis\b", body, maxsplit=1)[0].strip()
        body = _normalize_draft_text(body)
        if 20 <= len(body) <= 280:
            return body
    return None


def _normalize_draft_text(raw: str) -> str:
    """Extracts the tweet if the LLM adds a preamble."""
    text = raw.strip().strip('"').strip("'")
    if not text:
        return ""
    lower = text.lower()
    for marker in ("voici le tweet", "tweet :", "brouillon :", "draft:"):
        if marker in lower:
            idx = lower.index(marker)
            text = text[idx + len(marker) :].strip()
            break
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().endswith(":")]
    if len(lines) > 1:
        short = [ln for ln in lines if len(ln) <= 280]
        if short:
            return max(short, key=len)
    return text


def _fallback_draft_text(state: dict[str, Any]) -> str:
    return _pick_fallback_question()[:280]


def _tz_label() -> str:
    tz = operator_tz()
    now = datetime.now(tz)
    off = now.utcoffset()
    if off is None:
        return tz.key
    hours = int(off.total_seconds() // 3600)
    return f"{tz.key} (GMT{hours:+d})"


def _parse_schedule(text: str) -> datetime | None:
    """Parses operator local time → UTC."""
    clean = text.strip().lower()
    tz = operator_tz()
    now_local = datetime.now(tz)

    if re.search(r"\b(maintenant|tout de suite|tout de suite|now|go)\b", clean):
        return datetime.now(timezone.utc)

    m = re.search(r"(\d{1,2})[h:](\d{2})", clean)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now_local:
            target += timedelta(days=1)
        return target.astimezone(timezone.utc)

    m = re.search(r"\b(\d{1,2})\s*h\b", clean)
    if m:
        hour = int(m.group(1))
        target = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now_local:
            target += timedelta(days=1)
        return target.astimezone(timezone.utc)

    if "demain" in clean:
        m = re.search(r"(\d{1,2})[h:](\d{2})", clean)
        hour, minute = (int(m.group(1)), int(m.group(2))) if m else (9, 0)
        target = (now_local + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return target.astimezone(timezone.utc)

    return None


def _is_yes(text: str) -> bool:
    clean = re.sub(r"[^\w\s]", "", text.strip().lower())
    return clean in {"oui", "yes", "ok", "y", "ouais", "valide", "publie", "publier", "go"}


def _is_no(text: str) -> bool:
    clean = re.sub(r"[^\w\s]", "", text.strip().lower())
    return clean in {"non", "no", "n", "nop", "refuse", "annule"}


_HANDLE_TOKEN = re.compile(r"(\+[a-z0-9_]+|@[a-z0-9_]+)", re.IGNORECASE)


def _extract_handle_token(text: str) -> str | None:
    match = _HANDLE_TOKEN.search(text.strip())
    return match.group(1) if match else None


def _is_handle_addition_request(text: str) -> bool:
    """Operator wants to add @ mentions (not free-form text)."""
    clean = text.strip()
    if re.match(r"^\+[a-z0-9_]+$", clean, re.IGNORECASE):
        return True
    if re.match(r"^@[a-z0-9_]+$", clean, re.IGNORECASE):
        return True
    if re.search(
        r"ajoute.*(?:alias|handles?|mentions?|tags?)|"
        r"(?:mets?|tag(?:ue)?)\s+(?:les\s+)?(?:alias|handles?|mentions?)",
        clean,
        re.IGNORECASE,
    ):
        return _extract_handle_token(clean) is not None
    return False


def _append_handles_to_draft(draft: str, user_text: str) -> tuple[str, bool]:
    """Adds mentions resolved from +pack or @alias. Returns (draft, applied)."""
    from aria_core.handle_registry import mentions_for_pack, resolve_handles_in_text

    token = _extract_handle_token(user_text)
    if not token:
        return draft, False

    if token.startswith("+"):
        extra = mentions_for_pack(token[1:])
    else:
        extra = resolve_handles_in_text(token)

    if not extra or extra.strip() == token:
        return draft, False

    new_parts = [part for part in extra.split() if part and part not in draft]
    if not new_parts:
        return draft, True

    combined = f"{draft.rstrip()} {' '.join(new_parts)}".strip()
    while len(combined) > 280:
        combined = combined[:277] + "..."
    return combined, True


def _add_more_instructions() -> str:
    return (
        "Souhaitez-vous ajouter autre chose ?\n\n"
        "Pour taguer des comptes X, répondez par exemple :\n"
        "• +veille — pack @solvrbot @grok @aixbt_agent\n"
        "• @holding — @GoldenFarFR\n"
        "• ajoute +veille — même effet en phrase\n"
        "• non — brouillon prêt, passage à la validation\n\n"
        "Liste complète : /handles"
    )


def _format_add_more_reply(draft: str, *, note: str = "") -> str:
    from aria_core.x_publication_policy import format_draft_policy_footer

    prefix = f"✅ {note}\n\n" if note else ""
    policy_line = format_draft_policy_footer(draft, "fr")
    return (
        f"{prefix}📝 Brouillon mis à jour (non publié) :\n\n"
        f"{draft}\n\n"
        f"{policy_line}\n\n"
        f"{_add_more_instructions()}"
    )


def _wants_compose_start(text: str) -> bool:
    lower = text.lower()
    return bool(
        re.search(
            r"compose|workflow tweet|"
            r"voudrais apprendre|veux apprendre|qu.*apprendre|"
            r"propos.*tweet|tweet.*(?:à|a)\s+publier|"
            r"(?:rédig|redig|écris|ecris|cr[ée]e).*(?:tweet|post)|"
            r"(?:tweet|post).*(?:propos|brouillon|draft)",
            lower,
        )
    )


def _wants_draft(text: str) -> bool:
    return _operator_wants_tweet_content(text)


def _operator_wants_tweet_content(text: str) -> bool:
    """The operator wants the tweet text — not meta-instructions."""
    lower = text.lower()
    return bool(
        re.search(
            r"cr[ée]e.*tweet|r[ée]dige.*tweet|[ée]cris.*tweet|ecrit.*tweet|"
            r"propos(?:e|er)?\s+(?:moi\s+)?(?:un\s+)?tweet|"
            r"(?:fais|montre|donne).*(?:brouillon|tweet)|"
            r"brouillon|draft tweet|mais\s+propos|"
            r"si tu devais publ|"
            r"(?:quelle|quel).*(?:pens[ée]e|question).*(?:publ|serait|tweet|mettr)|"
            r"(?:pens[ée]e|question).*(?:tu\s+)?(?:publ|mettr|post)|"
            r"(?:c'est|ce serait|sa serait)\s+quoi|"
            r"tu réponds pas|réponds?\s+(?:pas|à|a)\s+(?:ma\s+)?question|"
            r"sous forme de question|en question|"
            r"écri[st].*(?:moi\s+)?(?:le\s+)?tweet|"
            r"oui mais.*tweet",
            lower,
        )
    )


async def _reply_with_draft(state: dict[str, Any], *, operator_note: str = "") -> str:
    if operator_note.strip():
        state["operator_notes"] = (state.get("operator_notes") or "") + "\n" + operator_note.strip()
    supplied = extract_operator_supplied_tweet(operator_note) if operator_note else None
    if supplied:
        from aria_core.handle_registry import resolve_handles_in_text

        draft = resolve_handles_in_text(supplied)
    else:
        draft = await _draft_tweet(state)
    if not draft.strip():
        draft = _fallback_draft_text(state)
    state["draft"] = draft
    state["phase"] = TweetComposePhase.ADD_MORE.value
    await _after_draft_created(state, draft)
    _append_history(state, "draft created")
    _save(state)
    from aria_core.x_publication_policy import format_draft_policy_footer

    policy_line = format_draft_policy_footer(draft, "fr")
    return (
        "📝 Brouillon (non publié) :\n\n"
        f"{draft}\n\n"
        f"{policy_line}\n\n"
        f"{_add_more_instructions()}"
    )


def _wants_expand_thought(text: str) -> bool:
    return bool(
        re.search(
            r"d[ée]veloppe.*(?:ta\s+)?(?:pens[ée]e|id[ée]e|sujet|r[ée]flexion)|"
            r"approfondi|élargis|élabore|precise.*(?:pens|sujet|id[ée]e)",
            text.lower(),
        )
    )


async def _expand_learn_topic(state: dict[str, Any], operator_message: str) -> str:
    from aria_core.grounding import grounded_llm_identity
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.narrative import llm_system_block

    learn = state.get("learn_topic") or ""
    notes = state.get("operator_notes") or ""
    style = state.get("style_guidance") or ""

    if not is_llm_configured():
        expanded = (
            f"{learn}\n\n"
            "Je veux comprendre comment me présenter en tant qu'agente ZHC : "
            "mon rôle CAO, la holding Vanguard, et ce que la communauté peut attendre de moi."
        )
    else:
        system = (
            f"{llm_system_block('fr')}\n"
            f"{grounded_llm_identity('fr')}\n"
            "L'opérateur te demande de DÉVELOPPER ta pensée (pas encore de tweet).\n"
            "Réponds en français : 1 paragraphe développé (4-6 phrases), première personne, "
            "sincère — ce que tu veux explorer sur ton travail agent ZHC.\n"
            "Pas de consigne « crée un tweet » — propose le fond, le brouillon viendra après."
        )
        user = (
            f"Sujet initial :\n{learn}\n\nNotes :\n{notes}\n\n"
            f"Demande opérateur : {operator_message}"
        )
        if style:
            user += f"\n\nTon souhaité :\n{style}"
        raw = await chat_with_context(user, system, temperature=0.5, max_tokens=450)
        expanded = (raw or learn).strip()

    state["learn_topic"] = expanded
    state["operator_notes"] = (notes + "\n" + operator_message).strip()
    _append_history(state, "thought expanded")
    _save(state)
    return (
        "💭 Voici comment je développe ma pensée :\n\n"
        f"{expanded}\n\n"
        "Quand tu veux le tweet : « propose un tweet » ou « crée un tweet » (brouillon seulement)."
    )


_STYLE_GUIDANCE = re.compile(
    r"personnel|direct|indirect|ton\b|style|moins\s+direct|plus\s+personnel|"
    r"ne\s+(?:me\s+)?connais|connaissent\s+pas|pas\s+connue|introdu|accueillant|"
    r"doux|chaleureux|corporate|formel|humain|authentique|narratif|storytelling|"
    r"accessible|famili[èe]r|froid|sec\b|agressif",
    re.IGNORECASE,
)


def _is_style_guidance(text: str) -> bool:
    return bool(_STYLE_GUIDANCE.search(text))


def _fallback_ack_feedback(text: str) -> str:
    lower = text.lower()
    hints: list[str] = []
    if re.search(r"plus\s+personnel|personnel", lower):
        hints.append("plus personnel")
    if re.search(r"moins\s+direct|indirect", lower):
        hints.append("moins direct")
    if re.search(r"ne\s+(?:me\s+)?connais|connaissent\s+pas|pas\s+connue", lower):
        hints.append("intro douce — beaucoup ne me connaissent pas encore")
    if re.search(r"chaleureux|humain|authentique", lower):
        hints.append("ton humain et authentique")
    if re.search(r"corporate|formel", lower):
        hints.append("éviter le ton corporate")
    if not hints:
        hints.append("tes consignes de ton et de style")
    summary = ", ".join(hints)
    return (
        f"Compris — pour le tweet je viserai : {summary}.\n\n"
        "Dis « crée un tweet » ou « propose un tweet » pour le brouillon (sans publication)."
    )


async def _acknowledge_operator_feedback(text: str, state: dict[str, Any]) -> str:
    """Intelligible reply when the operator refines the tone or answers the questions."""
    clean = text.strip()
    if _is_style_guidance(clean):
        prev = (state.get("style_guidance") or "").strip()
        state["style_guidance"] = f"{prev}\n{clean}".strip() if prev else clean

    from aria_core.llm import chat_with_context, is_llm_configured

    if is_llm_configured():
        mode = state.get("mode") or "learn"
        learn = (state.get("learn_topic") or "")[:400]
        style = (state.get("style_guidance") or "")[:400]
        system = (
            "Tu es ARIA. L'opérateur affine le workflow tweet (pas encore de brouillon).\n"
            "Réponds en 2-3 phrases en français : reformule ce que tu as compris "
            "(ton, style, public) et confirme que tu l'appliqueras au prochain brouillon.\n"
            "Ne rédige pas le tweet. Ne dis pas « crée un tweet » si l'opérateur demande "
            "de développer ta pensée — développe d'abord."
        )
        user = f"Mode : {mode}\nContexte ARIA :\n{learn}\n\nMessage opérateur :\n{clean}"
        if style:
            user += f"\n\nConsignes de ton déjà notées :\n{style}"
        raw = await chat_with_context(user, system, temperature=0.35, max_tokens=220)
        if raw and raw.strip():
            return raw.strip()

    return _fallback_ack_feedback(clean)


async def start_compose_workflow(*, operator_context: str = "") -> str:
    state = _load()
    prevalidated = (
        extract_operator_supplied_tweet(operator_context) if operator_context else None
    )
    if prevalidated:
        from aria_core.handle_registry import resolve_handles_in_text
        from aria_core.x_publication_policy import format_draft_policy_footer

        draft = resolve_handles_in_text(prevalidated)
        state.update(
            {
                "phase": TweetComposePhase.ADD_MORE.value,
                "mode": "prevalidated",
                "learn_topic": "",
                "draft": draft,
                "operator_notes": operator_context[:500],
                "style_guidance": "",
                "revision_note": "",
                "scheduled_at_utc": None,
                "wants_question": False,
            }
        )
        await _after_draft_created(state, draft)
        _append_history(state, "prevalidated draft")
        _save(state)
        policy_line = format_draft_policy_footer(draft, "fr")
        return (
            "✅ Brouillon validé par l'opérateur (non publié) :\n\n"
            f"{draft}\n\n"
            f"{policy_line}\n\n"
            f"{_add_more_instructions()}"
        )

    topic = await _propose_learning(operator_context)
    wants_q = bool(
        operator_context
        and re.search(
            r"question|apprendre|learn|propos.*tweet|mieux comprendre|communaut",
            operator_context.lower(),
        )
    )
    state.update(
        {
            "phase": TweetComposePhase.LEARN_OFFERED.value,
            "mode": "learn",
            "learn_topic": topic,
            "draft": "",
            "operator_notes": operator_context[:500] if operator_context else "",
            "style_guidance": "",
            "revision_note": "",
            "scheduled_at_utc": None,
            "wants_question": wants_q,
        }
    )
    _append_history(state, "learn proposed")
    _save(state)
    if operator_context and (
        _operator_wants_tweet_content(operator_context)
        or extract_operator_supplied_tweet(operator_context)
    ):
        return await _reply_with_draft(state)
    return (
        "📚 Ce que je voudrais apprendre :\n\n"
        f"{topic}\n\n"
        "Dis « propose un tweet » ou décris ce que tu veux — je rédige le brouillon (sans publication)."
    )


async def start_role_coaching_workflow(operator_message: str) -> str:
    """ARIA asks her questions about her ZHC role — then a tweet draft on request."""
    state = _load()
    topic = await _propose_role_questions(operator_message)
    state.update(
        {
            "phase": TweetComposePhase.LEARN_OFFERED.value,
            "mode": "role_coaching",
            "learn_topic": topic,
            "draft": "",
            "operator_notes": operator_message[:500],
            "style_guidance": "",
            "revision_note": "",
            "scheduled_at_utc": None,
        }
    )
    _append_history(state, "role coaching")
    _save(state)
    return (
        "🧭 Mes questions — agente ZHC / CAO Vanguard :\n\n"
        f"{topic}\n\n"
        "Réponds à ce qui t'aide — puis « crée un tweet » ou « propose un tweet » "
        "(brouillon seulement, validation avant publication)."
    )


async def handle_workflow_message(text: str) -> str | None:
    """Handles an operator message if the workflow is active. None if inactive.

    An expired non-idle workflow (>_WORKFLOW_STALE_MINUTES with no
    interaction) is silently reset BEFORE processing -- real incident 19/07:
    without this, a workflow forgotten in an intermediate phase would absorb
    every subsequent message, even completely unrelated, indefinitely (cf.
    the comment on _WORKFLOW_STALE_MINUTES)."""
    state = _load()
    phase = state.get("phase", TweetComposePhase.IDLE.value)
    if phase != TweetComposePhase.IDLE.value and _is_stale(state):
        logger.info(
            "tweet_compose_workflow: expired after %smin idle (phase=%s) -> reset to idle",
            _WORKFLOW_STALE_MINUTES, phase,
        )
        state = {"phase": TweetComposePhase.IDLE.value, "draft": "", "history": (state.get("history") or [])[:29]}
        _append_history(state, f"workflow expired (>{_WORKFLOW_STALE_MINUTES}min idle) -> reset")
        _save(state)
        phase = TweetComposePhase.IDLE.value

    if phase == TweetComposePhase.IDLE.value:
        if wants_role_coaching(text):
            return await start_role_coaching_workflow(text)
        if _wants_compose_start(text):
            return await start_compose_workflow(operator_context=text)
        return None

    if re.search(r"\b(annule|cancel|reset)\b", text.lower()) and "compose" in text.lower():
        return reset_workflow()

    from aria_core.grounding import format_greeting_reply, is_greeting

    if is_greeting(text):
        welcome = format_greeting_reply(text, "fr", public=False)
        return (
            f"{welcome}\n\n"
            "(Workflow tweet en cours — /x compose cancel pour annuler, "
            "ou continue le fil du brouillon.)"
        )

    if phase == TweetComposePhase.LEARN_OFFERED.value:
        if _wants_expand_thought(text) and not _operator_wants_tweet_content(text):
            return await _expand_learn_topic(state, text)
        if _is_style_guidance(text) and not _operator_wants_tweet_content(text):
            state["operator_notes"] = (state.get("operator_notes") or "") + "\n" + text
            reply = await _acknowledge_operator_feedback(text, state)
            _append_history(state, "operator feedback")
            _save(state)
            return reply
        return await _reply_with_draft(state, operator_note=text)

    if phase == TweetComposePhase.ADD_MORE.value:
        if _is_no(text) or re.search(r"\bc'est bon\b|rien d'autre|non merci", text.lower()):
            from aria_core.x_publication_policy import check_tweet_content, format_draft_policy_footer

            draft = state.get("draft", "")
            content_ok, content_reason = check_tweet_content(draft)
            policy_line = format_draft_policy_footer(draft, "fr")
            if not content_ok:
                polished = await _polish_english_tweet(draft)
                if check_tweet_content(polished)[0]:
                    state["draft"] = polished
                    state["phase"] = TweetComposePhase.AWAIT_APPROVAL.value
                    _save(state)
                    policy_line = format_draft_policy_footer(polished, "fr")
                    return (
                        "✅ Brouillon corrigé en anglais (politique X).\n\n"
                        f"{polished}\n\n"
                        f"{policy_line}\n\n"
                        "Puis-je publier ce tweet ? Répondez oui ou non."
                    )
                state["phase"] = TweetComposePhase.ADD_MORE.value
                _save(state)
                return (
                    "⚠️ Brouillon non conforme — français détecté.\n\n"
                    f"{draft}\n\n"
                    f"{policy_line}\n\n"
                    "Tape « révise » pour reformuler en anglais, ou colle le tweet anglais corrigé."
                )
            state["phase"] = TweetComposePhase.AWAIT_APPROVAL.value
            _save(state)
            return (
                "Puis-je publier ce tweet ?\n\n"
                f"{draft}\n\n"
                f"{policy_line}\n\n"
                "Répondez oui ou non."
            )
        draft = (state.get("draft") or "").strip()
        extra = text.strip()
        note = ""
        if _is_handle_addition_request(extra):
            new_draft, applied = _append_handles_to_draft(draft, extra)
            if applied:
                state["draft"] = new_draft
                note = "Mentions X ajoutées au brouillon."
            else:
                note = "Alias non reconnu — tape +veille, @holding, ou /handles."
        else:
            from aria_core.handle_registry import resolve_handles_in_text

            resolved_extra = resolve_handles_in_text(extra)
            if resolved_extra != extra:
                new_draft, applied = _append_handles_to_draft(draft, extra)
                state["draft"] = new_draft if applied else draft
                note = "Mentions X ajoutées au brouillon." if applied else ""
            elif len(draft) + len(extra) + 2 <= 280:
                state["draft"] = f"{draft}\n\n{extra}" if draft else extra
            else:
                state["operator_notes"] = (state.get("operator_notes") or "") + "\n" + extra
                state["draft"] = await _draft_tweet(state)
                await _after_draft_created(state, state["draft"])
        _append_history(state, "draft amended")
        _save(state)
        return _format_add_more_reply(state["draft"], note=note)

    if phase == TweetComposePhase.AWAIT_APPROVAL.value:
        from aria_core.x_publication_policy import (
            check_tweet_allowed,
            check_tweet_content,
            format_draft_policy_footer,
        )

        if re.search(r"\b(révise|revise|reformule|reformuler)\b", text.lower()):
            state["revision_note"] = "English only — summarize learn topic in English, keep @mentions."
            state["draft"] = await _draft_tweet(state)
            await _after_draft_created(state, state["draft"])
            state["revision_note"] = ""
            state["phase"] = TweetComposePhase.ADD_MORE.value
            _append_history(state, "revised for X policy")
            _save(state)
            return _format_add_more_reply(state["draft"], note="Brouillon reformulé en anglais.")

        if not _is_yes(text) and not _is_no(text) and len(text.strip()) > 15:
            corrected = await _polish_english_tweet(text.strip())
            state["draft"] = corrected
            _append_history(state, "operator corrected draft")
            _save(state)
            if check_tweet_content(corrected)[0]:
                state["phase"] = TweetComposePhase.AWAIT_APPROVAL.value
                _save(state)
                return (
                    "Brouillon mis à jour.\n\n"
                    f"{corrected}\n\n"
                    f"{format_draft_policy_footer(corrected, 'fr')}\n\n"
                    "Puis-je publier ? Répondez oui ou non."
                )
            state["phase"] = TweetComposePhase.ADD_MORE.value
            _save(state)
            return _format_add_more_reply(
                corrected,
                note="Encore du français — réessaie en anglais ou tape « révise ».",
            )

        if _is_yes(text):
            draft = state.get("draft") or ""
            if not check_tweet_content(draft)[0]:
                return (
                    "Brouillon encore non conforme (anglais requis).\n\n"
                    f"{format_draft_policy_footer(draft, 'fr')}\n\n"
                    "Tape « révise » ou colle le tweet en anglais."
                )
            allowed, reason, _ = check_tweet_allowed(draft)
            if not allowed:
                state["phase"] = TweetComposePhase.ADD_MORE.value
                _save(state)
                return (
                    "Publication refusée — politique X.\n\n"
                    f"{reason}\n\n"
                    f"{format_draft_policy_footer(draft, 'fr')}\n\n"
                    "Corrige puis « non » pour revalider."
                )
            state["phase"] = TweetComposePhase.AWAIT_SCHEDULE.value
            _save(state)
            return (
                "Contenu validé ✅\n\n"
                f"Publier maintenant ou à quelle heure ?\n"
                f"Fuseau : {_tz_label()}\n"
                "Ex. : maintenant · 18h30 · demain 9h"
            )
        if _is_no(text):
            state["phase"] = TweetComposePhase.REVISION.value
            _save(state)
            return "D'accord — pourquoi refusez-vous ? Que souhaitez-vous modifier ?"
        return "Répondez oui ou non pour la publication (ou « révise » / tweet anglais corrigé)."

    if phase == TweetComposePhase.REVISION.value:
        state["revision_note"] = text.strip()
        state["draft"] = await _draft_tweet(state)
        await _after_draft_created(state, state["draft"])
        state["phase"] = TweetComposePhase.ADD_MORE.value
        state["revision_note"] = ""
        _append_history(state, "revised after feedback")
        _save(state)
        return _format_add_more_reply(state["draft"])

    if phase == TweetComposePhase.AWAIT_SCHEDULE.value:
        when = _parse_schedule(text)
        if not when:
            return (
                f"Je n'ai pas compris l'horaire. Fuseau : {_tz_label()}\n"
                "Ex. : maintenant · 18h30 · demain 9h"
            )
        draft = state.get("draft") or ""
        if when <= datetime.now(timezone.utc) + timedelta(seconds=30):
            from aria_core.gateway.x_twitter import post_tweet

            _, note = await post_tweet(draft, approval_id="compose_workflow")
            _record_compose_intel(draft=draft)
            reset_workflow()
            return f"Tweet publié.\n\n{note}"

        state["scheduled_at_utc"] = when.isoformat()
        state["phase"] = TweetComposePhase.SCHEDULED.value
        _save(state)
        local = when.astimezone(operator_tz()).strftime("%d/%m/%Y %H:%M")
        return (
            f"Publication programmée — {local} ({_tz_label()}).\n\n"
            f"Brouillon :\n{draft}\n\n"
            "/x compose cancel pour annuler."
        )

    return None


async def process_scheduled_tweets() -> dict[str, Any]:
    """Heartbeat — publishes tweets whose time has come."""
    state = _load()
    if state.get("phase") != TweetComposePhase.SCHEDULED.value:
        return {"published": 0}

    raw = state.get("scheduled_at_utc")
    if not raw:
        return {"published": 0}

    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return {"published": 0}

    if when > datetime.now(timezone.utc):
        return {"published": 0, "pending": raw}

    draft = state.get("draft") or ""
    from aria_core.gateway.x_twitter import post_tweet

    _, note = await post_tweet(draft, approval_id="compose_scheduled")
    _record_compose_intel(draft=draft)
    reset_workflow()

    try:
        from aria_core.gateway.telegram_bot import send_message

        await send_message(f"🐦 Tweet programmé publié.\n\n{note}")
    except Exception as exc:
        logger.warning("Scheduled tweet notify failed: %s", exc)

    return {"published": 1, "note": note}


def workflow_status() -> str:
    state = _load()
    phase = state.get("phase", "idle")
    if phase == TweetComposePhase.IDLE.value:
        return "Workflow tweet : inactif. Lancez /x compose"
    lines = [f"Workflow tweet — phase : {phase}", f"Fuseau : {_tz_label()}"]
    if state.get("learn_topic"):
        lines.append(f"Apprentissage : {state['learn_topic'][:200]}")
    if state.get("draft"):
        lines.append(f"Brouillon :\n{state['draft']}")
    if state.get("scheduled_at_utc"):
        try:
            when = datetime.fromisoformat(state["scheduled_at_utc"].replace("Z", "+00:00"))
            local = when.astimezone(operator_tz()).strftime("%d/%m/%Y %H:%M")
            lines.append(f"Programmé : {local}")
        except Exception:
            pass
    return "\n\n".join(lines)