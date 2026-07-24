"""ARIA performance review by Claude -- anchored on her REAL measured data
(VC prediction calibration, paper-trading, Sepolia rehearsal telemetry), never
on free-form chatter. Calls Claude Sonnet 5 via OpenRouter (07/17, explicit
provider/model -- see the comment in `run_claude_mentor_cycle`), Haiku 4.5
backup then the existing global fallback -- no new secret, reuses the
existing LLM client (`llm.py`).

Two output channels, never a third one created for the occasion:
  1. Immediate remark posted in the existing Telegram relay (`relay_chat.py`)
     -- ARIA replies there in her real voice via `relay_conversation_cycle`, already wired.
  2. Finding judged durable -> GitHub ISSUE proposal (same label and same
     doctrine as `knowledge_inbox.py`: never an autonomous commit or merge,
     human review required).

Gated OFF by default (`ARIA_CLAUDE_MENTOR_ENABLED`), in addition to the relay
itself (`ARIA_RELAY_ACCESS_TOKEN`). Deliberately slow throughput (internal
throttle): this is a deep review, not a continuous chat -- less noise, less
cost, more signal.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())
TARGET_REPO = "ARIA"

MIN_INTERVAL_HOURS = 20.0  # ~once a day, never more frequent

_MENTOR_SYSTEM = (
    "You are Claude Code, the technical assistant of the operator (GoldenFarFR) who built "
    "ARIA. Here, your role is external reviewer -- NOT ARIA herself. You're shown her "
    "REAL measured performance data (VC prediction calibration, "
    "paper-trading, Sepolia rehearsal telemetry) -- never impressions, never "
    "chatter. Write ONE short, concrete observation, grounded in the provided figures "
    "(a specific strength, or a precise gap between what she predicted and what "
    "actually happened). If the data doesn't yet support saying something solid, "
    "say so honestly rather than inventing a critique. Answer "
    "STRICTLY in JSON: "
    '{"remark": "<short message addressed to ARIA, in French (their working language on '
    'this channel), direct tone between technical peers>", "durable": true|false, '
    '"proposal_title": "<short title if durable, else empty>", "proposal_body": '
    '"<structured proposal in markdown if durable -- target file knowledge/*.yaml or '
    "truth_ledger/canonical_facts.yaml, precise content proposed, contradiction risk -- "
    'else empty>"}. `durable` = true ONLY if the finding deserves to durably change her '
    "knowledge base, not for a passing remark."
)


def claude_mentor_enabled() -> bool:
    from aria_core import relay_chat

    if not relay_chat.relay_enabled():
        return False
    return os.environ.get("ARIA_CLAUDE_MENTOR_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS claude_mentor_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL, outcome TEXT NOT NULL)"
        )
        await db.commit()


async def _hours_since_last_ok() -> float | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT run_at FROM claude_mentor_log WHERE outcome = 'ok' ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
    if not row:
        return None
    last = datetime.fromisoformat(row[0])
    return (datetime.now(timezone.utc) - last).total_seconds() / 3600.0


async def _log_run(outcome: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO claude_mentor_log (run_at, outcome) VALUES (?, ?)", (_now(), outcome),
        )
        await db.commit()


async def _gather_performance_snapshot() -> dict[str, Any]:
    """Snapshot of the real measured data. Fail-closed PER SOURCE: an
    unavailable source never breaks the others, and is never replaced by an
    invented value."""
    snapshot: dict[str, Any] = {}
    try:
        from aria_core import vc_predictions

        snapshot["vc_predictions"] = await vc_predictions.metrics()
    except Exception as exc:  # noqa: BLE001
        snapshot["vc_predictions"] = {"error": str(exc)[:200]}

    try:
        from aria_core import paper_trader

        snapshot["paper_trading"] = await paper_trader.portfolio_summary()
    except Exception as exc:  # noqa: BLE001
        snapshot["paper_trading"] = {"error": str(exc)[:200]}

    try:
        from aria_core.onchain import sepolia_autonomous

        snapshot["sepolia_rehearsal"] = await sepolia_autonomous.autonomous_status()
    except Exception as exc:  # noqa: BLE001
        snapshot["sepolia_rehearsal"] = {"error": str(exc)[:200]}

    return snapshot


def _has_enough_signal(snapshot: dict[str, Any]) -> bool:
    vc = snapshot.get("vc_predictions") or {}
    pt = snapshot.get("paper_trading") or {}
    sep = snapshot.get("sepolia_rehearsal") or {}
    return bool(
        (vc.get("closed") or 0) > 0
        or (pt.get("closed_trades") or 0) > 0
        or (sep.get("cycles_total") or 0) > 0
    )


def _format_snapshot_for_prompt(snapshot: dict[str, Any]) -> str:
    vc = snapshot.get("vc_predictions") or {}
    pt = snapshot.get("paper_trading") or {}
    sep = snapshot.get("sepolia_rehearsal") or {}

    def _line(label: str, data: dict, keys: list[str]) -> str:
        if "error" in data:
            return f"{label}: unavailable ({data['error']})"
        parts = ", ".join(f"{k}={data.get(k)}" for k in keys)
        return f"{label}: {parts}"

    return "\n".join([
        "ARIA's real performance data (no invented value, raw sources):",
        _line(
            "VC prediction calibration", vc,
            ["closed", "buy_count", "hit_rate", "avg_win_pct", "avg_loss_pct", "avoid_count"],
        ),
        _line(
            "Paper-trading (fictional $1M)", pt,
            ["closed_trades", "win_rate", "return_pct", "realized_pnl"],
        ),
        _line(
            "Autonomous Sepolia rehearsal (testnet)", sep,
            ["cycles_total", "tx_count", "error_count", "hesitation_count"],
        ),
    ])


async def _propose_durable_knowledge(title: str, body: str, *, github_client=None) -> str | None:
    from aria_core.runtime import settings

    if github_client is None:
        token = (settings.github_token or "").strip()
        if not token:
            return None
        from aria_core.github_client import GitHubClient

        github_client = GitHubClient(token)

    owner = settings.github_owner
    body_full = (
        body
        + "\n\n---\n*Proposal generated by Claude (ARIA performance review, real "
        "measured data) -- human review required before any integration into the "
        "knowledge files. Never an autonomous commit or merge.*"
    )
    try:
        issue = await github_client.create_issue(
            owner, TARGET_REPO, f"[knowledge] {title}", body_full,
            labels=["aria-knowledge-proposal"],
        )
    except Exception:  # noqa: BLE001 -- a GitHub outage must never break the cycle
        return None
    return issue.get("html_url")


async def run_claude_mentor_cycle(*, llm=None, github_client=None) -> dict:
    """One review round. Fail-closed at every stage, internal throttle
    (~1x/day) even if the heartbeat calls more often -- cost and noise controlled."""
    if not claude_mentor_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}

    await _ensure_table()
    hours = await _hours_since_last_ok()
    if hours is not None and hours < MIN_INTERVAL_HOURS:
        return {"outcome": "throttled", "hours_since_last": round(hours, 1)}

    snapshot = await _gather_performance_snapshot()
    if not _has_enough_signal(snapshot):
        return {"outcome": "insufficient_data"}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    prompt = _format_snapshot_for_prompt(snapshot)
    # 07/17 -- Claude Sonnet 5 via OpenRouter chosen after a real deep-reasoning
    # review. 07/19 -- explicit operator decision ("switch to spark and once
    # spark runs low on value we'll move to anthropic as planned"): override
    # removed, now uses the global provider/fallback (Spark).
    raw = await llm(prompt, _MENTOR_SYSTEM, max_tokens=900, depth="claude_mentor")
    if not raw:
        await _log_run("llm_unavailable")
        return {"outcome": "llm_unavailable"}

    try:
        data = json.loads(raw)
        remark = str(data.get("remark", "")).strip()
        durable = bool(data.get("durable", False))
        proposal_title = str(data.get("proposal_title", "")).strip()
        proposal_body = str(data.get("proposal_body", "")).strip()
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        await _log_run("parse_failed")
        return {"outcome": "parse_failed"}

    if not remark:
        await _log_run("empty_remark")
        return {"outcome": "empty_remark"}

    from aria_core import relay_chat

    sent = await relay_chat.send_relay_reply(remark)

    issue_url = None
    if durable and proposal_title and proposal_body:
        issue_url = await _propose_durable_knowledge(
            proposal_title, proposal_body, github_client=github_client,
        )

    await _log_run("ok" if sent else "relay_send_failed")
    return {
        "outcome": "ok" if sent else "relay_send_failed",
        "remark": remark,
        "durable": durable,
        "issue_url": issue_url,
    }
