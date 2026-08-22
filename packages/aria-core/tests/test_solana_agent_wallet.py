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
