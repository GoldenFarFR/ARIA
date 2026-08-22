"""Real Solana wallet adapter: sell retries, decimals, caches."""
from __future__ import annotations

import pytest


class TestSellRetriesWithAFreshQuote:
    """22/08, real failure: Jupiter 0x1771 (slippage exceeded) on a sell -- the
    price moved >10% between quote and execution on a collapsing curve.

    Raising the ceiling is not an option (10% is an absolute project rule), so
    the answer is a FRESH quote immediately rather than the same stale one, and
    not a retry a full loop later while the token keeps falling."""

    @pytest.mark.asyncio
    async def test_a_slippage_failure_is_retried_with_a_new_quote(self, monkeypatch):
        from aria_core import solana_agent_wallet as w

        attempts = {"n": 0}

        async def fake_balance(mint, **k):
            return 1_000_000

        async def fake_swap_out(*, mint, amount_units, slippage_bps):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("swap status='failed' (0x1771)")
            return {"tx": "sig", "exit_price": 1.0, "proceeds_usd": 0.1}

        monkeypatch.setattr(w, "token_balance", fake_balance)
        monkeypatch.setattr(w, "_swap_out", fake_swap_out)

        out = await w.execute_real_sell("mint", 1.0)
        assert out and out["tx"] == "sig"
        assert attempts["n"] == 3, "each attempt must re-quote"

    @pytest.mark.asyncio
    async def test_retries_are_bounded(self, monkeypatch):
        """Past a few tries the market moves faster than we can quote it; the
        exit rule retries on its next pass rather than looping here."""
        from aria_core import solana_agent_wallet as w

        attempts = {"n": 0}

        async def fake_balance(mint, **k):
            return 1_000_000

        async def always_fails(**k):
            attempts["n"] += 1
            raise RuntimeError("0x1771")

        monkeypatch.setattr(w, "token_balance", fake_balance)
        monkeypatch.setattr(w, "_swap_out", always_fails)

        assert await w.execute_real_sell("mint", 1.0) is None
        assert attempts["n"] == w._SELL_RETRY_ATTEMPTS

    @pytest.mark.asyncio
    async def test_a_balance_that_vanished_stops_the_retry(self, monkeypatch):
        """If the tokens are gone between attempts, there is nothing to sell --
        selling more than is held fails differently and pointlessly."""
        from aria_core import solana_agent_wallet as w

        balances = iter([1_000_000, 0])

        async def fake_balance(mint, **k):
            return next(balances, 0)

        async def always_fails(**k):
            raise RuntimeError("0x1771")

        monkeypatch.setattr(w, "token_balance", fake_balance)
        monkeypatch.setattr(w, "_swap_out", always_fails)

        assert await w.execute_real_sell("mint", 1.0) is None


class TestAPartialExitIsNeverReportedAsComplete:
    """22/08, found by the operator on his own wallet.

    A liquidation printed "32 sold, 0 unsellable" while three tokens were still
    held, two of them already marked closed in the pocket's table. The sell path
    reported the amount it REQUESTED as the amount sold -- and `out_amount`
    itself came from the Jupiter QUOTE, not from the executed transaction. So a
    partial fill closed the row, stranded the remainder, and recorded a PnL that
    never happened.

    The chain is the only authority on what actually moved."""

    @pytest.fixture(autouse=True)
    def _no_real_sleeping(self, monkeypatch):
        """The settle poll is deliberately short in production; here it is pure
        waiting, so it is removed rather than endured."""
        import asyncio

        from aria_core import solana_agent_wallet as w

        async def instant(_seconds):
            return None

        monkeypatch.setattr(w.asyncio, "sleep", instant)
        assert asyncio is not None  # keeps the import meaningful to linters

    @pytest.mark.asyncio
    async def test_leftover_tokens_mark_the_exit_partial(self, monkeypatch):
        from aria_core import solana_agent_wallet as w

        # sold most of it, a chunk stays behind -- exactly the operator's case
        balances = iter([1_000_000, 400_000])

        async def fake_balance(mint, **k):
            return next(balances, 400_000)

        async def fake_swap_out(*, mint, amount_units, slippage_bps):
            return {"tx": "sig", "exit_price": 1.0, "proceeds_usd": 0.1,
                    "units_requested": amount_units, "quoted": True}

        monkeypatch.setattr(w, "token_balance", fake_balance)
        monkeypatch.setattr(w, "_swap_out", fake_swap_out)

        out = await w.execute_real_sell("mint", 1.0)

        assert out["partial"] is True
        assert out["fully_exited"] is False
        assert out["units_remaining"] == 400_000
        assert out["units_sold"] == 600_000, "sold is measured, not assumed"

    @pytest.mark.asyncio
    async def test_a_clean_exit_is_reported_as_complete(self, monkeypatch):
        from aria_core import solana_agent_wallet as w

        balances = iter([1_000_000, 0])

        async def fake_balance(mint, **k):
            return next(balances, 0)

        async def fake_swap_out(*, mint, amount_units, slippage_bps):
            return {"tx": "sig", "exit_price": 1.0, "proceeds_usd": 0.1,
                    "units_requested": amount_units, "quoted": True}

        monkeypatch.setattr(w, "token_balance", fake_balance)
        monkeypatch.setattr(w, "_swap_out", fake_swap_out)

        out = await w.execute_real_sell("mint", 1.0)

        assert out["partial"] is False
        assert out["fully_exited"] is True
        assert out["units_sold"] == 1_000_000

    @pytest.mark.asyncio
    async def test_an_unreadable_balance_says_unknown_not_done(self, monkeypatch):
        """Unknown must stay unknown. Claiming a clean exit we cannot see is the
        precise failure being fixed -- and claiming a failure we cannot see
        would reopen good exits forever."""
        from aria_core import solana_agent_wallet as w

        calls = {"n": 0}

        async def fake_balance(mint, **k):
            calls["n"] += 1
            return 1_000_000 if calls["n"] == 1 else None

        async def fake_swap_out(*, mint, amount_units, slippage_bps):
            return {"tx": "sig", "exit_price": 1.0, "proceeds_usd": 0.1,
                    "units_requested": amount_units, "quoted": True}

        monkeypatch.setattr(w, "token_balance", fake_balance)
        monkeypatch.setattr(w, "_swap_out", fake_swap_out)

        out = await w.execute_real_sell("mint", 1.0)

        assert out["fully_exited"] is None
        assert out["partial"] is None
        assert out["units_sold"] is None

    @pytest.mark.asyncio
    async def test_the_quote_is_never_presented_as_the_executed_amount(self, monkeypatch):
        """`_swap_out` may only report what it ASKED for, flagged as quoted.
        The old key name `units_sold` was the claim that caused the incident."""
        from aria_core import solana_agent_wallet as w

        async def fake_quote(*a, **k):
            return {"outAmount": 5_000, "slippage_bps_used": 1_000}

        async def fake_execute(*a, **k):
            return {"status": "ok", "tx": "sig", "out_amount": 5_000}

        async def fake_decimals(mint, **k):
            return 6

        async def fake_sol_usd(**k):
            return 200.0

        monkeypatch.setattr(w.jupiter, "fetch_quote", fake_quote)
        monkeypatch.setattr(w.jupiter_swap_signer, "execute_swap", fake_execute)
        monkeypatch.setattr(w, "token_decimals", fake_decimals)
        monkeypatch.setattr(w, "sol_usd_cached", fake_sol_usd)

        fill = await w._swap_out(mint="mint", amount_units=1_000, slippage_bps=1_000)

        assert "units_sold" not in fill, "only the chain may say what was sold"
        assert fill["units_requested"] == 1_000
        assert fill["quoted"] is True
