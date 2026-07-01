from aria_core.knowledge.gem_crush_backlog import backlog_items
from aria_core.skills.gem_crush_premium import PREMIUM_RELEASE_BUNDLES
from aria_core.skills.gem_crush_skill import (
    CSS_PATH,
    GAME_UI_PATH,
    MIN_RELEASE_ITEMS,
    unlimited_releases_enabled,
    resolve_heartbeat_release,
    validate_release_quality,
)
from aria_core.skills.gem_crush_synthesizer import (
    catalog_max_version,
    ensure_min_release_items,
    synthesize_open_release,
    synthesize_release_from_gap,
)


def test_catalog_extended_through_v55():
    assert catalog_max_version() >= 55
    assert 43 in PREMIUM_RELEASE_BUNDLES
    assert 55 in PREMIUM_RELEASE_BUNDLES


def test_synthesize_open_release_has_version_marker():
    release = synthesize_open_release(60)
    assert "v60" in release.title
    assert len(release.items) >= MIN_RELEASE_ITEMS
    assert any("v60" in (item.css or "") for item in release.items)


def test_ensure_min_release_items_pads_catalog():
    from aria_core.skills.gem_crush_premium import PREMIUM_RELEASE_BUNDLES

    raw = PREMIUM_RELEASE_BUNDLES[42]
    assert len(raw.items) < MIN_RELEASE_ITEMS
    padded = ensure_min_release_items(raw, 42)
    assert len(padded.items) == MIN_RELEASE_ITEMS


def test_synthesize_from_gap_obstacles():
    gap = next(i for i in backlog_items() if i.id == "obstacles_ice")
    release = synthesize_release_from_gap(43, gap)
    assert "gelée" in release.title.lower() or "frozen" in release.title.lower()
    assert len(release.items) >= 2


def test_resolve_heartbeat_no_queue_empty_beyond_catalog(test_settings):
    test_settings.aria_gem_crush_premium_mode = True
    test_settings.aria_gem_crush_incremental_mode = True
    test_settings.aria_gem_crush_unlimited_releases = True
    assert unlimited_releases_enabled()

    css = "/* aria-gem-crush-improve: 99 */\n.gem-crush {}"
    tsx = (
        '<div className="gem-crush__board-wrap" data-combo={comboLabel ? \'1\' : undefined}>'
        '<LevelMap level={level} />'
    )
    release, _critic, source = resolve_heartbeat_release(
        100,
        live_version=99,
        css=css,
        tsx=tsx,
        file_contents={CSS_PATH: css, GAME_UI_PATH: tsx},
    )
    assert release is not None
    assert len(release.items) >= MIN_RELEASE_ITEMS
    assert validate_release_quality(release) is None
    assert source in ("synthesis", "open", "catalog")