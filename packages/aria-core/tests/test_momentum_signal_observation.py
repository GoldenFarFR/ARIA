"""specs/016-momentum-signal-observation-layer -- read-after-decide
observation layer. Tests cover: one observation per evaluated candidate
(bought and rejected), strict available/not_available semantics (never a
silent neutral default), the forward-price resolver's funnel/dedup/never-
fabricate behavior, and social-signal availability (synchronous
conviction_research vs. passively-read signal_cascade_convergence vs.
permanently-unavailable radar_x)."""
from __future__ import annotations

from types import SimpleNamespace

import aiosqlite
import pytest

from aria_core import momentum_signal_observation as mso

CONTRACT = "0x" + "b" * 40
CHAIN = "base"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "momentum_signal_observation.db")
    monkeypatch.setattr(mso, "DB_PATH", db_path)
    yield db_path


# ---------------------------------------------------------------------------
# US1 -- one observation per evaluated candidate, bought or rejected (T004)
# ---------------------------------------------------------------------------

async def test_capture_observation_records_one_row_and_five_pending_horizons_for_buy():
    core_result = {
        "action": "BUY", "chain": CHAIN, "symbol": "TEST", "price": 1.23,
        "rr": 2.5, "align_score": 3, "volume_confirmed": True, "rvol_multiple": 4.2,
        "regime": "neutre", "gp_low": 1.0, "gp_high": 1.1, "rsi_gap": 8.0,
        "potential_score": 6.5, "dex_security_score": 62.0, "dex_security_breakdown": {"mint": 20},
        "reasons": ["setup clean"], "hold_reason": None,
    }
    await mso.capture_observation(CONTRACT, CHAIN, core_result)

    rows = await mso.list_recent(limit=10)
    assert len(rows) == 1
    obs = rows[0]
    assert obs["decision_action"] == "BUY"
    assert obs["reference_price_usd"] == 1.23
    assert obs["signal_version"] == mso.SIGNAL_VERSION

    fwd = await mso.forward_performance_for(obs["id"])
    assert len(fwd) == 5
    assert {r["horizon"] for r in fwd} == {"1m", "5m", "15m", "1h", "4h"}
    assert all(r["status"] == "pending" for r in fwd)


async def test_capture_observation_records_one_row_for_early_rejection():
    # Shape of a hard-gate rejection: sparse dict, no chart/on-chain keys at all.
    core_result = {
        "action": "HOLD", "chain": CHAIN,
        "reasons": ["contrat sur liste noire -- déjà confirmé problématique"],
        "hold_reason": "blacklisted",
    }
    await mso.capture_observation(CONTRACT, CHAIN, core_result)

    rows = await mso.list_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["decision_action"] == "HOLD"
    assert rows[0]["decision_reason"] == "blacklisted"
    assert rows[0]["reference_price_usd"] is None

    fwd = await mso.forward_performance_for(rows[0]["id"])
    assert len(fwd) == 5
    assert all(r["status"] == "pending" for r in fwd)


async def test_capture_observation_handles_none_core_result():
    """`best is None` (momentum_entry): no tradeable pair exists at all. This
    is an ABSENCE OF DATA, never a HOLD decision -- recording it as HOLD is
    exactly the confusion this layer exists to prevent (found in the first 120
    production observations, 02/09)."""
    await mso.capture_observation(CONTRACT, CHAIN, None)
    rows = await mso.list_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["reference_price_usd"] is None
    assert rows[0]["decision_action"] == "NO_CANDIDATE_DATA"
    assert rows[0]["decision_reason"] == "no_tradeable_pair_found"


async def test_a_real_hold_stays_distinguishable_from_missing_data():
    await mso.capture_observation(CONTRACT, CHAIN, {
        "action": "HOLD", "chain": CHAIN, "reasons": ["r"], "hold_reason": "weak_rr",
    })
    row = (await mso.list_recent(limit=1))[0]
    assert row["decision_action"] == "HOLD" and row["decision_reason"] == "weak_rr"


async def test_two_evaluations_of_the_same_token_produce_two_distinct_observations():
    core_result = {"action": "HOLD", "chain": CHAIN, "reasons": ["r"], "hold_reason": "x"}
    await mso.capture_observation(CONTRACT, CHAIN, core_result)
    await mso.capture_observation(CONTRACT, CHAIN, core_result)
    rows = await mso.list_recent(limit=10)
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]


async def test_capture_observation_never_raises_on_internal_failure(monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(mso, "_ensure_tables", _boom)
    # Must not raise -- best-effort, same posture as narrative_signal_shadow.
    await mso.capture_observation(CONTRACT, CHAIN, {"action": "BUY"})


# ---------------------------------------------------------------------------
# US1/US3 -- available vs not_available, never a silent neutral default (T005)
# ---------------------------------------------------------------------------

async def test_absent_chart_signals_are_not_available_never_neutral():
    core_result = {"action": "HOLD", "chain": CHAIN, "reasons": ["ohlcv indisponible"], "hold_reason": "ohlcv_unavailable"}
    await mso.capture_observation(CONTRACT, CHAIN, core_result)
    obs = (await mso.list_recent(limit=1))[0]

    # obs-v2 (02/09): this core_result has no "gp_low" key, so detect_entry never
    # ran -> every chart field is gate_not_reached. The one exception is
    # technical_align_score, which is EXCLUDED_BY_DESIGN on every path: its
    # absence is a deliberate choice to avoid changing risk_guard's sizing tier
    # (item #221), never a statement about this token.
    for key in ("golden_pocket_present", "rsi_divergence_present", "risk_reward_ratio", "rvol_confirmed", "market_regime"):
        sub = obs["chart"][key]
        assert sub["available"] is False, f"{key} should be not-available on an early rejection"
        assert sub["reason"] == mso.GATE_NOT_REACHED, f"{key} carried {sub['reason']}"
        assert "value" not in sub

    align = obs["chart"]["technical_align_score"]
    assert align["available"] is False
    assert align["reason"] == mso.EXCLUDED_BY_DESIGN


async def test_computed_neutral_chart_value_is_distinguishable_from_not_available():
    # align_score=0 is a REAL computed value (weak alignment), not "unknown".
    core_result = {
        "action": "HOLD", "chain": CHAIN, "reasons": ["r"], "hold_reason": "no_entry_signal",
        "align_score": 0, "volume_confirmed": False, "rvol_multiple": 0.5, "regime": "peur",
    }
    await mso.capture_observation(CONTRACT, CHAIN, core_result)
    obs = (await mso.list_recent(limit=1))[0]

    align = obs["chart"]["technical_align_score"]
    assert align["available"] is True
    assert align["value"] == 0  # a real zero, not missing

    rvol = obs["chart"]["rvol_confirmed"]
    assert rvol["available"] is True
    assert rvol["value"]["confirmed"] is False  # a real False, not missing


async def test_onchain_composite_absent_before_buy_stage_is_not_available():
    # dex_composite_score is only computed on the BUY-after-conviction-research
    # branch (research.md §2) -- absent here means genuinely never computed.
    core_result = {"action": "HOLD", "chain": CHAIN, "reasons": ["weak rr"], "hold_reason": "weak_rr"}
    await mso.capture_observation(CONTRACT, CHAIN, core_result)
    obs = (await mso.list_recent(limit=1))[0]
    assert obs["onchain"]["composite_score"]["available"] is False
    # obs-v2 (02/09): this core_result carries no "dex_security_score" KEY at
    # all, so the producing stage never ran -> gate_not_reached, distinct from
    # "it ran and found nothing". The four reasons are not interchangeable.
    assert obs["onchain"]["composite_score"]["reason"] == mso.GATE_NOT_REACHED


async def test_onchain_composite_available_on_buy_path():
    core_result = {"action": "BUY", "chain": CHAIN, "price": 1.0, "dex_security_score": 71.5, "dex_security_breakdown": {"a": 1}}
    await mso.capture_observation(CONTRACT, CHAIN, core_result)
    obs = (await mso.list_recent(limit=1))[0]
    assert obs["onchain"]["composite_score"]["available"] is True
    assert obs["onchain"]["composite_score"]["value"] == 71.5


# ---------------------------------------------------------------------------
# US3 -- social signal availability/staleness (T012)
# ---------------------------------------------------------------------------

async def test_conviction_research_fresh_by_construction_when_present():
    core_result = {"action": "BUY", "chain": CHAIN, "price": 1.0, "potential_score": 7.2}
    await mso.capture_observation(CONTRACT, CHAIN, core_result)
    obs = (await mso.list_recent(limit=1))[0]
    conv = obs["social"]["conviction_research_score"]
    assert conv["available"] is True
    assert conv["value"] == 7.2
    assert conv["data_timestamp"] == obs["decision_ts"]


async def test_signal_cascade_convergence_not_yet_scanned_when_no_row_exists():
    core_result = {"action": "HOLD", "chain": CHAIN, "reasons": ["r"], "hold_reason": "x"}
    await mso.capture_observation(CONTRACT, CHAIN, core_result)
    obs = (await mso.list_recent(limit=1))[0]
    cascade = obs["social"]["signal_cascade_convergence"]
    assert cascade["available"] is False
    assert cascade["reason"] == "not_yet_scanned"


async def test_signal_cascade_convergence_available_with_its_own_data_timestamp(_isolated_db):
    db_path = _isolated_db
    recorded_at = "2026-08-31T10:00:00+00:00"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS signal_cascade_convergence ("
            "contract TEXT NOT NULL, chain TEXT NOT NULL DEFAULT 'base', symbol TEXT, "
            "source TEXT NOT NULL, signal TEXT NOT NULL, accelerating INTEGER NOT NULL DEFAULT 0, "
            "detail TEXT, recorded_at TEXT NOT NULL, contract_confirmed_on_site INTEGER, "
            "PRIMARY KEY (contract, chain, source))"
        )
        await db.execute(
            "INSERT INTO signal_cascade_convergence (contract, chain, source, signal, accelerating, detail, recorded_at) "
            "VALUES (?, ?, 'github', 'active_commits', 1, 'detail', ?)",
            (CONTRACT.lower(), CHAIN, recorded_at),
        )
        await db.commit()

    core_result = {"action": "HOLD", "chain": CHAIN, "reasons": ["r"], "hold_reason": "x"}
    await mso.capture_observation(CONTRACT, CHAIN, core_result)
    obs = (await mso.list_recent(limit=1))[0]
    cascade = obs["social"]["signal_cascade_convergence"]
    assert cascade["available"] is True
    # data_timestamp is the SOURCE row's own recorded_at, distinct from the
    # observation's own decision_ts (User Story 3's acceptance scenario 3).
    assert cascade["data_timestamp"] == recorded_at
    assert cascade["data_timestamp"] != obs["decision_ts"]
    assert cascade["value"][0]["source"] == "github"


async def test_radar_x_always_unavailable_no_persisted_state():
    core_result = {"action": "BUY", "chain": CHAIN, "price": 1.0}
    await mso.capture_observation(CONTRACT, CHAIN, core_result)
    obs = (await mso.list_recent(limit=1))[0]
    radar = obs["social"]["radar_x_signal"]
    assert radar["available"] is False
    assert radar["reason"] == "no_persisted_state"


# ---------------------------------------------------------------------------
# US2 -- forward-price resolver: funnel, dedup, never-fabricate (T009)
# ---------------------------------------------------------------------------

async def _seed_observation(contract: str, chain: str, reference_price: float | None):
    core_result = {"action": "HOLD", "chain": chain, "price": reference_price, "reasons": ["r"], "hold_reason": "x"}
    await mso.capture_observation(contract, chain, core_result)
    return (await mso.list_recent(limit=1))[0]["id"]


async def _force_all_horizons_due(db_path: str, observation_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE momentum_signal_forward_performance SET due_at = '2000-01-01T00:00:00+00:00' "
            "WHERE observation_id = ?",
            (observation_id,),
        )
        await db.commit()


async def test_resolver_resolves_no_reference_price_without_any_network_call(_isolated_db, monkeypatch):
    db_path = _isolated_db
    obs_id = await _seed_observation(CONTRACT, CHAIN, None)
    await _force_all_horizons_due(db_path, obs_id)

    async def _should_not_be_called(*args, **kwargs):
        raise AssertionError("fetch_token_pairs must not be called when reference_price_usd is NULL")

    monkeypatch.setattr(mso.dexscreener, "fetch_token_pairs", _should_not_be_called)

    resolved = await mso.resolve_due_forward_prices()
    assert resolved == 5

    fwd = await mso.forward_performance_for(obs_id)
    assert all(r["status"] == "unavailable" for r in fwd)
    assert all(r["unavailable_reason"] == "no_reference_price" for r in fwd)


async def test_resolver_measures_and_computes_pct_change(_isolated_db, monkeypatch):
    db_path = _isolated_db
    obs_id = await _seed_observation(CONTRACT, CHAIN, 1.0)
    await _force_all_horizons_due(db_path, obs_id)

    async def _fake_fetch(contract, *, chain="base"):
        return [SimpleNamespace(price_usd=1.5)]

    monkeypatch.setattr(mso.dexscreener, "fetch_token_pairs", _fake_fetch)

    resolved = await mso.resolve_due_forward_prices()
    assert resolved == 5

    fwd = await mso.forward_performance_for(obs_id)
    assert all(r["status"] == "measured" for r in fwd)
    assert all(r["price_usd"] == 1.5 for r in fwd)
    assert all(abs(r["pct_change_vs_reference"] - 50.0) < 1e-9 for r in fwd)


async def test_resolver_marks_unpriceable_token_unavailable_never_fabricates(_isolated_db, monkeypatch):
    db_path = _isolated_db
    obs_id = await _seed_observation(CONTRACT, CHAIN, 1.0)
    await _force_all_horizons_due(db_path, obs_id)

    async def _no_pairs(contract, *, chain="base"):
        return []

    monkeypatch.setattr(mso.dexscreener, "fetch_token_pairs", _no_pairs)

    await mso.resolve_due_forward_prices()
    fwd = await mso.forward_performance_for(obs_id)
    assert all(r["status"] == "unavailable" for r in fwd)
    assert all(r["unavailable_reason"] == "token_unpriceable" for r in fwd)
    assert all(r["price_usd"] is None for r in fwd)


async def test_resolver_deduplicates_network_calls_by_token(_isolated_db, monkeypatch):
    db_path = _isolated_db
    obs_a = await _seed_observation(CONTRACT, CHAIN, 1.0)
    obs_b = await _seed_observation(CONTRACT, CHAIN, 2.0)  # same token, different observation
    await _force_all_horizons_due(db_path, obs_a)
    await _force_all_horizons_due(db_path, obs_b)

    call_count = {"n": 0}

    async def _counting_fetch(contract, *, chain="base"):
        call_count["n"] += 1
        return [SimpleNamespace(price_usd=3.0)]

    monkeypatch.setattr(mso.dexscreener, "fetch_token_pairs", _counting_fetch)

    resolved = await mso.resolve_due_forward_prices()
    assert resolved == 10  # 5 horizons x 2 observations
    assert call_count["n"] == 1  # but only ONE network call for the shared token


async def test_resolver_ignores_not_yet_due_and_already_resolved_rows(_isolated_db, monkeypatch):
    db_path = _isolated_db
    obs_id = await _seed_observation(CONTRACT, CHAIN, 1.0)
    # Horizons left at their real future due_at -- none should resolve yet.

    async def _should_not_be_called(*args, **kwargs):
        raise AssertionError("no horizon is due yet")

    monkeypatch.setattr(mso.dexscreener, "fetch_token_pairs", _should_not_be_called)

    resolved = await mso.resolve_due_forward_prices()
    assert resolved == 0
    fwd = await mso.forward_performance_for(obs_id)
    assert all(r["status"] == "pending" for r in fwd)


async def test_resolver_never_raises_on_price_lookup_failure(_isolated_db, monkeypatch):
    db_path = _isolated_db
    obs_id = await _seed_observation(CONTRACT, CHAIN, 1.0)
    await _force_all_horizons_due(db_path, obs_id)

    async def _boom(contract, *, chain="base"):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(mso.dexscreener, "fetch_token_pairs", _boom)

    resolved = await mso.resolve_due_forward_prices()  # must not raise
    assert resolved == 5
    fwd = await mso.forward_performance_for(obs_id)
    assert all(r["status"] == "unavailable" for r in fwd)
    assert all(r["unavailable_reason"] == "token_unpriceable" for r in fwd)


async def test_the_four_unavailability_reasons_stay_distinct():
    """obs-v2 (02/09): four reasons, and no code path may collapse them again.

    v1 wrote ``not_evaluated_this_gate`` for every absence, which made these
    indistinguishable in storage even though they mean opposite things to a
    replay: a gate that never ran, a gate that ran and found nothing, a field
    our own pipeline never forwards, and a field deliberately withheld. Reading
    the third as the second blames a token for our blind spot; reading the
    second as the first hides that we did measure and found nothing.

    The version bump is part of the invariant: rows written under v1 and v2 use
    different vocabularies and must never be pooled in one replay.
    """
    reasons = {mso.GATE_NOT_REACHED, mso.COMPUTED_NO_VALUE,
               mso.NOT_INSTRUMENTED, mso.EXCLUDED_BY_DESIGN}
    assert len(reasons) == 4, "two reasons collapsed onto the same string"
    assert "not_evaluated_this_gate" not in reasons, "the ambiguous v1 label came back"
    assert mso.SIGNAL_VERSION != "obs-v1", "vocabulary changed but the version did not"

    # gate never ran: no producing key present at all
    await mso.capture_observation(CONTRACT, CHAIN, {
        "action": "HOLD", "chain": CHAIN, "hold_reason": "blacklisted", "reasons": [],
    })
    early = (await mso.list_recent(limit=1))[0]
    assert early["chart"]["risk_reward_ratio"]["reason"] == mso.GATE_NOT_REACHED

    # gate ran (gp_low key present) but produced nothing for this token
    await mso.capture_observation(CONTRACT, CHAIN, {
        "action": "HOLD", "chain": CHAIN, "hold_reason": "no_entry_signal", "reasons": [],
        "gp_low": None, "gp_high": None, "rr": None,
    })
    ran = (await mso.list_recent(limit=1))[0]
    assert ran["chart"]["risk_reward_ratio"]["reason"] == mso.COMPUTED_NO_VALUE, (
        "a stage that ran and found nothing must not look like a stage that never ran"
    )
    # same row: a field this path does not forward at all is OUR gap, not the token's
    assert ran["chart"]["rvol_confirmed"]["reason"] == mso.NOT_INSTRUMENTED

    # never exposed on any path -> always our gap
    assert ran["onchain"]["holder_concentration_top10_pct"]["reason"] == mso.NOT_INSTRUMENTED
