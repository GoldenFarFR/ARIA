"""Gem Crush — recherche concurrence + brief stratégique avant chaque release premium."""

from __future__ import annotations

from dataclasses import dataclass

from aria_core.knowledge.web_verify import WebSource, fetch_web_snippets

BRIEFS_DIR = "docs/gem-crush-briefs"

# Concurrents ciblés — étudiés à chaque cycle avant ship
TARGET_COMPETITORS: tuple[str, ...] = (
    "Candy Crush Saga",
    "Clash Royale",
    "Royal Match",
    "Homescapes",
    "Toon Blast",
    "Gardenscapes",
    "Puzzle & Dragons",
)

# Requêtes DDG — ce qui marche, qui domine, UX premium match-3 / casual
RESEARCH_QUERIES: tuple[str, ...] = (
    "Candy Crush Saga most successful features player retention 2024 2025",
    "Clash Royale game feel polish UX what makes it addictive mobile",
    "Royal Match vs Candy Crush why players switch match-3",
    "Homescapes Toon Blast Gardenscapes progression rewards daily streak",
    "best match-3 puzzle game UI UX premium quality mobile 2024",
    "mobile puzzle game reward system stars level map engagement",
    "luxury casual game visual design gold aesthetic particles juice",
    "Clash Royale trophy league progression psychological hooks",
)

PILLARS: tuple[str, ...] = (
    "qualité graphique",
    "narrative / mascotte ARIA",
    "jouabilité & feedback",
    "récompenses & progression",
    "différenciation vs concurrence",
)


@dataclass(frozen=True)
class GemCrushResearchBrief:
    version: int
    release_title: str
    sources: tuple[WebSource, ...]
    markdown: str


def _competitor_insights(sources: tuple[WebSource, ...], lang: str) -> list[str]:
    blob = " ".join(s.text.lower() for s in sources)
    insights: list[str] = []
    mapping = {
        "candy crush": (
            "Candy Crush : courbe douce, objectifs clairs, juice sur chaque match, near-miss tension.",
            "Candy Crush: soft curve, clear goals, match juice, near-miss tension.",
        ),
        "clash royale": (
            "Clash Royale : feedback instantané, trophées, sensation compétitive même en solo.",
            "Clash Royale: instant feedback, trophies, competitive feel even in solo.",
        ),
        "royal match": (
            "Royal Match : narrative légère + rénovation, étoiles, coffres — rétention story-driven.",
            "Royal Match: light story + renovation, stars, chests — story-driven retention.",
        ),
        "homescapes": (
            "Homescapes : progression meta (maison) + puzzles — récompense hors plateau.",
            "Homescapes: meta progression (home) + puzzles — off-board rewards.",
        ),
        "toon blast": (
            "Toon Blast : blocs explosifs, rythme rapide, célébrations courtes et intenses.",
            "Toon Blast: explosive blocks, fast pace, short intense celebrations.",
        ),
        "gardenscapes": (
            "Gardenscapes : quêtes quotidiennes, mascotte attachante, events saisonniers.",
            "Gardenscapes: daily quests, mascot attachment, seasonal events.",
        ),
    }
    for key, (fr, en) in mapping.items():
        if key.replace(" ", "") in blob.replace(" ", "") or key in blob:
            insights.append(en if lang == "en" else fr)
    if not insights:
        fallback = (
            "Top grossing : juice visuel, streaks, étoiles, feedback invalide clair, pas de friction inutile.",
            "Top grossing: visual juice, streaks, stars, clear invalid feedback, no useless friction.",
        )
        insights.append(fallback[1] if lang == "en" else fallback[0])
    return insights[:6]


def _pillar_notes(sources: tuple[WebSource, ...], lang: str) -> dict[str, str]:
    blob = " ".join(s.text.lower() for s in sources)
    if lang == "en":
        return {
            "qualité graphique": "Polish, particles, premium materials — competitors invest heavily in juice.",
            "narrative / mascotte ARIA": "Royal Match / Homescapes use light story; ARIA ZHC narrative is our moat on Vanguard.",
            "jouabilité & feedback": "Clear goals, combos, invalid-move feedback — Candy Crush sets the bar.",
            "récompenses & progression": "Stars, streaks, level map, trophies — Clash Royale & Royal Match retention drivers.",
            "différenciation vs concurrence": (
                "Holding luxury (gold/black), ARIA co-founder story, no ads — premium ZHC positioning."
                if "royal" in blob or "candy" in blob or "clash" in blob
                else "Luxury Vanguard skin + ARIA autonomous improvements every 30 min."
            ),
        }
    return {
        "qualité graphique": "Juice, particules, matériaux premium — la concurrence investit lourd sur le ressenti.",
        "narrative / mascotte ARIA": "Royal Match / Homescapes utilisent une story légère ; la narrative ARIA ZHC sur Vanguard est notre moat.",
        "jouabilité & feedback": "Objectifs clairs, combos, retour coup invalide — Candy Crush fixe la barre.",
        "récompenses & progression": "Étoiles, streaks, carte de niveaux, trophées — leviers Clash Royale & Royal Match.",
        "différenciation vs concurrence": (
            "Holding luxe (or/noir), histoire co-fondateur ARIA, sans pub — positionnement ZHC haut de gamme."
            if "royal" in blob or "candy" in blob or "clash" in blob
            else "Skin Vanguard luxe + améliorations autonomes ARIA toutes les 30 min."
        ),
    }


def compose_brief_markdown(
    *,
    version: int,
    release_title: str,
    sources: tuple[WebSource, ...],
    planned_items: tuple[str, ...] = (),
    lang: str = "fr",
) -> str:
    ts_line = "Date : recherche auto ARIA (DuckDuckGo)"
    if lang == "en":
        header = f"# Gem Crush v{version} — strategic brief\n\n**Release:** {release_title}\n{ts_line}\n"
        comp_h = "## Competition landscape (web)\n"
        targets_h = "## Target competitors studied\n"
        diff_h = "## ARIA differentiation axes\n"
        ship_h = "## Planned ship (this version)\n"
    else:
        header = f"# Gem Crush v{version} — brief stratégique\n\n**Release :** {release_title}\n{ts_line}\n"
        comp_h = "## Paysage concurrence (web)\n"
        targets_h = "## Concurrents ciblés (étude)\n"
        diff_h = "## Axes différenciation ARIA\n"
        ship_h = "## Livraison prévue (cette version)\n"

    lines = [header, targets_h]
    for comp in TARGET_COMPETITORS:
        lines.append(f"- **{comp}**\n")
    insights = _competitor_insights(sources, lang)
    if insights:
        lines.append("\n### Enseignements concurrence\n" if lang == "fr" else "\n### Competitor takeaways\n")
        for note in insights:
            lines.append(f"- {note}\n")

    lines.append(f"\n{comp_h}")
    if sources:
        for i, src in enumerate(sources[:10], 1):
            url = f" — {src.url}" if src.url else ""
            lines.append(f"{i}. {src.text}{url}\n")
    else:
        lines.append("_Recherche web indisponible — brief heuristique ARIA._\n")

    notes = _pillar_notes(sources, lang)
    lines.append(f"\n{diff_h}")
    for pillar in PILLARS:
        lines.append(f"- **{pillar}** : {notes.get(pillar, '—')}\n")

    lines.append(f"\n{ship_h}")
    if planned_items:
        for item in planned_items:
            lines.append(f"- {item}\n")
    else:
        lines.append("- _Items définis dans la release premium._\n")

    lines.append("\n---\n_ARIA — recherche concurrence puis ship groupé massif (30 min)._")
    return "".join(lines)


async def run_match3_research(
    *,
    version: int,
    release_title: str,
    planned_items: tuple[str, ...] = (),
    lang: str = "fr",
) -> GemCrushResearchBrief:
    """Idée → recherche web concurrence → brief avant codage."""
    sources: list[WebSource] = []
    seen: set[str] = set()
    for q in RESEARCH_QUERIES:
        for src in await fetch_web_snippets(q, max_snippets=2):
            key = src.text.lower()[:60]
            if key not in seen:
                seen.add(key)
                sources.append(src)
        if len(sources) >= 12:
            break

    tup = tuple(sources[:12])
    md = compose_brief_markdown(
        version=version,
        release_title=release_title,
        sources=tup,
        planned_items=planned_items,
        lang=lang,
    )
    return GemCrushResearchBrief(version=version, release_title=release_title, sources=tup, markdown=md)


def brief_path_for(version: int) -> str:
    return f"{BRIEFS_DIR}/v{version}.md"