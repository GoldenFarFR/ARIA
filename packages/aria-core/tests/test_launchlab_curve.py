"""Raydium LaunchLab curve decoding -- the engine behind LetsBonk.

The fixtures below are REAL account bytes read on-chain on 23/08, kept verbatim
rather than synthesised: the first decoding attempt failed precisely because
plausible-looking made-up numbers cannot reveal that a layout is wrong.
"""
from __future__ import annotations

import pytest

from aria_core.services import launchlab_curve as lab


def _account(*, virtual_a: int, virtual_b: int, real_a: int, real_b: int,
             target_b: int = 85_000_000_000, dec_a: int = 6, dec_b: int = 9,
             status: int = 0, size: int = lab.ACCOUNT_SIZE,
             discriminator: bytes = lab.DISCRIMINATOR) -> bytes:
    raw = bytearray(size)
    raw[0:8] = discriminator
    raw[lab.OFF_STATUS] = status
    raw[lab.OFF_DECIMALS_A] = dec_a
    raw[lab.OFF_DECIMALS_B] = dec_b
    for off, val in ((lab.OFF_VIRTUAL_A, virtual_a), (lab.OFF_VIRTUAL_B, virtual_b),
                     (lab.OFF_REAL_A, real_a), (lab.OFF_REAL_B, real_b),
                     (lab.OFF_TOTAL_FUND_RAISING_B, target_b)):
        raw[off:off + 8] = val.to_bytes(8, "little")
    return bytes(raw)


# Pool Gt21e6YYVc, read on-chain 23/08. DexScreener reported 3.295e-08 SOL per
# token at that moment; the decoded state must reproduce it.
POOL_REEL = dict(virtual_a=1_073_025_605_596_382, virtual_b=30_000_852_951,
                 real_a=84_600_425_050_725, real_b=2_567_807_319)
PRIX_REFERENCE = 3.295e-08


def test_the_decoded_price_matches_the_public_one():
    """The whole point: our number and the market's must be the same number.

    Blind decoding produced three candidate fields and NO pair of them
    reproduced this price -- the formula is not a ratio of two virtual reserves
    the way pump.fun's is, the real amounts move them. The layout came from
    Raydium SDK v2, and this test is what proves the layout was read right.
    """
    pool = lab.decode_pool(_account(**POOL_REEL))
    assert pool is not None
    prix = lab.price_in_quote(pool)
    assert prix == pytest.approx(PRIX_REFERENCE, rel=0.001), (
        f"decoded {prix}, market said {PRIX_REFERENCE}"
    )


def test_a_wrong_sized_or_wrong_typed_account_is_refused():
    """Returning None beats returning numbers that look plausible.

    A layout change would shift every field; decoding anyway would yield prices
    that are wrong without looking wrong, which is how a pocket ends up trading
    on fiction.
    """
    assert lab.decode_pool(_account(**POOL_REEL, size=200)) is None
    assert lab.decode_pool(_account(**POOL_REEL,
                                    discriminator=bytes(8))) is None
    assert lab.decode_pool(b"") is None
    assert lab.decode_pool(None) is None


def test_progress_is_measured_on_the_quote_side():
    """Migration triggers on SOL raised, not on tokens sold."""
    pool = lab.decode_pool(_account(**{**POOL_REEL, "real_b": 42_500_000_000}))
    assert lab.progress(pool) == pytest.approx(0.5, abs=0.001)
    plein = lab.decode_pool(_account(**{**POOL_REEL, "real_b": 85_000_000_000}))
    assert lab.progress(plein) == pytest.approx(1.0)
    # never above 1.0, even if the pool overshoots its target
    trop = lab.decode_pool(_account(**{**POOL_REEL, "real_b": 99_000_000_000}))
    assert lab.progress(trop) == 1.0


def test_a_drained_base_side_yields_no_price_rather_than_a_division_error():
    vide = lab.decode_pool(_account(**{**POOL_REEL,
                                       "real_a": POOL_REEL["virtual_a"]}))
    assert lab.price_in_quote(vide) is None
