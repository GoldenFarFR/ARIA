"""ACP workflow quality gates — unit tests."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from aria_core.skills import acp_deliverable_quality as dq
from aria_core.skills import acp_onchain_scan as scan
from aria_core.skills import acp_provider_skill
from aria_core.skills import acp_workflow_engine as engine
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext


def _mock_ctx(
    *,
    contract: str = "0x" + "a" * 40,
    verdict: str = "CAUTION",
    score: int = 55,
) -> TokenScanContext:
    pair = PairSnapshot(
        pair_address="0xpair",
        dex_id="aerodrome",
        liquidity_usd=12_000,
        volume_24h_usd=5_000,
        buys_24h=40,
        sells_24h=30,
        base_symbol="TOK",
        quote_symbol="WETH",
    )
    return TokenScanContext(
        contract=contract,
        valid_address=True,
        pairs_found=1,
        best_pair=pair,
        risk_flags=["Liquidité modérée — size prudente."],
        security_score=score,
        lite_verdict=verdict,
        data_source="dexscreener",
    )


def test_resolve_workflow_key():
    assert dq.resolve_workflow_key("analyse_full_x1") == "analyse_full_x1"
    assert dq.resolve_workflow_key("veille_zhc_x1") == "veille_zhc_x1"
    assert dq.resolve_workflow_key("token_scan_lite") == "analyse_lite_x1"


def test_validate_lite_passes():
    d = engine.build_lite_deliverable(_mock_ctx())
    report = dq.validate_deliverable("analyse_lite_x1", d, onchain_score=55)
    assert report.passed
    assert report.score >= 70


def test_validate_lite_fails_short_alerts():
    report = dq.validate_deliverable(
        "analyse_lite_x1",
        {"liteVerdict": "CAUTION", "riskAlerts": "short"},
    )
    assert not report.passed
    assert any("riskAlerts" in i for i in report.issues)


def test_validate_full_requires_sections():
    ctx = _mock_ctx(score=60)
    d = engine.build_full_deliverable(ctx)
    report = dq.validate_deliverable("analyse_full_x1", d, onchain_score=60)
    assert report.passed
    assert len(d["auditReport"]) >= 400


def test_validate_veille_passes():
    d = engine.build_veille_deliverable(
        {"brief": "Watch BASE DeFi liquidity rotation over 7d", "symbols": "BASE, ETH"},
        _mock_ctx(),
    )
    report = dq.validate_deliverable("veille_zhc_x1", d)
    assert report.passed
    assert d["signal"] in ("WATCH", "ALERT", "CLEAR")


def test_safe_blocked_when_onchain_low():
    d = {"liteVerdict": "SAFE", "riskAlerts": "x" * 50}
    report = dq.validate_deliverable("analyse_lite_x1", d, onchain_score=20)
    assert not report.passed


def test_score_invalid_address():
    ctx = scan.TokenScanContext(contract="bad", valid_address=False)
    scan._score_and_verdict(ctx, None)
    assert ctx.lite_verdict == "DANGER"
    assert ctx.security_score <= 20


@pytest.mark.asyncio
async def test_build_deliverable_for_job_full(monkeypatch):
    monkeypatch.setattr(
        engine,
        "scan_base_token",
        AsyncMock(return_value=_mock_ctx(score=65)),
    )
    history = {
        "offeringName": "analyse_full_x1",
        "requirements": {"contractAddress": "0x" + "b" * 40},
    }
    deliverable, workflow, ctx = await engine.build_deliverable_for_job("analyse_full_x1", history)
    assert workflow == "analyse_full_x1"
    assert deliverable["verdict"] in ("AVOID", "SPECULATIVE", "SAFE")
    assert ctx is not None


@pytest.mark.asyncio
async def test_process_job_quality_block(monkeypatch, tmp_path):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(aria_acp_provider_enabled=True),
    )
    bad = {"liteVerdict": "SAFE", "riskAlerts": "x"}
    monkeypatch.setattr(acp_provider_skill, "job_history", lambda jid, **kw: ({"job": {"status": "funded"}, "offeringName": "analyse_lite_x1"}, None))
    monkeypatch.setattr(
        acp_provider_skill,
        "build_deliverable_for_job",
        AsyncMock(return_value=(bad, "analyse_lite_x1", _mock_ctx(verdict="SAFE", score=90))),
    )
    submitted = []

    def fake_submit(job_id, deliverable, **kw):
        submitted.append(job_id)
        return True, "ok"

    monkeypatch.setattr(acp_provider_skill, "provider_submit", fake_submit)
    action = await acp_provider_skill._process_job("job-q1", chain_id="8453")
    assert action == "quality_blocked:job-q1"
    assert not submitted
    receipt = tmp_path / "data" / "memory" / "acp_quality_receipts.jsonl"
    assert receipt.is_file()
    row = json.loads(receipt.read_text(encoding="utf-8").strip())
    assert row["submitted"] is False
    assert row["passed"] is False


@pytest.mark.asyncio
async def test_process_job_submit_when_quality_ok(monkeypatch, tmp_path):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(aria_acp_provider_enabled=True),
    )
    good = engine.build_lite_deliverable(_mock_ctx())
    monkeypatch.setattr(acp_provider_skill, "job_history", lambda jid, **kw: ({"job": {"status": "funded"}, "offeringName": "analyse_lite_x1"}, None))
    monkeypatch.setattr(
        acp_provider_skill,
        "build_deliverable_for_job",
        AsyncMock(return_value=(good, "analyse_lite_x1", _mock_ctx())),
    )
    monkeypatch.setattr(acp_provider_skill, "provider_submit", lambda jid, d, **kw: (True, "ok"))
    action = await acp_provider_skill._process_job("job-q2", chain_id="8453")
    assert action == "submit:job-q2"