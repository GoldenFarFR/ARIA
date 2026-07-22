"""Sourcing temps réel momentum via WebSocket DexScreener (#196). Vérifie le
gate, l'ingestion/dédoublonnage des frames et la vidange -- jamais un appel
réseau réel ici (``websockets.connect`` n'est jamais exercé), et jamais un
second chemin de décision (le pipeline momentum réel reste testé dans
test_momentum_entry.py/test_paper_trader.py)."""
from __future__ import annotations

import json
import time

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
async def test_ingest_frame_excludes_reference_tokens():
    """22/07 -- même bug/correctif que côté heartbeat classique (momentum_entry.
    _add_candidate) : WETH ne doit jamais rejoindre _pending via ce chemin
    WebSocket non plus -- il a son propre ajout de candidat, jamais couvert par
    le filtre côté découverte classique."""
    weth = "0x4200000000000000000000000000000000000006"
    listener = mw.MomentumWebsocketListener()
    await listener._ingest_frame(_listing_frame([
        _item(chain_id="base", token_address=weth),
        _item(chain_id="base", token_address=A),
    ]))
    assert (weth, "base") not in listener._pending
    assert (A, "base") in listener._pending


@pytest.mark.asyncio
async def test_ingest_frame_preserves_solana_address_case(monkeypatch):
    """19/07 -- bug réel trouvé en activant ce chemin pour la première fois : un
    .lower() aveugle corrompait toute adresse Solana (base58, sensible à la casse),
    cassant silencieusement la couverture RugCheck/GoPlus en aval (400 "invalid
    length" observé en prod). Base reste insensible à la casse -- vérifié séparément
    ci-dessous, non-régression.

    20/07 -- ``_ALLOWED_CHAINS`` monkeypatché explicitement : ``DEFAULT_CHAINS`` est
    resserré à Base seul (décision opérateur), ce test exerce la préservation de
    casse elle-même, indépendante du périmètre par défaut du moment."""
    monkeypatch.setattr(mw, "_ALLOWED_CHAINS", frozenset({"base", "solana"}))
    listener = mw.MomentumWebsocketListener()
    mixed_case = "GuSBoRgzPo6HC7msoRouqYPj3psxGAhm4amc9idHpump"
    await listener._ingest_frame(
        _listing_frame([_item(chain_id="solana", token_address=mixed_case)])
    )
    assert (mixed_case, "solana") in listener._pending
    assert (mixed_case.lower(), "solana") not in listener._pending


@pytest.mark.asyncio
async def test_ingest_frame_lowercases_base_address():
    listener = mw.MomentumWebsocketListener()
    mixed_case = "0xAbCdEf0000000000000000000000000000000000"
    await listener._ingest_frame(
        _listing_frame([_item(chain_id="base", token_address=mixed_case)])
    )
    assert (mixed_case.lower(), "base") in listener._pending


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
    listener._seen[(A, "base")] = (t[0], None)  # simule un déclenchement récent, prix inconnu

    t[0] += 60  # 1 minute plus tard -- toujours dans la fenêtre TTL (15 min)
    await listener._ingest_frame(_listing_frame([_item(token_address=A)]))
    assert (A, "base") not in listener._pending  # pas re-mis en attente


@pytest.mark.asyncio
async def test_ingest_frame_requeues_after_ttl_expires(monkeypatch):
    listener = mw.MomentumWebsocketListener()
    t = [1000.0]
    monkeypatch.setattr(mw.time, "time", lambda: t[0])

    listener._seen[(A, "base")] = (t[0], None)
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
    # 20/07 -- non-régression du vrai bug trouvé en conditions réelles (position MAGIC
    # achetée via ce chemin sans jamais notifier Telegram, seule sa vente -- gérée par
    # le heartbeat -- est arrivée) : ce chemin doit désormais passer le MÊME notifier
    # que le heartbeat, jamais un achat silencieux.
    from aria_core.gateway.telegram_bot import send_trading_notification

    assert captured["notifier"] is send_trading_notification


# ── cooldown adaptatif (22/07, décision opérateur explicite) ──────────────────

@pytest.mark.asyncio
async def test_drain_skips_candidate_in_cooldown_with_stable_price(monkeypatch):
    """22/07 -- un candidat déjà vu il y a moins de 4h, avec un prix stable
    (<10% de mouvement), ne redéclenche PAS d'évaluation -- économise l'appel,
    conforme à "toutes les 4h suffit"."""
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    listener = mw.MomentumWebsocketListener()
    now = time.time()
    listener._seen[(A, "base")] = (now - 3600.0, 1.0)  # vu il y a 1h, prix 1.0
    listener._pending[(A, "base")] = now

    async def _fake_prefilter(candidates):
        return [{**c, "price_usd": 1.02} for c in candidates]  # +2%, sous le seuil

    monkeypatch.setattr(mw, "_batch_liquidity_prefilter", _fake_prefilter)

    called = False

    async def _fake_run_paper_cycle(**kwargs):
        nonlocal called
        called = True
        return {"opened": []}

    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "run_paper_cycle", _fake_run_paper_cycle)

    await listener._drain_once()

    assert called is False  # aucune évaluation déclenchée
    refreshed_ts, refreshed_price = listener._seen[(A, "base")]
    assert refreshed_price == 1.02  # prix de référence rafraîchi
    assert refreshed_ts >= now  # timestamp rafraîchi par ce passage


@pytest.mark.asyncio
async def test_drain_reevaluates_candidate_in_cooldown_on_significant_price_move(monkeypatch):
    """22/07 -- même candidat en cooldown, mais le prix a bougé de plus de 10% --
    un vrai mouvement peut annoncer un nouveau setup, réévalué immédiatement
    malgré le cooldown de 4h."""
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    listener = mw.MomentumWebsocketListener()
    now = time.time()
    listener._seen[(A, "base")] = (now - 3600.0, 1.0)  # vu il y a 1h, prix 1.0
    listener._pending[(A, "base")] = now

    async def _fake_prefilter(candidates):
        return [{**c, "price_usd": 1.20} for c in candidates]  # +20%, au-delà du seuil

    monkeypatch.setattr(mw, "_batch_liquidity_prefilter", _fake_prefilter)

    captured: dict = {}

    async def _fake_run_paper_cycle(**kwargs):
        captured.update(kwargs)
        return {"opened": []}

    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "run_paper_cycle", _fake_run_paper_cycle)

    await listener._drain_once()

    assert captured.get("candidates") == [A]


@pytest.mark.asyncio
async def test_drain_reevaluates_candidate_after_full_cooldown_even_without_price_move(monkeypatch):
    """22/07 -- au-delà des 4h complètes, réévaluation normale même si le prix
    n'a pas bougé -- le cooldown protège contre un rescan RAPPROCHÉ, jamais un
    blocage permanent."""
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    listener = mw.MomentumWebsocketListener()
    now = time.time()
    listener._seen[(A, "base")] = (now - mw.RESCAN_COOLDOWN_SECONDS - 1.0, 1.0)
    listener._pending[(A, "base")] = now

    async def _fake_prefilter(candidates):
        return [{**c, "price_usd": 1.0} for c in candidates]  # prix inchangé

    monkeypatch.setattr(mw, "_batch_liquidity_prefilter", _fake_prefilter)

    captured: dict = {}

    async def _fake_run_paper_cycle(**kwargs):
        captured.update(kwargs)
        return {"opened": []}

    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "run_paper_cycle", _fake_run_paper_cycle)

    await listener._drain_once()

    assert captured.get("candidates") == [A]


@pytest.mark.asyncio
async def test_drain_never_blocks_on_missing_price_data(monkeypatch):
    """22/07 -- fail-open : si le prefilter ne renvoie aucun prix (panne,
    donnée absente), la comparaison est impossible -- jamais un blocage sur une
    incertitude, le candidat est réévalué normalement."""
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    listener = mw.MomentumWebsocketListener()
    now = time.time()
    listener._seen[(A, "base")] = (now - 3600.0, 1.0)
    listener._pending[(A, "base")] = now

    async def _fake_prefilter(candidates):
        return candidates  # aucun price_usd attaché

    monkeypatch.setattr(mw, "_batch_liquidity_prefilter", _fake_prefilter)

    captured: dict = {}

    async def _fake_run_paper_cycle(**kwargs):
        captured.update(kwargs)
        return {"opened": []}

    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "run_paper_cycle", _fake_run_paper_cycle)

    await listener._drain_once()

    assert captured.get("candidates") == [A]


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


def test_evaluation_budget_remaining_full_when_no_history():
    listener = mw.MomentumWebsocketListener()
    assert listener._evaluation_budget_remaining(1_000_000.0) == mw.MAX_EVALUATIONS_PER_HOUR


def test_evaluation_budget_remaining_decreases_with_recent_evaluations():
    listener = mw.MomentumWebsocketListener()
    now = 1_000_000.0
    listener._evaluation_timestamps.extend([now - 10] * 30)
    assert listener._evaluation_budget_remaining(now) == mw.MAX_EVALUATIONS_PER_HOUR - 30


def test_evaluation_budget_remaining_purges_entries_older_than_one_hour():
    listener = mw.MomentumWebsocketListener()
    now = 1_000_000.0
    listener._evaluation_timestamps.extend([now - 3700] * 50)  # >1h -- périmé
    listener._evaluation_timestamps.extend([now - 10] * 5)     # récent -- compte
    assert listener._evaluation_budget_remaining(now) == mw.MAX_EVALUATIONS_PER_HOUR - 5
    assert len(listener._evaluation_timestamps) == 5  # les 50 périmés purgés


def test_evaluation_budget_remaining_never_negative():
    listener = mw.MomentumWebsocketListener()
    now = 1_000_000.0
    listener._evaluation_timestamps.extend([now - 10] * (mw.MAX_EVALUATIONS_PER_HOUR + 50))
    assert listener._evaluation_budget_remaining(now) == 0


@pytest.mark.asyncio
async def test_drain_truncates_candidates_to_remaining_budget(monkeypatch):
    """19/07 -- réponse à la question opérateur sur le risque de saturer les API.
    Le budget horaire tronque la liste, dégradation progressive plutôt que tout-ou-rien."""
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    listener = mw.MomentumWebsocketListener()
    for i in range(10):
        contract = f"0x{i:040x}"
        listener._pending[(contract, "base")] = 0.0
    # Ne laisse que 3 évaluations de budget restant.
    listener._evaluation_timestamps.extend([time.time()] * (mw.MAX_EVALUATIONS_PER_HOUR - 3))

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

    assert len(captured["candidates"]) == 3
    # Le budget consommé se reflète immédiatement -- prochaine vidange à 0 restant.
    assert listener._evaluation_budget_remaining(time.time()) == 0


@pytest.mark.asyncio
async def test_drain_skips_entirely_when_hourly_budget_exhausted(monkeypatch):
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    listener = mw.MomentumWebsocketListener()
    listener._pending[(A, "base")] = 0.0
    listener._evaluation_timestamps.extend([time.time()] * mw.MAX_EVALUATIONS_PER_HOUR)

    async def _passthrough_prefilter(candidates):
        return candidates

    monkeypatch.setattr(mw, "_batch_liquidity_prefilter", _passthrough_prefilter)

    called = False

    async def _fake_run_paper_cycle(**kwargs):
        nonlocal called
        called = True
        return {"opened": []}

    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "run_paper_cycle", _fake_run_paper_cycle)

    await listener._drain_once()

    assert called is False
    # Le candidat en attente est déjà marqué "vu" (retiré de _pending côté verrou) --
    # comportement voulu : jamais retenté immédiatement au drain suivant.
    assert listener._pending == {}


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
