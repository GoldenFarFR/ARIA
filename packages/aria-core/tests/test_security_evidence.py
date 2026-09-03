"""Negative controls on the Security Scientist's evidence ledger (specs/019).

Every test here tries to manufacture something the module must refuse: a
conclusion smuggled into a raw observation (G1), evidence with no provenance
(G2), or a status value the shared PASS/FAIL/UNOBSERVED/UNKNOWN/STALE contract
doesn't recognize. DB isolated per test -- same pattern as
test_gate_audit_log.py, dedicated file (never aria.db, see research.md #6)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import security_posture as sp  # noqa: E402

from aria_core import security_evidence as se  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "DB_PATH", str(tmp_path / "security_scientist_test.db"))
    yield


async def test_observation_with_forbidden_key_is_rejected():
    obs_id = await se.record_observation(
        "proc-1234",
        {"pid": 1234, "status": "safe"},
        observer_version="v1",
        environment_identity={"hostname": "test"},
    )
    assert obs_id is None
    rejected = await se.list_rejected(surface_id="proc-1234")
    assert len(rejected) == 1
    assert "status" in rejected[0]["reason"]


async def test_observation_with_verdict_field_is_rejected():
    obs_id = await se.record_observation(
        "proc-1234",
        {"pid": 1234, "verdict": "PASS"},
        observer_version="v1",
        environment_identity={"hostname": "test"},
    )
    assert obs_id is None


async def test_observation_missing_observer_version_is_rejected():
    obs_id = await se.record_observation(
        "proc-1234",
        {"pid": 1234},
        observer_version="",
        environment_identity={"hostname": "test"},
    )
    assert obs_id is None
    rejected = await se.list_rejected(surface_id="proc-1234")
    assert len(rejected) == 1
    assert "provenance" in rejected[0]["reason"] or "observer_version" in rejected[0]["reason"]


async def test_observation_missing_environment_identity_is_rejected():
    obs_id = await se.record_observation(
        "proc-1234", {"pid": 1234}, observer_version="v1", environment_identity=None
    )
    assert obs_id is None


async def test_valid_observation_is_recorded():
    obs_id = await se.record_observation(
        "proc-1234",
        {"pid": 1234, "exe": "/usr/bin/python3"},
        observer_version="v1",
        environment_identity={"hostname": "test"},
    )
    assert obs_id is not None
    rows = await se.list_observations("proc-1234")
    assert len(rows) == 1
    assert rows[0]["payload"]["pid"] == 1234


async def test_two_observations_in_different_environments_stay_distinguishable():
    id_a = await se.record_observation(
        "proc-1234", {"pid": 1234}, observer_version="v1",
        environment_identity={"hostname": "host-a"},
    )
    id_b = await se.record_observation(
        "proc-1234", {"pid": 1234}, observer_version="v1",
        environment_identity={"hostname": "host-b"},
    )
    assert id_a != id_b
    rows = await se.list_observations("proc-1234")
    assert len(rows) == 2
    assert {r["environment_identity"]["hostname"] for r in rows} == {"host-a", "host-b"}


async def test_record_evaluation_rejects_invalid_status():
    with pytest.raises(ValueError):
        await se.record_evaluation(
            "proc-1234", "totally_safe",
            observations_used=[], self_critique_id="crit-1",
        )


async def test_evaluation_is_append_only_never_updated():
    now = time.time()
    await se.record_evaluation(
        "proc-1234", sp.PASS, observations_used=[], self_critique_id="crit-1",
        evaluated_at=now - 100,
    )
    await se.record_evaluation(
        "proc-1234", sp.STALE, observations_used=[], self_critique_id="crit-2",
        evaluated_at=now,
    )
    history = await se.list_evaluations("proc-1234")
    assert len(history) == 2  # both rows survive -- no UPDATE ever happened


async def test_state_at_reconstructs_the_state_true_at_a_past_time():
    now = time.time()
    await se.record_evaluation(
        "proc-1234", sp.PASS, observations_used=[], self_critique_id="crit-1",
        evaluated_at=now - 200,
    )
    await se.record_evaluation(
        "proc-1234", sp.STALE, observations_used=[], self_critique_id="crit-2",
        evaluated_at=now - 50,
    )
    # Queried at a time between the two evaluations: the earlier one was true then.
    past = await se.state_at("proc-1234", now - 100)
    assert past["status"] == sp.PASS
    current = await se.state_at("proc-1234", now)
    assert current["status"] == sp.STALE


async def test_state_at_before_any_evaluation_is_none():
    now = time.time()
    await se.record_evaluation(
        "proc-1234", sp.PASS, observations_used=[], self_critique_id="crit-1",
        evaluated_at=now,
    )
    assert await se.state_at("proc-1234", now - 1_000_000) is None


async def test_last_discovery_pass_age_is_none_when_never_run():
    assert await se.last_discovery_pass_age(time.time()) is None


async def test_last_discovery_pass_age_computes_the_real_gap():
    now = time.time()
    await se.record_observation(
        se.INVENTORY_SURFACE_ID, {"invocation": "ok"},
        observer_version="v1", environment_identity={"hostname": "test"},
        recorded_at=now - 300,
    )
    age = await se.last_discovery_pass_age(now)
    assert age is not None
    assert 295 <= age <= 310
