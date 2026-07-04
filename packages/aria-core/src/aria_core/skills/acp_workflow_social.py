"""ACP workflow-used social — auto tweet after successful job delivery."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aria_core.paths import memory_dir

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "acp_config.yaml"
_QUEUE_PATH_NAME = "x_workflow_used_queue.jsonl"


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    try:
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def workflow_social_enabled() -> bool:
    raw = os.environ.get("ARIA_ACP_WORKFLOW_USED_TWEET", "true").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        from aria_core.runtime import settings

        return bool(getattr(settings, "aria_acp_workflow_used_tweet", True))
    except Exception:
        return True


def workflow_tweet_allow_url() -> bool:
    raw = os.environ.get("ARIA_ACP_WORKFLOW_TWEET_ALLOW_URL", "true").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        from aria_core.runtime import settings

        return bool(getattr(settings, "aria_acp_workflow_tweet_allow_url", True))
    except Exception:
        return True


def build_workflow_url(offering_name: str, offering_id: str = "") -> str:
    """URL Virtuals vers l'agent ou l'offre (SSOT acp_config.yaml)."""
    cfg = _load_config()
    market = cfg.get("marketplace") or {}
    base_agent = str(market.get("agent_url") or "").strip()
    if not base_agent:
        agent_id = str(cfg.get("agent_id") or "").strip()
        base_url = str(market.get("base_url") or "https://app.virtuals.io/acp").rstrip("/")
        if agent_id:
            base_agent = f"{base_url}/agents/{agent_id}"
        else:
            base_agent = base_url
    name = (offering_name or "").strip()
    if name:
        sep = "&" if "?" in base_agent else "?"
        return f"{base_agent}{sep}offering={name}"
    if offering_id:
        return f"{base_agent}#offering-{offering_id}"
    return base_agent


def compose_workflow_used_tweet(
    *,
    offering_name: str,
    workflow_key: str,
    job_id: str,
    workflow_url: str,
) -> str:
    """Tweet EN — workflow used + lien (politique @Aria_ZHC)."""
    name = (offering_name or workflow_key or "acp_workflow").strip()
    short_job = (job_id or "")[:12]
    url = (workflow_url or "").strip()
    body = (
        f"ACP workflow {name} used — job {short_job} delivered on Base. "
        f"Hire the same workflow: {url}"
    )
    return body[:280]


def _queue_path() -> Path:
    path = memory_dir() / _QUEUE_PATH_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def enqueue_workflow_used_tweet(
    *,
    offering_name: str,
    workflow_key: str,
    job_id: str,
    offering_id: str = "",
) -> dict[str, Any]:
    """File un tweet « workflow used » après livraison job."""
    if not workflow_social_enabled():
        return {"queued": False, "reason": "disabled"}
    url = build_workflow_url(offering_name, offering_id)
    tweet = compose_workflow_used_tweet(
        offering_name=offering_name,
        workflow_key=workflow_key,
        job_id=job_id,
        workflow_url=url,
    )
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "offering": offering_name,
        "workflow": workflow_key,
        "job_id": job_id,
        "offering_id": offering_id,
        "workflow_url": url,
        "tweet_text": tweet,
    }
    path = _queue_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"queued": True, "tweet_text": tweet, "workflow_url": url}


def _peek_queue() -> dict[str, Any] | None:
    path = _queue_path()
    if not path.is_file():
        return None
    try:
        lines = [
            ln.strip()
            for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip()
        ]
    except Exception:
        return None
    if not lines:
        return None
    try:
        return json.loads(lines[0])
    except Exception:
        return None


def _pop_queue_head() -> None:
    path = _queue_path()
    if not path.is_file():
        return
    try:
        lines = [
            ln.strip()
            for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip()
        ]
    except Exception:
        return
    if not lines:
        path.unlink(missing_ok=True)
        return
    rest = lines[1:]
    if rest:
        path.write_text("\n".join(rest) + "\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


async def flush_workflow_used_tweet() -> dict[str, Any] | None:
    """Publie le prochain tweet workflow-used si politique X OK."""
    head = _peek_queue()
    if not head:
        return None
    tweet = str(head.get("tweet_text") or "").strip()
    if not tweet:
        _pop_queue_head()
        return {"posted": False, "reason": "empty_tweet"}
    from aria_core.gateway.x_twitter import is_x_post_configured, post_tweet
    from aria_core.x_publication_policy import check_workflow_used_tweet_allowed

    if not is_x_post_configured():
        return {"posted": False, "reason": "x_not_configured", "queued": head}
    allowed, reason, cost = check_workflow_used_tweet_allowed(tweet)
    if not allowed:
        return {
            "posted": False,
            "reason": reason,
            "cost_usd": cost,
            "offering": head.get("offering"),
            "workflow_url": head.get("workflow_url"),
        }
    _, note = await post_tweet(tweet, approval_id="acp_workflow_used")
    posted = "x.com/" in note.lower() and "/status/" in note.lower()
    if posted:
        _pop_queue_head()
    return {
        "posted": posted,
        "offering": head.get("offering"),
        "job_id": head.get("job_id"),
        "workflow_url": head.get("workflow_url"),
        "note": note[:400],
        "cost_usd": cost,
    }


def extract_offering_id(history: dict) -> str:
    for container in (history, history.get("job") or {}, history.get("offering") or {}):
        if not isinstance(container, dict):
            continue
        for key in ("offeringId", "offering_id", "id"):
            val = container.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""