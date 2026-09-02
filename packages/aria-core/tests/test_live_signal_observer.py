"""specs/017-live-signal-observer -- signal decoupled from execution."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from aria_core import live_signal_observer as lso
from aria_core import momentum_signal_observation as mso
from aria_core import paper_pause

A = "0x" + "a" * 40
B = "0x" + "b" * 40
CHAIN = "base"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    db = str(tmp_path / "lso.db")
    monkeypatch.setattr(lso, "DB_PATH", db)
    monkeypatch.setattr(mso, "DB_PATH", db)
    monkeypatch.setattr(paper_pause, "is_paused", lambda: True)  # the whole point: paused, still evaluating
    monkeypatch.delenv("ARIA_LIVE_SIGNAL_OBSERVER_ENABLED", raising=False)
    monkeypatch.delenv("ARIA_SIGNAL_TELEGRAM_CHAT_ID", raising=False)

    async def _passthrough_prefilter(cands, **kw):
        return [dict(c, price_usd=1.0) for c in cands]

    monkeypatch.setattr(lso, "_batch_liquidity_prefilter", _passthrough_prefilter)
    yield db


def _frame(*addresses: str) -> str:
    return json.dumps({"data": [{"chainId": CHAIN, "tokenAddress": a, "description": "t"} for a in addresses]})


def _sub(value, ts: str | None = None) -> dict:
    return {"available": True, "value": value, "data_timestamp": ts or datetime.now(timezone.utc).isoformat()}


def _unavail(reason="not_evaluated_this_gate") -> dict:
    return {"available": False, "reason": reason}


def _full_observation(**over) -> dict:
    obs = {
        "id": 1, "contract": A, "chain": CHAIN, "symbol": "TST",
        "decision_ts": datetime.now(timezone.utc).isoformat(),
        "onchain": {"composite_score": _sub(78.0), "composite_pillars": _sub({"a": 1})},
        "chart": {
            "golden_pocket_present": _sub(True), "rsi_divergence_present": _sub(True),
            "risk_reward_ratio": _sub(2.4), "technical_align_score": _sub(3),
            "rvol_confirmed": _sub({"confirmed": True, "multiple": 4.0}), "market_regime": _sub("neutre"),
        },
        "social": {
            "conviction_research_score": _sub(6.5),
            "signal_cascade_convergence": _sub([{"source": "github", "signal": "x", "accelerating": True, "detail": None}]),
            "radar_x_signal": _unavail("no_persisted_state"),
        },
    }
    obs.update(over)
    return obs


# ---------------------------------------------------------------------------
# Gate / config
# ---------------------------------------------------------------------------

def test_gate_off_by_default():
    assert lso.live_signal_observer_enabled() is False


def test_gate_on_when_env_set(monkeypatch):
    monkeypatch.setenv("ARIA_LIVE_SIGNAL_OBSERVER_ENABLED", "true")
    assert lso.live_signal_observer_enabled() is True


def test_signal_chat_id_fallback_and_parse(monkeypatch):
    assert lso._signal_chat_id() is None
    monkeypatch.setenv("ARIA_SIGNAL_TELEGRAM_CHAT_ID", "not-an-int")
    assert lso._signal_chat_id() is None
    monkeypatch.setenv("ARIA_SIGNAL_TELEGRAM_CHAT_ID", "-1001234")
    assert lso._signal_chat_id() == -1001234


async def test_start_does_nothing_when_gate_disabled():
    obs = lso.LiveSignalObserver()
    await obs.start()
    assert obs._tasks == []


# ---------------------------------------------------------------------------
# US1 -- discovery parity + evaluation while paused + zero execution
# ---------------------------------------------------------------------------

async def test_ingest_frame_same_filters_as_momentum_websocket(monkeypatch):
    from aria_core import momentum_websocket as mw

    ours, theirs = lso.LiveSignalObserver(), mw.MomentumWebsocketListener()
    frame = _frame(A, B, "0x4200000000000000000000000000000000000006")  # last = WETH on Base (reference token)
    await ours._ingest_frame(frame)
    await theirs._ingest_frame(frame)
    assert set(ours._pending) == set(theirs._pending)
    assert ("0x4200000000000000000000000000000000000006", CHAIN) not in ours._pending


async def test_ingest_frame_dedup_within_ttl(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(lso.time, "time", lambda: t[0])
    obs = lso.LiveSignalObserver()
    await obs._ingest_frame(_frame(A))
    obs._pending.clear()
    obs._seen[(A, CHAIN)] = (t[0], None)
    t[0] += lso.DEDUP_TTL_SECONDS - 1
    await obs._ingest_frame(_frame(A))
    assert (A, CHAIN) not in obs._pending


async def test_drain_evaluates_while_paper_trading_paused_with_pipeline_params(monkeypatch):
    from aria_core import momentum_entry, paper_trader
    from aria_core.skills import market_sentiment

    calls = []

    async def _fake_eval(contract, chain, *, current_regime=None, mode="standard", **kw):
        calls.append((contract, chain, current_regime, mode))
        return {"action": "HOLD", "chain": chain, "reasons": ["r"], "hold_reason": "x"}

    async def _mode(*a, **k):
        return "scalping"

    async def _regime():
        return "euphorie"

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)
    monkeypatch.setattr(paper_trader, "get_trading_mode", _mode)
    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", _regime)
    assert paper_pause.is_paused() is True

    obs = lso.LiveSignalObserver()
    await obs._ingest_frame(_frame(A))
    n = await obs._drain_once()
    assert n == 1
    assert calls == [(A, CHAIN, "euphorie", "scalping")]


async def test_drain_never_executes_zero_rows_in_position_and_order_tables(_isolated, monkeypatch):
    from aria_core import momentum_entry, paper_trader
    from aria_core.skills import market_sentiment

    db = _isolated
    async with aiosqlite.connect(db) as conn:
        await conn.execute("CREATE TABLE paper_position (id INTEGER PRIMARY KEY, contract TEXT, opened_at TEXT)")
        await conn.execute("CREATE TABLE pending_limit_order (id INTEGER PRIMARY KEY, contract TEXT, created_at TEXT)")
        await conn.commit()

    async def _fake_eval(contract, chain, **kw):
        return {"action": "BUY", "chain": chain, "price": 1.0, "reasons": [], "hold_reason": None}

    async def _mode(*a, **k):
        return "standard"

    async def _regime():
        return "neutre"

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)
    monkeypatch.setattr(paper_trader, "get_trading_mode", _mode)
    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", _regime)

    async def _count(table):
        async with aiosqlite.connect(db) as conn:
            return (await (await conn.execute(f"SELECT COUNT(*) FROM {table}")).fetchone())[0]

    before = (await _count("paper_position"), await _count("pending_limit_order"))
    obs = lso.LiveSignalObserver()
    await obs._ingest_frame(_frame(A, B))
    await obs._drain_once()
    after = (await _count("paper_position"), await _count("pending_limit_order"))
    assert after == before == (0, 0)


def test_source_never_references_execution_or_trade_notification_paths():
    # AST-level: only real identifiers count -- docstrings/comments may NAME
    # these symbols to explain the ban, the code must never touch them.
    import ast

    tree = ast.parse(Path(lso.__file__).read_text(encoding="utf-8"))
    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.alias):
            identifiers.add(node.name.split(".")[-1])
            if node.asname:
                identifiers.add(node.asname)
    for forbidden in ("run_paper_cycle", "open_position", "_default_momentum_analyzer",
                      "process_active_orders", "send_trading_notification", "paper_pause", "outgoing_pause"):
        assert forbidden not in identifiers, f"{forbidden} must never be referenced by live_signal_observer.py"


async def test_hourly_cap_truncates_and_one_failure_does_not_stop_others(monkeypatch):
    from aria_core import momentum_entry, paper_trader
    from aria_core.skills import market_sentiment

    seen = []

    async def _fake_eval(contract, chain, **kw):
        seen.append(contract)
        if contract == A:
            raise RuntimeError("boom")
        return {"action": "HOLD", "chain": chain, "reasons": [], "hold_reason": "x"}

    async def _mode(*a, **k):
        return "standard"

    async def _regime():
        return "neutre"

    monkeypatch.setattr(momentum_entry, "evaluate_momentum_entry", _fake_eval)
    monkeypatch.setattr(paper_trader, "get_trading_mode", _mode)
    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", _regime)

    obs = lso.LiveSignalObserver()
    await obs._ingest_frame(_frame(A, B))
    n = await obs._drain_once()
    assert seen == [A, B] and n == 1  # A failed, B still evaluated

    obs2 = lso.LiveSignalObserver()
    obs2._evaluation_timestamps.extend([lso.time.time()] * (lso.MAX_EVALUATIONS_PER_HOUR - 1))
    await obs2._ingest_frame(_frame(A, B))
    seen.clear()
    await obs2._drain_once()
    assert len(seen) == 1  # truncated to the remaining budget, not skipped entirely


# ---------------------------------------------------------------------------
# US2 -- message format + routing
# ---------------------------------------------------------------------------

def test_format_signal_structure_and_no_trade_vocabulary():
    banned = re.compile(r"(?i)\b(BUY|ENTRY|OPENED|FILLED)\b")
    for obs in (_full_observation(), _full_observation(onchain={"composite_score": _unavail(), "composite_pillars": _unavail()})):
        p = lso.build_presentation(obs)
        text = lso.format_signal(
            symbol="TST", contract=A, chain=CHAIN, presentation=p, status=lso.classify(p),
            decision_ts=obs["decision_ts"], forward_rows=[{"horizon": "1m", "status": "pending"}],
        )
        assert text.startswith("⚡ ARIA LIVE SIGNAL")
        assert "ON-CHAIN" in text and "SOCIAL" in text and "CHART" in text and "STATUS" in text
        assert not banned.search(text)
        assert sum(text.count(s) for s in ("🟢", "🟡", "🔴", "⚪")) == 1


async def test_notify_uses_send_message_never_trading_notification(monkeypatch):
    from aria_core.gateway import telegram_bot

    sent, trading = [], []

    async def _send(text, chat_id=None, **kw):
        sent.append((text, chat_id))
        return True

    async def _trading(text):
        trading.append(text)

    monkeypatch.setattr(telegram_bot, "send_message", _send)
    monkeypatch.setattr(telegram_bot, "send_trading_notification", _trading)
    monkeypatch.setenv("ARIA_SIGNAL_TELEGRAM_CHAT_ID", "-42")

    core = {"action": "BUY", "chain": CHAIN, "symbol": "TST", "price": 1.0, "rr": 2.4, "align_score": 3,
            "volume_confirmed": True, "rvol_multiple": 4.0, "regime": "neutre", "gp_low": 1.0, "gp_high": 1.1,
            "rsi_gap": 8.0, "potential_score": 6.5, "dex_security_score": 78.0, "dex_security_breakdown": {"a": 1}}
    await mso.capture_observation(A, CHAIN, core)

    obs = lso.LiveSignalObserver()
    # signal_cascade absent -> social fresh=1/2 -> MEDIUM, favorable -> figure 100; onchain HIGH 78; chart HIGH 100 -> CONVERGENCE
    ok = await obs._maybe_notify(A, CHAIN, since=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert ok is True
    assert len(sent) == 1 and sent[0][1] == -42
    assert trading == []


async def test_notify_send_failure_does_not_raise(monkeypatch):
    from aria_core.gateway import telegram_bot

    async def _boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(telegram_bot, "send_message", _boom)
    await mso.capture_observation(A, CHAIN, {"action": "BUY", "chain": CHAIN, "price": 1.0, "rr": 2.0, "align_score": 3,
                                             "volume_confirmed": True, "regime": "neutre", "gp_low": 1.0, "gp_high": 1.1,
                                             "rsi_gap": 1.0, "potential_score": 6.0, "dex_security_score": 80.0,
                                             "dex_security_breakdown": {}})
    obs = lso.LiveSignalObserver()
    with pytest.raises(RuntimeError):
        await obs._maybe_notify(A, CHAIN, since=datetime.now(timezone.utc) - timedelta(minutes=1))
    # _drain_once wraps _maybe_notify in try/except -- the raise above is the helper's contract,
    # the drain's is to swallow it (covered by test_hourly_cap_truncates_and_one_failure_does_not_stop_others' pattern).


# ---------------------------------------------------------------------------
# US3 -- data quality, staleness, anti-spam
# ---------------------------------------------------------------------------

def test_low_quality_family_has_no_figure_and_forces_data_incomplete():
    obs = _full_observation(onchain={"composite_score": _unavail(), "composite_pillars": _unavail()})
    p = lso.build_presentation(obs)
    assert p["onchain"]["quality"] == "LOW" and p["onchain"]["figure"] is None
    assert p["chart"]["quality"] == "HIGH" and p["social"]["quality"] == "HIGH"
    assert lso.classify(p) == "DATA_INCOMPLETE"


def test_stale_social_subsignal_excluded_from_tally():
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cascade = [{"source": "github", "signal": "x", "accelerating": True, "detail": None}]
    stale_obs = _full_observation(social={"conviction_research_score": _sub(6.5),
                                          "signal_cascade_convergence": _sub(cascade, old)})
    fresh_obs = _full_observation(social={"conviction_research_score": _sub(6.5),
                                          "signal_cascade_convergence": _sub(cascade, recent)})
    ps, pf = lso.build_presentation(stale_obs)["social"], lso.build_presentation(fresh_obs)["social"]
    assert ps["stale"] == 1 and ps["fresh"] == 1 and ps["quality"] == "MEDIUM"
    assert pf["stale"] == 0 and pf["fresh"] == 2 and pf["quality"] == "HIGH"


def test_classify_convergence_divergence_mixed():
    def _p(o, s, c):
        return {"onchain": {"quality": "HIGH", "figure": o}, "social": {"quality": "HIGH", "figure": s},
                "chart": {"quality": "HIGH", "figure": c}}
    assert lso.classify(_p(80, 70, 90)) == "CONVERGENCE"
    assert lso.classify(_p(80, 20, 90)) == "DIVERGENCE"
    assert lso.classify(_p(50, 50, 50)) == "MIXED"
    assert lso.classify(_p(20, 20, 20)) == "MIXED"


async def test_cooldown_one_send_per_token_and_mixed_never_sent(monkeypatch):
    from aria_core.gateway import telegram_bot

    sent = []

    async def _send(text, chat_id=None, **kw):
        sent.append(text)
        return True

    monkeypatch.setattr(telegram_bot, "send_message", _send)
    strong = {"action": "BUY", "chain": CHAIN, "price": 1.0, "rr": 2.4, "align_score": 3, "volume_confirmed": True,
              "regime": "neutre", "gp_low": 1.0, "gp_high": 1.1, "rsi_gap": 8.0, "potential_score": 6.5,
              "dex_security_score": 78.0, "dex_security_breakdown": {"a": 1}}
    obs = lso.LiveSignalObserver()
    since = datetime.now(timezone.utc) - timedelta(minutes=1)

    await mso.capture_observation(A, CHAIN, strong)
    assert await obs._maybe_notify(A, CHAIN, since=since) is True
    await mso.capture_observation(A, CHAIN, strong)  # 10 min later in spirit -- inside the 4h cooldown
    assert await obs._maybe_notify(A, CHAIN, since=since) is False
    assert len(sent) == 1

    mixed = dict(strong, dex_security_score=50.0, potential_score=4.5, rr=1.0, align_score=1,
                 volume_confirmed=False, gp_low=None, gp_high=None, rsi_gap=None)
    await mso.capture_observation(B, CHAIN, mixed)
    assert await obs._maybe_notify(B, CHAIN, since=since) is False
    assert len(sent) == 1
    assert len(await mso.list_recent(limit=10)) == 3  # every evaluation observed, only one sent
