import json

import pytest

from aria_core.capability_gap import (
    CAPABILITY_TITLES,
    _recently_filed,
    file_capability_gap,
    format_gap_reply,
)


@pytest.mark.asyncio
async def test_file_capability_gap_dedup(monkeypatch, tmp_path):
    from aria_core import capability_gap as mod

    async def _not_resolved(_cid: str) -> bool:
        return False

    monkeypatch.setattr(mod, "gap_runtime_resolved", _not_resolved)
    monkeypatch.setattr(mod, "_gaps_dir", lambda: tmp_path)
    rec = {
        "capability_id": "x_oauth_write",
        "filed_at": "2099-01-01T12:00:00+00:00",
    }
    (tmp_path / "x_oauth_write.json").write_text(json.dumps(rec), encoding="utf-8")
    monkeypatch.setattr(mod, "_recently_filed", mod._recently_filed)

    out = await file_capability_gap("x_oauth_write", context="again")
    assert out["status"] == "dedup"


@pytest.mark.asyncio
async def test_file_capability_gap_skips_when_resolved(monkeypatch, tmp_path):
    from aria_core import capability_gap as mod

    monkeypatch.setattr(mod, "_gaps_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "_recently_filed", lambda _cid: None)

    async def _resolved(_cid: str) -> bool:
        return True

    monkeypatch.setattr(mod, "gap_runtime_resolved", _resolved)
    out = await file_capability_gap("image_api_key", context="no token")
    assert out["status"] == "skipped_resolved"


@pytest.mark.asyncio
async def test_file_capability_gap_logs_locally_and_notifies(monkeypatch, tmp_path):
    """Ne fait plus JAMAIS d'écriture GitHub ni de délégation à un tiers (10/07) --
    seulement une trace locale + une notification Telegram."""
    from aria_core import capability_gap as mod

    async def _not_resolved(_cid: str) -> bool:
        return False

    notified: list[str] = []

    async def _fake_notify(cid, record, *, lang):
        notified.append(cid)

    monkeypatch.setattr(mod, "gap_runtime_resolved", _not_resolved)
    monkeypatch.setattr(mod, "_gaps_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "_recently_filed", lambda _cid: None)
    monkeypatch.setattr(mod, "_notify_gap", _fake_notify)
    monkeypatch.setattr(mod, "append_memory", lambda *a, **k: None)

    out = await file_capability_gap("image_api_key", context="no token")

    assert out["status"] == "logged"
    assert "issue_url" not in out
    assert "pr_url" not in out
    assert (tmp_path / "image_api_key.json").is_file()
    assert notified == ["image_api_key"]


def test_capability_titles_has_known_entries():
    assert "x_profile_banner" in CAPABILITY_TITLES


def test_format_gap_reply_dedup():
    text = format_gap_reply({"status": "dedup", "capability_id": "x_oauth_write"}, lang="fr")
    assert "signal" in text.lower()


def test_format_gap_reply_logged():
    text = format_gap_reply({"status": "logged", "capability_id": "x_oauth_write"}, lang="fr")
    assert "x_oauth_write" in text
