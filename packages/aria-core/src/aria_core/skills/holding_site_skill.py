"""Holding site skill — Aria Vanguard ZHC web presence (plan + verified GitHub audit)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from aria_core.github_client import GitHubClient
from aria_core.holding import holding_name
from aria_core.memory import append_memory
from aria_core.paths import memory_dir
from aria_core.runtime import settings
from aria_core.skills.github_skill import (
    github_configured,
    repo_read_allowed,
    repo_write_allowed,
)

INITIATIVE_PATH = memory_dir() / "holding_site_initiative.md"
REPO = "aria-vanguard"
DOMAIN = settings.holding_domain or "ariavanguardzhc.com"
AUDIT_DOC_PATH = "docs/aria-holding-audit.md"
_KEY_FILES = (
    "src/pages/VanguardSite.tsx",
    "src/components/FaqSection.tsx",
    "src/components/VanguardNav.tsx",
)
SHOOTING_STAR_PATH = "src/components/ShootingStar.tsx"
VANGUARD_SITE_PATH = "src/pages/VanguardSite.tsx"
INDEX_CSS_PATH = "src/index.css"
SHOOTING_STAR_MARKER = "shooting-star-fly"

SHOOTING_STAR_TSX = """/** Shooting star accent for the Vanguard hero — ARIA patch */
export function ShootingStar() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      <div className="shooting-star" />
    </div>
  )
}
"""

SHOOTING_STAR_CSS = """
/* ── Shooting star (ARIA) ── */
.shooting-star {
  position: absolute;
  top: 18%;
  left: -8%;
  width: 140px;
  height: 2px;
  background: linear-gradient(90deg, transparent, #e8d5a8 40%, #c9a962 70%, transparent);
  border-radius: 2px;
  opacity: 0;
  transform: rotate(-22deg);
  animation: shooting-star-fly 9s ease-in-out infinite;
  animation-delay: 3s;
  box-shadow: 0 0 10px rgba(201, 169, 98, 0.55);
}

@keyframes shooting-star-fly {
  0%, 76%, 100% {
    opacity: 0;
    transform: translateX(0) translateY(0) rotate(-22deg);
  }
  80% { opacity: 1; }
  96% {
    opacity: 0.4;
    transform: translateX(115vw) translateY(32vh) rotate(-22deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .shooting-star { animation: none; display: none; }
}
"""


def wants_holding_site_decorate(message: str) -> bool:
    lower = message.lower()
    if re.search(r"\bjuno\b", lower):
        return False
    if re.search(r"étoile\s*filante|etoile\s*filante|shooting\s*star", lower):
        return bool(
            re.search(
                r"vanguard|aria-vanguard|holding|accueil|homepage|page\s+d",
                lower,
            )
        )
    return bool(
        re.search(r"ajoute|ajouter|add\b|met\b|mets\b", lower)
        and re.search(
            r"vanguard|aria-vanguard|holding|accueil|homepage|page\s+d",
            lower,
        )
        and re.search(r"décor|decor|animation|effet|visuel|étoile|etoile|star", lower)
    )


def wants_holding_site(message: str) -> bool:
    lower = message.lower()
    if re.search(r"\bjuno\b", lower):
        return False
    if wants_holding_site_decorate(message):
        return True
    if wants_holding_site_execute(message):
        return True
    return bool(
        re.search(
            r"site web|site holding|constru.*site|build.*site|créer.*site|creer.*site|"
            r"aria.?vanguard|vanguard.*site|holding.*site|ariavanguardzhc|"
            r"devenir autonome|prendre des initiatives?|initiative",
            lower,
        )
    )


def wants_holding_site_execute(message: str) -> bool:
    lower = message.lower()
    if re.search(r"\bjuno\b", lower):
        return False
    return bool(
        re.search(
            r"(?:lancer|démarr|demarr|start|commence|exécute|execute|go)\b.*\b(?:site|holding|vanguard)|"
            r"(?:lancer|start)\s+(?:le\s+)?site|"
            r"push\s+(?:hero\s+)?holding|push.*aria-vanguard|"
            r"start\s+the\s+site",
            lower,
        )
    )


def _read_initiative() -> str:
    if INITIATIVE_PATH.exists():
        return INITIATIVE_PATH.read_text(encoding="utf-8")[:4000]
    return ""


def _write_initiative(body: str) -> None:
    INITIATIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    INITIATIVE_PATH.write_text(body, encoding="utf-8")


async def _audit_repo(client: GitHubClient, owner: str, repo: str) -> dict:
    exists = await client.repo_exists(owner, repo)
    if not exists:
        return {"exists": False, "files": {}, "src_entries": []}

    files: dict[str, bool] = {}
    for path in _KEY_FILES:
        text, _ = await client.get_file_text(owner, repo, path)
        files[path] = bool(text.strip())

    src_entries = await client.list_directory(owner, repo, "src")
    src_names = [e.get("name", "") for e in src_entries if isinstance(e, dict)]

    return {
        "exists": True,
        "files": files,
        "src_entries": src_names[:20],
        "mvp_ready": all(files.values()),
    }


async def _execute_holding_site_build(user_message: str, lang: str) -> tuple[str, dict]:
    h = holding_name()
    owner = settings.github_owner
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not github_configured():
        msg = (
            f"Exécution site {h} — bloquée\n\n"
            "GITHUB_TOKEN non configuré. Ajoute-le dans production.env puis sync Render.\n"
            "Sans token : pas d'audit GitHub vérifiable."
            if lang == "fr"
            else f"Holding site execution — blocked\n\nGITHUB_TOKEN not configured."
        )
        return msg, {"audit_complete": False, "error": "github_disabled"}

    if not repo_read_allowed(owner, REPO):
        msg = (
            f"Lecture refusée sur {owner}/{REPO}.\n"
            "Élargis GITHUB_READ_REPOS ou mets * sous GoldenFarFR."
            if lang == "fr"
            else f"Read denied on {owner}/{REPO}."
        )
        return msg, {"audit_complete": False, "error": "read_denied"}

    client = GitHubClient(settings.github_token)
    audit = await _audit_repo(client, owner, REPO)
    if not audit["exists"]:
        msg = (
            f"Repo introuvable : {owner}/{REPO}."
            if lang == "fr"
            else f"Repository not found: {owner}/{REPO}."
        )
        return msg, {"audit_complete": False, "error": "not_found"}

    lines_fr = [
        f"Audit holding — {h}",
        f"Repo : https://github.com/{owner}/{REPO}",
        f"Site live : https://{DOMAIN}",
        f"Horodatage : {ts}",
        "",
        "Fichiers clés :",
    ]
    for path, ok in audit["files"].items():
        lines_fr.append(f"  {'✓' if ok else '✗'} {path}")

    mvp = audit["mvp_ready"]
    lines_fr.append("")
    if mvp:
        lines_fr.append(
            "Verdict : MVP déjà présent (hero, FAQ, nav) — pas de pages fantômes créées."
        )
    else:
        missing = [p for p, ok in audit["files"].items() if not ok]
        lines_fr.append(f"Verdict : manques détectés — {', '.join(missing)}.")
    lines_fr.append("")
    lines_fr.append("Prochaine étape réelle : modifier le code via Cursor ou push explicite GitHub.")

    journal_body = "\n".join(lines_fr)
    _write_initiative(journal_body)
    append_memory("holding", f"[site execute] audit {REPO} mvp_ready={mvp}")

    data: dict = {
        "audit_complete": True,
        "repo": f"{owner}/{REPO}",
        "domain": DOMAIN,
        "mvp_ready": mvp,
        "files": audit["files"],
        "committed": False,
    }

    if repo_write_allowed(owner, REPO):
        audit_md = (
            f"# ARIA holding audit\n\n"
            f"Generated: {ts}\n\n"
            f"Operator message: {user_message[:200]}\n\n"
            f"## Key files\n\n"
            + "\n".join(f"- {'OK' if ok else 'MISSING'} `{p}`" for p, ok in audit["files"].items())
            + f"\n\nMVP ready: **{mvp}**\n"
        )
        try:
            _, sha = await client.get_file_text(owner, REPO, AUDIT_DOC_PATH)
            result = await client.put_file(
                owner,
                REPO,
                AUDIT_DOC_PATH,
                audit_md,
                message="chore(aria): holding site audit (verified)",
                sha=sha,
            )
            commit_sha = (result.get("commit") or {}).get("sha", "")
            html_url = (result.get("commit") or {}).get("html_url", "")
            data["committed"] = True
            data["github_commit_sha"] = commit_sha
            data["github_commit_url"] = html_url
            lines_fr.append("")
            lines_fr.append(f"Journal GitHub : {AUDIT_DOC_PATH}")
            if html_url:
                lines_fr.append(f"Commit : {html_url}")
            lines_fr.append("Render redéploiera si branch main est branch de prod.")
        except Exception as exc:
            lines_fr.append("")
            lines_fr.append(f"Écriture GitHub échouée : {str(exc)[:200]}")
            data["write_error"] = str(exc)[:200]
    else:
        data["write_denied"] = True
        lines_fr.append("")
        lines_fr.append(
            f"Écriture refusée sur {owner}/{REPO} (GITHUB_WRITE_REPOS). "
            "Audit local enregistré — pas de commit."
        )

    if lang != "fr":
        out = journal_body.replace("Audit holding", "Holding audit").replace("Verdict", "Verdict")
        return "\n".join(lines_fr), data
    return "\n".join(lines_fr), data


def _vanguard_has_shooting_star(text: str) -> bool:
    return "ShootingStar" in text and "<ShootingStar" in text


def _patch_vanguard_site(text: str) -> str | None:
    if _vanguard_has_shooting_star(text):
        return None
    import_line = "import { ShootingStar } from '../components/ShootingStar'\n"
    nav_import = "import { VanguardNav } from '../components/VanguardNav'\n"
    if nav_import not in text:
        return None
    patched = text.replace(nav_import, nav_import + import_line, 1)
    orb_anchor = (
        '<Orb className="w-[360px] h-[360px] bg-[#8a7344]/10 top-1/3 -right-48 '
        'animate-vanguard-float-delayed" />\n'
    )
    if orb_anchor not in patched:
        return None
    return patched.replace(
        orb_anchor,
        orb_anchor + "\n        <ShootingStar />\n",
        1,
    )


def _patch_index_css(text: str) -> str | None:
    if SHOOTING_STAR_MARKER in text:
        return None
    return text.rstrip() + "\n" + SHOOTING_STAR_CSS


async def _execute_shooting_star_patch(user_message: str, lang: str) -> tuple[str, dict]:
    h = holding_name()
    owner = settings.github_owner
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not github_configured():
        msg = (
            f"Étoile filante — bloquée\n\nGITHUB_TOKEN non configuré sur Render."
            if lang == "fr"
            else "Shooting star patch — blocked (GITHUB_TOKEN missing)."
        )
        return msg, {"patch_complete": False, "error": "github_disabled"}

    if not repo_read_allowed(owner, REPO):
        msg = (
            f"Lecture refusée sur {owner}/{REPO}."
            if lang == "fr"
            else f"Read denied on {owner}/{REPO}."
        )
        return msg, {"patch_complete": False, "error": "read_denied"}

    client = GitHubClient(settings.github_token)
    exists = await client.repo_exists(owner, REPO)
    if not exists:
        msg = f"Repo introuvable : {owner}/{REPO}." if lang == "fr" else f"Repo not found: {owner}/{REPO}."
        return msg, {"patch_complete": False, "error": "not_found"}

    site_text, _ = await client.get_file_text(owner, REPO, VANGUARD_SITE_PATH)
    css_text, _ = await client.get_file_text(owner, REPO, INDEX_CSS_PATH)
    component_text, component_sha = await client.get_file_text(owner, REPO, SHOOTING_STAR_PATH)

    if not site_text.strip():
        msg = (
            f"{VANGUARD_SITE_PATH} introuvable ou vide."
            if lang == "fr"
            else f"{VANGUARD_SITE_PATH} missing or empty."
        )
        return msg, {"patch_complete": False, "error": "site_missing"}

    needs_component = not component_text.strip()
    patched_site = _patch_vanguard_site(site_text)
    patched_css = _patch_index_css(css_text) if css_text.strip() else None

    already = (
        not needs_component
        and patched_site is None
        and patched_css is None
        and _vanguard_has_shooting_star(site_text)
    )
    if already:
        msg = (
            f"Étoile filante déjà présente sur https://{DOMAIN} "
            f"({owner}/{REPO})."
            if lang == "fr"
            else f"Shooting star already live on https://{DOMAIN}."
        )
        append_memory("holding", f"[site decorate] shooting star already present — {REPO}")
        return msg, {
            "patch_complete": True,
            "already_present": True,
            "repo": f"{owner}/{REPO}",
            "domain": DOMAIN,
        }

    if not repo_write_allowed(owner, REPO):
        msg = (
            f"Patch étoile filante — lecture OK, écriture refusée sur {owner}/{REPO}.\n"
            "Ajoute GoldenFarFR/aria-vanguard (ou *) dans GITHUB_WRITE_REPOS, puis redeploie."
            if lang == "fr"
            else f"Shooting star patch — write denied on {owner}/{REPO}. Set GITHUB_WRITE_REPOS."
        )
        return msg, {
            "patch_complete": True,
            "write_denied": True,
            "repo": f"{owner}/{REPO}",
            "would_patch": {
                "component": needs_component,
                "vanguard_site": patched_site is not None,
                "index_css": patched_css is not None,
            },
        }

    if patched_site is None and not _vanguard_has_shooting_star(site_text):
        msg = (
            "Structure VanguardSite.tsx non reconnue — patch manuel via Cursor."
            if lang == "fr"
            else "VanguardSite.tsx structure not recognized for auto-patch."
        )
        return msg, {"patch_complete": False, "error": "patch_structure"}

    commits: list[str] = []
    data: dict = {
        "patch_complete": True,
        "repo": f"{owner}/{REPO}",
        "domain": DOMAIN,
        "committed": False,
        "files_patched": [],
    }

    lines_fr = [
        f"Étoile filante — {h}",
        f"Repo : https://github.com/{owner}/{REPO}",
        f"Site : https://{DOMAIN}",
        f"Horodatage : {ts}",
        "",
    ]

    try:
        if needs_component:
            result = await client.put_file(
                owner,
                REPO,
                SHOOTING_STAR_PATH,
                SHOOTING_STAR_TSX,
                message="feat(aria): add ShootingStar hero accent",
            )
            url = (result.get("commit") or {}).get("html_url", "")
            if url:
                commits.append(url)
            data["files_patched"].append(SHOOTING_STAR_PATH)

        if patched_site is not None:
            _, site_sha = await client.get_file_text(owner, REPO, VANGUARD_SITE_PATH)
            result = await client.put_file(
                owner,
                REPO,
                VANGUARD_SITE_PATH,
                patched_site,
                message="feat(aria): mount ShootingStar on Vanguard homepage",
                sha=site_sha,
            )
            url = (result.get("commit") or {}).get("html_url", "")
            if url:
                commits.append(url)
            data["files_patched"].append(VANGUARD_SITE_PATH)

        if patched_css is not None:
            _, css_sha = await client.get_file_text(owner, REPO, INDEX_CSS_PATH)
            result = await client.put_file(
                owner,
                REPO,
                INDEX_CSS_PATH,
                patched_css,
                message="feat(aria): shooting star keyframes for Vanguard hero",
                sha=css_sha,
            )
            url = (result.get("commit") or {}).get("html_url", "")
            if url:
                commits.append(url)
            data["files_patched"].append(INDEX_CSS_PATH)

        if data["files_patched"]:
            data["committed"] = True
            data["github_commit_url"] = commits[-1] if commits else ""
            lines_fr.append("Fichiers modifiés :")
            for path in data["files_patched"]:
                lines_fr.append(f"  ✓ {path}")
            lines_fr.append("")
            lines_fr.append("Verdict : commit GitHub réel — Render redéploiera si main = prod.")
            for url in commits:
                lines_fr.append(f"Commit : {url}")
        else:
            lines_fr.append("Aucun fichier à modifier.")

        append_memory(
            "holding",
            f"[site decorate] shooting star patch files={data['files_patched']} committed={data['committed']}",
        )
        _write_initiative("\n".join(lines_fr))
    except Exception as exc:
        err = str(exc)[:200]
        data["write_error"] = err
        lines_fr.append(f"Écriture GitHub échouée : {err}")

    return "\n".join(lines_fr), data


async def execute_holding_site(user_message: str, lang: str = "fr") -> tuple[str, dict]:
    if wants_holding_site_decorate(user_message):
        return await _execute_shooting_star_patch(user_message, lang)
    if wants_holding_site_execute(user_message):
        return await _execute_holding_site_build(user_message, lang)

    h = holding_name()
    initiative = _read_initiative()
    owner = settings.github_owner

    plan_fr = (
        f"Priorité opérateur : site {h}\n\n"
        f"Verdict : plan holding — exécution via GitHub audit vérifiable.\n\n"
        f"Repo cible : {owner}/{REPO}\n"
        f"Domaine : https://{DOMAIN}\n"
        f"Stack : React + Vite (existant), déploiement Render/Cloudflare\n\n"
        f"Plan (≤5 étapes) :\n"
        f"1. Audit GitHub — fichiers clés (VanguardSite, FAQ, nav)\n"
        f"2. Écarts — ce qui manque vs MVP public\n"
        f"3. Commit audit — docs/aria-holding-audit.md si droits d'écriture\n"
        f"4. Code — Cursor ou push explicite (pas de succès inventé)\n"
        f"5. Journal — holding_site_initiative.md\n\n"
        f"Pour exécuter l'audit maintenant : « lancer le site holding »."
    )
    plan_en = (
        f"Operator priority: {h} website\n\n"
        f"Verdict: holding plan — run verified GitHub audit.\n\n"
        f"Target: {owner}/{REPO} — https://{DOMAIN}\n\n"
        f"Say « lancer le site holding » to run the audit (no fake deploy claims)."
    )
    plan = plan_fr if lang == "fr" else plan_en

    if initiative:
        excerpt = initiative[:1200] + ("…" if len(initiative) > 1200 else "")
        plan += f"\n\nÉtat initiative :\n{excerpt}" if lang == "fr" else f"\n\nInitiative state:\n{excerpt}"

    append_memory("holding", f"[site] Plan proposé — {REPO} / {DOMAIN}")
    return plan, {"repo": f"{owner}/{REPO}", "domain": DOMAIN, "plan_only": True}