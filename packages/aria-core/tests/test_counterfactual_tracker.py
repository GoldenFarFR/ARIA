"""#176 (20/07), volet apprentissage b -- DB isolée par test (même patron que
test_momentum_funnel_log.py/test_momentum_blacklist.py), aucun appel réseau réel
(paper_trader._default_pair_lookup toujours mocké quand exercé)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import counterfactual_tracker as ct

A = "0x" + "a" * 40
B = "0x" + "b" * 40


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "DB_PATH", str(tmp_path / "counterfactual_test.db"))


async def _backdate(contract: str, days: float) -> None:
    """Recule ``rejected_at`` d'une ligne déjà enregistrée -- seul moyen de tester
    ``list_due_for_revisit`` sans attendre le vrai délai."""
    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(ct.DB_PATH) as db:
        await db.execute(
            "UPDATE counterfactual_rejection SET rejected_at = ? WHERE contract = ?", (when, contract),
        )
        await db.commit()


class TestIsTrackableReason:
    @pytest.mark.parametrize("reason", [
        "no_entry_signal", "ohlcv_unavailable", "blacklisted",
        "honeypot_rejected", "honeypot_unavailable", "chain_not_covered",
    ])
    def test_excluded_reasons_not_trackable(self, reason):
        assert ct.is_trackable_reason(reason) is False

    @pytest.mark.parametrize("reason", [
        "insufficient_liquidity", "volume_too_low", "wash_trading_ratio",
        "already_parabolic", "pair_too_young", "no_verified_profile",
        "holder_concentration", "rr_below_ambiguous_floor", "volume_not_confirmed",
        "some_future_gate_never_seen_before",
    ])
    def test_discretionary_gates_trackable(self, reason):
        """Fail-open à l'inclusion -- même une raison JAMAIS vue avant (futur garde-fou)
        reste trackable par défaut, jamais besoin de mettre à jour une allowlist."""
        assert ct.is_trackable_reason(reason) is True

    def test_none_or_empty_not_trackable(self):
        assert ct.is_trackable_reason(None) is False
        assert ct.is_trackable_reason("") is False


@pytest.mark.asyncio
class TestRecordRejection:
    async def test_excluded_reason_is_noop(self):
        await ct.record_rejection(A, "base", "AAA", "no_entry_signal", 1.0)
        due = await ct.list_due_for_revisit(older_than_days=0)
        assert due == []

    async def test_missing_price_is_noop(self):
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", None)
        due = await ct.list_due_for_revisit(older_than_days=0)
        assert due == []

    async def test_zero_or_negative_price_is_noop(self):
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 0.0)
        await ct.record_rejection(B, "base", "BBB", "insufficient_liquidity", -1.0)
        due = await ct.list_due_for_revisit(older_than_days=0)
        assert due == []

    async def test_trackable_reason_with_valid_price_recorded(self):
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.5)
        due = await ct.list_due_for_revisit(older_than_days=0)
        assert len(due) == 1
        assert due[0]["contract"] == A
        assert due[0]["chain"] == "base"
        assert due[0]["symbol"] == "AAA"
        assert due[0]["reject_reason"] == "insufficient_liquidity"
        assert due[0]["price_at_rejection"] == 1.5
        assert due[0]["revisited_at"] is None

    async def test_never_raises_on_any_input(self):
        """Télémétrie best-effort -- jamais une exception qui remonterait à
        run_paper_cycle."""
        await ct.record_rejection("", "", "", "insufficient_liquidity", 1.0)  # contrat vide, ne plante pas


@pytest.mark.asyncio
class TestListDueForRevisit:
    async def test_recent_rejection_not_yet_due(self):
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.0)
        due = await ct.list_due_for_revisit(older_than_days=7.0)
        assert due == []

    async def test_old_rejection_is_due(self):
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.0)
        await _backdate(A, days=8.0)
        due = await ct.list_due_for_revisit(older_than_days=7.0)
        assert len(due) == 1
        assert due[0]["contract"] == A

    async def test_already_revisited_never_returned_again(self):
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.0)
        await _backdate(A, days=8.0)
        due = await ct.list_due_for_revisit(older_than_days=7.0)
        await ct.record_revisit(due[0]["id"], 1.2)
        assert await ct.list_due_for_revisit(older_than_days=7.0) == []

    async def test_oldest_first_ordering(self):
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.0)
        await _backdate(A, days=10.0)
        await ct.record_rejection(B, "base", "BBB", "insufficient_liquidity", 1.0)
        await _backdate(B, days=20.0)
        due = await ct.list_due_for_revisit(older_than_days=7.0)
        assert [r["contract"] for r in due] == [B, A]

    async def test_respects_limit(self):
        for i in range(5):
            c = f"0x{i:040d}"
            await ct.record_rejection(c, "base", f"T{i}", "insufficient_liquidity", 1.0)
            await _backdate(c, days=8.0)
        due = await ct.list_due_for_revisit(older_than_days=7.0, limit=2)
        assert len(due) == 2


@pytest.mark.asyncio
class TestRecordRevisit:
    async def test_computes_price_change_pct(self):
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.0)
        due = await ct.list_due_for_revisit(older_than_days=0)
        await ct.record_revisit(due[0]["id"], 1.5)
        summary = await ct.summarize_revisited()
        assert summary["by_reason"]["insufficient_liquidity"]["avg_price_change_pct"] == pytest.approx(50.0)

    async def test_missing_price_marks_revisited_without_change_pct(self):
        """Prix introuvable à la revisite (ex. token illiquide/rug depuis) -- marqué
        revisité quand même (jamais retenté en boucle), mais price_change_pct reste
        NULL, jamais un 0% inventé."""
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.0)
        due = await ct.list_due_for_revisit(older_than_days=0)
        await ct.record_revisit(due[0]["id"], None)
        assert await ct.list_due_for_revisit(older_than_days=0) == []  # jamais retentée
        summary = await ct.summarize_revisited()
        assert summary["by_reason"] == {}  # exclue de l'agrégat (change_pct NULL)


@pytest.mark.asyncio
class TestRunRevisitCycle:
    async def test_no_due_candidates_is_noop(self):
        result = await ct.run_revisit_cycle()
        assert result == {"due": 0, "revisited": 0, "price_unavailable": 0}

    async def test_revisits_due_candidates_using_real_pair_lookup(self, monkeypatch):
        from aria_core import paper_trader
        from aria_core.services.dexscreener import PairSnapshot

        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.0)
        await _backdate(A, days=8.0)

        async def fake_pair_lookup(contract, *, chain="base"):
            return PairSnapshot(pair_address="0xpool", price_usd=1.3, liquidity_usd=100_000.0, base_symbol="AAA")

        monkeypatch.setattr(paper_trader, "_default_pair_lookup", fake_pair_lookup)
        result = await ct.run_revisit_cycle()
        assert result == {"due": 1, "revisited": 1, "price_unavailable": 0}
        summary = await ct.summarize_revisited()
        assert summary["by_reason"]["insufficient_liquidity"]["avg_price_change_pct"] == pytest.approx(30.0)

    async def test_pair_lookup_failure_on_one_candidate_does_not_block_others(self, monkeypatch):
        from aria_core import paper_trader
        from aria_core.services.dexscreener import PairSnapshot

        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.0)
        await _backdate(A, days=8.0)
        await ct.record_rejection(B, "base", "BBB", "insufficient_liquidity", 1.0)
        await _backdate(B, days=8.0)

        async def flaky_pair_lookup(contract, *, chain="base"):
            if contract == A:
                raise RuntimeError("panne réseau simulée")
            return PairSnapshot(pair_address="0xpool", price_usd=1.1, liquidity_usd=100_000.0, base_symbol="BBB")

        monkeypatch.setattr(paper_trader, "_default_pair_lookup", flaky_pair_lookup)
        result = await ct.run_revisit_cycle()
        assert result == {"due": 2, "revisited": 2, "price_unavailable": 1}


@pytest.mark.asyncio
class TestSummarizeRevisited:
    async def test_empty_when_nothing_resolved(self):
        summary = await ct.summarize_revisited()
        assert summary == {"resolved_total": 0, "by_reason": {}}

    async def test_segments_by_reject_reason(self):
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.0)
        due_a = await ct.list_due_for_revisit(older_than_days=0)
        await ct.record_revisit(due_a[0]["id"], 1.5)  # +50%

        await ct.record_rejection(B, "base", "BBB", "already_parabolic", 1.0)
        due_b = await ct.list_due_for_revisit(older_than_days=0)
        [row] = [r for r in due_b if r["contract"] == B]
        await ct.record_revisit(row["id"], 0.5)  # -50%

        summary = await ct.summarize_revisited()
        assert summary["resolved_total"] == 2
        assert summary["by_reason"]["insufficient_liquidity"]["count"] == 1
        assert summary["by_reason"]["already_parabolic"]["count"] == 1

    async def test_counts_would_have_gained_50pct_or_more(self):
        await ct.record_rejection(A, "base", "AAA", "insufficient_liquidity", 1.0)
        due = await ct.list_due_for_revisit(older_than_days=0)
        await ct.record_revisit(due[0]["id"], 2.0)  # +100%, >= 50%

        await ct.record_rejection(B, "base", "BBB", "insufficient_liquidity", 1.0)
        due_b = [r for r in await ct.list_due_for_revisit(older_than_days=0) if r["contract"] == B]
        await ct.record_revisit(due_b[0]["id"], 1.1)  # +10%, < 50%

        summary = await ct.summarize_revisited()
        assert summary["by_reason"]["insufficient_liquidity"]["would_have_gained_50pct_or_more"] == 1
        assert summary["by_reason"]["insufficient_liquidity"]["count"] == 2


class TestFormatCounterfactualSummary:
    def test_empty_summary_shows_honest_placeholder(self):
        text = ct.format_counterfactual_summary({"resolved_total": 0, "by_reason": {}})
        assert "Aucun contrefactuel résolu" in text

    def test_populated_summary_shows_reason_stats(self):
        summary = {
            "resolved_total": 3,
            "by_reason": {
                "insufficient_liquidity": {
                    "count": 3, "avg_price_change_pct": 25.0, "median_price_change_pct": 20.0,
                    "would_have_gained_50pct_or_more": 1,
                },
            },
        }
        text = ct.format_counterfactual_summary(summary)
        assert "insufficient_liquidity" in text
        assert "+25.0%" in text
        assert "1 auraient pris" in text
        assert "prudente" in text.lower()  # avertissement taille d'échantillon toujours présent
