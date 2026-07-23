"""Signal "substance Docs" -- profondeur RÉELLE d'une documentation/whitepaper
projet, via ``services.tavily.TavilyClient.crawl`` sur l'URL "Docs" DÉCLARÉE
par le projet (23/07, demande opérateur directe : "la doc doit aussi être lue
en entière... https://docs.crynux.io/" -- un crawl enraciné sur le lien
Website ne garantit PAS de couvrir la doc si elle vit sur un domaine sans
rapport, contrairement au cas CNX où docs.crynux.io est un sous-domaine
découvert incidemment).

Aucune extraction de documentation n'existait avant ce signal : le seul texte
de site déjà récupéré ailleurs (``site_snapshot.py``) est plafonné à 600
caractères -- vérifié en conditions réelles que docs.crynux.io fait à lui
seul 535 526 caractères de HTML brut, sur plusieurs pages (architecture,
tokenomics, guides). Même doctrine que ``website_substance.py`` : des FAITS
mesurables sur le texte réellement récupéré, jamais un jugement fabriqué sur
une donnée absente."""
from __future__ import annotations

from dataclasses import dataclass, field

# Mots-clés de profondeur TECHNIQUE (proxy textuel -- présence réelle de
# contenu sur ces sujets, jamais une vérification de véracité du contenu
# lui-même, qui reste déclaratif comme tout project_links).
_TECHNICAL_KEYWORDS = (
    "architecture", "protocol", "algorithm", "consensus", "smart contract",
    "contrat intelligent", "api", "sdk", "node", "noeud",
)
_ROADMAP_KEYWORDS = ("roadmap", "feuille de route", "milestone", "jalon")
_TOKENOMICS_KEYWORDS = ("tokenomics", "allocation", "vesting", "supply", "émission")
_RISK_KEYWORDS = ("risk", "risque", "disclaimer", "avertissement", "limitation")

# Sous ce nombre de mots RÉELS cumulés, l'échantillon est trop faible pour
# juger honnêtement (esprit du "< 800 mots -> plafonné" de la proposition
# externe, mais ``unknown`` plutôt qu'un score forcé -- même doctrine que le
# reste du projet : jamais un chiffre fabriqué sur un signal trop faible).
_MIN_WORDS_FOR_SIGNAL = 300
_WORD_COUNT_FULL_SCORE = 5000
_TECHNICAL_KEYWORDS_FULL_SCORE = 4


@dataclass
class DocsSubstanceFacts:
    available: bool = False
    error: str | None = None
    pages_found: int = 0
    total_words: int = 0
    technical_keywords_found: int = 0
    has_roadmap: bool = False
    has_tokenomics: bool = False
    has_risk_disclosure: bool = False


@dataclass
class DocsSubstanceVerdict:
    signal: str  # "positive" | "neutral" | "weak" | "unknown"
    score: float | None
    points: list[str] = field(default_factory=list)


async def _default_crawl(url: str):
    from aria_core.services.tavily import tavily_client

    return await tavily_client.crawl(url, caller="docs_substance")


async def gather_docs_substance_facts(docs_url: str | None, *, crawl_fn=None) -> DocsSubstanceFacts:
    """Récolte best-effort, jamais bloquant. ``crawl_fn`` injectable pour les tests."""
    url = (docs_url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return DocsSubstanceFacts(available=False, error="URL invalide")

    crawl_fn = crawl_fn or _default_crawl
    try:
        result = await crawl_fn(url)
    except Exception as exc:  # noqa: BLE001 -- jamais bloquant
        return DocsSubstanceFacts(available=False, error=str(exc))

    if not result.available or not result.pages:
        return DocsSubstanceFacts(available=False, error=result.error or "aucune page exploitable")

    combined = " ".join(p.raw_content for p in result.pages)
    total_words = len(combined.split())
    if total_words < _MIN_WORDS_FOR_SIGNAL:
        return DocsSubstanceFacts(available=False, error="contenu réel trop faible pour juger")

    lower = combined.lower()
    technical = sum(1 for kw in _TECHNICAL_KEYWORDS if kw in lower)

    return DocsSubstanceFacts(
        available=True,
        pages_found=len(result.pages),
        total_words=total_words,
        technical_keywords_found=technical,
        has_roadmap=any(kw in lower for kw in _ROADMAP_KEYWORDS),
        has_tokenomics=any(kw in lower for kw in _TOKENOMICS_KEYWORDS),
        has_risk_disclosure=any(kw in lower for kw in _RISK_KEYWORDS),
    )


def judge_docs_substance(facts: DocsSubstanceFacts) -> DocsSubstanceVerdict:
    """Jugement pur, aucun appel réseau. 4 critères pondérés."""
    if not facts.available:
        return DocsSubstanceVerdict(signal="unknown", score=None, points=[facts.error or "indisponible"])

    depth_score = min(1.0, facts.total_words / _WORD_COUNT_FULL_SCORE) * 100
    technical_score = min(1.0, facts.technical_keywords_found / _TECHNICAL_KEYWORDS_FULL_SCORE) * 100
    roadmap_score = 100.0 if facts.has_roadmap else 0.0
    transparency_score = 100.0 if (facts.has_tokenomics and facts.has_risk_disclosure) else (
        50.0 if (facts.has_tokenomics or facts.has_risk_disclosure) else 0.0
    )

    score = 0.35 * depth_score + 0.25 * technical_score + 0.20 * roadmap_score + 0.20 * transparency_score

    points = [
        f"substance {score:.1f}/100 -- {facts.total_words} mots réels sur {facts.pages_found} page(s), "
        f"{facts.technical_keywords_found}/{len(_TECHNICAL_KEYWORDS)} terme(s) technique(s) trouvé(s), "
        f"roadmap {'présente' if facts.has_roadmap else 'absente'}, "
        f"tokenomics {'présente' if facts.has_tokenomics else 'absente'}, "
        f"risques {'mentionnés' if facts.has_risk_disclosure else 'non mentionnés'}",
    ]

    if score >= 70:
        signal = "positive"
    elif score >= 40:
        signal = "neutral"
    else:
        signal = "weak"

    return DocsSubstanceVerdict(signal=signal, score=round(score, 1), points=points)
