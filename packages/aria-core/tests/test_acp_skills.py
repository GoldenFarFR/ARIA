import json
from pathlib import Path

import pytest

from aria_core.skills import acp_cli, acp_provider_skill
from aria_core.skills.acp_client_skill import (
    execute_acp_marketplace,
    wants_acp_marketplace,
)


def test_wants_acp_marketplace():
    assert wants_acp_marketplace("acp status")
    assert wants_acp_marketplace("concernant acp quel plan")
    assert not wants_acp_marketplace("bonjour")


def test_extract_job_id():
    ev = {"type": "job.funded", "jobId": "job-123"}
    assert acp_provider_skill._extract_job_id(ev) == "job-123"
    ev2 = {"type": "x", "data": {"job_id": "abc"}}
    assert acp_provider_skill._extract_job_id(ev2) == "abc"


def test_heuristic_deliverable_lite():
    d = acp_provider_skill._heuristic_audit("0x" + "a" * 40, full=False)
    assert d["liteVerdict"] in ("SAFE", "CAUTION", "DANGER")
    assert d["riskAlerts"]


def test_heuristic_deliverable_full():
    d = acp_provider_skill._heuristic_audit("0x" + "b" * 40, full=True)
    assert "verdict" in d
    assert "auditReport" in d
    assert "securityScore" in d


@pytest.mark.asyncio
async def test_drain_events_file(tmp_path, monkeypatch):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    data_dir = tmp_path / "data"
    configure_test_runtime(
        data_dir=data_dir,
        settings=AriaRuntimeSettings(aria_acp_provider_enabled=True),
    )
    events = tmp_path / "events.jsonl"
    events.write_text(
        '{"type":"job.funded","jobId":"j1"}\n'
        '{"type":"ping"}\n',
        encoding="utf-8",
    )
    batch, _ = acp_provider_skill.drain_events_file(str(events))
    assert len(batch) == 2
    batch2, _ = acp_provider_skill.drain_events_file(str(events))
    assert len(batch2) == 0


@pytest.mark.asyncio
async def test_run_provider_cycle_no_cli(monkeypatch):
    monkeypatch.setattr(acp_provider_skill, "is_acp_available", lambda: False)
    result = await acp_provider_skill.run_provider_cycle()
    assert result["ok"] is False
    assert result["errors"]


@pytest.mark.asyncio
async def test_acp_status_command(monkeypatch):
    monkeypatch.setattr(acp_cli, "is_acp_available", lambda: True)
    monkeypatch.setattr(
        acp_cli,
        "list_agents",
        lambda: ([{"name": "Aria Vanguard ZHC", "id": "x", "role": "HYBRID"}], None),
    )
    monkeypatch.setattr(
        acp_cli,
        "list_offerings",
        lambda: ([{"name": "analyse_lite_x1", "priceValue": 1.99}], None),
    )
    reply, data = await execute_acp_marketplace("acp status", lang="fr")
    assert "ACP STATUS" in reply
    assert data.get("acp") == "status"
    assert "analyse_lite" in reply


@pytest.mark.asyncio
async def test_acp_revenue_plan(monkeypatch):
    monkeypatch.setattr(acp_cli, "list_offerings", lambda: ([{"name": "analyse_lite_x1"}], None))
    reply, data = await execute_acp_marketplace(
        "concernant acp quel est ton plan pour generer des revenus",
        lang="fr",
    )
    assert data.get("acp") == "revenue_plan"
    assert "Plan revenus" in reply


def test_resolve_acp_command_windows(monkeypatch):
    monkeypatch.setenv("APPDATA", str(Path("C:/Users/X/AppData/Roaming")))
    fake = Path("C:/Users/X/AppData/Roaming/npm/acp.cmd")

    def fake_is_file(self):
        return str(self).endswith("acp.cmd")

    monkeypatch.setattr(Path, "is_file", fake_is_file, raising=False)
    cmd = acp_cli.resolve_acp_command()
    assert cmd[0] == "cmd.exe"
    assert "acp.cmd" in cmd[-1]