"""X interaction kill-switch (24/08) -- /offx and /onx on Telegram. Distinct
from outgoing_pause (real capital AND X, the absolute switch) and from
paper_pause (paper-trading scanning only): this one is X-only, and fails
CLOSED (opposite of paper_pause's fail-open) because a tweet/like/profile
sync posted by mistake is irreversible and public.
"""
import json

from aria_core import x_pause
from aria_core.paths import configure_data_dir


def test_default_not_paused(tmp_path):
    configure_data_dir(tmp_path)
    assert x_pause.is_paused() is False
    st = x_pause.pause_status()
    assert st["paused"] is False
    assert st["since"] is None


def test_pause_then_resume(tmp_path):
    configure_data_dir(tmp_path)
    x_pause.pause(by=12345, reason="silence X during debugging")
    assert x_pause.is_paused() is True
    st = x_pause.pause_status()
    assert st["paused"] is True
    assert st["by"] == 12345
    assert st["reason"] == "silence X during debugging"
    assert st["since"] is not None

    x_pause.resume(by=12345)
    assert x_pause.is_paused() is False
    assert x_pause.pause_status()["since"] is None


def test_state_persists_on_disk_own_file(tmp_path):
    configure_data_dir(tmp_path)
    x_pause.pause(by=1)
    state_file = tmp_path / "x_pause_state.json"
    assert state_file.exists()
    assert not (tmp_path / "paper_pause_state.json").exists()
    assert not (tmp_path / "custody_pause_state.json").exists()
    assert not (tmp_path / "pause_state.json").exists()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["paused"] is True


def test_corrupt_file_fails_closed(tmp_path):
    """Opposite doctrine from paper_pause: an unreadable state must default
    to blocking X, never to silently keeping it live."""
    configure_data_dir(tmp_path)
    (tmp_path / "x_pause_state.json").write_text("{ not valid json", encoding="utf-8")
    assert x_pause.is_paused() is True
    assert x_pause.pause_status()["readable"] is False


def test_never_shares_state_with_other_pauses(tmp_path):
    from aria_core import custody_pause, outgoing_pause, paper_pause

    configure_data_dir(tmp_path)
    outgoing_pause.pause(by="manual-owner")
    custody_pause.pause(by="auto:agent_wallet_monitor")
    paper_pause.pause(by="owner")
    assert x_pause.is_paused() is False  # none of the other three touch this one

    outgoing_pause.resume(by="manual-owner")
    custody_pause.resume(by="auto:agent_wallet_monitor")
    paper_pause.resume(by="owner")
    x_pause.pause(by="owner")
    assert outgoing_pause.is_paused() is False
    assert custody_pause.is_paused() is False
    assert paper_pause.is_paused() is False


def test_post_tweet_honors_x_pause(tmp_path, monkeypatch):
    """The exact mechanism /offx must control -- verified at the real call
    site, not just the module in isolation."""
    configure_data_dir(tmp_path)
    import asyncio

    from aria_core.gateway import x_twitter

    x_pause.pause(by="owner")
    result, notice = asyncio.run(x_twitter.post_tweet("hello"))
    assert result is None
    assert "pause" in notice.lower()

    x_pause.resume(by="owner")


def test_like_tweet_honors_x_pause(tmp_path):
    configure_data_dir(tmp_path)
    from aria_core.gateway import x_engagement

    x_pause.pause(by="owner")
    assert x_engagement._like_tweet_sync("u1", "t1") is False
    x_pause.resume(by="owner")


def test_reply_tweet_honors_x_pause(tmp_path):
    configure_data_dir(tmp_path)
    import asyncio

    from aria_core.gateway import x_twitter

    x_pause.pause(by="owner")
    reply_id, notice = asyncio.run(
        x_twitter.reply_to_tweet("salut", in_reply_to_tweet_id="123")
    )
    assert reply_id is None
    assert "pause" in notice.lower()
    x_pause.resume(by="owner")


def test_profile_writes_honor_x_pause(tmp_path):
    """Moved from test_outgoing_pause.py (24/08): these three writes only
    ever honored outgoing_pause by coincidence (no X credentials in test ->
    they fail regardless of pause state) -- x_pause is the real, sole gate
    now (see x_twitter.py call sites)."""
    configure_data_dir(tmp_path)
    import asyncio
    from pathlib import Path

    from aria_core.gateway.x_twitter import (
        apply_profile_banner,
        apply_profile_image,
        apply_x_profile_fields,
    )

    x_pause.pause(by="owner")
    assert asyncio.run(apply_profile_image(Path("x.png"))) is False
    assert asyncio.run(apply_x_profile_fields({"name": "X"})) is False
    assert asyncio.run(apply_profile_banner(Path("x.png"))) is False
    x_pause.resume(by="owner")
