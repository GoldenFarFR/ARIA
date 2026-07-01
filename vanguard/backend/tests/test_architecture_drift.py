"""CI guard — cerveau ARIA = aria-sandbox, pas app/aria legacy host."""
from pathlib import Path


def test_app_aria_removed():
    legacy = Path(__file__).resolve().parents[1] / "app" / "aria"
    assert not legacy.is_dir(), f"SSOT violation: {legacy} must not exist"


def test_requirements_install_aria_core():
    import re

    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    text = req.read_text(encoding="utf-8")
    assert "aria-core" in text
    assert "aria-sandbox" in text
    assert re.search(
        r"aria-sandbox\.git@[a-f0-9]{40}#subdirectory=packages/aria-core",
        text,
    ), "aria-core must be pinned to a full git SHA for reproducible deploys"