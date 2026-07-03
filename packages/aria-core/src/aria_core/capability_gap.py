"""Phase 3 auto-developpement — lacune capacite -> issue + PR aria-sandbox."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aria_core.memory import append_memory
from aria_core.runtime import settings

logger = logging.getLogger(__name__)

DEDUP_DAYS = 7

CAPABILITY_SPECS: dict[str, dict[str, Any]] = {
    "x_profile_banner": {
        "title": "Capacite: banniere profil X (update_profile_banner)",
        "labels": ["aria-self-improve", "capability-gap"],
        "target_files": [
            "packages/aria-core/src/aria_core/gateway/x_twitter.py",
            "packages/aria-core/src/aria_core/x_banner.py",
            "packages/aria-core/src/aria_core/self_maintenance.py",
        ],
        "acceptance": [
            "apply_profile_banner() upload via API X v1.1",
            "Boucle curiosite self_maintenance reussit sans blocage",
            "Test unitaire mock upload",
        ],
    },
    "x_oauth_write": {
        "title": "Capacite: cles X OAuth Read+Write configurees",
        "labels": ["aria-self-improve", "ops"],
        "target_files": ["production.env via operator/sync-render.ps1"],
        "acceptance": ["is_x_post_configured() True sur Render"],
    },
    "image_api_key": {
        "title": "Capacite: generation banniere X 3:1 (IMAGE_API_KEY — ≠ avatar carre)",
        "labels": ["aria-self-improve", "capability-gap"],
        "target_files": [
            "packages/aria-core/src/aria_core/portrait_scene.py",
            "packages/aria-core/src/aria_core/x_banner.py",
            "aria-vanguard/operator/production.env.example",
        ],
        "acceptance": [
            "IMAGE_API_KEY configure sur Render (xai-...)",
            "generate_banner_creative (Imagine text-to-image) retourne JPEG 3:1 (x_banner.jpg 1500x500, <=3 Mo)",
            "Distinct de current.jpg (avatar profil carre)",
        ],
    },
    "identity_anchor": {
        "title": "Capacite: ancre identite visage (≠ avatar actif, ≠ banniere X)",
        "labels": ["aria-self-improve", "capability-gap"],
        "target_files": [
            "packages/aria-core/src/aria_core/avatar_identity.py",
        ],
        "acceptance": [
            "has_identity_anchor() True sur Render",
            "Telegram /avatar identity — reference visage pour banniere/scenes",
            "Ne remplace pas x_banner.jpg (header 3:1)",
        ],
    },
    "x_banner_generate": {
        "title": "Capacite: asset banniere X local (x_banner.jpg 3:1)",
        "labels": ["aria-self-improve", "capability-gap"],
        "target_files": [
            "packages/aria-core/src/aria_core/x_banner.py",
            "packages/aria-core/src/aria_core/portrait_scene.py",
        ],
        "acceptance": [
            "x_banner.jpg present ou genere depuis ancre identite",
            "Upload X via apply_profile_banner (distinct de avatar profil)",
        ],
    },
    "security_ip_changed_vault": {
        "title": "Securite: IP changee lors acces vault/sync",
        "labels": ["aria-security", "capability-gap"],
        "target_repo": "aria-local-sync",
        "open_pr": False,
        "target_files": ["security/github-trust.yaml", "scripts/report-machine-ip.ps1"],
        "acceptance": ["IP enregistree pour machine connue", "Pas de critical ip_changed_vault"],
    },
    "security_unknown_machine_vault": {
        "title": "Securite: machine inconnue a touche vault",
        "labels": ["aria-security", "capability-gap"],
        "target_repo": "aria-local-sync",
        "open_pr": False,
        "target_files": ["security/github-trust.yaml"],
        "acceptance": ["Machine ajoutee a known_machines ou acces revoque"],
    },
    "security_github_foreign_actor": {
        "title": "Securite: push GitHub par acteur non autorise",
        "labels": ["aria-security", "capability-gap"],
        "target_repo": "aria-local-sync",
        "open_pr": False,
        "target_files": ["security/github-trust.yaml", "scripts/audit-github-security.ps1"],
        "acceptance": ["trusted_github_logins a jour", "Pas de push etranger"],
    },
    "security_vault_untrusted_origin": {
        "title": "Securite: origine/IP non enregistree vault",
        "labels": ["aria-security", "capability-gap"],
        "target_repo": "aria-local-sync",
        "open_pr": False,
        "target_files": ["security/github-trust.yaml", "scripts/report-machine-ip.ps1"],
        "acceptance": ["IP reportee via report-machine-ip.ps1"],
    },
    "health_render_regression": {
        "title": "Incident: regression health Render (3 echecs)",
        "labels": ["aria-ops", "capability-gap"],
        "target_repo": "aria-vanguard",
        "open_pr": False,
        "target_files": ["operator/check-aria-status.ps1", "backend/app/main.py"],
        "acceptance": ["GET /api/health status=ok", "check-aria-status.ps1 exit 0"],
    },
    "operator_health_check_failed": {
        "title": "Incident operateur: check-aria-status en echec",
        "labels": ["aria-ops", "capability-gap"],
        "target_repo": "aria-vanguard",
        "open_pr": False,
        "target_files": ["operator/check-aria-status.ps1", "operator/sync-render.ps1"],
        "acceptance": ["check-aria-status.ps1 exit 0", "operator_pitfalls.yaml mis a jour"],
    },
    "operator_env_mismatch": {
        "title": "Incident operateur: divergence env Render vs production.env",
        "labels": ["aria-ops"],
        "target_repo": "aria-vanguard",
        "open_pr": False,
        "target_files": ["operator/sync-render.ps1"],
        "acceptance": ["Secrets alignes Render/local", "Health OK apres redeploy"],
    },
    "skill_missing": {
        "title": "Capacite: skill Grok/Cursor manquant (aria-skills)",
        "labels": ["aria-self-improve", "capability-gap"],
        "target_repo": "aria-skills",
        "open_pr": True,
        "target_files": [".grok/skills/<skill>/SKILL.md"],
        "acceptance": ["SKILL.md present dans aria-skills", "Template installe sur PC operateur"],
    },
    "post_session_aria_core_bump": {
        "title": "Deploy: session a modifie aria-core — bump pin requirements",
        "labels": ["aria-self-improve", "deploy"],
        "target_repo": "aria-vanguard",
        "open_pr": True,
        "target_files": ["backend/requirements.txt", "operator/sync-render.ps1"],
        "acceptance": [
            "Pin aria-core pointe vers commit sandbox recent",
            "sync-render.ps1 + health aria_core_build a jour",
        ],
    },
}

SECURITY_RULE_TO_GAP: dict[str, str] = {
    "ip_changed_vault": "security_ip_changed_vault",
    "unknown_machine_vault": "security_unknown_machine_vault",
    "github_foreign_actor": "security_github_foreign_actor",
    "vault_untrusted_origin": "security_vault_untrusted_origin",
}


def _gaps_dir() -> Path:
    from aria_core.paths import data_dir

    path = data_dir() / "capability-gaps"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug_branch(capability_id: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", capability_id.lower()).strip("-")
    return f"aria/gap-{clean[:40]}"


def _load_record(capability_id: str) -> dict[str, Any] | None:
    path = _gaps_dir() / f"{capability_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_record(capability_id: str, record: dict[str, Any]) -> None:
    path = _gaps_dir() / f"{capability_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _recently_filed(capability_id: str) -> dict[str, Any] | None:
    rec = _load_record(capability_id)
    if not rec or not rec.get("filed_at"):
        return None
    try:
        filed = datetime.fromisoformat(rec["filed_at"])
        if filed.tzinfo is None:
            filed = filed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if datetime.now(timezone.utc) - filed < timedelta(days=DEDUP_DAYS):
        return rec
    return None


async def _find_open_github_gap_issue(
    owner: str,
    repo: str,
    capability_id: str,
) -> dict[str, Any] | None:
    """Évite les doublons quand le disque Render est éphémère (JSON local perdu)."""
    from aria_core.github_client import GitHubClient

    marker = f"`{capability_id}`"
    client = GitHubClient(settings.github_token.strip())
    try:
        issues = await client.list_open_issues(owner, repo, labels="capability-gap")
    except Exception as exc:
        logger.warning("cap-gap github list issues failed: %s", exc)
        return None
    for issue in issues:
        body = str(issue.get("body") or "")
        if marker in body or f"Capability gap: {capability_id}" in body:
            return {
                "issue_url": issue.get("html_url"),
                "issue_number": issue.get("number"),
                "filed_at": issue.get("created_at"),
            }
    return None


def capability_available(capability_id: str) -> bool:
    """Introspection legere — True si le code semble present."""
    if capability_id == "x_profile_banner":
        try:
            from aria_core.gateway.x_twitter import apply_profile_banner  # noqa: F401
            return True
        except ImportError:
            return False
    if capability_id == "x_oauth_write":
        from aria_core.gateway.x_twitter import is_x_post_configured
        return is_x_post_configured()
    if capability_id == "image_api_key":
        from aria_core.portrait_scene import _image_api_key
        return bool(_image_api_key())
    if capability_id == "identity_anchor":
        from aria_core.avatar_identity import has_identity_anchor
        return has_identity_anchor()
    return False


def _build_spec_markdown(
    capability_id: str,
    *,
    context: str,
    spec: dict[str, Any],
) -> str:
    files = spec.get("target_files") or []
    acceptance = spec.get("acceptance") or []
    lines = [
        f"# Capability gap: `{capability_id}`",
        "",
        f"Genere par ARIA le {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Contexte",
        context.strip() or "(aucun detail)",
        "",
        "## Fichiers cibles",
    ]
    lines.extend(f"- `{f}`" for f in files)
    lines.append("")
    lines.append("## Criteres d'acceptation")
    lines.extend(f"- [ ] {a}" for a in acceptance)
    lines.extend([
        "",
        "## Boucle curiosite",
        "",
        "1. Observer le gap",
        "2. Ouvrir cette issue + PR spec",
        "3. Implementer dans aria-core",
        "4. Deploy Render + preuve operateur",
    ])
    return "\n".join(lines)


async def file_capability_gap(
    capability_id: str,
    *,
    context: str = "",
    lang: str = "fr",
    open_pr: bool = True,
) -> dict[str, Any]:
    """
    Ouvre issue GitHub (+ PR spec optionnelle) sur aria-sandbox.
    Dedup 7 jours par capability_id.
    """
    from aria_core.skills.github_skill import github_configured, repo_write_allowed

    spec = CAPABILITY_SPECS.get(capability_id, {
        "title": f"Capacite manquante: {capability_id}",
        "labels": ["aria-self-improve"],
        "target_files": [],
        "acceptance": [],
    })

    existing = _recently_filed(capability_id)
    if existing:
        return {
            "status": "dedup",
            "capability_id": capability_id,
            "issue_url": existing.get("issue_url"),
            "pr_url": existing.get("pr_url"),
            "filed_at": existing.get("filed_at"),
        }

    owner = settings.github_owner.strip()
    repo = (spec.get("target_repo") or settings.github_sandbox_repo).strip()

    if github_configured():
        gh_open = await _find_open_github_gap_issue(owner, repo, capability_id)
        if gh_open:
            record = {
                "capability_id": capability_id,
                "filed_at": gh_open.get("filed_at"),
                "issue_url": gh_open.get("issue_url"),
                "issue_number": gh_open.get("issue_number"),
            }
            _save_record(capability_id, record)
            return {
                "status": "dedup",
                "capability_id": capability_id,
                "issue_url": gh_open.get("issue_url"),
                "pr_url": None,
                "filed_at": gh_open.get("filed_at"),
                "dedup_source": "github_open_issue",
            }
    do_open_pr = open_pr and spec.get("open_pr", True)
    title = spec.get("title") or f"Gap: {capability_id}"
    body = _build_spec_markdown(capability_id, context=context, spec=spec)
    record: dict[str, Any] = {
        "capability_id": capability_id,
        "context": context[:2000],
        "filed_at": datetime.now(timezone.utc).isoformat(),
        "issue_url": None,
        "pr_url": None,
        "local_spec": str(_gaps_dir() / f"{capability_id}.md"),
    }

    spec_path = _gaps_dir() / f"{capability_id}.md"
    spec_path.write_text(body, encoding="utf-8")

    if not github_configured():
        append_memory("self-improve", f"[cap-gap] {capability_id} — local seulement (pas GITHUB_TOKEN)")
        _save_record(capability_id, record)
        await _notify_gap(capability_id, record, lang=lang)
        record["status"] = "local_only"
        await _enqueue_worker_after_gap(capability_id, context, record, lang)
        return record

    if not repo_write_allowed(owner, repo):
        append_memory("self-improve", f"[cap-gap] {capability_id} — ecriture {owner}/{repo} refusee")
        _save_record(capability_id, record)
        await _notify_gap(capability_id, record, lang=lang)
        record["status"] = "write_denied"
        await _enqueue_worker_after_gap(capability_id, context, record, lang)
        return record

    from aria_core.github_client import GitHubClient

    client = GitHubClient(settings.github_token.strip())
    try:
        issue = await client.create_issue(
            owner,
            repo,
            title,
            body,
            labels=list(spec.get("labels") or []),
        )
        record["issue_url"] = issue.get("html_url")
        record["issue_number"] = issue.get("number")
    except Exception as exc:
        logger.warning("capability_gap issue failed: %s", exc)
        record["issue_error"] = str(exc)[:300]
        _save_record(capability_id, record)
        await _notify_gap(capability_id, record, lang=lang)
        record["status"] = "issue_failed"
        await _enqueue_worker_after_gap(capability_id, context, record, lang)
        return record

    if do_open_pr and repo == settings.github_sandbox_repo.strip():
        branch = _slug_branch(capability_id)
        doc_path = f"docs/capability-gaps/{capability_id}.md"
        try:
            base_sha = await client.get_branch_sha(owner, repo, "main")
            await client.create_branch(owner, repo, branch, from_sha=base_sha)
            pr_body = (
                f"Spec auto-generee par ARIA.\n\n"
                f"Liee a issue #{record.get('issue_number')}.\n\n"
                f"{body[:4000]}"
            )
            await client.put_file(
                owner,
                repo,
                doc_path,
                body,
                message=f"docs: capability gap {capability_id}",
                branch=branch,
            )
            pr = await client.create_pull_request(
                owner,
                repo,
                title=f"spec: {capability_id}",
                head=branch,
                body=pr_body,
            )
            record["pr_url"] = pr.get("html_url")
            record["branch"] = branch
        except Exception as exc:
            logger.warning("capability_gap PR failed: %s", exc)
            record["pr_error"] = str(exc)[:300]

    _save_record(capability_id, record)
    append_memory(
        "self-improve",
        f"[cap-gap] {capability_id} issue={record.get('issue_url')} pr={record.get('pr_url')}",
    )
    await _notify_gap(capability_id, record, lang=lang)
    try:
        from aria_core.aria_worker_queue import enqueue_from_capability_gap

        worker = await enqueue_from_capability_gap(
            capability_id,
            context=context,
            gap_result=record,
            lang=lang,
        )
        record["worker_queue"] = worker
    except Exception as exc:
        logger.warning("aria_worker_queue after cap-gap failed: %s", exc)
    return {**record, "status": "filed"}


def count_resolved_gaps(*, days: int = 7) -> int:
    """Gaps filees recemment dont la capacite est maintenant disponible."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    resolved = 0
    gaps_path = _gaps_dir()
    if not gaps_path.is_dir():
        return 0
    for path in gaps_path.glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        filed_at = rec.get("filed_at")
        cap_id = rec.get("capability_id") or path.stem
        if not filed_at:
            continue
        try:
            filed = datetime.fromisoformat(filed_at)
            if filed.tzinfo is None:
                filed = filed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if filed < cutoff:
            continue
        if capability_available(cap_id):
            resolved += 1
    return resolved


async def file_audit_security_gaps(
    findings: list[dict[str, Any]],
    *,
    lang: str = "fr",
) -> list[dict[str, Any]]:
    """Ouvre une issue par regle critical (dedup 7j par capability_id)."""
    results: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    for finding in findings:
        if finding.get("severity") != "critical":
            continue
        rule = str(finding.get("rule") or "")
        cap_id = SECURITY_RULE_TO_GAP.get(rule)
        if not cap_id or rule in seen_rules:
            continue
        seen_rules.add(rule)
        ctx = (
            f"repo={finding.get('repo')} rule={rule}\n"
            f"{finding.get('detail', '')}"
        )
        spec = CAPABILITY_SPECS.get(cap_id, {})
        result = await file_capability_gap(
            cap_id,
            context=ctx.strip(),
            lang=lang,
            open_pr=bool(spec.get("open_pr", False)),
        )
        results.append(result)
    return results


async def file_operator_incident(
    incident_id: str,
    context: str,
    *,
    pitfall: dict[str, Any] | None = None,
    lang: str = "fr",
) -> dict[str, Any]:
    """Issue GitHub + append operator_pitfalls.yaml si nouveau."""
    cap_id = incident_id if incident_id.startswith("operator_") else f"operator_{incident_id}"
    if pitfall:
        from aria_core.knowledge.operator_runbook import append_pitfall_if_new

        append_pitfall_if_new(pitfall)
    spec = CAPABILITY_SPECS.get(cap_id, {})
    return await file_capability_gap(
        cap_id,
        context=context,
        lang=lang,
        open_pr=bool(spec.get("open_pr", False)),
    )


async def file_skill_gap(skill_slug: str, context: str = "", *, lang: str = "fr") -> dict[str, Any]:
    slug = re.sub(r"[^a-z0-9_-]+", "-", skill_slug.lower()).strip("-")[:60]
    ctx = f"skill={skill_slug}\n{context}".strip()
    return await file_capability_gap("skill_missing", context=ctx, lang=lang)


async def file_post_session_bump(context: str, *, lang: str = "fr") -> dict[str, Any]:
    return await file_capability_gap("post_session_aria_core_bump", context=context, lang=lang)


async def _enqueue_worker_after_gap(
    capability_id: str,
    context: str,
    record: dict[str, Any],
    lang: str,
) -> None:
    try:
        from aria_core.aria_worker_queue import enqueue_from_capability_gap

        worker = await enqueue_from_capability_gap(
            capability_id,
            context=context,
            gap_result=record,
            lang=lang,
        )
        record["worker_queue"] = worker
    except Exception as exc:
        logger.warning("aria_worker_queue after cap-gap failed: %s", exc)


async def _notify_gap(capability_id: str, record: dict[str, Any], *, lang: str) -> None:
    try:
        from aria_core.gateway.telegram_bot import notify_admin
    except ImportError:
        return

    if lang == "fr":
        msg = (
            "ARIA — lacune capacite detectee\n\n"
            f"ID : {capability_id}\n"
        )
    else:
        msg = f"ARIA — capability gap: {capability_id}\n\n"
    if record.get("issue_url"):
        msg += f"Issue : {record['issue_url']}\n"
    if record.get("pr_url"):
        msg += f"PR : {record['pr_url']}\n"
    if record.get("local_spec"):
        msg += f"Spec locale : {record['local_spec']}\n"
    if record.get("status") == "dedup":
        msg += "(deja filee cette semaine)\n"
    await notify_admin(msg.strip())


def format_gap_reply(result: dict[str, Any], *, lang: str = "fr") -> str:
    if result.get("status") == "dedup":
        if lang == "fr":
            return (
                "J'ai deja ouvert une tache cette semaine pour cette capacite.\n"
                f"Issue : {result.get('issue_url') or '—'}"
            )
        return f"Already filed this week. Issue: {result.get('issue_url')}"

    lines = []
    if lang == "fr":
        lines.append("Auto-developpement — j'ai documente la lacune :")
    else:
        lines.append("Self-improvement — capability gap filed:")
    if result.get("issue_url"):
        lines.append(f"Issue : {result['issue_url']}")
    if result.get("pr_url"):
        lines.append(f"PR spec : {result['pr_url']}")
    if result.get("local_spec") and not result.get("issue_url"):
        lines.append(f"Spec locale : {result['local_spec']}")
    lines.append("")
    if lang == "fr":
        lines.append("Prochaine etape : implementer dans aria-core puis sync-render.")
    return "\n".join(lines)