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

_LIVE_INFO_STRONG_RE = re.compile(
    r"rugby|stade\s+toulousain|toulousain|top\s*14|top14|"
    r"coupe du monde|world cup|\bmatchs?\b|\bmatche[sd]?\b|fixture|football|soccer|"
    r"\bnba\b|tennis|formule\s*1|\bf1\b|"
    r"bitcoin|\bbtc\b|crypto|ethereum|\beth\b|"
    r"\bprix\b|\bprices?\b|\bbaisse\b|\bhausse\b|"
    r"\bmonte\b|\bmonter\b|\bdescend\b|\bdescendre\b|\bdescendu\w*\b|"
    r"\bpump(?:e|ing|é)?\b|\bdump(?:e|ing|é)?\b|\bath\b|plus\s+haut\s+historique|all[\s-]time\s+high|to\s+the\s+moon|\brekt\b|"
    r"\bactu\b|actualité|news",
    re.I,
)
# "cours" à part : homographe (cotation vs classe/cours de yoga) -- cf. _J_AI_COURS_RE.
_LIVE_INFO_CORE_RE = re.compile(_LIVE_INFO_STRONG_RE.pattern + r"|\bcours\b", re.I)
_TIME_RE = re.compile(
    r"quelle?\s+heure|à\s+quelle\s+heure|what\s+time|when\s+does|when\s+is", re.I
)
_LIVE_INFO_RE = re.compile(_LIVE_INFO_CORE_RE.pattern + "|" + _TIME_RE.pattern, re.I)
# NB : "aujourd'hui/ce soir/demain" seuls ne déclenchent PLUS le chemin web (retiré, 09/07) --
# trop de faux positifs sur du smalltalk banal ("comment vas-tu aujourd'hui ?"). Ces mots
# restent utiles pour DATER une requête déjà légitime (cf. _query_variants ci-dessous), mais
# ne doivent plus, seuls, décider qu'une question est "de l'actu".
#
# NB 2 (09/07, test 500 cas) : mots courts bornés en \b -- "cours"/"actu"/"match"/"monte"/
# "descend" matchaient en sous-chaîne dans des mots sans rapport (parcours, discours, concours,
# recours, secours, actuellement, actuelle, matcha, remonte, redescendre...). Bornage sans
# perte de couverture ("actualité" reste sa propre alternative, "match" garde pluriel/conjugué).
#
# NB 3 (09/07, test 500 cas x3) : _LIVE_INFO_CORE_RE séparé de _TIME_RE pour pouvoir exclure
# "à quelle heure on se voit demain ?" (planning perso, PAS de l'actu) sans toucher "à quelle
# heure joue le match" (sport, légitime) -- cf. _PERSONAL_MEETING_RE dans is_live_info_question.
# "cours" reste un homographe non résolu (classe de yoga vs cours de bourse) hors _J_AI_COURS_RE
# (cas fréquent isolé) -- limite assumée d'un filtre par mots-clés, pas de vraie ambiguïté NLP.
_PERSONAL_MEETING_RE = re.compile(
    r"on\s+se\s+voit|se\s+revoit|rendez-vous|\brdv\b|"
    r"notre\s+(?:call|point|r[ée]union|meeting|rdv)|"
    r"works?\s+best\s+for\s+you|good\s+time\s+for\s+you|suits?\s+you\s+best",
    re.I,
)
_J_AI_COURS_RE = re.compile(
    r"j'ai\s+cours\b|\bcours\s+de\s+(?:yoga|maths?|sport|danse|musique|anglais|fran[çc]ais|guitare|piano)\b",
    re.I,
)

# Demande EXPLICITE de recherche/vérification web -- distinct de is_live_info_question
# (actu/sport/prix). Sert le principe opérateur : si l'assistant (Claude Code) n'a pas
# accès web depuis sa session, il passe par ARIA (qui, elle, a Tavily) -- ex. vérifier un
# label Etherscan/Arkham, une adresse, une source. Sans ce déclencheur dédié, ces demandes
# ne matchaient aucun mot-clé de _LIVE_INFO_RE et tombaient sur une réponse de mémoire.
#
# _GAP tolère 0-2 mots de remplissage naturels entre le verbe et la cible ("cherche VITE FAIT
# sur internet") -- sûr car is_explicit_web_request revérifie ensuite _NEGATED_WEB_REQUEST_RE,
# donc une négation qui profite du même gap ("cherche PAS sur internet") est re-supprimée juste
# après, jamais renvoyée telle quelle.
_WEB_TARGET = r"(?:sur\s+(?:le\s+)?(?:web|internet)|en\s*ligne)"
_GAP = r"(?:\s+\w+){0,2}\s+"

_EXPLICIT_WEB_REQUEST_RE = re.compile(
    rf"v[ée]rifie(?:r|s|z)?{_GAP}{_WEB_TARGET}|"
    rf"cherche(?:r|s|z)?{_GAP}{_WEB_TARGET}|"
    rf"regarde(?:r|s|z)?{_GAP}{_WEB_TARGET}|"
    rf"creuse(?:r|s|z)?{_GAP}{_WEB_TARGET}|"
    r"recherch(?:e|er|es|ez)\s+(?:sur\s+)?(?:le\s+)?(?:web|internet|en\s*ligne)|"
    r"confirme(?:r)?\s+(?:via|avec)\s+une\s+recherche|"
    r"fai(?:s|t|sons|tes|re)\s+une\s+recherche|"
    r"fai(?:s|t|sons|tes)\s+un\s+tour\s+(?:sur\s+(?:le\s+)?(?:web|internet)|en\s*ligne)|"
    r"jet(?:te|ter|tez|er|ez)\s+un\s+(?:œil|oeil)\s+(?:sur\s+(?:le\s+)?(?:web|internet)|en\s*ligne)|"
    r"search(?:ing)?\s+(?:the\s+)?(?:web|internet|online)|"
    r"check(?:ing)?\s+(?:this\s+)?(?:online|on\s+(?:the\s+)?(?:web|internet))|"
    rf"check(?:er|es?|ez)?{_GAP}{_WEB_TARGET}|"
    r"check(?:er|es?|ez)?(?:\s+\w+){0,2}\s+le\s+web\b|"
    r"verify(?:ing)?\s+(?:this\s+)?(?:online|on\s+(?:the\s+)?(?:web|internet))|"
    r"look\s+(?:this\s+)?up\s+online|"
    r"take\s+a\s+look\s+online|(?:have|having)\s+a\s+look\s+(?:online|on\s+(?:the\s+)?(?:web|internet))|"
    r"do\s+(?:some\s+)?research\s+online|research\s+this\s+online|"
    r"dig(?:ging)?\s+up(?:\s+\w+){0,3}\s+online",
    re.I,
)

# Négation de la demande -- trouvé en testant le filtre sur 500 cas (09/07) : "inutile de
# chercher sur internet", "don't search the web"... matchaient _EXPLICIT_WEB_REQUEST_RE tel
# quel (le verbe + sa cible apparaissent bien dans la phrase, la négation seule ne les sépare
# pas). Couvre aussi le "ne" élidé à l'oral/texto ("cherche pas sur internet").
_NEG_GAP = r"(?:\s+\w+){0,2}"

_NEGATED_WEB_REQUEST_RE = re.compile(
    rf"(?:ne\s+)?cherche\w*{_NEG_GAP}\s+pas|"
    rf"(?:ne\s+)?recherch\w*{_NEG_GAP}\s+pas|"
    rf"(?:ne\s+)?v[ée]rifie\w*{_NEG_GAP}\s+pas|"
    rf"(?:ne\s+)?regarde\w*{_NEG_GAP}\s+pas|"
    r"(?:ne\s+)?fais\s+pas|"
    r"pas\s+(?:\w+\s+){0,3}(?:à\s+)?(?:chercher|v[ée]rifier|rechercher|creuser|regarder)|"
    r"pas?\s+besoin\s+de\s+(?:chercher|rechercher|v[ée]rifier|regarder|creuser|check\w*)|"
    r"aucun\s+besoin\s+de\s+(?:chercher|rechercher|v[ée]rifier|regarder|creuser|check\w*)|"
    r"aucun\s+int[ée]r[êe]t\s+(?:à|a)\s+(?:chercher|v[ée]rifier|rechercher|regarder|creuser|check\w*)|"
    r"(?:inutile?|pas\s+la\s+peine)\s+de\s+(?:chercher|rechercher|v[ée]rifier|regarder|creuser|check\w*|faire\s+une\s+recherche)|"
    r"n'?utilise\w*\s+pas\s+de\s+recherche|"
    r"[ée]vite(?:r)?\s+de\s+(?:chercher|v[ée]rifier|rechercher|regarder|creuser)|"
    r"arr[êe]te(?:r)?\s+de\s+(?:chercher|v[ée]rifier|rechercher|regarder|creuser)|"
    r"oublie(?:r)?\s+(?:la\s+)?recherche|"
    r"laisse\s+tomber\s+(?:la\s+)?recherche|"
    r"(?:ça|cela|ca)\s+sert\s+à\s+rien\s+de\s+(?:chercher|v[ée]rifier|rechercher|regarder)|"
    r"rien\s+ne\s+sert\s+de\s+(?:chercher|v[ée]rifier|rechercher|regarder)|"
    r"gagnerai(?:t|s|ent)?\s+rien\s+à\s+(?:chercher|v[ée]rifier|rechercher|faire\s+une\s+recherche)|"
    r"don'?t\s+(?:bother\s+)?(?:search|check)|do\s+not\s+(?:search|check)|"
    r"don'?t\s+look\s+(?:this\s+)?up|do\s+not\s+look\s+(?:this\s+)?up|"
    r"don'?t\s+have\s+to\s+look|don'?t\s+need\s+to\s+(?:search|look|verify|check|research)|"
    r"let'?s\s+not\s+(?:search|check)|"
    r"no\s+need\s+to\s+(?:search|look|verify|check|research)|"
    r"not\s+necessary\s+to\s+search|"
    r"why\s+bother\s+(?:search|check)|"
    r"no\s+point\s+(?:in\s+)?(?:search\w*|look\w*|check\w*)",
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


def is_operator_local_question(query: str) -> bool:
    """Questions opérateur ARIA — jamais de DuckDuckGo (impôts, admin perso, etc.)."""
    from aria_core.operator_readiness import wants_operator_status_pulse

    if wants_operator_status_pulse(query):
        return True
    lower = (query or "").lower()
    if re.search(
        r"d[eé]clar(?:ation|er)|imp[oô]t|fiscal|urssaf|caf\b|"
        r"runbook|aria-worker|ouvrier|worker\s+queue|"
        r"collegue\.md|journal\s+aria|check-aria-status",
        lower,
    ):
        return True
    return False


def should_use_web_verify(query: str, *, public: bool = True) -> bool:
    """Web autorisé : visiteurs publics, ou opérateur + actu live/demande explicite.

    `public` doit refléter CE MESSAGE précis (opérateur vs visiteur), pas un réglage de
    déploiement global. Avant ce correctif (09/07), la fonction lisait is_public_mode()
    (ARIA_PUBLIC_MODE — réglage de déploiement, TOUJOURS True en prod) au lieu du `public`
    par-message pourtant correctement calculé dans brain.py : elle renvoyait donc
    systématiquement True, quel que soit l'expéditeur ou le sujet, cassant la protection
    is_operator_local_question/is_live_info_question pour l'opérateur (le signal
    public=False ne remontait jamais jusqu'ici — cf. resolve_calibrated_answer/
    _general_response). Incident réel : une question opérateur auto-réflexive ("remonte-moi
    tous les bugs détectés") a déclenché une recherche web hors-sujet, présentée comme
    "ACTU — sources web vérifiées".
    """
    if is_operator_local_question(query):
        return False
    if public:
        return True
    return is_live_info_question(query) or is_explicit_web_request(query)


def is_live_info_question(query: str) -> bool:
    """Sport, horaires, actu jour J — nécessite souvent une recherche web."""
    if is_ecosystem_product_query(query):
        return False
    if is_operator_local_question(query):
        return False
    if not _LIVE_INFO_STRONG_RE.search(query):
        if _J_AI_COURS_RE.search(query):
            # "j'ai cours" / "cours de yoga" -- homographe de "cours" (classe, pas cotation) --
            # et aucun autre signal fort (bitcoin/prix/actu/...) ne vient corroborer l'actu.
            return False
        if _TIME_RE.search(query) and _PERSONAL_MEETING_RE.search(query):
            # "à quelle heure on se voit demain ?" -- planning perso, pas de l'actu sportive.
            return False
    return bool(_LIVE_INFO_RE.search(query))


def is_explicit_web_request(query: str) -> bool:
    """Demande EXPLICITE de recherche/vérification web (ex. "vérifie sur le web...",
    "cherche sur internet..."), indépendamment du sujet -- voir _EXPLICIT_WEB_REQUEST_RE.
    Une négation ("inutile de chercher...", "don't search...") annule la détection."""
    if is_ecosystem_product_query(query):
        return False
    if is_operator_local_question(query):
        return False
    if _NEGATED_WEB_REQUEST_RE.search(query):
        return False
    return bool(_EXPLICIT_WEB_REQUEST_RE.search(query))


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


def _web_search_provider() -> str:
    """Fournisseur de recherche web actif (défaut : ddg gratuit). Bascule opt-in vers
    'tavily' via ARIA_WEB_SEARCH_PROVIDER + TAVILY_API_KEY (cf. aria_values free_brain)."""
    from aria_core.runtime import settings

    return str(getattr(settings, "aria_web_search_provider", "ddg") or "ddg").strip().lower()


async def _fetch_tavily_snippets(query: str, max_snippets: int) -> list[WebSource]:
    """Provider Tavily (dôme). Dégradation douce : liste vide si indisponible."""
    from aria_core.services.tavily import is_tavily_configured, tavily_client

    if not is_tavily_configured():
        return []
    result = await tavily_client.search(query, max_results=max_snippets)
    if not result.available:
        logger.info("web_verify tavily indisponible: %s", result.error)
        return []
    sources: list[WebSource] = []
    # La réponse synthétique Tavily d'abord (souvent la plus directe), puis les extraits.
    if result.answer:
        src = _as_source(result.answer)
        if src:
            sources.append(src)
    for text, url in result.snippets:
        src = _as_source(text, url)
        if src:
            sources.append(src)
        if len(sources) >= max_snippets:
            break
    return sources[:max_snippets]


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

    # Provider opt-in : Tavily si configuré/activé, sinon DuckDuckGo (défaut gratuit).
    if _web_search_provider() == "tavily":
        tavily_sources = await _fetch_tavily_snippets(query, max_snippets)
        if tavily_sources:
            set_cached(query, tavily_sources)
            return tavily_sources
        # Tavily indisponible (clé absente, quota, panne) -> dégradation douce sur DDG.

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
AVANT d'utiliser un extrait : vérifie qu'il parle bien de la MÊME compétition/événement
que la question (même équipes ≠ même compétition — ex. Ligue des Nations ≠ Coupe du monde,
match amical ≠ match officiel). Un extrait sur un autre événement doit être ignoré.
Si le résultat dépend d'un tour/match qui n'est pas encore joué ou terminé (ex. l'adversaire
en demi-finale n'est pas encore connu tant que le quart de finale n'est pas fini), réponds
FAIT: INCERTAIN et dis explicitement que ce n'est pas encore déterminé — n'invente jamais
un adversaire ou un résultat plausible mais non confirmé.

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
BEFORE using a snippet: verify it is about the SAME competition/event as the question
(same teams != same competition — e.g. Nations League != World Cup, friendly != official
match). Ignore any snippet about a different event.
If the outcome depends on a round/match not yet played or finished (e.g. the semifinal
opponent is unknown until the quarterfinal concludes), reply FAIT: INCERTAIN and state
explicitly that it is not yet determined — never invent a plausible but unconfirmed
opponent or result.

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
    public: bool = True,
) -> tuple[str | None, dict]:
    """Re-calibre via Groq + extraits web si incertain (ou force=True)."""
    from aria_core.knowledge.epistemic import _parse_groq_calibrated
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.runtime import settings

    if not getattr(settings, "aria_epistemic_web_verify", True):
        return reply, meta
    if not should_use_web_verify(query, public=public) and not force:
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


async def web_first_answer(query: str, lang: str = "fr", *, public: bool = True) -> tuple[str | None, dict]:
    """Recherche web puis Groq — pour actu/sport quand Groq seul échoue."""
    meta = {"p_true": 0.3, "truth": "INCERTAIN", "groq_calibrated": False}
    reply, meta = await web_enhance_calibrated(query, None, meta, lang, force=True, public=public)
    if reply:
        return reply, meta

    from aria_core.knowledge.epistemic import groq_calibrated_answer

    g_reply, g_meta = await groq_calibrated_answer(query, lang)
    if g_reply and not g_meta.get("abstain"):
        return g_reply, g_meta
    return None, meta


def should_web_verify(meta: dict) -> bool:
    return _web_verify_threshold(meta)