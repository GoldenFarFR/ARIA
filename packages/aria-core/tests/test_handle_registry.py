import pytest

from aria_core.handle_registry import (
    REGISTRY_PATH,
    add_handle,
    format_registry_help,
    format_registry_short,
    mentions_for_pack,
    remove_handle,
    resolve_handles_in_text,
    set_alias,
)


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    path = tmp_path / "x_handle_registry.json"
    monkeypatch.setattr("aria_core.handle_registry.REGISTRY_PATH", path)
    monkeypatch.setattr("aria_core.handle_registry.data_dir", lambda: tmp_path)
    from aria_core.handle_registry import _all_aliases, _merged_handles

    _merged_handles.cache_clear()
    _all_aliases.cache_clear()


def test_resolve_alias_veille():
    out = resolve_handles_in_text("Question du jour @veille")
    assert "@solvrbot" in out
    assert "@grok" in out
    assert "@veille" not in out


def test_plus_pack_suffix():
    out = resolve_handles_in_text("Hello world +holding")
    assert "@GoldenFarFR" in out
    assert "+holding" not in out


def test_add_and_remove_handle():
    add_handle("TestAgent123", role="test")
    out = resolve_handles_in_text("@TestAgent123")
    assert "@TestAgent123" in out
    remove_handle("TestAgent123")


def test_custom_alias():
    set_alias("amis", ["solvrbot", "grok"])
    out = resolve_handles_in_text("cc @amis")
    assert "@solvrbot" in out and "@grok" in out


def test_mentions_for_pack():
    m = mentions_for_pack("holding")
    assert m == "@GoldenFarFR"


def test_format_registry_short_lists_aliases():
    text = format_registry_short()
    assert "@holding" in text
    assert "@veille" in text
    assert "/handles" in text


def test_format_registry_help_lists_builtin_aliases():
    text = format_registry_help()
    assert "holding" in text
    assert "veille" in text
    assert "@solvrbot" in text