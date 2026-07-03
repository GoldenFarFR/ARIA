"""Tests vision KART ouvrier."""
from __future__ import annotations

from pathlib import Path

from ouvrier_vision import (
    build_image_context,
    direct_image_reply,
    extract_image_paths,
    resolve_image_path,
    wants_image_context,
)

SCREENSHOT = Path(
    r"C:\Users\Studi\Pictures\Screenpresso\2026-07-03_18h32_03.png"
)


def test_extract_windows_path():
    msg = (
        r"C:\Users\Studi\Pictures\Screenpresso\2026-07-03_18h32_03.png "
        "RED FLAG urgent regle sa"
    )
    paths = extract_image_paths(msg)
    assert len(paths) == 1
    assert paths[0].endswith("18h32_03.png")


def test_wants_image_reference_with_session():
    session = {"last_image_path": str(SCREENSHOT)}
    assert wants_image_context("tu a pu lire limage ?", session)


def test_resolve_from_session():
    session = {"last_image_path": str(SCREENSHOT)}
    if SCREENSHOT.is_file():
        path = resolve_image_path("tu a pu lire limage ?", session)
        assert path == SCREENSHOT.resolve()


def test_build_context_detects_path():
    if not SCREENSHOT.is_file():
        return
    block, img, analysis = build_image_context(
        f"{SCREENSHOT} que vois-tu ?",
        {},
    )
    assert img
    assert "ANALYSE IMAGE" in block or "vision indisponible" in block.lower()


def test_direct_image_reply():
    reply = direct_image_reply(
        "tu a pu lire limage ?",
        "Capture terminal ARIA avec fallback Groq 429.",
        str(SCREENSHOT),
    )
    assert reply
    assert "Oui" in reply
    assert "18h32_03.png" in reply