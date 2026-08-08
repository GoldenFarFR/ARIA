"""Détecteur de mécanisme de burn -- calibré sur des cas réels de cette session
(gitlawb : ~0.33% de supply brûlée cumulée, négligeable malgré un marketing
"flywheel LIVE" -- doit ressortir minor_or_marketing_only, jamais significant)."""
from __future__ import annotations

import pytest

from aria_core.services.blockscout import TokenMetadataResult, TokenTransfer, TokenTransfersResult
from aria_core.skills import burn_mechanism as bm


def _transfer(to_address: str, amount: float | None, timestamp: str | None) -> TokenTransfer:
    return TokenTransfer(
        tx_hash="0xabc", from_address="0xfrom", to_address=to_address,
        token_address="0xtoken", token_symbol="TOK", token_name="Tok",
        amount=amount, timestamp=timestamp,
    )


class _FakeClient:
    def __init__(self, *, metadata: TokenMetadataResult, transfers: TokenTransfersResult):
        self._metadata = metadata
        self._transfers = transfers

    async def get_token_metadata(self, contract: str) -> TokenMetadataResult:
        return self._metadata

    async def get_token_transfers_for_token(self, contract: str, limit: int = 500, *, max_pages: int = 10):
        return self._transfers


BURN_ADDR = "0x000000000000000000000000000000000000dead"


@pytest.mark.asyncio
async def test_unavailable_total_supply_degrades_cleanly():
    client = _FakeClient(
        metadata=TokenMetadataResult(available=False, error="quota GoPlus/Blockscout épuisé"),
        transfers=TokenTransfersResult(available=True, transfers=[]),
    )
    result = await bm.assess_burn_mechanism("0xtoken", client=client)
    assert result.available is False
    assert result.verdict == bm.VERDICT_UNAVAILABLE
    assert result.error == "quota GoPlus/Blockscout épuisé"
    assert "supply totale indisponible" in result.note


@pytest.mark.asyncio
async def test_unavailable_transfers_degrades_cleanly():
    client = _FakeClient(
        metadata=TokenMetadataResult(available=True, total_supply=1_000_000.0, decimals=18),
        transfers=TokenTransfersResult(available=False, error="rate limit"),
    )
    result = await bm.assess_burn_mechanism("0xtoken", client=client)
    assert result.available is False
    assert result.verdict == bm.VERDICT_UNAVAILABLE
    assert result.error == "rate limit"


@pytest.mark.asyncio
async def test_no_transfers_at_all_is_none_detected():
    client = _FakeClient(
        metadata=TokenMetadataResult(available=True, total_supply=1_000_000.0, decimals=18),
        transfers=TokenTransfersResult(available=True, transfers=[]),
    )
    result = await bm.assess_burn_mechanism("0xtoken", client=client)
    assert result.available is True
    assert result.verdict == bm.VERDICT_NONE_DETECTED_IN_WINDOW


@pytest.mark.asyncio
async def test_transfers_present_but_none_to_burn_address_is_none_detected():
    transfers = [
        _transfer("0xrandomwallet1", 100.0, "2026-08-01T10:00:00.000000Z"),
        _transfer("0xrandomwallet2", 200.0, "2026-08-02T10:00:00.000000Z"),
    ]
    client = _FakeClient(
        metadata=TokenMetadataResult(available=True, total_supply=1_000_000.0, decimals=18),
        transfers=TokenTransfersResult(available=True, transfers=transfers),
    )
    result = await bm.assess_burn_mechanism("0xtoken", client=client)
    assert result.verdict == bm.VERDICT_NONE_DETECTED_IN_WINDOW
    assert result.window_start == "2026-08-01T10:00:00.000000Z"
    assert result.window_end == "2026-08-02T10:00:00.000000Z"


@pytest.mark.asyncio
async def test_gitlawb_like_negligible_single_burn_is_marketing_only():
    """Cas réel de calibration : ~0.33% de supply brûlée cumulée, 1 seul
    événement -- ne doit JAMAIS ressortir significant_recurring malgré un
    marketing "flywheel"."""
    total_supply = 100_000_000_000.0  # 100B, comme gitlawb
    burned_amount = 325_740_000.0  # 325.74M, comme le vrai total gitlawb (~0.326%)
    transfers = [
        _transfer(BURN_ADDR, burned_amount, "2026-08-01T10:00:00.000000Z"),
        _transfer("0xrandomwallet", 50.0, "2026-08-02T10:00:00.000000Z"),
    ]
    client = _FakeClient(
        metadata=TokenMetadataResult(available=True, total_supply=total_supply, decimals=18),
        transfers=TokenTransfersResult(available=True, transfers=transfers),
    )
    result = await bm.assess_burn_mechanism("0xtoken", client=client)
    assert result.verdict == bm.VERDICT_MINOR_OR_MARKETING_ONLY
    assert result.burn_events == 1
    assert result.supply_burned_pct_in_window is not None
    assert result.supply_burned_pct_in_window < 1.0


@pytest.mark.asyncio
async def test_significant_and_recurring_burn_is_flagged():
    """Ratio au-dessus du seuil ET réparti sur >= 2 semaines distinctes."""
    total_supply = 1_000_000.0
    transfers = [
        _transfer(BURN_ADDR, 3_000.0, "2026-07-01T10:00:00.000000Z"),  # semaine 1
        _transfer(BURN_ADDR, 3_000.0, "2026-07-15T10:00:00.000000Z"),  # semaine 3
    ]
    client = _FakeClient(
        metadata=TokenMetadataResult(available=True, total_supply=total_supply, decimals=18),
        transfers=TokenTransfersResult(available=True, transfers=transfers),
    )
    result = await bm.assess_burn_mechanism("0xtoken", client=client)
    assert result.verdict == bm.VERDICT_SIGNIFICANT_RECURRING
    assert result.distinct_weeks_with_burn == 2
    assert result.supply_burned_pct_in_window == pytest.approx(0.6, rel=1e-6)


@pytest.mark.asyncio
async def test_high_ratio_but_single_event_stays_marketing_only():
    """Ratio au-dessus du seuil mais UN SEUL événement (jamais récurrent) --
    verrouille la condition ET (ratio ET récurrence), pas OU."""
    total_supply = 1_000_000.0
    transfers = [_transfer(BURN_ADDR, 6_000.0, "2026-07-01T10:00:00.000000Z")]  # 0.6%, 1 event
    client = _FakeClient(
        metadata=TokenMetadataResult(available=True, total_supply=total_supply, decimals=18),
        transfers=TokenTransfersResult(available=True, transfers=transfers),
    )
    result = await bm.assess_burn_mechanism("0xtoken", client=client)
    assert result.verdict == bm.VERDICT_MINOR_OR_MARKETING_ONLY
    assert result.distinct_weeks_with_burn == 1


@pytest.mark.asyncio
async def test_recurring_but_low_ratio_stays_marketing_only():
    """Récurrent (>= 2 semaines) mais ratio sous le seuil -- verrouille la
    condition ET dans l'autre sens."""
    total_supply = 1_000_000_000.0  # 1B -- même montant brûlé, ratio dérisoire
    transfers = [
        _transfer(BURN_ADDR, 3_000.0, "2026-07-01T10:00:00.000000Z"),
        _transfer(BURN_ADDR, 3_000.0, "2026-07-15T10:00:00.000000Z"),
    ]
    client = _FakeClient(
        metadata=TokenMetadataResult(available=True, total_supply=total_supply, decimals=18),
        transfers=TokenTransfersResult(available=True, transfers=transfers),
    )
    result = await bm.assess_burn_mechanism("0xtoken", client=client)
    assert result.verdict == bm.VERDICT_MINOR_OR_MARKETING_ONLY


@pytest.mark.asyncio
async def test_burn_transfer_missing_amount_is_ignored_not_crashed():
    total_supply = 1_000_000.0
    transfers = [
        _transfer(BURN_ADDR, None, "2026-07-01T10:00:00.000000Z"),  # décimales indisponibles
        _transfer(BURN_ADDR, 3_000.0, "2026-07-15T10:00:00.000000Z"),
    ]
    client = _FakeClient(
        metadata=TokenMetadataResult(available=True, total_supply=total_supply, decimals=18),
        transfers=TokenTransfersResult(available=True, transfers=transfers),
    )
    result = await bm.assess_burn_mechanism("0xtoken", client=client)
    assert result.burn_events == 1
    assert result.total_burned == 3_000.0


@pytest.mark.asyncio
async def test_window_truncated_flag_is_propagated():
    client = _FakeClient(
        metadata=TokenMetadataResult(available=True, total_supply=1_000_000.0, decimals=18),
        transfers=TokenTransfersResult(
            available=True,
            transfers=[_transfer("0xrandom", 1.0, "2026-08-01T10:00:00.000000Z")],
            truncated=True,
        ),
    )
    result = await bm.assess_burn_mechanism("0xtoken", client=client)
    assert result.window_truncated is True


def test_iso_week_key_groups_same_week_together():
    # 2026-07-01 et 2026-07-03 sont dans la même semaine ISO
    assert bm._iso_week_key("2026-07-01T10:00:00.000000Z") == bm._iso_week_key("2026-07-03T10:00:00.000000Z")


def test_iso_week_key_distinguishes_different_weeks():
    assert bm._iso_week_key("2026-07-01T10:00:00.000000Z") != bm._iso_week_key("2026-07-15T10:00:00.000000Z")


def test_iso_week_key_invalid_timestamp_is_none():
    assert bm._iso_week_key("not-a-date") is None


def test_span_note_missing_bounds_is_unknown():
    assert bm._span_note(None, None) == "fenêtre temporelle inconnue"


def test_span_note_sub_hour_flags_high_volume():
    note = bm._span_note("2026-08-01T10:00:00.000000Z", "2026-08-01T10:30:00.000000Z")
    assert "< 1h" in note


def test_span_note_multi_day():
    note = bm._span_note("2026-08-01T10:00:00.000000Z", "2026-08-05T10:00:00.000000Z")
    assert "4j" in note
