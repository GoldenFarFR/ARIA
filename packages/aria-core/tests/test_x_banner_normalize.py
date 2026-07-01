"""Bannière X — normalisation 3:1 (≠ avatar carré)."""

from io import BytesIO

from PIL import Image

from aria_core.x_banner import (
    BANNER_HEIGHT,
    BANNER_MAX_BYTES,
    BANNER_WIDTH,
    normalize_banner_jpeg,
)


def _jpeg_bytes(size: tuple[int, int], color: tuple[int, int, int] = (40, 40, 40)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_normalize_banner_landscape_to_3_1():
    out = normalize_banner_jpeg(_jpeg_bytes((2400, 800)))
    img = Image.open(BytesIO(out))
    assert img.size == (BANNER_WIDTH, BANNER_HEIGHT)
    assert abs(img.size[0] / img.size[1] - 3.0) < 0.01


def test_normalize_banner_portrait_crops_height():
    out = normalize_banner_jpeg(_jpeg_bytes((1200, 1600)))
    img = Image.open(BytesIO(out))
    assert img.size == (BANNER_WIDTH, BANNER_HEIGHT)


def test_normalize_banner_square_crops_to_wide():
    out = normalize_banner_jpeg(_jpeg_bytes((1000, 1000)))
    img = Image.open(BytesIO(out))
    assert img.size == (BANNER_WIDTH, BANNER_HEIGHT)
    assert img.size[0] > img.size[1]


def test_normalize_banner_under_x_max_size():
    out = normalize_banner_jpeg(_jpeg_bytes((4000, 2000)))
    assert len(out) <= BANNER_MAX_BYTES