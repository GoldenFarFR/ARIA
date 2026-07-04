import pytest

from aria_core.capability_gap import (
    CAPABILITY_SPECS,
    _build_spec_markdown,
    _recently_filed,
    file_capability_gap,
    format_gap_reply,
)


def test_build_spec_markdown():
    md = _build_spec_markdown(
        "x_profile_banner",
        context="test context",
        spec=CAPABILITY_SPECS["x_profile_banner"],
    )
    assert "x_profile_banner" in md
    assert "test context" in md
    assert "apply_profile_banner" in md


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
        "issue_url": "https://github.com/GoldenFarFR/aria-sandbox/issues/1",
    }
    (tmp_path / "x_oauth_write.json").write_text(
        __import__("json").dumps(rec), encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_recently_filed", mod._recently_filed)

    out = await file_capability_gap("x_oauth_write", context="again")
    assert out["status"] == "dedup"
    assert "issues/1" in out["issue_url"]


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
async def test_file_capability_gap_local_only(monkeypatch, tmp_path):
    from aria_core import capability_gap as mod

    async def _not_resolved(_cid: str) -> bool:
        return False

    monkeypatch.setattr(mod, "gap_runtime_resolved", _not_resolved)
    monkeypatch.setattr(mod, "_gaps_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "_recently_filed", lambda _cid: None)
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    async def _noop_notify(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "_notify_gap", _noop_notify)
    monkeypatch.setattr("aria_core.capability_gap.append_memory", lambda *a, **k: None)

    out = await file_capability_gap("image_api_key", context="no token")
    assert out["status"] == "local_only"
    assert (tmp_path / "image_api_key.md").is_file()


@pytest.mark.asyncio
async def test_file_capability_gap_github_dedup(monkeypatch, tmp_path):
    from aria_core import capability_gap as mod

    monkeypatch.setattr(mod, "_gaps_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "_recently_filed", lambda _cid: None)
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    async def fake_gh(*_a, **_k):
        return {
            "issue_url": "https://github.com/GoldenFarFR/aria-sandbox/issues/33",
            "issue_number": 33,
            "filed_at": "2026-06-20T14:08:22Z",
        }

    monkeypatch.setattr(mod, "_find_open_github_gap_issue", fake_gh)

    out = await file_capability_gap("identity_anchor", context="dup")
    assert out["status"] == "dedup"
    assert out.get("dedup_source") == "github_open_issue"
    assert "issues/33" in out["issue_url"]


def test_format_gap_reply_fr():
    text = format_gap_reply(
        {"status": "filed", "issue_url": "https://github.com/i/1", "pr_url": "https://github.com/p/1"},
        lang="fr",
    )
    assert "Issue" in text
    assert "PR spec" in text