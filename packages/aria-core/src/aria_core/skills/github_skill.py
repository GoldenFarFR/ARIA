"""GitHub skill — operator read/write across GoldenFar repos (*), sandbox experiments for all."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from aria_core.github_client import GitHubClient
from aria_core.locale import LANG_EN
from aria_core.memory import append_memory
from aria_core.runtime import settings

WILDCARD = "*"


def _parse_repo_list(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _excluded_repo_names() -> set[str]:
    names = _parse_repo_list(settings.github_excluded_repos.replace("/", ","))
    return {n.split("/")[-1].lower() for n in names if n}


def _protected_repo_names() -> set[str]:
    names = _parse_repo_list(settings.github_protected_repos.replace("/", ","))
    return {n.split("/")[-1].lower() for n in names if n}


def _repo_protected(repo: str) -> bool:
    return repo.lower() in _protected_repo_names()


def _delete_permission_hint(lang: str, error: str, info: dict[str, object] | None = None) -> str:
    owner = settings.github_owner
    if lang == "fr":
        base = (
            "GitHub refuse la suppression (403 — droits admin requis).\n\n"
            "Le token `GITHUB_TOKEN` sur Render n'a pas le droit de supprimer des repos.\n\n"
            "Option A — PAT classique (recommandé) :\n"
            "1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)\n"
            "2. Generate new token avec la scope **`delete_repo`** (ou **`repo`** complet)\n"
            "3. Colle le token dans le coffre `production.env` → `GITHUB_TOKEN`\n"
            "4. Lance `sync-render.ps1` puis redeploie\n\n"
            f"Option B — Fine-grained : accès **All repositories** sur `{owner}` + permission "
            "**Administration: Read and write**.\n\n"
            f"Le compte du token doit être **Owner** ou **Admin** sur l'org/compte `{owner}`."
        )
    else:
        base = (
            "GitHub refused delete (403 — admin rights required).\n\n"
            "Regenerate `GITHUB_TOKEN` with **`delete_repo`** (classic PAT) or fine-grained "
            "**Administration: Read and write** on all target repos.\n\n"
            f"Token user must be Owner/Admin on `{owner}`."
        )
    if info:
        login = info.get("login") or "?"
        scopes = info.get("scopes") or []
        fine = info.get("fine_grained")
        if lang == "fr":
            base += f"\n\nToken actuel : @{login}"
            if scopes:
                base += f"\nScopes détectés : {', '.join(scopes)}"
                if "delete_repo" not in scopes and "repo" not in scopes:
                    base += "\n→ `delete_repo` manquant."
            elif fine:
                base += "\nType : fine-grained (vérifie Administration read/write dans le dashboard GitHub)."
        else:
            base += f"\n\nCurrent token: @{login}, scopes: {scopes or 'fine-grained'}"
    if error and len(error) < 200:
        base += f"\n\nGitHub: {error}"
    return base


def github_configured() -> bool:
    return bool(settings.github_token.strip())


def github_unlimited_access() -> bool:
    read = settings.github_read_repos.strip()
    write = settings.github_write_repos.strip()
    owner_wild = f"{settings.github_owner.strip()}/*"
    return WILDCARD in {read, write, owner_wild}


def _repo_excluded(repo: str) -> bool:
    return repo.lower() in _excluded_repo_names()


def repo_read_allowed(owner: str, repo: str) -> bool:
    if _repo_excluded(repo):
        return False
    if github_unlimited_access():
        return owner.lower() == settings.github_owner.strip().lower()
    full = f"{owner}/{repo}"
    return full in allowed_read_repos()


def repo_write_allowed(owner: str, repo: str) -> bool:
    if _repo_excluded(repo):
        return False
    if github_unlimited_access() or settings.github_write_repos.strip() == WILDCARD:
        return owner.lower() == settings.github_owner.strip().lower()
    full = f"{owner}/{repo}"
    return full in allowed_write_repos()


def allowed_write_repos() -> list[str]:
    raw = settings.github_write_repos.strip()
    if raw == WILDCARD or raw == f"{settings.github_owner}/*":
        return [f"{settings.github_owner}/*"]
    repos = _parse_repo_list(raw)
    if not repos:
        repos = [f"{settings.github_owner}/{settings.github_sandbox_repo}"]
        token_repo = settings.github_token_repo.strip()
        if token_repo:
            full = f"{settings.github_owner}/{token_repo}"
            if full not in repos:
                repos.append(full)
    return [r for r in repos if not _repo_excluded(r.split("/")[-1])]


def allowed_read_repos() -> list[str]:
    raw = settings.github_read_repos.strip()
    if raw == WILDCARD or raw == f"{settings.github_owner}/*":
        return [f"{settings.github_owner}/*"]
    repos = _parse_repo_list(raw)
    if not repos:
        repos = [
            f"{settings.github_owner}/{settings.github_sandbox_repo}",
            f"{settings.github_owner}/aria-vanguard",
        ]
    return [r for r in repos if not _repo_excluded(r.split("/")[-1])]


def _split_full_repo(full: str) -> tuple[str, str]:
    if "/" in full:
        o, r = full.split("/", 1)
        return o, r
    return settings.github_owner, full


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:48] or "experiment")


def looks_like_repo_delete(message: str) -> bool:
    lower = message.lower()
    if not any(w in lower for w in ("supprim", "delete", "remove", "effac", "destroy")):
        return False
    if any(
        phrase in lower
        for phrase in (
            "répertoire",
            "repertoire",
            "du répertoire",
            "from repertoire",
            "du repertoire",
        )
    ):
        return False
    return bool(
        re.search(r"\brepo(?:sitory|s)?\b", lower)
        or re.search(r"goldenfarfr/", lower)
        or _extract_repo_from_message(message)
    )


def _extract_delete_repo_name(message: str) -> str | None:
    pair = _extract_repo_from_message(message)
    if pair:
        return pair[1]
    patterns = (
        r"(?:supprim(?:e|er)?|delete|remove|effac(?:e|er)?|destroy)\s+"
        r"(?:le\s+|la\s+|un\s+)?(?:repo(?:sitory)?|dépôt|depot)\s+[`'\"]?([a-zA-Z0-9_.-]+)",
        r"(?:repo(?:sitory)?|dépôt|depot)\s+[`'\"]?([a-zA-Z0-9_.-]+)[`'\"]?\s+"
        r"(?:à supprimer|to delete|supprim)",
    )
    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if match:
            return match.group(1)
    return None


def _extract_repo_from_message(message: str) -> tuple[str, str] | None:
    m = re.search(
        r"(?:repo|dépôt|repository)\s+[`'\"]?([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)[`'\"]?",
        message,
        re.I,
    )
    if m:
        token = m.group(1)
        if "/" in token:
            return _split_full_repo(token)
        return settings.github_owner, token
    m = re.search(r"GoldenFarFR/([A-Za-z0-9_.-]+)", message, re.I)
    if m:
        return settings.github_owner, m.group(1)
    return None


def _extract_file_path(message: str) -> str | None:
    m = re.search(
        r"(?:file|fichier|path|chemin)\s+[`'\"]([^`']+)[`'\"]",
        message,
        re.I,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:read|lis|lire|open|ouvre)\s+([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)", message, re.I)
    if m:
        return m.group(1).strip()
    return None


async def execute_github_sandbox(user_message: str, lang: str = "en") -> tuple[str, dict]:
    lower = user_message.lower()

    if not github_configured():
        msg = (
            "GitHub non configuré. Ajoute `GITHUB_TOKEN` dans `.env` (local) ou `production.env` (Render)."
            if lang == "fr"
            else "GitHub not configured. Add `GITHUB_TOKEN` to `.env` (local) or `production.env` (Render)."
        )
        return msg, {"status": "disabled"}

    client = GitHubClient(settings.github_token)
    owner = settings.github_owner

    if any(w in lower for w in ("status", "config", "droits", "rights", "allowed", "permissions", "scopes")):
        unlimited = github_unlimited_access()
        lines = [
            "GitHub — mode opérateur" if lang == "fr" else "GitHub — operator mode",
            f"Unlimited: {'yes' if unlimited else 'no'} ({owner})",
            f"Write: {', '.join(allowed_write_repos())}",
            f"Read: {', '.join(allowed_read_repos())}",
            f"Excluded: {', '.join(sorted(_excluded_repo_names())) or 'none'}",
            f"Protected (no delete): {', '.join(sorted(_protected_repo_names())) or 'none'}",
            f"Sandbox: {owner}/{settings.github_sandbox_repo}",
        ]
        if settings.github_token_repo.strip():
            lines.append(f"Token repo: {owner}/{settings.github_token_repo.strip()}")
        try:
            info = await client.token_info()
            login = info.get("login") or "?"
            scopes = info.get("scopes") or []
            fine = bool(info.get("fine_grained"))
            can_del = GitHubClient.delete_capable(scopes, fine_grained=fine)
            lines.append(f"Token user: @{login}")
            if scopes:
                lines.append(f"Scopes: {', '.join(scopes)}")
            else:
                lines.append("Scopes: (fine-grained — voir Administration sur GitHub)")
            if can_del is True:
                lines.append("Delete repos: oui (delete_repo/repo scope)" if lang == "fr" else "Delete repos: yes")
            elif can_del is False:
                lines.append(
                    "Delete repos: NON — regénère le token avec scope delete_repo"
                    if lang == "fr"
                    else "Delete repos: NO — regenerate token with delete_repo"
                )
            else:
                lines.append(
                    "Delete repos: inconnu — teste /github delete sur un repo demo"
                    if lang == "fr"
                    else "Delete repos: unknown — try /github delete on a demo repo"
                )
        except Exception as exc:
            lines.append(f"Token check failed: {str(exc)[:120]}")
        if unlimited:
            names = await client.list_org_repos(owner)
            visible = [n for n in names if not _repo_excluded(n)]
            lines.append(f"Repos visibles ({len(visible)}):")
            for name in visible:
                lines.append(f"  • {owner}/{name}")
            hidden = len(names) - len(visible)
            if hidden:
                lines.append(f"  (exclus: {hidden} — secrets)")
        else:
            for full in allowed_write_repos():
                if full.endswith("/*"):
                    continue
                o, r = _split_full_repo(full)
                ok = await client.repo_exists(o, r)
                lines.append(f"  • {full} — {'exists' if ok else 'not created yet'}")
        return "\n".join(lines), {"status": "ok", "unlimited": unlimited}

    if looks_like_repo_delete(user_message):
        repo_name = _extract_delete_repo_name(user_message)
        if not repo_name:
            msg = (
                "Précise le repo : `supprime repo kikou` ou `/github delete kikou`."
                if lang == "fr"
                else "Specify the repo: `delete repo kikou` or `/github delete kikou`."
            )
            return msg, {"error": "missing_name"}
        if _repo_excluded(repo_name) or _repo_protected(repo_name):
            return (
                f"Repo `{repo_name}` protégé — suppression refusée."
                if lang == "fr"
                else f"Repository `{repo_name}` is protected — delete refused."
            ), {"error": "protected", "repo": f"{owner}/{repo_name}"}
        if not repo_write_allowed(owner, repo_name):
            return (
                f"Suppression refusée pour {owner}/{repo_name}."
                if lang == "fr"
                else f"Delete denied for {owner}/{repo_name}."
            ), {"error": "write_denied"}
        if not await client.repo_exists(owner, repo_name):
            return (
                f"Repo introuvable : {owner}/{repo_name}."
                if lang == "fr"
                else f"Repository not found: {owner}/{repo_name}."
            ), {"error": "not_found", "repo": f"{owner}/{repo_name}"}
        try:
            await client.delete_repo(owner, repo_name)
        except FileNotFoundError:
            return (
                f"Repo introuvable : {owner}/{repo_name}."
                if lang == "fr"
                else f"Repository not found: {owner}/{repo_name}."
            ), {"error": "not_found"}
        except Exception as exc:
            err = str(exc)
            info: dict[str, object] | None = None
            if "403" in err or "admin" in err.lower():
                try:
                    info = await client.token_info()
                except Exception:
                    pass
            if "403" in err or "admin" in err.lower():
                return _delete_permission_hint(lang, err, info), {"error": "forbidden", "http": 403}
            hint = (
                " Vérifie GITHUB_TOKEN sur Render."
                if lang == "fr"
                else " Check GITHUB_TOKEN on Render."
            )
            return f"Échec suppression : {err[:300]}{hint}", {"error": err}
        append_memory("github", f"[delete_repo] {owner}/{repo_name}")
        if lang == "fr":
            out = f"Repo supprimé ✅\n\n**{owner}/{repo_name}** retiré de GitHub."
        else:
            out = f"Repository deleted ✅\n\n**{owner}/{repo_name}** removed from GitHub."
        return out, {"repo": f"{owner}/{repo_name}", "deleted": True}

    if looks_like_repo_create(user_message):
        repo_name = await _resolve_new_repo_name(user_message)
        if not repo_name:
            msg = (
                "Précise le nom : `créer repo mon-projet` ou `create repo aria-demo`."
                if lang == "fr"
                else "Specify the name: `create repo my-project`."
            )
            return msg, {"error": "missing_name"}
        if _repo_excluded(repo_name):
            return f"Repo `{repo_name}` exclu (secrets).", {"error": "excluded"}
        if await client.repo_exists(owner, repo_name):
            url = f"https://github.com/{owner}/{repo_name}"
            return (
                f"Le repo existe déjà : {url}"
                if lang == "fr"
                else f"Repository already exists: {url}"
            ), {"url": url, "exists": True}
        description = _extract_summary(user_message) or f"Created by ARIA ZHC for {owner}"
        try:
            result = await client.create_repo(
                owner,
                repo_name,
                private=True,
                description=description,
                auto_init=True,
            )
        except Exception as exc:
            err = str(exc)
            hint = (
                " Vérifie que le token GitHub a le droit de créer des repos sur l'org."
                if lang == "fr"
                else " Check GitHub token has org repo creation permission."
            )
            return f"Échec création repo : {err[:300]}{hint}", {"error": err}
        url = result.get("html_url", f"https://github.com/{owner}/{repo_name}")
        append_memory("github", f"[create_repo] {owner}/{repo_name}")
        readme_path = "README.md"
        readme = f"# {repo_name}\n\n{description}\n\n*Créé par ARIA ZHC — {owner}*\n"
        try:
            await client.put_file(owner, repo_name, readme_path, readme, f"ARIA: init {repo_name}")
        except Exception:
            pass
        if lang == "fr":
            out = f"Repo créé ✅\n\n**{owner}/{repo_name}** (privé)\n{url}"
        else:
            out = f"Repository created ✅\n\n**{owner}/{repo_name}** (private)\n{url}"
        return out, {"url": url, "repo": f"{owner}/{repo_name}"}

    repo_pair = _extract_repo_from_message(user_message)
    if repo_pair and any(
        w in lower for w in (
            "vois", "voir", "see", "connais", "know", "existe", "exists",
            "accès", "access", "détect", "detect", "trouve", "find",
        )
    ):
        o, repo = repo_pair
        if _repo_excluded(repo):
            return f"Repo `{repo}` exclu (secrets).", {"error": "excluded", "repo": f"{o}/{repo}"}
        if not repo_read_allowed(o, repo):
            return f"Lecture refusée pour {o}/{repo}.", {"error": "read_denied"}
        exists = await client.repo_exists(o, repo)
        url = f"https://github.com/{o}/{repo}"
        if lang == "fr":
            if exists:
                out = f"Oui — je vois **{o}/{repo}** ✅\n{url}\nLecture et écriture autorisées."
            else:
                out = f"Non — **{o}/{repo}** introuvable sur GitHub."
        else:
            out = (
                f"Yes — I see **{o}/{repo}** ✅\n{url}"
                if exists
                else f"No — **{o}/{repo}** not found on GitHub."
            )
        return out, {"repo": f"{o}/{repo}", "exists": exists, "url": url}

    if any(w in lower for w in ("list repos", "liste repos", "all repos", "tous les repos")):
        if not github_unlimited_access():
            return (
                "Liste complète réservée au mode `GITHUB_READ_REPOS=*`."
                if lang == "fr"
                else "Full repo list requires `GITHUB_READ_REPOS=*`."
            ), {"error": "not_unlimited"}
        names = await client.list_org_repos(owner)
        visible = [n for n in names if not _repo_excluded(n)]
        body = "Repos:\n" + "\n".join(f"- {owner}/{n}" for n in visible)
        return body, {"repos": visible}

    file_path = _extract_file_path(user_message)
    repo_pair = _extract_repo_from_message(user_message)
    if file_path and repo_pair:
        o, repo = repo_pair
        if not repo_read_allowed(o, repo):
            return f"Read denied for {o}/{repo}", {"error": "read_denied"}
        text, _ = await client.get_file_text(o, repo, file_path)
        if not text:
            return f"File not found: {o}/{repo}/{file_path}", {"error": "not_found"}
        preview = text[:3500] + ("…" if len(text) > 3500 else "")
        return f"`{file_path}` ({o}/{repo}):\n\n{preview}", {"path": file_path, "repo": f"{o}/{repo}"}

    if any(w in lower for w in ("list", "liste", "experiments", "expériences")):
        o, r = owner, settings.github_sandbox_repo
        entries = await client.list_directory(o, r, "experiments")
        names = [e["name"] for e in entries if e.get("type") == "dir"]
        if not names:
            body = (
                "No experiments yet — ask ARIA to create one."
                if lang == LANG_EN
                else "Aucune expérience — demande à ARIA d'en créer une."
            )
            return body, {"experiments": []}
        return "Experiments:\n" + "\n".join(f"- {n}" for n in names), {"experiments": names}

    slug = _extract_slug(user_message) or f"exp-{_slugify(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M'))}"
    summary = _extract_summary(user_message)
    path = f"experiments/{slug}/README.md"
    o, repo = owner, settings.github_sandbox_repo
    if repo_pair and repo_write_allowed(*repo_pair):
        o, repo = repo_pair
        if file_path:
            path = file_path

    if not repo_write_allowed(o, repo):
        return f"Write denied for {o}/{repo}", {"error": "write_denied"}

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    content = f"""# Experiment: {slug}

Created by **ARIA ZHC** — {ts}

## Intent
{summary}

## Status
`draft` — sandbox only, not production.

## Next steps
1. Scaffold minimal prototype
2. Deploy preview (Render static) when ready
3. `/learn` the pattern if it works
4. Discard or promote — operator decides

---
*Aria Vanguard ZHC holding — Builder Queen sandbox*
"""
    _, sha = await client.get_file_text(o, repo, path)
    commit_msg = f"ARIA: {slug}" if path.startswith("experiments/") else f"ARIA: update {path}"
    result = await client.put_file(o, repo, path, content, commit_msg, sha=sha)
    html = result.get("content", {}).get("html_url", f"https://github.com/{o}/{repo}/tree/main/{path}")
    append_memory("github", f"[write] {o}/{repo}/{path}: {summary[:80]}")
    if lang == "fr":
        out = (
            f"Écriture GitHub OK.\n\n"
            f"**{o}/{repo}** → {path}\n{html}\n\n"
            f"Accès opérateur : lecture/écriture sur tous les repos `{owner}/*` "
            f"(sauf exclus : {', '.join(sorted(_excluded_repo_names())) or 'aucun'})."
        )
    else:
        out = (
            f"GitHub write OK.\n\n"
            f"**{o}/{repo}** → {path}\n{html}\n\n"
            f"Operator access: read/write on all `{owner}/*` repos "
            f"(excluded: {', '.join(sorted(_excluded_repo_names())) or 'none'})."
        )
    return out, {"slug": slug, "url": html, "repo": f"{o}/{repo}", "path": path}


def _extract_slug(message: str) -> str | None:
    m = re.search(r"(?:experiment|expérience|sandbox|repo)\s+[`'\"]?([a-zA-Z0-9_-]+)", message, re.I)
    if m:
        return _slugify(m.group(1))
    m = re.search(r"`([^`]+)`", message)
    if m:
        return _slugify(m.group(1))
    return None


def _extract_new_repo_name(message: str) -> str | None:
    patterns = (
        r"(?:créer|crée|creer|cree|create|nouveau|new)\s+(?:le\s+|un\s+)?(?:repo|repository|dépôt|depot)\s+[`'\"]?([a-zA-Z0-9_.-]+)",
        r"(?:repo|repository)\s+[`'\"]?([a-zA-Z0-9_.-]+)[`'\"]?\s+(?:sur github|on github)",
        r"GoldenFarFR/([A-Za-z0-9_.-]+)\s+(?:nouveau|new)",
        r"\b(aria-[a-z0-9][a-z0-9-]*)\b",
    )
    for pattern in patterns:
        m = re.search(pattern, message, re.I)
        if m:
            return _slugify(m.group(1))
    return None


def _wants_create_repo(message: str) -> bool:
    lower = message.lower()
    if any(
        phrase in lower
        for phrase in (
            "créer un repo",
            "créer repo",
            "crée le repo",
            "crée un repo",
            "creer un repo",
            "creer repo",
            "cree le repo",
            "create repo",
            "create repository",
            "create the repo",
            "nouveau repo",
            "new repo",
            "nouveau dépôt",
        )
    ):
        return True
    return bool(re.search(r"(crée|créer|creer|create).{0,30}(repo|repository|dépôt)", lower))


def looks_like_repo_create(message: str) -> bool:
    from aria_core.tweet_compose_workflow import is_tweet_operator_context

    if is_tweet_operator_context(message):
        return False
    if _wants_create_repo(message):
        return True
    lower = message.lower()
    return bool(
        re.search(r"\baria-[a-z0-9][a-z0-9-]*\b", lower)
        and re.search(r"(repo|template|github|cré|cre|create)", lower)
    )


async def _resolve_new_repo_name(user_message: str) -> str | None:
    name = _extract_new_repo_name(user_message)
    if name:
        return name
    if not (_wants_create_repo(user_message) or looks_like_repo_create(user_message)):
        return None
    from aria_core import repertoire_db

    messages = await repertoire_db.get_messages(limit=8, visitor_id=None)
    for msg in messages:
        if msg["role"] != "user":
            continue
        name = _extract_new_repo_name(msg["content"])
        if name:
            return name
    return None


def _extract_summary(message: str) -> str:
    for prefix in ("create", "créer", "build", "construire", "experiment", "sandbox"):
        if prefix in message.lower():
            parts = re.split(rf"{prefix}\s+", message, flags=re.I, maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                return parts[1].strip()[:500]
    return message.strip()[:500]