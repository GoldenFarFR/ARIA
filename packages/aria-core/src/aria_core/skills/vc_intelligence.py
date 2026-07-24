"""Crypto VC thesis watch -- inspiration + strategic calibration proposal.

Follows a small number of recognized crypto VCs (X, verified accounts -- cf.
`x_watchlist.yaml::vc_handles`) to spot public thesis/conviction signals, and --
ONLY if an LLM judges the finding durable -- PROPOSES (never imposes) a strategic
calibration lead via a GitHub issue. Never an autonomous commit or merge on
the strategy files (`docs/protocole-argent-reel.md`,
`docs/strategie-aria-investissement.md` stay locked, explicit operator validation
required -- scope confirmed by the operator on 09/07: "observe + propose").

Wallets angle (on-chain analysis of known VCs, "the hidden face"): DELIBERATELY
EMPTY seam. No wallet address is wired in until verified by a
reliable source -- never a guessed address (cf. the most recent HANDOFF for the state
of the ongoing verification).

Reuses the SAME fetch as the existing curiosity cycle (`fetch_curiosity_feed()`, called
once by `curiosity.run_curiosity_cycle()`) -- no extra X call, same
doctrine as `opportunity_radar.mine_curiosity_items`.
"""
from __future__ import annotations

import json
import os
from typing import Any

TARGET_REPO = "ARIA"

_VC_SYNTHESIS_SYSTEM = (
    "You are ARIA, on-chain analyst at Aria Vanguard ZHC. You're shown recent tweets "
    "from recognized crypto VCs (a16z, Paradigm, Dragonfly, Variant, Coinbase "
    "Ventures, Electric Capital, IOSG) -- read-only, never a source of truth on its "
    "own. Summarize in 2-4 sentences what they're signaling as current "
    "conviction/thesis (sector, narrative, type of project they seem to favor). If "
    "the content is too thin or generic to draw a real signal, say so honestly "
    "rather than inventing a trend. Answer STRICTLY in JSON: "
    '{"summary": "<short summary, in French (their working language on this '
    'channel)>", "durable": true|false, '
    '"proposal_title": "<short title in English if durable, else empty>", '
    '"proposal_body": "<calibration lead structured in markdown, in English, if '
    "durable -- never a direct rewrite of the strategy files, a LEAD for the "
    'operator to evaluate -- else empty>"}. `durable` = true ONLY if the signal is '
    "strong and coherent enough to deserve real strategic thought, never for an "
    "isolated tweet or noise."
)


def vc_intelligence_enabled() -> bool:
    return os.environ.get("ARIA_VC_INTELLIGENCE_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _format_vc_items_for_prompt(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items[:20]:
        topic = str(item.get("topic") or "?")
        text = str(item.get("text") or "").strip()[:280]
        if text:
            lines.append(f"- {topic}: {text}")
    return "\n".join(lines)


async def _propose_strategy_issue(title: str, body: str, *, github_client=None) -> str | None:
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
        + "\n\n---\n*Lead generated from VC thesis monitoring (read-only, public X) -- "
        "never a rewrite of the strategy files. Operator review and decision required "
        "before any integration.*"
    )
    try:
        issue = await github_client.create_issue(
            owner, TARGET_REPO, f"[strategy] {title}", body_full,
            labels=["aria-strategy-proposal"],
        )
    except Exception:  # noqa: BLE001 -- a GitHub outage must never break the cycle
        return None
    return issue.get("html_url")


async def run_vc_intelligence_cycle(
    *,
    items: list[dict[str, Any]],
    llm=None,
    notifier=None,
    github_client=None,
) -> dict:
    """One pass: synthesizes the VC items already filtered (cf. `curiosity.py`), pushes a
    read-only digest to the operator, proposes an issue ONLY if judged durable."""
    if not vc_intelligence_enabled():
        return {"outcome": "skipped_disabled"}
    if not items:
        return {"outcome": "no_items"}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    from aria_core.runtime import settings
    from aria_core.spark_config import DEFAULT_MODEL_DEVELOP

    develop_model = (
        getattr(settings, "aria_llm_model_develop", None) or ""
    ).strip() or DEFAULT_MODEL_DEVELOP

    prompt = _format_vc_items_for_prompt(items)
    raw = await llm(
        prompt, _VC_SYNTHESIS_SYSTEM, max_tokens=500, model=develop_model, depth="vc_intelligence",
    )
    if not raw:
        return {"outcome": "llm_unavailable"}

    try:
        data = json.loads(raw)
        summary = str(data.get("summary", "")).strip()
        durable = bool(data.get("durable", False))
        proposal_title = str(data.get("proposal_title", "")).strip()
        proposal_body = str(data.get("proposal_body", "")).strip()
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return {"outcome": "parse_failed"}

    if not summary:
        return {"outcome": "empty_summary"}

    if notifier is not None:
        try:
            await notifier(f"🧠 Veille VC\n\n{summary}")
        except Exception:  # noqa: BLE001 -- a failed send never blocks the cycle
            pass

    issue_url = None
    if durable and proposal_title and proposal_body:
        issue_url = await _propose_strategy_issue(
            proposal_title, proposal_body, github_client=github_client,
        )

    return {"outcome": "ok", "summary": summary, "durable": durable, "issue_url": issue_url}
