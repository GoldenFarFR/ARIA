"""Pump/dump autopsy — a "24/7 knowledge" building block (task #8, 07/09).

``weekly_training.resolve_due`` closes every prediction on a point-to-point
entry->current price at expiry — this hides a pump-then-crash that occurred
IN BETWEEN (e.g. entry $1, peak at $4 along the way, back down to $1.10 at
expiry: the point-to-point says "+10%", when in reality the token 4x'd then
gave almost all of it back). This module re-reads the REAL OHLCV series
covered during the holding period (``services/ohlcv``, already wired
elsewhere — no new client), DETERMINISTICALLY detects (no LLM, no
invention) whether a real pump/dump pattern occurred, and if so, asks the LLM
for a short autopsy: did the original thesis already carry a signal
that flagged this risk (already-logged risk/potential/position size),
what do the real numbers show, one lesson.

Two outputs, never a third channel created for the occasion:
  1. Local log (``pump_dump_autopsy_log``) — full traceability, never published.
  2. If the lesson is judged durable: a GitHub ISSUE proposal (label
     ``aria-playbook-proposal``) — never an autonomous commit or merge,
     same strict doctrine as ``knowledge_inbox.py`` / ``claude_mentor.py``.

Gated OFF by default (``ARIA_PUMP_DUMP_AUTOPSY_ENABLED``). A prediction is
autopsied only once (deduplicated by ``prediction_id``, a ``UNIQUE``
constraint in the database).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
TARGET_REPO = "ARIA"

# Detection thresholds — deterministic, on the real OHLCV series (never an LLM
# for the detection itself). A real "pump" must have reached at least this
# multiple of the entry; the "dump" afterward must have given back at least this
# share of the peak. Both must be true to call it a pump/dump.
PUMP_MULTIPLE_MIN = 1.5
DUMP_DRAWDOWN_MIN = 0.4
AUTOPSY_WINDOW_DAYS = 3  # only re-reads recently closed predictions (sliding window)
MAX_PER_CYCLE = 5  # a common-sense cap (LLM + GitHub cost), not a risk cap

_AUTOPSY_SYSTEM = (
    "You are Claude Code, external reviewer of ARIA (not ARIA herself). You're shown "
    "a VC prediction she actually issued, and what REALLY happened to the price "
    "(real OHLCV data, never invented): a pump-then-dump pattern was detected. Write "
    "ONE short, concrete autopsy: did the original thesis already carry a signal "
    "that flagged this risk (already-stated risk, potential, position size), or is "
    "this a real blind spot? Never speculate beyond the provided figures. Answer "
    "STRICTLY in JSON: "
    '{"lesson": "<short concrete lesson, one sentence>", "durable": true|false, '
    '"proposal_title": "<short title if durable, else empty>", "proposal_body": '
    '"<structured proposal in markdown if durable -- pattern to add to a pump/dump '
    'playbook, which signal would have flagged it earlier, else empty>"}. '
    '`durable` = true ONLY if the case reveals a reusable pattern for future '
    "analyses, not for an isolated case with no possible generalization."
)


def pump_dump_autopsy_enabled() -> bool:
    return os.environ.get("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pump_dump_autopsy_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL UNIQUE,
                contract TEXT,
                run_at TEXT NOT NULL,
                outcome TEXT NOT NULL,
                peak_multiple REAL,
                drawdown_pct REAL,
                lesson TEXT,
                durable INTEGER NOT NULL DEFAULT 0,
                issue_url TEXT
            )
            """
        )
        await db.commit()


def detect_pump_dump(candles: list, entry_price: float | None, *, since_ts: int | None = None) -> dict | None:
    """Facts-only, deterministic detection, no LLM. ``candles`` = real OHLCV series
    (objects with ``.ts``/``.high``/``.close``). ``since_ts`` filters to candles from the
    real holding period (otherwise a pre-entry peak could distort the peak).
    ``None`` if no real pump/dump pattern is detected."""
    if not candles or not entry_price or entry_price <= 0:
        return None
    window = [c for c in candles if since_ts is None or (getattr(c, "ts", 0) or 0) >= since_ts]
    if not window:
        return None
    highs = [c.high for c in window if getattr(c, "high", None) is not None]
    if not highs:
        return None
    peak = max(highs)
    peak_multiple = peak / entry_price
    if peak_multiple < PUMP_MULTIPLE_MIN:
        return None
    last_close = next((c.close for c in reversed(window) if getattr(c, "close", None) is not None), None)
    if last_close is None or peak <= 0:
        return None
    drawdown_pct = (peak - last_close) / peak
    if drawdown_pct < DUMP_DRAWDOWN_MIN:
        return None
    return {"peak_multiple": round(peak_multiple, 2), "drawdown_pct": round(drawdown_pct, 4)}


def _epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _format_case_for_prompt(prediction: dict, pattern: dict) -> str:
    return "\n".join([
        "ARIA's original prediction (already logged, never rewritten):",
        f"- Recommendation: {prediction.get('recommandation')}",
        f"- Stated potential: {prediction.get('potentiel')}/10",
        f"- Stated risk: {prediction.get('risque')}",
        f"- Suggested size: {prediction.get('taille_pct')}%",
        f"- Real entry price: {prediction.get('entry_price')}",
        f"- Derived target: {prediction.get('target_price')}",
        f"- Derived invalidation: {prediction.get('invalidation_price')}",
        "",
        "What REALLY happened (real OHLCV, never invented):",
        f"- Peak reached: {pattern['peak_multiple']}x the entry price",
        f"- Drawdown from peak at close: {pattern['drawdown_pct']:.0%}",
        f"- Already-logged point-to-point result: {prediction.get('outcome_pct')}%",
    ])


async def _propose_playbook(title: str, body: str, *, github_client=None) -> str | None:
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
        + "\n\n---\n*Proposal generated by the pump/dump autopsy (real OHLCV data) -- "
        "human review required before any integration into a playbook. Never an "
        "autonomous commit or merge.*"
    )
    try:
        issue = await github_client.create_issue(
            owner, TARGET_REPO, f"[playbook pump/dump] {title}", body_full,
            labels=["aria-playbook-proposal"],
        )
    except Exception:  # noqa: BLE001 -- a GitHub failure must never break the cycle
        return None
    return issue.get("html_url")


async def _autopsy_one(prediction: dict, *, ohlcv_fetch=None, llm=None, github_client=None) -> dict:
    contract = prediction.get("contract") or ""
    pool = (prediction.get("pool_address") or "").strip()
    entry = prediction.get("entry_price")
    created_ts = _epoch(prediction.get("created_at"))

    if not pool or not entry:
        return {"outcome": "skipped_no_pool_or_entry", "prediction_id": prediction["id"]}

    if ohlcv_fetch is None:
        from aria_core.services.ohlcv import ohlcv_client

        async def ohlcv_fetch(pool_address: str, network: str):
            res = await ohlcv_client.get_ohlcv(pool_address, network=network)
            return res.candles if res.available else []

    candles = await ohlcv_fetch(pool, prediction.get("network") or "base")
    pattern = detect_pump_dump(candles, entry, since_ts=created_ts)
    if pattern is None:
        return {"outcome": "no_pattern", "prediction_id": prediction["id"]}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    prompt = _format_case_for_prompt(prediction, pattern)
    # 07/17 -- same switch as claude_mentor.py (see its comment for the review
    # detail): Sonnet 5 via OpenRouter, explicit Haiku 4.5 backup, then the existing
    # global fallback (Grok/Groq). max_tokens raised to 900 -- 500 was systematically
    # truncating Opus/Sonnet autopsies during the real 07/17 test (finish_reason=length mid-word).
    raw = await llm(
        prompt, _AUTOPSY_SYSTEM, max_tokens=900, depth="pump_dump_autopsy",
        provider="openrouter", model="anthropic/claude-sonnet-5",
        fallback_provider="openrouter", fallback_model="anthropic/claude-haiku-4.5",
    )

    lesson = ""
    durable = False
    issue_url = None
    if raw:
        try:
            data = json.loads(raw)
            lesson = str(data.get("lesson", "")).strip()
            durable = bool(data.get("durable", False))
            proposal_title = str(data.get("proposal_title", "")).strip()
            proposal_body = str(data.get("proposal_body", "")).strip()
            if durable and proposal_title and proposal_body:
                issue_url = await _propose_playbook(
                    proposal_title, proposal_body, github_client=github_client,
                )
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            lesson = ""

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO pump_dump_autopsy_log "
            "(prediction_id, contract, run_at, outcome, peak_multiple, drawdown_pct, "
            "lesson, durable, issue_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                prediction["id"], contract, _now(), "autopsied" if lesson else "llm_unavailable",
                pattern["peak_multiple"], pattern["drawdown_pct"], lesson, int(durable), issue_url,
            ),
        )
        await db.commit()

    return {
        "outcome": "autopsied" if lesson else "llm_unavailable",
        "prediction_id": prediction["id"],
        "contract": contract,
        "pattern": pattern,
        "lesson": lesson,
        "durable": durable,
        "issue_url": issue_url,
    }


async def run_pump_dump_autopsy_cycle(*, ohlcv_fetch=None, llm=None, github_client=None) -> dict:
    """One collection + autopsy round. Fail-closed if disabled. Never breaks the
    heartbeat (a failure on one case doesn't prevent other cases from being processed)."""
    if not pump_dump_autopsy_enabled():
        return {"outcome": "skipped_disabled"}

    await _ensure_table()

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}

    from aria_core import vc_predictions

    async with aiosqlite.connect(DB_PATH) as db:
        already = {
            row[0]
            for row in await (
                await db.execute("SELECT prediction_id FROM pump_dump_autopsy_log")
            ).fetchall()
        }

    since = (datetime.now(timezone.utc) - timedelta(days=AUTOPSY_WINDOW_DAYS)).isoformat()
    closed = await vc_predictions.list_recently_closed(since, limit=50)
    candidates = [p for p in closed if p["id"] not in already][:MAX_PER_CYCLE]

    if not candidates:
        return {"outcome": "nothing_to_autopsy", "checked": len(closed)}

    results = []
    for prediction in candidates:
        try:
            result = await _autopsy_one(
                prediction, ohlcv_fetch=ohlcv_fetch, llm=llm, github_client=github_client,
            )
        except Exception as exc:  # noqa: BLE001 -- a failed autopsy never breaks the cycle
            logger.warning("pump_dump_autopsy: failed on prediction %s -- %s", prediction["id"], exc)
            result = {"outcome": "error", "prediction_id": prediction["id"], "error": str(exc)[:200]}
        results.append(result)

    autopsied = sum(1 for r in results if r["outcome"] == "autopsied")
    return {"outcome": "ok", "checked": len(closed), "autopsied": autopsied, "results": results}
