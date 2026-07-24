"""Suivi des trades perdants par lot de 10 (trade_loss_batch_review.py, 07/24,
demande opérateur explicite après le design du coupe-circuit de drawdown
capital réel : "un suivit de tout les trades perdant... traité par lot de 10
pour eviter lisolation d'une malchance et comprendre et reajuster la
trajectoire" -- une alerte par trade perdant avait été explicitement rejetée
avant ça). Vérifie : le gating, l'accumulation (rien avant 10 pertes), le
dédoublonnage par position, l'exclusion des trades GAGNANTS du décompte,
l'ordre chronologique des lots, le sens unique (un ajustement confirmé reste
en base pour toujours), et le format court injecté dans les prompts momentum."""
from __future__ import annotations

import json

import pytest

from aria_core.skills import trade_loss_batch_review as tlbr


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(tlbr, "DB_PATH", str(tmp_path / "trade_loss_batch_review_test.db"))
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: False)
    monkeypatch.delenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", raising=False)
    yield


def _loss(id_, **overrides):
    base = {
        "id": id_, "contract": f"0x{id_:040x}", "symbol": f"L{id_}",
        "thesis": "golden pocket + R/R 2.2", "pnl_usd": -100.0, "pnl_pct": -10.0,
        "close_reason": "stop suiveur", "close_notes": "Stop déclenché.",
        "discovery_channel": "floor", "conviction_tier": "faible",
        "chain": "base", "entry_regime": "neutre", "strategy": "momentum",
        "closed_at": f"2026-07-{id_:02d}T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _win(id_, **overrides):
    base = _loss(id_, pnl_usd=250.0, pnl_pct=15.0, close_reason="prise de profit")
    base.update(overrides)
    return base


def _llm_returning(payload: dict):
    async def _llm(*args, **kwargs):
        return json.dumps(payload)
    return _llm


# ── gate ─────────────────────────────────────────────────────────────────────────

def test_disabled_by_default():
    assert tlbr.trade_loss_batch_review_enabled() is False


def test_enabled_when_set(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")
    assert tlbr.trade_loss_batch_review_enabled() is True


# ── run_trade_loss_batch_review_cycle: gating / accumulation ────────────────────

@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled():
    result = await tlbr.run_trade_loss_batch_review_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_paused(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: True)
    result = await tlbr.run_trade_loss_batch_review_cycle()
    assert result == {"outcome": "skipped_paused"}


@pytest.mark.asyncio
async def test_fewer_than_10_losses_just_accumulates(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")

    async def positions_fetch():
        return [_loss(i) for i in range(1, 6)]  # 5 losses

    result = await tlbr.run_trade_loss_batch_review_cycle(positions_fetch=positions_fetch)
    assert result == {"outcome": "accumulating", "pending": 5, "needed": 5}


@pytest.mark.asyncio
async def test_winning_trades_never_count_toward_the_batch(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")

    async def positions_fetch():
        # 9 losses + 5 wins -- still short of the 10-loss threshold.
        return [_loss(i) for i in range(1, 10)] + [_win(i) for i in range(100, 105)]

    result = await tlbr.run_trade_loss_batch_review_cycle(positions_fetch=positions_fetch)
    assert result["outcome"] == "accumulating"
    assert result["pending"] == 9


@pytest.mark.asyncio
async def test_unknown_pnl_never_counts_as_a_loss(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")

    async def positions_fetch():
        return [_loss(i) for i in range(1, 10)] + [_loss(50, pnl_usd=None, pnl_pct=None)]

    result = await tlbr.run_trade_loss_batch_review_cycle(positions_fetch=positions_fetch)
    assert result["outcome"] == "accumulating"
    assert result["pending"] == 9


# ── run_trade_loss_batch_review_cycle: batch trigger at exactly 10 ──────────────

@pytest.mark.asyncio
async def test_exactly_10_losses_triggers_one_batch(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")

    async def positions_fetch():
        return [_loss(i) for i in range(1, 11)]

    llm = _llm_returning({"pattern_found": False, "pattern_summary": "", "adjustment": ""})
    result = await tlbr.run_trade_loss_batch_review_cycle(llm=llm, positions_fetch=positions_fetch)
    assert result["outcome"] == "ok"
    assert result["batches_reviewed"] == 1
    assert result["results"][0]["batch_number"] == 1
    assert result["results"][0]["position_ids"] == list(range(1, 11))


@pytest.mark.asyncio
async def test_second_cycle_does_not_rereview_already_batched_positions(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")

    async def positions_fetch():
        return [_loss(i) for i in range(1, 11)]

    llm = _llm_returning({"pattern_found": False, "pattern_summary": "", "adjustment": ""})
    await tlbr.run_trade_loss_batch_review_cycle(llm=llm, positions_fetch=positions_fetch)

    result2 = await tlbr.run_trade_loss_batch_review_cycle(llm=llm, positions_fetch=positions_fetch)
    assert result2 == {"outcome": "accumulating", "pending": 0, "needed": 10}


@pytest.mark.asyncio
async def test_11_new_losses_only_reviews_one_batch_per_cycle(monkeypatch):
    """Sanity cap sur le coût LLM (même discipline que trade_devils_advocate) :
    même avec 21 pertes en attente, un seul lot de 10 est traité par cycle --
    le reliquat attend le cycle suivant."""
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")

    async def positions_fetch():
        return [_loss(i) for i in range(1, 22)]  # 21 losses

    llm = _llm_returning({"pattern_found": False, "pattern_summary": "", "adjustment": ""})
    result = await tlbr.run_trade_loss_batch_review_cycle(llm=llm, positions_fetch=positions_fetch)
    assert result["batches_reviewed"] == 1
    assert result["still_pending"] == 11


@pytest.mark.asyncio
async def test_batches_process_oldest_losses_first(monkeypatch):
    """closed_at le plus ancien d'abord -- un lot reflète le VRAI ordre
    chronologique des pertes, jamais un mélange arbitraire (get_closed_positions
    renvoie le plus récent en premier)."""
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")

    async def positions_fetch():
        # Most-recent-first, like the real get_closed_positions ordering.
        return list(reversed([_loss(i) for i in range(1, 11)]))

    llm = _llm_returning({"pattern_found": False, "pattern_summary": "", "adjustment": ""})
    result = await tlbr.run_trade_loss_batch_review_cycle(llm=llm, positions_fetch=positions_fetch)
    assert result["results"][0]["position_ids"] == list(range(1, 11))


@pytest.mark.asyncio
async def test_one_batch_failure_does_not_break_the_cycle(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")

    async def positions_fetch():
        return [_loss(i) for i in range(1, 11)]

    async def broken_llm(*args, **kwargs):
        raise RuntimeError("panne réseau")

    result = await tlbr.run_trade_loss_batch_review_cycle(llm=broken_llm, positions_fetch=positions_fetch)
    assert result["outcome"] == "ok"
    assert "error" in result["results"][0]


# ── pattern_found vs no-pattern (never fabricated) ──────────────────────────────

@pytest.mark.asyncio
async def test_pattern_found_with_adjustment_is_promoted_active(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")

    async def positions_fetch():
        return [_loss(i) for i in range(1, 11)]

    llm = _llm_returning({
        "pattern_found": True,
        "pattern_summary": "9/10 pertes viennent du canal de découverte 'floor'",
        "adjustment": "réduire la taille des trades forcés par le plancher quotidien",
    })
    await tlbr.run_trade_loss_batch_review_cycle(llm=llm, positions_fetch=positions_fetch)
    adjustments = await tlbr.active_trajectory_adjustments()
    assert len(adjustments) == 1
    assert "plancher quotidien" in adjustments[0]["adjustment"]


@pytest.mark.asyncio
async def test_no_pattern_writes_no_active_adjustment(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")

    async def positions_fetch():
        return [_loss(i) for i in range(1, 11)]

    llm = _llm_returning({"pattern_found": False, "pattern_summary": "", "adjustment": ""})
    await tlbr.run_trade_loss_batch_review_cycle(llm=llm, positions_fetch=positions_fetch)
    assert await tlbr.active_trajectory_adjustments() == []


@pytest.mark.asyncio
async def test_pattern_found_without_adjustment_text_not_promoted():
    """Un pattern_found=True sans texte d'ajustement concret (LLM incomplet)
    ne doit jamais polluer le jeu actif avec une entrée vide."""
    llm = _llm_returning({"pattern_found": True, "pattern_summary": "quelque chose", "adjustment": ""})
    await tlbr._review_batch([{"id": i, "pnl_usd": -1} for i in range(1, 11)], 1, llm=llm)
    assert await tlbr.active_trajectory_adjustments() == []


@pytest.mark.asyncio
async def test_unparsable_llm_output_defaults_to_no_pattern():
    async def llm(*args, **kwargs):
        return "pas du JSON du tout"
    result = await tlbr._review_batch([{"id": i, "pnl_usd": -1} for i in range(1, 11)], 1, llm=llm)
    assert result["pattern_found"] is False


@pytest.mark.asyncio
async def test_none_llm_reply_defaults_to_no_pattern():
    async def llm(*args, **kwargs):
        return None
    result = await tlbr._review_batch([{"id": i, "pnl_usd": -1} for i in range(1, 11)], 1, llm=llm)
    assert result["pattern_found"] is False


# ── active_trajectory_adjustments / format_trajectory_line ──────────────────────

@pytest.mark.asyncio
async def test_active_adjustments_respects_limit(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")
    for batch_n in range(1, 6):
        llm = _llm_returning({
            "pattern_found": True, "pattern_summary": f"pattern {batch_n}",
            "adjustment": f"ajustement {batch_n}",
        })
        positions = [{"id": (batch_n * 100) + i, "pnl_usd": -1} for i in range(10)]
        await tlbr._review_batch(positions, batch_n, llm=llm)
    adjustments = await tlbr.active_trajectory_adjustments(limit=3)
    assert len(adjustments) == 3


def test_format_trajectory_line_empty():
    assert tlbr.format_trajectory_line([]) == ""


def test_format_trajectory_line_joins_multiple():
    adjustments = [
        {"batch_number": 1, "pattern_summary": "x", "adjustment": "réduire la taille sur le canal floor"},
        {"batch_number": 2, "pattern_summary": "y", "adjustment": "éviter les entrées en régime peur sur solana"},
    ]
    line = tlbr.format_trajectory_line(adjustments)
    assert "réduire la taille sur le canal floor" in line
    assert "éviter les entrées en régime peur sur solana" in line


def test_format_trajectory_line_truncates_long_content():
    adjustments = [{"batch_number": 1, "pattern_summary": "y", "adjustment": "z" * 500}]
    line = tlbr.format_trajectory_line(adjustments)
    assert len(line) < 500
    assert "…" in line


def test_format_trajectory_line_skips_entries_without_adjustment_text():
    adjustments = [{"batch_number": 1, "pattern_summary": "y", "adjustment": ""}]
    assert tlbr.format_trajectory_line(adjustments) == ""


# ── format_batch_alert ───────────────────────────────────────────────────────────

def test_format_batch_alert_with_pattern():
    result = {
        "batch_number": 3, "pattern_found": True,
        "pattern_summary": "toutes sur chain solana", "adjustment": "désactiver solana en régime peur",
    }
    text = tlbr.format_batch_alert(result)
    assert "lot n°3" in text
    assert "désactiver solana en régime peur" in text


def test_format_batch_alert_without_pattern():
    result = {"batch_number": 4, "pattern_found": False, "pattern_summary": "", "adjustment": ""}
    text = tlbr.format_batch_alert(result)
    assert "Aucun dénominateur commun" in text
