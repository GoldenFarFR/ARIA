"""ARIA Gem Crush — réponses ancrées (POC Vanguard), jamais web APK tiers."""

from __future__ import annotations

import re
from typing import Any

from aria_core.runtime import settings
from aria_core.skills.gem_crush_skill import (
    CSS_PATH,
    REPO,
    VERSION_PATH,
    improve_interval_minutes,
    parse_improve_version,
    premium_mode_enabled,
    release_for_version,
)

# Produit écosystème — pas Candy Crush, pas « Gem Crush Epic » APK
GEM_CRUSH_ECOSYSTEM_RE = re.compile(
    r"gem[\s-]?crush|aria\s+gem|match[\s-]?3.*(?:vanguard|aria|zhc)|"
    r"(?:vanguard|aria|zhc).*match[\s-]?3|#poc.*gem|gem.*#poc",
    re.I,
)

_VERSION_RE = re.compile(r"GEM_CRUSH_VERSION\s*=\s*(\d+)")
_TITLE_RE = re.compile(r"GEM_CRUSH_RELEASE_TITLE\s*=\s*['\"]([^'\"]+)['\"]")
_UPDATED_RE = re.compile(r"GEM_CRUSH_UPDATED_AT\s*=\s*['\"]([^'\"]+)['\"]")


def is_gem_crush_ecosystem_question(query: str) -> bool:
    """True si la question concerne notre POC ARIA Gem Crush (pas le web générique)."""
    return bool(GEM_CRUSH_ECOSYSTEM_RE.search(query or ""))


def _parse_version_ts(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if m := _VERSION_RE.search(text):
        out["version"] = int(m.group(1))
    if m := _TITLE_RE.search(text):
        out["title"] = m.group(1).strip()
    if m := _UPDATED_RE.search(text):
        out["updated_at"] = m.group(1).strip()
    return out


async def _fetch_github_status() -> dict[str, Any]:
    from aria_core.github_client import GitHubClient
    from aria_core.skills.github_skill import github_configured, repo_read_allowed

    owner = settings.github_owner.strip()
    if not github_configured() or not repo_read_allowed(owner, REPO):
        return {"ok": False}

    client = GitHubClient(settings.github_token.strip())
    version_text, _ = await client.get_file_text(owner, REPO, VERSION_PATH)
    css_text, _ = await client.get_file_text(owner, REPO, CSS_PATH)
    parsed = _parse_version_ts(version_text or "")
    css_ver = parse_improve_version(css_text or "")
    current = int(parsed.get("version") or css_ver or 0)
    next_ver = current + 1
    nxt = release_for_version(next_ver)
    return {
        "ok": True,
        "version": current,
        "title": parsed.get("title") or "",
        "updated_at": parsed.get("updated_at") or "",
        "next_version": next_ver,
        "next_queued": nxt is not None,
        "next_title": nxt.title if nxt else "",
        "next_items": len(nxt.items) if nxt else 0,
        "repo": f"{owner}/{REPO}",
    }


async def answer_gem_crush_status(query: str, *, lang: str = "fr") -> str:
    """
    Réponse grounded — version GitHub, intervalle heartbeat, lien Vanguard.
    Ne jamais chercher « Gem Crush » sur le web (APK clones).
    """
    interval = improve_interval_minutes()
    premium = premium_mode_enabled()
    status = await _fetch_github_status()
    site = (getattr(settings, "site_base_url", None) or "https://ariavanguardzhc.com").rstrip("/")

    if lang == "fr":
        lines = [
            "ARIA Gem Crush — notre match-3 sur le holding Vanguard (pas un jeu APK tiers).",
            f"Jouable : {site}/#poc",
            "",
        ]
        if status.get("ok"):
            lines.append(f"Version en prod : v{status['version']}")
            if status.get("title"):
                lines.append(f"Dernière release : {status['title']}")
            if status.get("updated_at"):
                lines.append(f"Mise à jour : {status['updated_at']}")
            if status.get("next_queued"):
                lines.append(
                    f"Prochaine version : v{status['next_version']} — {status.get('next_title', '')} "
                    f"({status.get('next_items', 0)} améliorations en file)"
                )
                lines.append(f"Cadence ARIA : toutes les {interval} min (heartbeat auto).")
            else:
                lines.append(
                    "Prochaine version : ARIA planifie la release suivante (file vide côté skill)."
                )
            lines.append(f"Source : GitHub {status.get('repo')} (vérifié).")
        else:
            lines.append(
                f"Version locale non lue (GitHub) — cadence prévue : {interval} min entre releases."
            )
            lines.append("Je n'utilise pas le web pour « Gem Crush » (évite les clones type Gem Crush Epic).")
        if premium:
            lines.append("Mode : premium — recherche concurrence puis ship massif CSS+TS.")
        if re.search(r"quand|when|prochaine?|next|arrive", query, re.I):
            lines.append("")
            if status.get("ok") and status.get("next_queued"):
                lines.append(
                    f"Réponse directe : la v{status['next_version']} part au prochain cycle "
                    f"(~{interval} min max si Render tourne)."
                )
            else:
                lines.append(
                    f"Réponse directe : prochaine amélioration dans ~{interval} min "
                    "(quand une release est en file)."
                )
        return "\n".join(lines)

    lines = [
        "ARIA Gem Crush — our match-3 POC on Vanguard (not a third-party APK).",
        f"Play: {site}/#poc",
        "",
    ]
    if status.get("ok"):
        lines.append(f"Live version: v{status['version']} — {status.get('title', '')}")
        if status.get("next_queued"):
            lines.append(f"Next: v{status['next_version']} in ~{interval} min (heartbeat).")
        lines.append(f"Source: GitHub {status.get('repo')}")
    else:
        lines.append(f"GitHub status unavailable — release cadence: {interval} min.")
    return "\n".join(lines)