"""ACP workflow-used auto tweets."""
from __future__ import annotations

import json

from aria_core.skills import acp_workflow_social as social
from aria_core.x_publication_policy import check_workflow_used_tweet_allowed


def test_build_workflow_url_with_offering():
    url = social.build_workflow_url("analyse_lite_x1")
    assert "virtuals.io" in url
    assert "offering=analyse_lite_x1" in url


def test_compose_workflow_used_tweet_includes_link():
    tw = social.compose_workflow_used_tweet(
        offering_name="analyse_full_x1",
        workflow_key="analyse_full_x1",
        job_id="job-abc-123",
        workflow_url="https://app.virtuals.io/acp/agents/x?offering=analyse_full_x1",
    )
    assert "workflow analyse_full_x1 used" in tw
    assert "https://" in tw
    assert len(tw) <= 280


def test_enqueue_workflow_used(tmp_path, monkeypatch):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(aria_acp_workflow_used_tweet=True),
    )
    out = social.enqueue_workflow_used_tweet(
        offering_name="veille_zhc_x1",
        workflow_key="veille_zhc_x1",
        job_id="job-1",
        offering_id="off-1",
    )
    assert out["queued"] is True
    qpath = tmp_path / "data" / "memory" / "x_workflow_used_queue.jsonl"
    assert qpath.is_file()
    row = json.loads(qpath.read_text(encoding="utf-8").strip())
    assert row["offering"] == "veille_zhc_x1"
    assert row["workflow_url"]


def test_workflow_used_tweet_allows_url_when_enabled(monkeypatch):
    from aria_core import runtime
    from aria_core.testing import AriaRuntimeSettings

    cfg = AriaRuntimeSettings(
        x_block_urls_in_posts=True,
        aria_acp_workflow_tweet_allow_url=True,
        x_monthly_spend_cap_usd=5.0,
    )
    runtime.configure(cfg)
    tw = (
        "ACP workflow analyse_lite_x1 used — job job1 delivered on Base. "
        "Hire the same workflow: https://app.virtuals.io/acp/agents/x?offering=analyse_lite_x1"
    )
    allowed, reason, cost = check_workflow_used_tweet_allowed(tw, force=True)
    assert allowed, reason
    assert cost >= 0.15