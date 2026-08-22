"""End-to-end simulation of the REAL trading cycle on the late-bonding pocket.

Every piece is the pocket's genuine code -- entry recording, the shared exit
rule, the ladder, the persistence -- with only the two money-touching seams
(`execute_fn`, `sell_fn`) replaced by recorders. That is exactly the boundary
real trading crosses, so this proves the wiring without spending anything.

What it exists to catch is the failure the seams were built for: a pocket that
buys for real but simulates its exits, leaving real tokens stranded while the
table reports a clean stop.
"""
from __future__ import annotations

import pytest

from aria_core import solana_late_bonding_shadow as pocket


class FakeSnapshot:
    """Shape `_apply_exit_check` reads. Mirrors the real feed's fields."""

    def __init__(self, price, *, reserve=50_000.0, dex="pumpfun", high=None, low=None):
        self.available = True
        self.price_usd = price
        self.reserve_usd = reserve
        self.dex_id = dex
        self.price_high_since_last_read = high if high is not None else price
        self.price_low_since_last_read = low if low is not None else price


class SellRecorder:
    """Stands in for the wallet. Records what would really have been sold."""

    def __init__(self, *, refuse=False, raises=False):
        self.calls = []
        self.refuse = refuse
        self.raises = raises

    async def __call__(self, mint, fraction, *, chain="solana"):
        self.calls.append({"mint": mint, "fraction": fraction, "chain": chain})
        if self.raises:
            raise RuntimeError("RPC down mid-sell")
        if self.refuse:
            return None
        return {"tx": f"sig{len(self.calls)}", "exit_price": 1.0, "proceeds_usd": 0.1}


@pytest.fixture
async def open_position(tmp_path):
    """A real row, written by the pocket's own schema."""
    db = str(tmp_path / "cycle.db")
    await pocket._ensure_table(db)
    import aiosqlite
    from datetime import datetime, timezone

    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute(
            f"""INSERT INTO {pocket.TABLE}
                (pool_address, token_address, chain, detected_at, entry_price,
                 reserve_usd, remaining_qty, realized_proceeds, peak_price,
                 realistic_entry_price, bonding_progress_at_entry, buy_tx)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("pool1", "mint1", "solana", datetime.now(timezone.utc).isoformat(),
             1.0, 50_000.0, 1.0, 0.0, 1.0, 1.0, 0.80, "sigBUY"),
        )
        await conn.commit()
        row_id = cur.lastrowid
    return db, row_id


async def _row(db, row_id):
    import aiosqlite

    async with aiosqlite.connect(db) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(f"SELECT * FROM {pocket.TABLE} WHERE id = ?", (row_id,))
        return dict(await cur.fetchone())


class TestRealSellIsActuallyCalled:
    @pytest.mark.asyncio
    async def test_a_stop_sells_the_entire_real_holding(self, open_position):
        """The whole point: a closure must sell 100%, not a modelled share."""
        db, row_id = open_position
        seller = SellRecorder()
        row = await _row(db, row_id)

        # -30%: well past HARD_STOP_PCT.
        await pocket._apply_exit_check(
            row, FakeSnapshot(0.70, low=0.70), chain="solana", db_path=db, sell_fn=seller,
        )

        assert len(seller.calls) == 1
        assert seller.calls[0]["fraction"] == 1.0
        assert seller.calls[0]["mint"] == "mint1"
        closed = await _row(db, row_id)
        assert closed["exit_reason"]
        assert "real_tx=sig1" in (closed["exit_detail"] or "")

    @pytest.mark.asyncio
    async def test_a_profit_rung_sells_only_its_share(self, open_position):
        """First rung is 25% at +50%. It must not sell the position."""
        db, row_id = open_position
        seller = SellRecorder()
        row = await _row(db, row_id)

        await pocket._apply_exit_check(
            row, FakeSnapshot(1.60, high=1.60), chain="solana", db_path=db, sell_fn=seller,
        )

        assert len(seller.calls) == 1
        fraction = seller.calls[0]["fraction"]
        assert 0 < fraction < 1.0
        after = await _row(db, row_id)
        assert after["exit_reason"] in (None, "")
        assert after["remaining_qty"] < 1.0

    @pytest.mark.asyncio
    async def test_no_movement_sells_nothing(self, open_position):
        """A check that changes nothing must never touch the wallet."""
        db, row_id = open_position
        seller = SellRecorder()
        row = await _row(db, row_id)

        await pocket._apply_exit_check(
            row, FakeSnapshot(1.02), chain="solana", db_path=db, sell_fn=seller,
        )

        assert seller.calls == []


class TestFailedSellNeverBecomesAClosure:
    """The defect this seam exists to prevent, from both directions."""

    @pytest.mark.asyncio
    async def test_a_refused_sell_leaves_the_position_open(self, open_position):
        db, row_id = open_position
        seller = SellRecorder(refuse=True)
        row = await _row(db, row_id)

        out = await pocket._apply_exit_check(
            row, FakeSnapshot(0.70, low=0.70), chain="solana", db_path=db, sell_fn=seller,
        )

        assert out["closed"] == 0
        still = await _row(db, row_id)
        assert still["exit_reason"] in (None, "")
        assert still["remaining_qty"] == 1.0

    @pytest.mark.asyncio
    async def test_a_raising_sell_leaves_the_position_open(self, open_position):
        db, row_id = open_position
        seller = SellRecorder(raises=True)
        row = await _row(db, row_id)

        out = await pocket._apply_exit_check(
            row, FakeSnapshot(0.70, low=0.70), chain="solana", db_path=db, sell_fn=seller,
        )

        assert out["closed"] == 0
        still = await _row(db, row_id)
        assert still["exit_reason"] in (None, "")

    @pytest.mark.asyncio
    async def test_the_retry_on_the_next_pass_succeeds(self, open_position):
        """A transient failure must not strand the position forever."""
        db, row_id = open_position
        row = await _row(db, row_id)

        await pocket._apply_exit_check(
            row, FakeSnapshot(0.70, low=0.70), chain="solana", db_path=db,
            sell_fn=SellRecorder(refuse=True),
        )
        row = await _row(db, row_id)
        seller = SellRecorder()
        await pocket._apply_exit_check(
            row, FakeSnapshot(0.70, low=0.70), chain="solana", db_path=db, sell_fn=seller,
        )

        assert seller.calls[0]["fraction"] == 1.0
        assert (await _row(db, row_id))["exit_reason"]


class TestSimulationStaysIdentical:
    @pytest.mark.asyncio
    async def test_without_sell_fn_behaviour_is_unchanged(self, open_position):
        """The shadow must keep running byte-identically for everyone else."""
        db, row_id = open_position
        row = await _row(db, row_id)

        out = await pocket._apply_exit_check(
            row, FakeSnapshot(0.70, low=0.70), chain="solana", db_path=db,
        )

        assert out["closed"] == 1
        closed = await _row(db, row_id)
        assert closed["exit_reason"]
        # no sell_fn means no sale was attempted, so no sell tx is recorded
        assert "real_tx" not in (closed["exit_detail"] or "")


class TestEveryExitPathCarriesTheSellSeam:
    """22/08, after a real stranding: the sell seam was wired into the polling
    sweep but NOT into the event-driven path, which then closed a position in
    the table while the wallet still held all its tokens. A seam present on
    one of two exit paths is worse than none -- it reads as covered."""

    def test_both_public_exit_entrypoints_accept_sell_fn(self):
        import inspect

        for name in ("advance_exit_simulation", "advance_position_by_pool"):
            fn = getattr(pocket, name)
            assert "sell_fn" in inspect.signature(fn).parameters, (
                f"{name} can close a position without selling it"
            )

    def test_apply_exit_check_is_the_only_writer(self):
        """Keeps the guarantee auditable: one write path means one place where
        the sell must happen. A second UPDATE elsewhere would silently escape
        every check above."""
        import re

        source = inspect_source()
        writers = [
            m for m in re.findall(r"UPDATE \{TABLE\} SET remaining_qty", source)
        ]
        assert len(writers) == 1, (
            f"{len(writers)} closure-writing statements found; the sell seam only guards one"
        )


def inspect_source() -> str:
    import inspect

    return inspect.getsource(pocket)


class TestReconcileWithChain:
    """The repair path that makes skipping confirmation safe. Untested until
    now, which is exactly the shape of defect this whole night produced: a
    mechanism that exists, is trusted, and was never exercised."""

    @staticmethod
    async def _row(db, row_id):
        import aiosqlite

        async with aiosqlite.connect(db) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(f"SELECT * FROM {pocket.TABLE} WHERE id = ?", (row_id,))
            return dict(await cur.fetchone())

    @staticmethod
    async def _insert(db, *, mint, closed, real=True):
        import aiosqlite
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(db) as conn:
            cur = await conn.execute(
                f"""INSERT INTO {pocket.TABLE}
                    (pool_address, token_address, chain, detected_at, entry_price,
                     reserve_usd, remaining_qty, realized_proceeds, peak_price,
                     exit_reason, exit_detail, last_checked_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("pool", mint, "solana", now, 1.0, 5000.0, 0.0 if closed else 1.0, 0.0, 1.0,
                 "fixed_stop" if closed else None,
                 "real_tx=sig" if real else "simulated", now),
            )
            await conn.commit()
            return cur.lastrowid

    @pytest.mark.asyncio
    async def test_a_buy_that_never_landed_is_cancelled(self, tmp_path):
        db = str(tmp_path / "r.db")
        await pocket._ensure_table(db)
        rid = await self._insert(db, mint="ghost", closed=False)

        async def holdings():
            return {}  # nothing held

        out = await pocket.reconcile_with_chain(holdings_fn=holdings, db_path=db)
        assert out["cancelled"] == 1
        assert (await self._row(db, rid))["exit_reason"] == "buy_never_landed"

    @pytest.mark.asyncio
    async def test_a_sell_that_never_landed_reopens_the_position(self, tmp_path):
        """The FOMO failure: closed in the table, tokens still in the wallet."""
        db = str(tmp_path / "r.db")
        await pocket._ensure_table(db)
        rid = await self._insert(db, mint="stranded", closed=True)

        async def holdings():
            return {"stranded": 7057.0}

        out = await pocket.reconcile_with_chain(holdings_fn=holdings, db_path=db)
        assert out["reopened"] == 1
        row = await self._row(db, rid)
        assert row["exit_reason"] in (None, "")
        assert row["remaining_qty"] == 1.0
        assert row["ladder_done"] == 0.0, "rungs must reset or the retry sells nothing"

    @pytest.mark.asyncio
    async def test_a_consistent_position_is_left_alone(self, tmp_path):
        db = str(tmp_path / "r.db")
        await pocket._ensure_table(db)
        open_id = await self._insert(db, mint="held", closed=False)
        closed_id = await self._insert(db, mint="sold", closed=True)

        async def holdings():
            return {"held": 100.0}

        out = await pocket.reconcile_with_chain(holdings_fn=holdings, db_path=db)
        assert out == {"cancelled": 0, "reopened": 0, "skipped": False}
        assert (await self._row(db, open_id))["exit_reason"] in (None, "")
        assert (await self._row(db, closed_id))["exit_reason"] == "fixed_stop"

    @pytest.mark.asyncio
    async def test_an_unreadable_wallet_reconciles_NOTHING(self, tmp_path):
        """The dangerous failure mode: a hiccup must not cancel every position."""
        db = str(tmp_path / "r.db")
        await pocket._ensure_table(db)
        rid = await self._insert(db, mint="ghost", closed=False)

        async def holdings():
            raise RuntimeError("rpc down")

        out = await pocket.reconcile_with_chain(holdings_fn=holdings, db_path=db)
        assert out["skipped"] is True
        assert out["cancelled"] == 0
        assert (await self._row(db, rid))["exit_reason"] in (None, "")

    @pytest.mark.asyncio
    async def test_none_holdings_also_reconciles_nothing(self, tmp_path):
        db = str(tmp_path / "r.db")
        await pocket._ensure_table(db)
        await self._insert(db, mint="ghost", closed=False)

        async def holdings():
            return None

        out = await pocket.reconcile_with_chain(holdings_fn=holdings, db_path=db)
        assert out["skipped"] is True and out["cancelled"] == 0

    @pytest.mark.asyncio
    async def test_simulated_rows_are_never_touched(self, tmp_path):
        """Only rows carrying a real tx are the wallet's business."""
        db = str(tmp_path / "r.db")
        await pocket._ensure_table(db)
        rid = await self._insert(db, mint="paper", closed=False, real=False)

        async def holdings():
            return {}

        out = await pocket.reconcile_with_chain(holdings_fn=holdings, db_path=db)
        assert out["cancelled"] == 0
        assert (await self._row(db, rid))["exit_reason"] in (None, "")


class TestSellOnlyAppliesToRealPositions:
    """22/08, three minutes after enabling real trading: a SHADOW position hit
    its stop, sell_fn found no tokens, refused, the row stayed open -- and
    every loop retried it several times per second, forever. Simulated rows
    must close in the table exactly as they always did."""

    @pytest.mark.asyncio
    async def test_a_shadow_position_never_calls_sell_fn(self, open_position):
        import aiosqlite

        db, row_id = open_position
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                f"UPDATE {pocket.TABLE} SET buy_tx = NULL WHERE id = ?", (row_id,)
            )
            await conn.commit()
        seller = SellRecorder()
        row = await _row(db, row_id)
        assert not row.get("buy_tx")

        out = await pocket._apply_exit_check(
            row, FakeSnapshot(0.70, low=0.70), chain="solana", db_path=db, sell_fn=seller,
        )

        assert seller.calls == [], "a simulated position must never reach the wallet"
        assert out["closed"] == 1, "and it must still close normally"
        assert (await _row(db, row_id))["exit_reason"]

    @pytest.mark.asyncio
    async def test_a_real_position_still_calls_sell_fn(self, open_position):
        """The other half: the guard must not silence real exits."""
        db, row_id = open_position
        seller = SellRecorder()
        row = await _row(db, row_id)
        await pocket._apply_exit_check(
            row, FakeSnapshot(0.70, low=0.70), chain="solana", db_path=db, sell_fn=seller,
        )
        assert len(seller.calls) == 1 and seller.calls[0]["fraction"] == 1.0
