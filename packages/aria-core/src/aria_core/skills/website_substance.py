"""Signal "substance Website" -- contenu RÉEL multi-pages d'un site projet,
via ``services.tavily.TavilyClient.crawl`` (23/07, demande opérateur directe
sur une capture réelle de liens projet : "elle doit pouvoir extraire tout pour
noter").

Distinct de ``services/site_snapshot.py`` (600 caractères, une seule page,
conçu pour enrichir un prompt LLM avec un aperçu -- jamais pour auditer un
site). Vérifié en conditions réelles (23/07) sur crynux.io : la homepage seule
fait 110 798 caractères de HTML brut, le snapshot existant n'en extrait que
600 -- le crawl Tavily rend le JS et suit les liens internes (sous-domaines
compris, ex. docs.<site>), 15 pages réelles récupérées en un seul appel.

Même esprit que ``github_substance.py`` : des FAITS mesurables, jamais un
jugement que l'infrastructure actuelle n'a pas les moyens de porter -- la
cohérence visuelle/le design ("template memecoin générique" vs. identité
propre) exigerait une capture d'écran + un modèle de vision, absent du
pipeline auto d'ARIA. Documenté comme limite honnête, jamais simulé ni
fabriqué (règle absolue CLAUDE.md)."""
from __future__ import annotations

from dataclasses import dataclass, field

# Mots-clés de sections structurantes -- proxy TEXTUEL de la transparence/
# structure d'un site (présence réelle de contenu sur ces sujets), jamais une
# vérification de navigation à l'oeil. Volontairement large (FR/EN) : un site
# n'utilise souvent qu'une des deux langues.
_KEY_SECTION_KEYWORDS = (
    "team", "équipe", "roadmap", "feuille de route", "tokenomics",
    "whitepaper", "livre blanc", "docs", "documentation", "audit",
)
_GENERIC_PLACEHOLDER_MARKERS = ("lorem ipsum",)

# Sous ce nombre de mots RÉELS cumulés (toutes pages confondues), l'échantillon
# est trop faible pour juger honnêtement -- jamais un score fabriqué dessus
# (même doctrine que ``_MIN_RAW_COMMITS_BEFORE_DETAIL`` de github_substance.py).
_MIN_WORDS_FOR_SIGNAL = 150

# Score de profondeur plein à ce nombre de mots cumulés -- calibré sur le cas
# réel CNX (~4 500 mots utiles sur les pages principales du crawl), jamais un
# chiffre arbitraire non ancré.
_WORD_COUNT_FULL_SCORE = 3000
_KEY_SECTIONS_FULL_SCORE = 4
_PAGES_FOUND_FULL_SCORE = 5


@dataclass
class WebsiteSubstanceFacts:
    available: bool = False
    error: str | None = None
    pages_found: int = 0
    total_words: int = 0
    https: bool = False
    key_sections_found: int = 0
    has_generic_placeholder: bool = False


@dataclass
class WebsiteSubstanceVerdict:
    signal: str  # "positive" | "neutral" | "weak" | "unknown"
    score: float | None
    points: list[str] = field(default_factory=list)


async def _default_crawl(url: str):
    from aria_core.services.tavily import tavily_client

    return await tavily_client.crawl(url, caller="website_substance")


async def gather_website_substance_facts(
    website_url: str | None, *, crawl_fn=None,
) -> WebsiteSubstanceFacts:
    """Récolte best-effort, jamais bloquant. ``crawl_fn`` injectable pour les
    tests (même patron que ``fetch=`` de ``github_substance.py``)."""
    url = (website_url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return WebsiteSubstanceFacts(available=False, error="URL invalide")

    crawl_fn = crawl_fn or _default_crawl
    try:
        result = await crawl_fn(url)
    except Exception as exc:  # noqa: BLE001 -- jamais bloquant
        return WebsiteSubstanceFacts(available=False, error=str(exc))

    if not result.available or not result.pages:
        return WebsiteSubstanceFacts(available=False, error=result.error or "aucune page exploitable")

    combined = " ".join(p.raw_content for p in result.pages)
    total_words = len(combined.split())
    if total_words < _MIN_WORDS_FOR_SIGNAL:
        return WebsiteSubstanceFacts(available=False, error="contenu réel trop faible pour juger")

    lower = combined.lower()
    key_sections = sum(1 for kw in _KEY_SECTION_KEYWORDS if kw in lower)
    generic = any(marker in lower for marker in _GENERIC_PLACEHOLDER_MARKERS)

    return WebsiteSubstanceFacts(
        available=True,
        pages_found=len(result.pages),
        total_words=total_words,
        https=url.lower().startswith("https://"),
        key_sections_found=key_sections,
        has_generic_placeholder=generic,
    )


def judge_website_substance(facts: WebsiteSubstanceFacts) -> WebsiteSubstanceVerdict:
    """Jugement pur, aucun appel réseau. 4 critères pondérés, VOLONTAIREMENT
    réduits par rapport à une proposition externe plus large (axes "cohérence
    visuelle"/"mobile-friendly rigoureux"/"liens cassés exhaustifs" retirés --
    non mesurables honnêtement avec l'infrastructure texte actuelle, jamais
    approximés en silence)."""
    if not facts.available:
        return WebsiteSubstanceVerdict(signal="unknown", score=None, points=[facts.error or "indisponible"])

    if facts.has_generic_placeholder:
        return WebsiteSubstanceVerdict(
            signal="weak", score=10.0,
            points=["texte de remplacement générique détecté (lorem ipsum) -- site probablement inachevé"],
        )

    depth_score = min(1.0, facts.total_words / _WORD_COUNT_FULL_SCORE) * 100
    structure_score = min(1.0, facts.key_sections_found / _KEY_SECTIONS_FULL_SCORE) * 100
    reach_score = min(1.0, facts.pages_found / _PAGES_FOUND_FULL_SCORE) * 100
    https_score = 100.0 if facts.https else 0.0

    score = 0.40 * depth_score + 0.25 * structure_score + 0.20 * reach_score + 0.15 * https_score

    points = [
        f"substance {score:.1f}/100 -- {facts.total_words} mots réels sur {facts.pages_found} page(s), "
        f"{facts.key_sections_found}/{len(_KEY_SECTION_KEYWORDS)} section(s) clé(s) détectée(s)"
        f"{', HTTPS' if facts.https else ', PAS de HTTPS'}",
    ]

    if score >= 70:
        signal = "positive"
    elif score >= 40:
        signal = "neutral"
    else:
        signal = "weak"

    return WebsiteSubstanceVerdict(signal=signal, score=round(score, 1), points=points)
