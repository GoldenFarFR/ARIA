from __future__ import annotations

import re

from aria_core import repertoire_db
from aria_core.holding import FLAGSHIP_PRODUCT, holding_name
from aria_core.locale import LANG_FR
from aria_core.memory import append_memory
from aria_core.models import EntityType, RepertoireItemStatus


def wants_manage_repertoire(message: str) -> bool:
    lower = message.lower()
    return bool(
        re.search(
            r"supprim|supprime|delete|remove|retir|retire|archiv|archive|enlev",
            lower,
        )
        and re.search(r"répertoire|repertoire|projet|project|entrée|entry|filiale|venture", lower)
    )


def _extract_target_name(message: str) -> str:
    verb = r"(?:supprim(?:e|er)?|delete|remove|retir(?:e|er)?|archiv(?:e|er)?|enlev(?:e|er)?)"
    patterns = [
        rf"{verb}\s+(?:du\s+)?(?:répertoire|repertoire)\s+(.+)",
        rf"(?:répertoire|repertoire)\s+{verb}\s+(.+)",
        rf"{verb}\s+(?:le\s+la\s+les\s+)?(?:projet|project|entrée|entry|filiale|venture)\s+(.+)",
        rf"{verb}\s+(.+)",
    ]
    for pat in patterns:
        m = re.search(pat, message, re.I)
        if m:
            target = m.group(1).strip()
            target = re.sub(r"\s*(du répertoire|from repertoire|please|s'il te plaît)\s*$", "", target, flags=re.I)
            return target.strip(" \"'")
    return ""


async def execute_manage_repertoire(message: str, lang: str = LANG_FR) -> tuple[str, dict]:
    lower = message.lower()
    archive = bool(re.search(r"archiv", lower))
    target = _extract_target_name(message)

    if not target:
        items = await repertoire_db.get_all()
        if lang == "en":
            lines = ["Repertoire — usage:", "/repertoire list", "/repertoire delete <name>", "/repertoire archive <name>", "", "Or: delete <project> from repertoire"]
        else:
            lines = [
                "Répertoire — usage :",
                "/repertoire list",
                "/repertoire delete <nom>",
                "/repertoire archive <nom>",
                "",
                "Ou : supprime <projet> du répertoire",
            ]
        if items:
            lines.append("")
            lines.append("Entrées actuelles :" if lang == LANG_FR else "Current entries:")
            for item in items:
                prot = " 🔒" if repertoire_db.deletion_blocked_reason(item) else ""
                lines.append(f"- {item.name} ({item.status.value}){prot}")
        return "\n".join(lines), {"action": "help", "count": len(items)}

    matches = await repertoire_db.find_by_name(target)
    if not matches:
        reason = f"Aucune entrée pour « {target} »." if lang == LANG_FR else f"No entry for « {target} »."
        return reason, {"action": "archive" if archive else "delete", "ok": False}
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        reason = (
            f"Plusieurs entrées ({names}) — précise le nom exact."
            if lang == LANG_FR
            else f"Multiple entries ({names}) — use exact name."
        )
        return reason, {"action": "archive" if archive else "delete", "ok": False}

    if archive:
        ok, reason, item = await repertoire_db.archive_item(matches[0].id)
    else:
        ok, reason, item = await repertoire_db.delete_item(matches[0].id)

    append_memory("repertoire", f"{'Archive' if archive else 'Delete'}: {reason}")
    remaining = await repertoire_db.get_all()
    if lang == "en":
        suffix = f"\n\n{len(remaining)} entries left."
    else:
        suffix = f"\n\n{len(remaining)} entrées restantes."
    if not ok:
        if lang == "en":
            reason = reason.replace("Entrée", "Entry").replace("introuvable", "not found")
        return reason + suffix, {"action": "archive" if archive else "delete", "ok": False}
    return reason + suffix, {
        "action": "archive" if archive else "delete",
        "ok": True,
        "item": item.name if item else target,
    }


async def execute_develop_repertoire(lang: str = LANG_FR) -> tuple[str, dict]:
    items = await repertoire_db.get_all()
    if not items:
        market = await repertoire_db.create(
            name=FLAGSHIP_PRODUCT,
            description=(
                f"Subsidiary of {holding_name()}. "
                "Watchlist-first DEX analyzer — signals, alerts, DexScreener embeds (Aria Market)"
            ),
            category="product",
            status=RepertoireItemStatus.BUILDING,
            priority=5,
            tags=["dex", "trading", "zhc", "flagship"],
            zhc_aligned=True,
            notes="Current flagship. Next: autonomous portfolio agent + transparency page.",
        )
        items = [market]

    building = [i for i in items if i.status == RepertoireItemStatus.BUILDING]
    ideas = [i for i in items if i.status == RepertoireItemStatus.IDEA]
    live = [i for i in items if i.status == RepertoireItemStatus.LIVE]

    suggestions: list[str] = []
    h = holding_name()

    # Auto-detect and hard-flag stale DEXPulse entries so the repertoire stops lying.
    stale_dexpulse = [i for i in items if i.name.lower() == "dexpulse" or "dexpulse" in (i.description or "").lower()]
    if stale_dexpulse:
        for item in stale_dexpulse:
            if item.status != RepertoireItemStatus.ARCHIVED:
                suggestions.append(
                    f"ARCHIVER maintenant : /repertoire archive {item.name}  (DEXPulse retiré 2026-06-19 — plus flagship, plus live)"
                )
            if "flagship" in [t.lower() for t in (item.tags or [])] or "flagship" in (item.description or "").lower():
                suggestions.append(
                    f"Retirer « flagship » de l'entrée retirée DEXPulse (elle n'est plus la filiale phare)"
                )

    if lang == "en":
        if not any(i.entity_type == EntityType.HOLDING for i in items):
            suggestions.append(f"Ensure {h} is registered as parent holding in repertoire")
        if len(building) > 0 and not any("revenue" in i.tags for i in items):
            suggestions.append(
                f"Add a revenue stream to {FLAGSHIP_PRODUCT} subsidiary (premium alerts)"
            )
        if len(ideas) == 0:
            suggestions.append(
                f"Register a new venture as subsidiary of {h} — e.g. Telegram Premium Alerts"
            )
        suggestions.append(
            f"Publish a transparency page for {FLAGSHIP_PRODUCT} under {h} (building in public)"
        )
        if not any(i.entity_type == EntityType.HOLDING for i in items):
            suggestions.append(f"Ensure {h} is registered as parent holding in repertoire")
        if len(building) > 0 and not any("revenue" in i.tags for i in items):
            suggestions.append(
                f"Add a revenue stream to {FLAGSHIP_PRODUCT} subsidiary (premium alerts)"
            )
        if len(ideas) == 0:
            suggestions.append(
                f"Register a new venture as subsidiary of {h} — e.g. Telegram Premium Alerts"
            )
        suggestions.append(
            f"Publish a transparency page for {FLAGSHIP_PRODUCT} under {h} (building in public)"
        )

        lines = [
            f"Repertoire — {len(items)} entries ({len(live)} live, {len(building)} building, {len(ideas)} ideas)",
            "",
        ]
        for item in sorted(items, key=lambda x: -x.priority):
            zhc = " [ZHC]" if item.zhc_aligned else ""
            lines.append(f"- {item.name} ({item.status.value}) P{item.priority}{zhc}")
            if item.description:
                lines.append(f"  {item.description}")
        lines.append("")
        lines.append("Recommended actions:")
        for s in suggestions:
            lines.append(f"→ {s}")
    else:
        if not any(i.entity_type == EntityType.HOLDING for i in items):
            suggestions.append(f"Vérifier que {h} est bien la holding mère dans le répertoire")
        if len(building) > 0 and not any("revenue" in i.tags for i in items):
            suggestions.append(
                f"Ajouter une source de revenu à la filiale {FLAGSHIP_PRODUCT}"
            )
        if len(ideas) == 0:
            suggestions.append(
                f"Enregistrer une nouvelle filiale sous {h} — ex. Alertes Telegram Premium"
            )
        suggestions.append(
            f"Publier une page transparence pour {FLAGSHIP_PRODUCT} sous {h} (building in public)"
        )

        lines = [
            f"Répertoire — {len(items)} entrées ({len(live)} live, {len(building)} en construction, {len(ideas)} idées)",
            "",
        ]
        for item in sorted(items, key=lambda x: -x.priority):
            zhc = " [ZHC]" if item.zhc_aligned else ""
            lines.append(f"- {item.name} ({item.status.value}) P{item.priority}{zhc}")
            if item.description:
                lines.append(f"  {item.description}")
        lines.append("")
        lines.append("Actions recommandées :")
        for s in suggestions:
            lines.append(f"→ {s}")

    summary = "\n".join(lines)
    append_memory("repertoire", f"Repertoire development: {len(suggestions)} suggestions")
    return summary, {
        "total": len(items),
        "building": len(building),
        "ideas": len(ideas),
        "live": len(live),
        "suggestions": suggestions,
    }


async def get_repertoire_summary(lang: str = LANG_FR) -> str:
    items = await repertoire_db.get_all()
    if not items:
        h = holding_name()
        if lang == "en":
            return (
                f"Empty repertoire — {FLAGSHIP_PRODUCT} subsidiary will be seeded under {h}."
            )
        return (
            f"Répertoire vide — la filiale {FLAGSHIP_PRODUCT} sera créée sous {h}."
        )
    names = [f"{i.name} ({i.status.value})" for i in items]
    if lang == "en":
        return f"{len(items)} projects: {', '.join(names)}"
    return f"{len(items)} projets : {', '.join(names)}"