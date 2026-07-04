"""Auto-reply on watched external showcase PR threads (e.g. Virtual-Protocol/acp-cli-demos#37)."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aria_core.github_client import GitHubClient
from aria_core.memory import append_memory
from aria_core.paths import memory_dir
from aria_core.runtime import settings
from aria_core.skills.github_skill import github_configured

logger = logging.getLogger(__name__)

_WATCH_CMD_RE = re.compile(
    r"(?i)(?:showcase\s+pr\s+watch|watch\s+showcase\s+pr|surveiller\s+pr\s+showcase|pr\s+37\s+watch)"
)

_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "knowledge" / "showcase_pr_watch.yaml"
)
_STATE_PATH = memory_dir() / "showcase_pr_watch_state.json"

_DEFAULT_BLOCKER_SINCE = "2026-07-04"

_CLARIFY_TEMPLATE = """Thanks for the comment.

**Clarifying our situation** (not a showcase format issue):

- **Agent:** Aria Vanguard ZHC (HYBRID provider, Base chain `8453`)
- **Agent id:** {agent_id}
- **Email:** {agent_email}
- **Live offering:** `{offering}` on the ACP marketplace

**Blocker since ~{blocker_since}:**
`acp browse`, job history, `acp events listen`, and provider submit return **Server error 500** (Privy/Alchemy path on Virtuals).

**Impact:** We cannot auto-fulfill funded jobs or attach a paid escrow receipt to complete this showcase proof.

We **closed PR #{pr_number} ourselves** until the receipt gate is green — not because the package is invalid.

**Questions for the team:**
1. Is this a known incident on your side?
2. Any ETA or workaround for provider submit?
3. Should we reopen after a successful funded job, or track this in a separate issue?

Happy to share CLI timestamps and output on request.
"""

_THANKS_REOPEN_TEMPLATE = """Thanks for the review.

We will reopen PR #{pr_number} once we have:
- `node scripts/validate-showcase.mjs` passing, and
- a funded escrow job receipt with a public job id (blocked today by API 500 on provider submit).

Package remains on `GoldenFarFR/acp-cli-demos@showcase/aria-vanguard-zhc` until then.
"""

_ACK_INCIDENT_TEMPLATE = """Thanks — noted on the API / Privy side.

We are standing by in **degraded mode** (email watch + manual Hermes submit) for provider jobs until submit works again. We will update this thread when smoke test + escrow receipt succeed.
"""

_URGENT_TEMPLATE = """Thanks for the urgent ping — we're on it.

**Quick summary:**
- Live ACP provider (Aria Vanguard ZHC) blocked by API **500** on provider submit since ~{blocker_since}
- PR #{pr_number} adds degraded mode (`acp_email_watch` + `acp_prepare_skill`) so we can still fulfill manually via Hermes

**What we need:**
1. Is Privy/Alchemy 500 a known incident on your side?
2. ETA for provider submit restoration?

We will update this thread with smoke-test output or an escrow job receipt as soon as submit works.
"""


def wants_showcase_pr_watch(message: str) -> bool:
    return bool(_WATCH_CMD_RE.search((message or "").strip()))


@lru_cache(maxsize=1)
def load_watch_targets() -> list[dict[str, Any]]:
    if not _REGISTRY_PATH.is_file():
        return []
    data = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return []
    targets = data.get("targets") or []
    return [t for t in targets if isinstance(t, dict) and t.get("enabled")]


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return {"handled": {}, "replies": []}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"handled": {}, "replies": []}


def _save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["replies"] = list(state.get("replies") or [])[-200:]
    _STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _our_logins(target: dict[str, Any]) -> set[str]:
    raw = target.get("our_logins") or ["GoldenFarFR"]
    return {str(x).strip().lower() for x in raw if str(x).strip()}


def _test_reviewer_marker(target: dict[str, Any]) -> str:
    return str(target.get("test_reviewer_marker") or "").strip()


def _is_external_comment(row: dict[str, Any], ours: set[str], target: dict[str, Any]) -> bool:
    author_l = (row.get("author") or "").lower()
    body = (row.get("body") or "").strip()
    marker = _test_reviewer_marker(target)
    if marker and marker in body:
        return True
    return bool(author_l and author_l not in ours)


def _comment_key(kind: str, comment_id: int | str) -> str:
    return f"{kind}:{comment_id}"


def compose_reply(their_body: str, *, target: dict[str, Any]) -> str:
    text = (their_body or "").strip().lower()
    pr_number = int(target.get("pr_number") or 0)
    ctx = {
        "agent_id": target.get("agent_id") or "019f0522-b57b-7e8e-a70a-aab2070e070e",
        "agent_email": target.get("agent_email") or "aria_vanguard_zhc@agents.world",
        "offering": target.get("offering") or "analyse_lite_x1",
        "blocker_since": _DEFAULT_BLOCKER_SINCE,
        "pr_number": pr_number,
    }

    if re.search(
        r"\b(merge|approved?|lgtm|looks good|ship it|ready to merge)\b",
        text,
    ):
        return _THANKS_REOPEN_TEMPLATE.format(**ctx)

    if re.search(
        r"\b(urgent|urgence|asap|besoin.*retour|need.*response|need.*reply)\b",
        text,
    ):
        return _URGENT_TEMPLATE.format(**ctx)

    if re.search(
        r"\b(500|privy|alchemy|incident|outage|degraded|maintenance|known issue)\b",
        text,
    ):
        return _ACK_INCIDENT_TEMPLATE.format(**ctx)

    if re.search(
        r"(don'?t understand|do not understand|not clear|unclear|confused|what is the|"
        r"what'?s the problem|can you explain)",
        text,
    ):
        return _CLARIFY_TEMPLATE.format(**ctx)

    return _CLARIFY_TEMPLATE.format(**ctx)


def _normalize_comments(
    issue_comments: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in issue_comments:
        author = ((row.get("user") or {}).get("login") or "").strip()
        cid = row.get("id")
        body = (row.get("body") or "").strip()
        if not cid or not body:
            continue
        rows.append(
            {
                "key": _comment_key("issue", cid),
                "id": cid,
                "kind": "issue",
                "author": author,
                "body": body,
                "created_at": row.get("created_at") or "",
                "url": row.get("html_url") or "",
            }
        )
    for row in reviews:
        author = ((row.get("user") or {}).get("login") or "").strip()
        rid = row.get("id")
        body = (row.get("body") or "").strip()
        if not rid or not body:
            continue
        rows.append(
            {
                "key": _comment_key("review", rid),
                "id": rid,
                "kind": "review",
                "author": author,
                "body": body,
                "created_at": row.get("submitted_at") or row.get("created_at") or "",
                "url": row.get("html_url") or "",
            }
        )
    rows.sort(key=lambda r: r.get("created_at") or "")
    return rows


async def run_showcase_pr_watch(*, post_replies: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "targets": 0,
        "scanned": 0,
        "new_external": 0,
        "replied": [],
        "errors": [],
    }

    if not github_configured():
        result["errors"].append("GITHUB_TOKEN missing")
        return result

    targets = load_watch_targets()
    result["targets"] = len(targets)
    if not targets:
        result["errors"].append("no watch targets enabled")
        return result

    client = GitHubClient(settings.github_token.strip())
    state = _load_state()
    handled: dict[str, str] = dict(state.get("handled") or {})
    replies_log: list[dict[str, Any]] = list(state.get("replies") or [])

    for target in targets:
        owner = str(target.get("owner") or "").strip()
        repo = str(target.get("repo") or "").strip()
        pr_number = int(target.get("pr_number") or 0)
        if not owner or not repo or pr_number < 1:
            continue

        ours = _our_logins(target)
        try:
            issue_comments = await client.list_issue_comments(owner, repo, pr_number)
            reviews = await client.list_pull_reviews(owner, repo, pr_number)
        except Exception as exc:
            logger.warning("showcase_pr_watch fetch failed %s/%s#%s: %s", owner, repo, pr_number, exc)
            result["errors"].append(f"{owner}/{repo}#{pr_number}: {exc}")
            continue

        comments = _normalize_comments(issue_comments, reviews)
        result["scanned"] += len(comments)

        for row in comments:
            key = row.get("key") or ""
            if not key or not _is_external_comment(row, ours, target):
                continue
            if key in handled:
                continue

            result["new_external"] += 1
            reply_body = compose_reply(row.get("body") or "", target=target)
            reply_meta: dict[str, Any] = {
                "target": target.get("id") or f"{owner}/{repo}#{pr_number}",
                "trigger_key": key,
                "trigger_author": row.get("author"),
                "trigger_url": row.get("url"),
                "at": datetime.now(timezone.utc).isoformat(),
            }

            if post_replies:
                try:
                    posted = await client.create_issue_comment(owner, repo, pr_number, reply_body)
                    reply_meta["reply_id"] = posted.get("id")
                    reply_meta["reply_url"] = posted.get("html_url")
                    handled[key] = str(posted.get("id") or "posted")
                    replies_log.append(reply_meta)
                    result["replied"].append(reply_meta)
                    append_memory(
                        "github",
                        f"[showcase_pr] auto-reply to @{row.get('author')} on {owner}/{repo}#{pr_number}",
                    )
                except Exception as exc:
                    logger.warning("showcase_pr_watch reply failed: %s", exc)
                    result["errors"].append(f"reply {key}: {exc}")
            else:
                handled[key] = "dry-run"
                reply_meta["dry_run"] = True
                result["replied"].append(reply_meta)

    state["handled"] = handled
    state["replies"] = replies_log
    state["last_scan_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    result["ok"] = not result["errors"] or bool(result["replied"])
    return result


async def execute_showcase_pr_watch(message: str, lang: str = "fr") -> tuple[str, dict]:
    dry = "dry" in (message or "").lower() or "test" in (message or "").lower()
    scan = await run_showcase_pr_watch(post_replies=not dry)
    replied = scan.get("replied") or []
    lines = [
        "SHOWCASE PR WATCH",
        f"Targets: {scan.get('targets', 0)} · scanned: {scan.get('scanned', 0)} · "
        f"new external: {scan.get('new_external', 0)} · replied: {len(replied)}",
    ]
    if scan.get("errors"):
        lines.append("Errors:")
        for err in scan["errors"][:4]:
            lines.append(f"  - {err}")
    for row in replied[:3]:
        lines.append(f"  → @{row.get('trigger_author')} · {row.get('reply_url') or 'dry-run'}")
    return "\n".join(lines), {"github": "showcase_pr_watch", "scan": scan}