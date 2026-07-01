"""BASE launchpad selection skill — volume, builders, community, exposure."""

from __future__ import annotations

import re

from aria_core.knowledge.base_launchpads import (
    LAUNCHPADS,
    compare_launchpads_markdown,
    methodology_markdown,
    primary_pick,
    rank_launchpads,
    recommendation_verdict,
    registry_markdown,
    touch_refresh,
)
from aria_core.memory import append_memory


def wants_launchpad_methodology(message: str) -> bool:
    """Follow-up: explain sources/weights for volume, builders, visibility, etc."""
    lower = message.lower()
    method_q = re.search(
        r"source|méthodolog|methodolog|d'où|d ou tu|d'ou tu|explain|explique|"
        r"comment.*(?:note|score|évalu|calcul)|quelle.*source|how.*(?:score|rating)|"
        r"pourquoi.*score|critère|critere|pondér|ponder|methodology|méthode",
        lower,
    )
    axis_q = re.search(
        r"visibilit|exposition|volume|développeur|developpeur|builder|communaut|community|"
        r"catégor|categor|holding.?fit|fit holding|chaque.*(?:note|score|axe)",
        lower,
    )
    launchpad_ctx = re.search(
        r"launchpad|clanker|bankr|flaunch|virtuals?|zora|base.*launch|lancer.*token|"
        r"token.*base|sélection aria|aria selection",
        lower,
    )
    return bool(method_q and (axis_q or launchpad_ctx))


async def execute_launchpad_select(user_message: str, lang: str = "en") -> tuple[str, dict]:
    lower = user_message.lower()
    holding = any(
        w in lower
        for w in (
            "vanguard", "holding", "aria", "token", "jeton", "zhc",
            "utility", "deflation", "notre", "our",
        )
    )
    if not holding:
        holding = True  # default for Aria Vanguard operator context

    if wants_launchpad_methodology(user_message):
        body = methodology_markdown(lang=lang, holding_context=holding)
        touch_refresh()
        append_memory("launchpad", "[methodology] sources/weights explained")
        return body, {"mode": "methodology", "holding_context": holding}

    if any(w in lower for w in ("list", "liste", "all", "tous", "registry")) and not re.search(
        r"source|méthodolog|methodolog", lower
    ):
        body = registry_markdown()
        if lang == "fr":
            header = "Registre complet des launchpads BASE :\n\n"
        else:
            header = "Full BASE launchpad registry:\n\n"
        return header + body[:3500], {"mode": "registry", "count": len(LAUNCHPADS)}

    if any(w in lower for w in ("compare", "compar", "vs", "versus")):
        ranked = rank_launchpads(holding_context=holding)
        ids = [lp.id for lp, _ in ranked[:5]]
        for lp in LAUNCHPADS:
            if lp.name.lower() in lower or lp.id in lower:
                if lp.id not in ids:
                    ids.insert(0, lp.id)
        compared = [next(x for x in LAUNCHPADS if x.id == lid) for lid in ids[:4]]
        body = compare_launchpads_markdown(
            compared, lang=lang, holding_context=holding,
        )
        return body, {"mode": "compare", "ids": ids[:4]}

    verdict = recommendation_verdict(lang=lang, holding_context=holding)
    pick = primary_pick(holding_context=holding)
    touch_refresh()
    append_memory(
        "launchpad",
        f"[select] pick={pick.id} holding_context={holding} — {pick.summary[:100]}",
    )
    return verdict, {
        "mode": "verdict",
        "pick": pick.id,
        "pick_name": pick.name,
        "holding_context": holding,
        "top5": [lp.id for lp, _ in rank_launchpads(holding_context=holding)[:5]],
    }