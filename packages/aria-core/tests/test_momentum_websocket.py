"""Sourcing temps réel momentum via WebSocket DexScreener (#196). Vérifie le
gate, l'ingestion/dédoublonnage des frames et la vidange -- jamais un appel
réseau réel ici (``websockets.connect`` n'est jamais exercé), et jamais un
second chemin de décision (le pipeline momentum réel reste testé dans
test_momentum_entry.py/test_paper_trader.py)."""
from __future__ import annotations

import json

import pytest

from aria_core import momentum_websocket as mw
from aria_core import outgoing_pause

A = "0x" + "a" * 40
B = "0x" + "b" * 40


@pytest.fixture(autouse=True)
def _gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_MOMENTUM_WEBSOCKET_ENABLED", raising=False)
    monkeypatch.delenv("ARIA_PAPER_TRADING_ENABLED", raising=False)
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda **kw: False)
    yield


def _listing_frame(items: list[dict]) -> str:
    return json.dumps({"limit": len(items), "data": items})


def _item(*, chain_id="base", token_address=A, description="test") -> dict:
    return {"chainId": chain_id, "tokenAddress": token_address, "description": description, "links": []}


# ── gate ──────────────────────────────────────────────────────────────────────

def test_gate_off_by_default():
    assert mw.momentum_websocket_enabled() is False


def test_gate_on_when_env_set(monkeypatch):
    monkeypatch.setenv("ARIA_MOMENTUM_WEBSOCKET_ENABLED", "true")
    assert mw.momentum_websocket_enabled() is True


def test_paper_trading_gate_off_by_default():
    assert mw._paper_trading_enabled() is False


# ── start()/stop() -- respecte le gate, aucun appel réseau tant que OFF ───────

@pytest.mark.asyncio
async def test_start_does_nothing_when_gate_disabled():
    listener = mw.MomentumWebsocketListener()
    await listener.start()
    assert listener._running is False
    assert listener._tasks == []
    await listener.stop()  # ne doit jamais lever, même jamais démarré


@pytest.mark.asyncio
async def test_start_stop_lifecycle_when_gate_enabled(monkeypatch):
    monkeypatch.setenv("ARIA_MOMENTUM_WEBSOCKET_ENABLED", "true")

    async def _never_connect(*args, **kwargs):
        raise RuntimeError("jamais de vrai réseau dans les tests")

    monkeypatch.setattr("websockets.connect", _never_connect)

    listener = mw.MomentumWebsocketListener()
    await listener.start()
    assert listener._running is True
    assert len(listener._tasks) == len(mw.ENDPOINTS) + 1  # 4 endpoints + 1 boucle de vidange

    await listener.stop()
    assert listener._running is False
    assert listener._tasks == []


# ── ingestion de frames ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_frame_queues_new_candidate_on_allowed_chain():
    listener = mw.MomentumWebsocketListener()
    await listener._ingest_frame(_listing_frame([_item(chain_id="base", token_address=A)]))
    assert (A, "base") in listener._pending


@pytest.mark.asyncio
async def test_ingest_frame_ignores_heartbeat_frame():
    listener = mw.MomentumWebsocketListener()
    await listener._ingest_frame(json.dumps({"type": "heartbeat"}))
    assert listener._pending == {}


@pytest.mark.asyncio
async def test_ingest_frame_ignores_malformed_json():
    listener = mw.MomentumWebsocketListener()
    await listener._ingest_frame("pas du json valide {{{")
    assert listener._pending == {}


@pytest.mark.asyncio
async def test_ingest_frame_ignores_disallowed_chain():
    listener = mw.MomentumWebsocketListener()
    await listener._ingest_frame(_listing_frame([_item(chain_id="ethereum", token_address=A)]))
    assert listener._pending == {}


@pytest.mark.asyncio
async def test_ingest_frame_ignores_entry_missing_contract_or_chain():
    listener = mw.MomentumWebsocketListener()
    await listener._ingest_frame(_listing_frame([_item(chain_id="", token_address=A)]))
    await listener._ingest_frame(_listing_frame([_item(chain_id="base", token_address="")]))
    assert listener._pending == {}


@pytest.mark.asyncio
async def test_ingest_frame_dedup_within_ttl_window(monkeypatch):
    listener = mw.MomentumWebsocketListener()
    t = [1000.0]
    monkeypatch.setattr(mw.time, "time", lambda: t[0])

    await listener._ingest_frame(_listing_frame([_item(token_address=A)]))
    listener._pending.pop((A, "base"))
    listener._seen[(A, "base")] = t[0]  # simule un déclenchement récent

    t[0] += 60  # 1 minute plus tard -- toujours dans la fenêtre TTL (15 min)
    await listener._ingest_frame(_listing_frame([_item(token_address=A)]))
    assert (A, "base") not in listener._pending  # pas re-mis en attente


@pytest.mark.asyncio
async def test_ingest_frame_requeues_after_ttl_expires(monkeypatch):
    listener = mw.MomentumWebsocketListener()
    t = [1000.0]
    monkeypatch.setattr(mw.time, "time", lambda: t[0])

    listener._seen[(A, "base")] = t[0]
    t[0] += mw.DEDUP_TTL_SECONDS + 1  # TTL expirée

    await listener._ingest_frame(_listing_frame([_item(token_address=A)]))
    assert (A, "base") in listener._pending


# ── vidange ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drain_skips_when_paper_trading_disabled(monkeypatch):
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "false")
    listener = mw.MomentumWebsocketListener()
    listener._pending[(A, "base")] = 0.0

    called = False

    async def _fake_prefilter(candidates):
        nonlocal called
        called = True
        return candidates

    monkeypatch.setattr(mw, "_batch_liquidity_prefilter", _fake_prefilter)
    await listener._drain_once()
    assert called is False


@pytest.mark.asyncio
async def test_drain_skips_when_kill_switch_paused(monkeypatch):
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda **kw: True)
    listener = mw.MomentumWebsocketListener()
    listener._pending[(A, "base")] = 0.0

    called = False

    async def _fake_prefilter(candidates):
        nonlocal called
        called = True
        return candidates

    monkeypatch.setattr(mw, "_batch_liquidity_prefilter", _fake_prefilter)
    await listener._drain_once()
    assert called is False


@pytest.mark.asyncio
async def test_drain_triggers_run_paper_cycle_with_skip_position_management(monkeypatch):
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    listener = mw.MomentumWebsocketListener()
    listener._pending[(A, "base")] = 0.0
    listener._pending[(B, "solana")] = 0.0

    async def _passthrough_prefilter(candidates):
        return candidates

    monkeypatch.setattr(mw, "_batch_liquidity_prefilter", _passthrough_prefilter)

    captured: dict = {}

    async def _fake_run_paper_cycle(**kwargs):
        captured.update(kwargs)
        return {"opened": []}

    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "run_paper_cycle", _fake_run_paper_cycle)

    await listener._drain_once()

    assert captured["skip_position_management"] is True
    assert captured["max_new"] == mw.MAX_NEW_PER_DRAIN
    assert sorted(captured["candidates"]) == sorted([A, B])
    assert listener._pending == {}
    assert (A, "base") in listener._seen
    assert (B, "solana") in listener._seen


@pytest.mark.asyncio
async def test_drain_respects_max_candidates_per_drain_cap(monkeypatch):
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    listener = mw.MomentumWebsocketListener()
    for i in range(mw.MAX_CANDIDATES_PER_DRAIN + 5):
        contract = f"0x{i:040x}"
        listener._pending[(contract, "base")] = 0.0

    async def _passthrough_prefilter(candidates):
        return candidates

    monkeypatch.setattr(mw, "_batch_liquidity_prefilter", _passthrough_prefilter)

    captured: dict = {}

    async def _fake_run_paper_cycle(**kwargs):
        captured.update(kwargs)
        return {"opened": []}

    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "run_paper_cycle", _fake_run_paper_cycle)

    await listener._drain_once()

    assert len(captured["candidates"]) == mw.MAX_CANDIDATES_PER_DRAIN
    assert len(listener._pending) == 5  # le reste attend la prochaine vidange


@pytest.mark.asyncio
async def test_drain_does_nothing_when_pending_empty(monkeypatch):
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    listener = mw.MomentumWebsocketListener()

    from aria_core import paper_trader

    called = False

    async def _fake_run_paper_cycle(**kwargs):
        nonlocal called
        called = True
        return {"opened": []}

    monkeypatch.setattr(paper_trader, "run_paper_cycle", _fake_run_paper_cycle)
    await listener._drain_once()
    assert called is False
