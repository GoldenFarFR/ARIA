"""Vérification web ciblée — DuckDuckGo instant + HTML fallback (sans clé API)."""

from __future__ import annotations

import html as html_module
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

import httpx


@dataclass(frozen=True)
class WebSource:
    text: str
    url: str = ""

logger = logging.getLogger(__name__)

_DDG_API = "https://api.duckduckgo.com/"
_DDG_HTML = "https://html.duckduckgo.com/html/"
_USER_AGENT = "Mozilla/5.0 (compatible; ARIA-ZHC/1.0)"

_LIVE_INFO_RE = re.compile(
    r"rugby|stade\s+toulousain|toulousain|top\s*14|top14|"
    r"coupe du monde|world cup|match|fixture|football|soccer|"
    r"nba|tennis|formule\s*1|f1\b|"
    r"bitcoin|btc|crypto|ethereum|eth\b|"
    r"prix|cours|baisse|hausse|monte|descend|"
    r"actu|actualité|news|"
    r"aujourd|today|ce soir|tonight|demain|tomorrow|"
    r"quelle?\s+heure|à\s+quelle\s+heure|what\s+time|when\s+does|when\s+is",
    re.I,
)


def _resolve_ddg_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        q = parse_qs(parsed.query)
        uddg = (q.get("uddg") or [""])[0]
        if uddg:
            return unquote(uddg)
    return href


def _as_source(text: str, url: str = "") -> WebSource | None:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) < 15:
        return None
    return WebSource(text=text[:280], url=(url or "").strip())


def is_live_info_question(query: str) -> bool:
    """Sport, horaires, actu jour J — nécessite souvent une recherche web."""
    if is_ecosystem_product_query(query):
        return False
    return bool(_LIVE_INFO_RE.search(query))


def is_ecosystem_product_query(query: str) -> bool:
    """Produits ARIA — pas de recherche web (évite APK/clones hors écosystème)."""
    lower = (query or "").lower()
    if not re.search(r"aria\s+market|aria\s+vanguard|goldenfar|ariavanguardzhc", lower):
        return False
    return bool(re.search(r"apk|télécharger|telecharger|download|play\s*store|app\s*store", lower))


def _query_variants(query: str) -> list[str]:
    """Plusieurs formulations — l'API instant DDG rate souvent le sport / actu."""
    q = query.strip()
    if len(q) < 4:
        return []
    today = datetime.now(timezone.utc)
    iso = today.strftime("%Y-%m-%d")
    fr_date = today.strftime("%d %B %Y")
    lower = q.lower()
    variants = [q]
    if re.search(r"aujourd|today|ce jour|this day", lower):
        variants.append(f"{q} {iso}")
    if re.search(r"coupe du monde|world cup", lower):
        variants.append(f"FIFA World Cup 2026 fixtures {iso}")
        variants.append(f"World Cup 2026 matches {today.strftime('%B %d %Y')}")
    if re.search(r"rugby|stade\s+toulousain|toulousain|top\s*14|top14", lower):
        variants.append(f"Stade Toulousain match horaire {fr_date}")
        variants.append(f"Top 14 demi-finale {iso} horaire")
    if re.search(r"match|joue|fixture|heure", lower) and "rugby" not in lower:
        variants.append(f"{q} {iso}")
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            out.append(v[:200])
    return out[:5]


async def _fetch_ddg_once(client: httpx.AsyncClient, q: str) -> list[WebSource]:
    sources: list[WebSource] = []
    try:
        resp = await client.get(
            _DDG_API,
            params={"q": q, "format": "json", "no_redirect": 1, "no_html": 1},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("web_verify DDG API failed for %r: %s", q[:40], exc)
        return sources

    abstract = (data.get("AbstractText") or "").strip()
    if abstract and len(abstract) > 20:
        src = _as_source(abstract, data.get("AbstractURL") or "")
        if src:
            sources.append(src)

    def _walk_topics(topics: list) -> None:
        for topic in topics or []:
            if not isinstance(topic, dict):
                continue
            if topic.get("Topics"):
                _walk_topics(topic["Topics"])
                continue
            text = (topic.get("Text") or "").strip()
            url = topic.get("FirstURL") or ""
            src = _as_source(text, url)
            if src:
                sources.append(src)

    _walk_topics(data.get("RelatedTopics") or [])
    return sources


def _parse_ddg_html(html: str) -> list[WebSource]:
    blocks = re.findall(
        r'class="result__body".*?</div>\s*</div>',
        html,
        re.S,
    )
    sources: list[WebSource] = []
    for block in blocks:
        href_m = re.search(r'class="result__a"[^>]*href="([^"]+)"', block)
        snip_m = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        if not snip_m:
            continue
        text = html_module.unescape(re.sub(r"<[^>]+>", "", snip_m.group(1)))
        url = _resolve_ddg_url(href_m.group(1)) if href_m else ""
        src = _as_source(text, url)
        if src:
            sources.append(src)
    if sources:
        return sources

    raw_snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    raw_urls = re.findall(r'class="result__a"[^>]*href="([^"]+)"', html)
    for i, chunk in enumerate(raw_snips):
        text = html_module.unescape(re.sub(r"<[^>]+>", "", chunk))
        url = _resolve_ddg_url(raw_urls[i]) if i < len(raw_urls) else ""
        src = _as_source(text, url)
        if src:
            sources.append(src)
    return sources


async def _fetch_ddg_html(client: httpx.AsyncClient, q: str) -> list[str]:
    headers = {"User-Agent": _USER_AGENT}
    for attempt in (
        lambda: client.post(_DDG_HTML, data={"q": q}, headers=headers, follow_redirects=True),
        lambda: client.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": q},
            headers=headers,
            follow_redirects=True,
        ),
    ):
        try:
            resp = await attempt()
            resp.raise_for_status()
            parsed = _parse_ddg_html(resp.text)
            if parsed:
                return parsed
        except Exception as exc:
            logger.warning("web_verify DDG HTML failed for %r: %s", q[:40], exc)
    return []


async def fetch_web_snippets(query: str, max_snippets: int = 4, **_kwargs: object) -> list[WebSource]:
    if is_ecosystem_product_query(query):
        return []
    variants = _query_variants(query)
    if not variants:
        return []

    from aria_core.knowledge.ddg_cache import get_cached, set_cached

    cached = get_cached(query)
    if cached:
        return [
            WebSource(text=c.text, url=c.url)
            for c in cached[:max_snippets]
        ]

    sources: list[WebSource] = []
    seen_text: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=14.0) as client:
            for q in variants:
                for src in await _fetch_ddg_once(client, q):
                    key = src.text.lower()[:80]
                    if key not in seen_text:
                        seen_text.add(key)
                        sources.append(src)
                    if len(sources) >= max_snippets:
                        return sources[:max_snippets]
            if not sources:
                for q in variants:
                    for src in await _fetch_ddg_html(client, q):
                        key = src.text.lower()[:80]
                        if key not in seen_text:
                            seen_text.add(key)
                            sources.append(src)
                        if len(sources) >= max_snippets:
                            return sources[:max_snippets]
    except Exception as exc:
        logger.warning("web_verify failed: %s", exc)
    result = sources[:max_snippets]
    if result:
        set_cached(query, result)
    return result


def _web_verify_threshold(meta: dict) -> bool:
    p_true = float(meta.get("p_true", meta.get("p_vrai", 0.5)))
    truth = str(meta.get("truth", meta.get("fait", ""))).upper()
    if "INCERTAIN" in truth or "UNCERTAIN" in truth:
        return True
    if p_true < 0.65:
        return True
    return False


_WEB_RECAL_PROMPT_FR = """Tu es ARIA ZHC. Des extraits web viennent d'être récupérés.

DATE DU JOUR (UTC) : {today}

RÈGLES : base ta réponse sur les extraits si pertinents ; cite l'horaire/date si présent.
N'invente pas de faits ARIA/GoldenFar non documentés.
Ne dis pas « données futures » si la question concerne aujourd'hui.

Extraits web :
{snippets}

Question : {query}

Réponds EXACTEMENT 5 lignes :
FAIT: VRAI ou FAUX ou INCERTAIN ou OPINION
REPONSE: <réponse DIRECTE à la question en 1-2 phrases nettes, max 60 mots, sans répétition>
P_VRAI: 0.00 à 1.00
P_FAUX: 0.00 à 1.00
RAISON: <12 mots max>"""

_WEB_RECAL_PROMPT_EN = """You are ARIA ZHC. Web snippets were fetched.

TODAY (UTC): {today}

RULES: base answer on snippets when relevant; cite time/date if present.
Never invent undocumented ARIA/GoldenFar facts.

Web snippets:
{snippets}

Question: {query}

Reply EXACTLY 5 lines:
FAIT: VRAI or FAUX or INCERTAIN or OPINION
REPONSE: <DIRECT answer in 1-2 clear sentences, max 60 words, no repetition>
P_VRAI: 0.00 to 1.00
P_FAUX: 0.00 to 1.00
RAISON: <12 words max>"""


async def web_enhance_calibrated(
    query: str,
    reply: str | None,
    meta: dict,
    lang: str = "fr",
    *,
    force: bool = False,
) -> tuple[str | None, dict]:
    """Re-calibre via Groq + extraits web si incertain (ou force=True)."""
    from aria_core.knowledge.epistemic import _parse_groq_calibrated
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.runtime import settings

    if not getattr(settings, "aria_epistemic_web_verify", True):
        return reply, meta
    if not force and not _web_verify_threshold(meta):
        return reply, meta

    from aria_core.knowledge.epistemic import groq_reponse_only
    from aria_core.presentation import format_live_info_response

    sources = await fetch_web_snippets(query)
    if not sources:
        hint = (
            "\n\n(Sources web insuffisantes pour l'actu — précise la date ou le championnat.)"
            if lang == "fr"
            else "\n\n(Web sources insufficient — specify date or league.)"
        )
        if reply and "non disponible" in reply.lower():
            return reply + hint, {**meta, "web_verify": "no_snippets"}
        return reply, {**meta, "web_verify": "no_snippets"}

    if not is_llm_configured():
        brief = format_live_info_response(
            None, sources, lang=lang, query=query, fallback=True,
        )
        return brief, {
            **meta,
            "web_verify": "snippets_only",
            "web_snippets": len(sources),
            "source": "web_snippets",
        }

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tpl = _WEB_RECAL_PROMPT_FR if lang == "fr" else _WEB_RECAL_PROMPT_EN
    prompt = tpl.format(
        snippets="\n".join(
            f"- {s.text}" + (f" ({s.url})" if s.url else "") for s in sources
        ),
        query=query[:400],
        today=today,
    )
    raw = await chat_with_context(query[:400], prompt, temperature=0.1, max_tokens=280)
    if not raw or "FAIT:" not in raw.upper():
        brief = format_live_info_response(
            None, sources, lang=lang, query=query, fallback=True,
        )
        return brief, {
            **meta,
            "web_verify": "snippets_fallback",
            "web_snippets": len(sources),
            "source": "web_snippets",
        }

    direct = groq_reponse_only(raw)
    if not direct:
        brief = format_live_info_response(
            None, sources, lang=lang, query=query, fallback=True,
        )
        return brief, {
            **meta,
            "web_verify": "snippets_fallback",
            "web_snippets": len(sources),
            "source": "web_snippets",
        }

    new_reply, new_meta = _parse_groq_calibrated(raw, lang)
    new_meta["web_verified"] = True
    new_meta["web_verify"] = "recalibrated"
    new_meta["web_snippets"] = len(sources)
    new_meta["source"] = "groq_web_verified"
    new_meta["groq_calibrated"] = True
    formatted = format_live_info_response(
        direct, sources, lang=lang, query=query, fallback=False,
    )
    return formatted, new_meta


async def web_first_answer(query: str, lang: str = "fr") -> tuple[str | None, dict]:
    """Recherche web puis Groq — pour actu/sport quand Groq seul échoue."""
    meta = {"p_true": 0.3, "truth": "INCERTAIN", "groq_calibrated": False}
    reply, meta = await web_enhance_calibrated(query, None, meta, lang, force=True)
    if reply:
        return reply, meta

    from aria_core.knowledge.epistemic import groq_calibrated_answer

    g_reply, g_meta = await groq_calibrated_answer(query, lang)
    if g_reply and not g_meta.get("abstain"):
        return g_reply, g_meta
    return None, meta


def should_web_verify(meta: dict) -> bool:
    return _web_verify_threshold(meta)