"""Épistémique ARIA — YAML = identité/politiques ZHC ; Groq = toute question avec P(vrai)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

_EPISTEMIC_PATH = Path(__file__).parent / "epistemic_core.yaml"
_CACHE: list[dict] | None = None

STATIC_SCOPES = frozenset({"policy", "holding", "ops"})
EPISTEMIC_DIRECT_SCORE = 8
EPISTEMIC_LLM_MIN_SCORE = 4
THRESHOLD_AFFIRM = 0.85
THRESHOLD_UNCERTAIN = 0.40


@dataclass(frozen=True)
class EpistemicMatch:
    claim: dict
    score: int


def _load_epistemic() -> list[dict]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not _EPISTEMIC_PATH.exists():
        _CACHE = []
        return _CACHE
    raw = yaml.safe_load(_EPISTEMIC_PATH.read_text(encoding="utf-8")) or []
    _CACHE = raw if isinstance(raw, list) else []
    return _CACHE


def _claim_scope(claim: dict) -> str:
    scope = (claim.get("scope") or "").strip().lower()
    if scope in STATIC_SCOPES:
        return scope
    verdict = (claim.get("verdict") or "").strip().lower()
    if verdict == "policy":
        return "policy"
    tags = [str(t).lower() for t in claim.get("tags") or []]
    if any(t in tags for t in ("holding", "aria", "zhc", "dexpulse", "ops")):
        return "holding"
    return "dynamic"


def _static_claims() -> list[dict]:
    return [c for c in _load_epistemic() if _claim_scope(c) in STATIC_SCOPES]


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower().strip())
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", stripped)


def _score_claim(query: str, claim: dict) -> int:
    q = _normalize(query)
    if not q:
        return 0
    score = 0

    for trigger in claim.get("triggers") or []:
        t = _normalize(str(trigger))
        if t and t in q:
            score += 14

    question = _normalize(claim.get("question") or "")
    if question and question in q:
        score += 12
    for token in re.findall(r"[a-z0-9]{4,}", q):
        if token in question:
            score += 3

    for field in ("claim_fr", "claim_en", "tags"):
        blob = _normalize(
            " ".join(claim.get("tags") or [])
            if field == "tags"
            else str(claim.get(field) or "")
        )
        for token in re.findall(r"[a-z0-9]{4,}", q):
            if token in blob:
                score += 2

    topic = _normalize(claim.get("topic") or "")
    if topic and topic in q:
        score += 2

    return score


def search_epistemic(query: str, limit: int = 3, *, static_only: bool = False) -> list[EpistemicMatch]:
    items = _static_claims() if static_only else _load_epistemic()
    if not query.strip():
        return [EpistemicMatch(c, 1) for c in items[:limit]]
    scored = [EpistemicMatch(item, _score_claim(query, item)) for item in items]
    scored = [m for m in scored if m.score > 0]
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:limit]


def epistemic_relevance_score(query: str) -> int:
    matches = search_epistemic(query, limit=1, static_only=True)
    return matches[0].score if matches else 0


def _confidence_label(p_true: float, lang: str) -> str:
    pct = round(p_true * 100, 1 if p_true < 0.995 else 0)
    if lang == "fr":
        if p_true >= 0.95:
            return f"établi (~{pct}%)"
        if p_true >= THRESHOLD_AFFIRM:
            return f"très probable (~{pct}%)"
        if p_true >= THRESHOLD_UNCERTAIN:
            return f"incertain (~{pct}%)"
        return f"peu probable (~{pct}%)"
    if p_true >= 0.95:
        return f"established (~{pct}%)"
    if p_true >= THRESHOLD_AFFIRM:
        return f"very likely (~{pct}%)"
    if p_true >= THRESHOLD_UNCERTAIN:
        return f"uncertain (~{pct}%)"
    return f"unlikely (~{pct}%)"


def _verdict_label(verdict: str, lang: str) -> str:
    mapping_fr = {
        "established": "fait établi",
        "policy": "politique ARIA",
        "opinion": "avis calibré",
    }
    mapping_en = {
        "established": "established fact",
        "policy": "ARIA policy",
        "opinion": "calibrated opinion",
    }
    m = mapping_fr if lang == "fr" else mapping_en
    return m.get(verdict, verdict)


def format_epistemic_reply(match: EpistemicMatch, lang: str = "en") -> str:
    item = match.claim
    p_true = float(item.get("p_true", 0.5))
    p_false = float(item.get("p_false", max(0.0, 1.0 - p_true)))
    verdict = str(item.get("verdict") or "established")
    claim_text = (
        item.get("claim_fr") if lang == "fr" else item.get("claim_en")
    ) or item.get("claim_fr") or item.get("claim_en") or ""
    sources = item.get("sources") or []
    source_str = ", ".join(str(s) for s in sources[:3]) if sources else "politique ARIA"

    if lang == "fr":
        header = "Réponse calibrée (politique / holding)"
        lines = [
            header,
            "",
            claim_text.strip(),
            "",
            f"Certitude : {_confidence_label(p_true, lang)} · {_verdict_label(verdict, lang)}",
            f"P(vrai)={p_true:.4f} · P(faux)={p_false:.4f}",
            f"Sources : {source_str}",
        ]
    else:
        header = "Calibrated answer (policy / holding)"
        lines = [
            header,
            "",
            claim_text.strip(),
            "",
            f"Confidence: {_confidence_label(p_true, lang)} · {_verdict_label(verdict, lang)}",
            f"P(true)={p_true:.4f} · P(false)={p_false:.4f}",
            f"Sources: {source_str}",
        ]
    return "\n".join(lines)


def epistemic_static_answer(
    query: str, lang: str = "en",
) -> tuple[str | None, dict]:
    """YAML uniquement pour politiques / holding / ops — pas le monde général."""
    matches = search_epistemic(query, limit=3, static_only=True)
    strong = [m for m in matches if m.score >= EPISTEMIC_DIRECT_SCORE]
    if not strong:
        return None, {
            "epistemic_static": False,
            "top_score": matches[0].score if matches else 0,
        }
    best = strong[0]
    p_true = float(best.claim.get("p_true", 0.5))
    if p_true < THRESHOLD_UNCERTAIN and best.claim.get("verdict") != "policy":
        return None, {"epistemic_static": False, "low_confidence": True}
    return format_epistemic_reply(best, lang), {
        "epistemic_static": True,
        "match_id": best.claim.get("id"),
        "p_true": p_true,
        "source": "epistemic_core.yaml",
    }


def epistemic_direct_answer(query: str, lang: str = "en") -> tuple[str | None, dict]:
    """Alias rétrocompat — static only."""
    return epistemic_static_answer(query, lang)


def _today_context(lang: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if lang == "fr":
        return (
            f"DATE DU JOUR (UTC) : {today}. "
            "Les événements à cette date ne sont pas « dans le futur » — réponds avec tes connaissances "
            "ou dis INCERTAIN si tu n'as vraiment pas l'info (pas « données futures » si la date est passée ou aujourd'hui)."
        )
    return (
        f"TODAY (UTC): {today}. "
        "Events on this date are not « in the future » — answer from knowledge or say INCERTAIN; "
        "do not claim « future data » for today's events."
    )


_GROQ_CALIBRATED_PROMPT_FR = """Tu es ARIA ZHC, Chief Autonomous Officer d'Aria Vanguard ZHC.

Réponds à TOUTE question (comme Grok) avec calibration épistémique — rien n'est pré-enregistré.

{_today_context}

RÈGLES ABSOLUES :
- N'invente JAMAIS revenus, profits, métriques ou succès ARIA/GoldenFar non documentés
- Pas de conseil financier personnalisé (crypto inclus)
- Pas de faux débat « deux opinions égales » sur faits scientifiques établis
- Si tu ne sais pas : FAIT=INCERTAIN et P_VRAI bas — dis-le clairement

Réponds EXACTEMENT 5 lignes (rien d'autre) :
FAIT: VRAI ou FAUX ou INCERTAIN ou OPINION
REPONSE: <réponse claire, max 90 mots, français, texte simple Telegram>
P_VRAI: nombre 0.00 à 1.00
P_FAUX: nombre 0.00 à 1.00
RAISON: <12 mots max>"""

_GROQ_CALIBRATED_PROMPT_EN = """You are ARIA ZHC, Chief Autonomous Officer of Aria Vanguard ZHC.

Answer ANY question (Grok-style) with epistemic calibration — nothing is pre-recorded.

{_today_context}

ABSOLUTE RULES:
- NEVER invent ARIA/GoldenFar revenue, profits, metrics, or undocumented success
- No personalized financial advice (crypto included)
- No false balance on established science vs conspiracy
- If unsure: FAIT=INCERTAIN and low P_VRAI — say so clearly

Reply EXACTLY 5 lines (nothing else):
FAIT: VRAI or FAUX or INCERTAIN or OPINION
REPONSE: <clear answer, max 90 words, plain Telegram text>
P_VRAI: number 0.00 to 1.00
P_FAUX: number 0.00 to 1.00
RAISON: <12 words max>"""


def groq_reponse_only(raw: str) -> str:
    """Extrait la ligne REPONSE du format calibré Groq (sans en-tête épistémique)."""
    for line in (raw or "").strip().splitlines():
        upper = line.strip().upper()
        if upper.startswith("REPONSE:") or upper.startswith("RÉPONSE:"):
            return line.split(":", 1)[-1].strip()
    return ""


def _parse_groq_calibrated(raw: str, lang: str) -> tuple[str | None, dict]:
    fait = "INCERTAIN"
    reponse = ""
    p_vrai = 0.0
    p_faux = 0.0
    raison = "groq"

    for line in (raw or "").strip().splitlines():
        upper = line.strip().upper()
        if upper.startswith("FAIT:"):
            fait = upper.split(":", 1)[-1].strip()
        elif upper.startswith("REPONSE:") or upper.startswith("RÉPONSE:"):
            reponse = line.split(":", 1)[-1].strip()
        elif upper.startswith("P_VRAI:") or upper.startswith("P-VRAI:"):
            try:
                p_vrai = float(re.sub(r"[^0-9.]", "", line.split(":", 1)[-1]) or "0")
            except ValueError:
                p_vrai = 0.0
        elif upper.startswith("P_FAUX:") or upper.startswith("P-FAUX:"):
            try:
                p_faux = float(re.sub(r"[^0-9.]", "", line.split(":", 1)[-1]) or "0")
            except ValueError:
                p_faux = 0.0
        elif upper.startswith("RAISON:"):
            raison = line.split(":", 1)[-1].strip()[:80]

    meta = {
        "groq_calibrated": True,
        "p_true": p_vrai,
        "p_false": p_faux,
        "truth": fait.lower(),
        "source": "groq_calibrated",
    }

    if not reponse:
        return None, {**meta, "empty": True}

    unknown_phrases = ("je ne sais pas", "i don't know", "pas d'information")
    if any(p in reponse.lower() for p in unknown_phrases) and p_vrai < THRESHOLD_UNCERTAIN:
        return None, {**meta, "abstain": True}

    header = "Réponse calibrée (LLM)" if lang == "fr" else "Calibrated answer (LLM)"
    truth_fr = {
        "VRAI": "fait vrai",
        "TRUE": "fait vrai",
        "FAUX": "fait faux",
        "FALSE": "fait faux",
        "INCERTAIN": "incertain",
        "OPINION": "opinion",
    }
    truth_label = truth_fr.get(fait.split()[0] if fait else "", fait.lower())

    body = (
        f"{header}\n\n{reponse}\n\n"
        f"FAIT : {truth_label}\n"
        f"P(vrai)={p_vrai:.2f} · P(faux)={p_faux:.2f}\n"
        f"Certitude : {_confidence_label(p_vrai, lang)} — {raison}"
    )
    return body, meta


async def groq_calibrated_answer(query: str, lang: str = "fr") -> tuple[str | None, dict]:
    """Moteur principal — toute question via Groq + probabilités."""
    from aria_core.llm import chat_with_context, is_llm_configured

    if not is_llm_configured():
        return None, {"groq_calibrated": False}

    lang_key = "fr" if lang == "fr" else "en"
    tpl = _GROQ_CALIBRATED_PROMPT_FR if lang_key == "fr" else _GROQ_CALIBRATED_PROMPT_EN
    prompt = tpl.format(_today_context=_today_context(lang_key))
    raw = await chat_with_context(
        query[:600],
        prompt,
        temperature=0.15,
        max_tokens=220,
    )
    if not raw or "FAIT:" not in raw.upper():
        return None, {"groq_calibrated": True, "empty": True}
    return _parse_groq_calibrated(raw, lang)


async def groq_factual_answer(query: str, lang: str = "fr") -> tuple[str | None, dict]:
    """Alias — même moteur."""
    return await groq_calibrated_answer(query, lang)


async def resolve_calibrated_answer(
    query: str, lang: str = "fr", *, public: bool = True,
) -> tuple[str | None, dict]:
    """Politique/holding YAML si match, sinon Groq + vérif web si incertain.

    `public` doit venir du VRAI expéditeur de CE message (brain.py._general_response),
    jamais d'un défaut supposé -- corrigé le 09/07 : ce paramètre n'existait pas, le signal
    public=False de l'opérateur (pourtant correctement calculé dans brain.py) était donc
    perdu à cet appel, et should_use_web_verify() se rabattait sur un réglage de déploiement
    global toujours permissif (cf. son propre correctif). `web_topic_ok` re-vérifie en plus
    le sujet à CHAQUE branche de repli (pas seulement la première) pour l'opérateur -- sans
    ça, un futur faux positif de is_live_info_question/is_explicit_web_request à l'entrée de
    brain.py suffirait à rouvrir une recherche web incontrôlée dès que Groq hésite, quel que
    soit le sujet réel.
    """
    from aria_core.knowledge.web_verify import (
        is_ecosystem_product_query,
        is_explicit_web_request,
        is_live_info_question,
        is_operator_local_question,
        should_use_web_verify,
        web_first_answer,
    )
    from aria_core.memory.self_context import is_self_context_question
    from aria_core.operator_readiness import wants_operator_status_pulse

    if is_self_context_question(query):
        return None, {"self_context": True, "skip_web": True}

    if wants_operator_status_pulse(query) or is_operator_local_question(query):
        return None, {"operator_local": True, "skip_web": True}

    static, static_data = epistemic_static_answer(query, lang)
    if static:
        return static, static_data

    use_web = should_use_web_verify(query, public=public)
    web_topic_ok = public or is_live_info_question(query) or is_explicit_web_request(query)

    if use_web and web_topic_ok:
        wf_reply, wf_meta = await web_first_answer(query, lang, public=public)
        if wf_reply:
            return wf_reply, wf_meta

    reply, meta = await groq_calibrated_answer(query, lang)
    if not reply or meta.get("abstain") or meta.get("empty"):
        if use_web and web_topic_ok and not is_ecosystem_product_query(query):
            wf_reply, wf_meta = await web_first_answer(query, lang, public=public)
            if wf_reply:
                return wf_reply, wf_meta
        return None, meta

    if meta.get("groq_calibrated"):
        from aria_core.knowledge.epistemic_pipeline import enhance_calibrated_answer

        reply, meta = await enhance_calibrated_answer(query, reply, meta, lang, public=public)

    if not reply or float(meta.get("p_true", 0)) < 0.65:
        if use_web and web_topic_ok and not is_ecosystem_product_query(query):
            wf_reply, wf_meta = await web_first_answer(query, lang, public=public)
            if wf_reply:
                return wf_reply, wf_meta

    return reply, meta


async def resolve_factual_answer(query: str, lang: str = "fr") -> tuple[str | None, dict]:
    """Alias rétrocompat."""
    return await resolve_calibrated_answer(query, lang)


def epistemic_context_block(query: str, limit: int = 3) -> str:
    matches = search_epistemic(query, limit=limit, static_only=True)
    if not matches:
        return ""
    lines = ["## [Epistemic policies] (holding / ZHC rules only)"]
    for m in matches:
        cid = m.claim.get("id", "?")
        p = float(m.claim.get("p_true", 0.5))
        text = m.claim.get("claim_en") or m.claim.get("claim_fr") or ""
        lines.append(f"- [{cid}] P(true)={p:.2f}: {text[:200]}")
    return "\n".join(lines)