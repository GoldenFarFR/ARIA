"""Rent-deposit recovery: classification, instruction shape, and refusals."""
from __future__ import annotations

import pytest

from aria_core import solana_rent_recovery as rr

OWNER = "w8e1uR4MjRgz67Lx1hezRrzFa1AePW1m3krDAMqhoAW"
MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
ACCT = "6bU5m6Y1e2q4hkGZR9EJqbxNZQKcSc8QcXWpUqfyZ3xz"


def _account(*, amount: int, state: str = "initialized"):
    return {"state": state, "tokenAmount": {"amount": str(amount), "decimals": 6}}


class TestClassification:
    def test_zero_balance_is_empty(self):
        assert rr._classify(_account(amount=0), value_usd=None) == "empty"

    def test_worthless_holding_is_dust(self):
        assert rr._classify(_account(amount=5_000), value_usd=0.0001) == "dust"

    def test_holding_worth_keeping_is_valued(self):
        assert rr._classify(_account(amount=5_000), value_usd=1.50) == "valued"

    def test_unknown_price_is_valued_never_dust(self):
        """A price feed being down must never authorise a burn."""
        assert rr._classify(_account(amount=5_000), value_usd=None) == "valued"

    def test_frozen_beats_every_other_case(self):
        """Frozen is checked first: a frozen account can be empty and still
        unclosable, so the zero-balance shortcut must not win."""
        assert rr._classify(_account(amount=0, state="frozen"), value_usd=None) == "frozen"

    def test_dust_ceiling_is_far_below_the_deposit(self):
        """Guards the invariant, not the number: burning must never be a way to
        trade a real position for its rent."""
        assert rr.DUST_CEILING_USD < rr.TYPICAL_RENT_LAMPORTS / 1e9 * 10


class TestInstructions:
    def test_empty_account_closes_with_one_instruction(self):
        ixs = rr.build_close_instructions(
            {"case": "empty", "address": ACCT, "mint": MINT, "amount": 0}, OWNER
        )
        assert len(ixs) == 1
        assert bytes(ixs[0].data) == bytes([rr._IX_CLOSE_ACCOUNT])

    def test_dust_account_burns_then_closes(self):
        ixs = rr.build_close_instructions(
            {"case": "dust", "address": ACCT, "mint": MINT, "amount": 1234}, OWNER
        )
        assert len(ixs) == 2
        assert bytes(ixs[0].data) == bytes([rr._IX_BURN]) + (1234).to_bytes(8, "little")
        assert bytes(ixs[1].data) == bytes([rr._IX_CLOSE_ACCOUNT])

    def test_reclaimed_deposit_goes_to_the_owner(self):
        """The destination is structural, not configurable."""
        ixs = rr.build_close_instructions(
            {"case": "empty", "address": ACCT, "mint": MINT, "amount": 0}, OWNER
        )
        destination = ixs[0].accounts[1]
        assert str(destination.pubkey) == OWNER
        assert destination.is_writable

    def test_only_the_owner_signs(self):
        ixs = rr.build_close_instructions(
            {"case": "dust", "address": ACCT, "mint": MINT, "amount": 7}, OWNER
        )
        for ix in ixs:
            signers = [a for a in ix.accounts if a.is_signer]
            assert len(signers) == 1
            assert str(signers[0].pubkey) == OWNER

    @pytest.mark.parametrize("case", ["valued", "frozen"])
    def test_unclosable_cases_raise_rather_than_no_op(self, case):
        with pytest.raises(rr.RentRecoveryError):
            rr.build_close_instructions(
                {"case": case, "address": ACCT, "mint": MINT, "amount": 10}, OWNER
            )

    def test_dust_with_zero_amount_refuses_to_burn(self):
        """A contradictory record is a bug upstream -- refuse, don't improvise."""
        with pytest.raises(rr.RentRecoveryError):
            rr.build_close_instructions(
                {"case": "dust", "address": ACCT, "mint": MINT, "amount": 0}, OWNER
            )


class TestGate:
    def test_closed_by_default(self, monkeypatch):
        monkeypatch.delenv(rr.GATE_ENV, raising=False)
        assert rr.rent_recovery_enabled() is False

    @pytest.mark.parametrize("value", ["", "1", "yes", "TRUE ", "false", "on"])
    def test_only_the_exact_word_true_opens_it(self, value, monkeypatch):
        monkeypatch.setenv(rr.GATE_ENV, value)
        assert rr.rent_recovery_enabled() is (value.strip().lower() == "true")

    def test_gate_is_distinct_from_the_trade_pilot(self):
        """Cleanup being on must never be implied by trading being on."""
        from aria_core import solana_trade_pilot

        assert rr.GATE_ENV != "ARIA_SOLANA_TRADE_PILOT_ENABLED"
        assert hasattr(solana_trade_pilot, "solana_trade_pilot_enabled")


class TestInventory:
    @pytest.mark.asyncio
    async def test_totals_split_by_case_and_frozen_is_reported_as_lost(self):
        payload = {
            "result": {
                "value": [
                    {
                        "pubkey": ACCT,
                        "account": {
                            "lamports": 2_039_280,
                            "data": {"parsed": {"info": {"mint": MINT, **_account(amount=0)}}},
                        },
                    },
                    {
                        "pubkey": "F" * 32,
                        "account": {
                            "lamports": 2_039_280,
                            "data": {
                                "parsed": {
                                    "info": {
                                        "mint": MINT,
                                        **_account(amount=99, state="frozen"),
                                    }
                                }
                            },
                        },
                    },
                ]
            }
        }

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        class _Client:
            async def post(self, *a, **k):
                return _Resp()

        out = await rr.inventory(OWNER, rpc_http_url="http://rpc", client=_Client())
        assert out["totals"]["empty"]["count"] == 1
        assert out["totals"]["frozen"]["count"] == 1
        assert out["reclaimable_lamports"] == 2_039_280
        assert out["lost_to_frozen_lamports"] == 2_039_280

    @pytest.mark.asyncio
    async def test_unreadable_census_raises_never_returns_empty(self):
        """An empty inventory would read as 'nothing to reclaim'."""

        class _Client:
            async def post(self, *a, **k):
                raise RuntimeError("rpc down")

        with pytest.raises(rr.RentRecoveryError):
            await rr.inventory(OWNER, rpc_http_url="http://rpc", client=_Client())

    @pytest.mark.asyncio
    async def test_unrecognised_account_shape_is_skipped_not_guessed(self):
        payload = {"result": {"value": [{"pubkey": ACCT, "account": {"lamports": 1}}]}}

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        class _Client:
            async def post(self, *a, **k):
                return _Resp()

        out = await rr.inventory(OWNER, rpc_http_url="http://rpc", client=_Client())
        assert out["accounts"] == []


class TestReclaimRefusals:
    """Every refusal must land BEFORE a key is loaded or a byte is signed."""

    @pytest.mark.asyncio
    async def test_closed_gate_refuses(self, monkeypatch):
        monkeypatch.delenv(rr.GATE_ENV, raising=False)
        with pytest.raises(rr.RentRecoveryError, match=rr.GATE_ENV):
            await rr.reclaim(
                [{"case": "empty", "address": ACCT, "mint": MINT, "amount": 0,
                  "rent_lamports": 2_039_280}],
                "/nonexistent/key.json",
                rpc_http_url="http://rpc",
            )

    @pytest.mark.asyncio
    async def test_kill_switch_refuses(self, monkeypatch):
        monkeypatch.setenv(rr.GATE_ENV, "true")
        from aria_core import outgoing_pause

        monkeypatch.setattr(outgoing_pause, "is_paused", lambda **kw: True)
        with pytest.raises(rr.RentRecoveryError, match="kill-switch"):
            await rr.reclaim(
                [{"case": "empty", "address": ACCT, "mint": MINT, "amount": 0,
                  "rent_lamports": 2_039_280}],
                "/nonexistent/key.json",
                rpc_http_url="http://rpc",
            )

    @pytest.mark.asyncio
    async def test_custody_pause_also_refuses(self, monkeypatch):
        """The trade pilot checks both pauses; a weaker check here is a bypass."""
        monkeypatch.setenv(rr.GATE_ENV, "true")
        from aria_core import custody_pause, outgoing_pause

        monkeypatch.setattr(outgoing_pause, "is_paused", lambda **kw: False)
        monkeypatch.setattr(custody_pause, "is_paused", lambda **kw: True)
        with pytest.raises(rr.RentRecoveryError, match="kill-switch"):
            await rr.reclaim(
                [{"case": "empty", "address": ACCT, "mint": MINT, "amount": 0,
                  "rent_lamports": 2_039_280}],
                "/nonexistent/key.json",
                rpc_http_url="http://rpc",
            )

    @pytest.mark.asyncio
    async def test_nothing_to_close_is_a_no_op_not_an_error(self, monkeypatch):
        monkeypatch.setenv(rr.GATE_ENV, "true")
        from aria_core import custody_pause, outgoing_pause

        monkeypatch.setattr(outgoing_pause, "is_paused", lambda **kw: False)
        monkeypatch.setattr(custody_pause, "is_paused", lambda **kw: False)
        out = await rr.reclaim([], "/nonexistent/key.json", rpc_http_url="http://rpc")
        assert out == {"status": "ok", "closed": 0, "reclaimed_lamports": 0, "tx": None}


class TestCapacity:
    def test_capacity_is_deposits_not_dollars(self):
        """0.146 SOL spendable: 71 distinct tokens, whatever the trade size."""
        out = rr.projected_capacity(146_451_000)
        assert out["distinct_tokens"] == 71

    def test_zero_balance_holds_nothing_open(self):
        assert rr.projected_capacity(0)["distinct_tokens"] == 0

    def test_negative_balance_does_not_wrap(self):
        assert rr.projected_capacity(-5)["distinct_tokens"] == 0

    def test_zero_rent_refuses_rather_than_dividing(self):
        with pytest.raises(rr.RentRecoveryError):
            rr.projected_capacity(1_000, rent_lamports=0)
