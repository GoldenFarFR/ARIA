from pathlib import Path

from aria_core.skills.gem_crush_premium import PREMIUM_RELEASE_BUNDLES
from aria_core.skills.gem_crush_skill import (
    DAILY_CSS_PATCHES,
    GAME_UI_PATH,
    MIN_PREMIUM_ITEMS,
    MIN_RELEASE_ITEMS,
    RELEASE_BUNDLES,
    incremental_mode_enabled,
    premium_mode_enabled,
    _append_changelog,
    _bump_marker,
    _build_release_css,
    _build_version_ts,
    _patches_by_path,
    apply_patches,
    dry_run_patches,
    FilePatch,
    format_ship_telegram,
    improve_interval_minutes,
    parse_improve_version,
    release_for_version,
    validate_micro_release_quality,
    validate_release_quality,
)

VANGUARD_ROOT = Path(__file__).resolve().parents[5] / "aria-vanguard"


def test_parse_improve_version():
    css = "/* aria-gem-crush-improve: 1 — polish */\n.gem-crush {}"
    assert parse_improve_version(css) == 1
    assert parse_improve_version(".gem {}") == 0


def test_bump_marker():
    css = "/* aria-gem-crush-improve: 1 */\nbody {}"
    out = _bump_marker(css, 2)
    assert "aria-gem-crush-improve: 2" in out
    assert "aria-gem-crush-improve: 1" not in out


def test_daily_patches_queue():
    assert 8 in DAILY_CSS_PATCHES
    assert 20 in DAILY_CSS_PATCHES


def test_release_bundles_are_grouped():
    release = RELEASE_BUNDLES[21]
    assert len(release.items) >= 5
    assert "Pack juice" in release.title


def test_gameplay_bundle_v25_has_engine_patches():
    release = RELEASE_BUNDLES[25]
    paths = _patches_by_path(release)
    assert "src/games/aria-gem-crush/game/engine.ts" in paths
    assert len(paths["src/games/aria-gem-crush/game/engine.ts"]) == 3


def test_release_for_version_prefers_bundle():
    bundle = release_for_version(21)
    assert bundle is not None
    assert len(bundle.items) >= 5


def test_release_for_version_legacy_fallback():
    legacy = release_for_version(8)
    assert legacy is not None
    assert len(legacy.items) == 1


def test_build_release_css_concatenates_items():
    release = release_for_version(21)
    css = _build_release_css(release, 21)
    assert "aria-gem-crush-release: v21" in css
    assert "gem-fall-bounce" in css
    assert "gem-match-flash" in css


def test_apply_patches_v25_on_local_engine():
    engine = VANGUARD_ROOT / "src/games/aria-gem-crush/game/engine.ts"
    if not engine.is_file():
        return
    release = RELEASE_BUNDLES[25]
    patches = _patches_by_path(release)["src/games/aria-gem-crush/game/engine.ts"]
    out = apply_patches(engine.read_text(encoding="utf-8"), patches)
    assert "aria-gem-crush-v25" in out
    assert "matched.size * 12" in out
    assert "0.5))" in out


def test_apply_patches_v26_on_local_constants():
    constants = VANGUARD_ROOT / "src/games/aria-gem-crush/game/constants.ts"
    if not constants.is_file():
        return
    release = RELEASE_BUNDLES[26]
    patches = _patches_by_path(release)["src/games/aria-gem-crush/game/constants.ts"]
    out = apply_patches(constants.read_text(encoding="utf-8"), patches)
    assert "moves: 32" in out
    assert "750 + (level - 1) * 550" in out


def test_premium_release_v31():
    from aria_core.skills.gem_crush_synthesizer import ensure_min_release_items

    release = ensure_min_release_items(PREMIUM_RELEASE_BUNDLES[31], 31)
    assert len(release.items) >= MIN_RELEASE_ITEMS
    assert "Prestige" in release.title


def test_apply_patches_v31_on_local_game_ui():
    ui = VANGUARD_ROOT / "src/games/aria-gem-crush/components/GemCrushGame.tsx"
    if not ui.is_file():
        return
    release = PREMIUM_RELEASE_BUNDLES[31]
    patches = _patches_by_path(release)[GAME_UI_PATH]
    out = apply_patches(ui.read_text(encoding="utf-8"), patches)
    assert "aria-gem-crush-v31" in out
    assert "Chapitre" in out
    assert "Les gemmes chantent" in out


def test_apply_patches_v37_on_local_game_ui():
    ui = VANGUARD_ROOT / "src/games/aria-gem-crush/components/GemCrushGame.tsx"
    if not ui.is_file():
        return
    release = PREMIUM_RELEASE_BUNDLES[37]
    patches = _patches_by_path(release)[GAME_UI_PATH]
    out = apply_patches(ui.read_text(encoding="utf-8"), patches)
    assert "aria-gem-crush-v37" in out
    assert "BLAST ×3 !" in out
    assert "BOOM ! ★★★" in out


def test_apply_patches_v41_on_local_game_ui():
    """v41 data-combo pré-appliqué par sprint assets — ancre alignée prod."""
    ui = VANGUARD_ROOT / "src/games/aria-gem-crush/components/GemCrushGame.tsx"
    if not ui.is_file():
        return
    release = PREMIUM_RELEASE_BUNDLES[41]
    patches = _patches_by_path(release)[GAME_UI_PATH]
    out = apply_patches(ui.read_text(encoding="utf-8"), patches)
    assert "data-combo={comboLabel" in out


def test_game_ui_no_visible_patch_markers_in_jsx():
    """Les marqueurs // aria-gem-crush-vN dans le JSX s'affichent à l'écran — interdit."""
    ui = VANGUARD_ROOT / "src/games/aria-gem-crush/components/GemCrushGame.tsx"
    if not ui.is_file():
        return
    text = ui.read_text(encoding="utf-8")
    start = text.find("  return (")
    assert start >= 0
    jsx = text[start:]
    for line in jsx.splitlines():
        stripped = line.strip()
        if "// aria-gem-crush-v" not in stripped:
            continue
        assert stripped.startswith("{") or "className=" in stripped or stripped.startswith("//"), (
            f"marqueur patch visible dans JSX: {stripped[:80]}"
        )


def test_premium_catalog_v34_v42_has_items():
    from aria_core.skills.gem_crush_synthesizer import ensure_min_release_items

    for ver in (34, 35, 36, 37, 38, 39, 40, 41, 42):
        release = ensure_min_release_items(PREMIUM_RELEASE_BUNDLES[ver], ver)
        assert len(release.items) >= MIN_RELEASE_ITEMS


def test_premium_catalog_unlimited_v43_plus():
    from aria_core.skills.gem_crush_synthesizer import ensure_min_release_items

    for ver in (43, 48, 55):
        release = ensure_min_release_items(PREMIUM_RELEASE_BUNDLES[ver], ver)
        assert len(release.items) >= MIN_RELEASE_ITEMS


def test_unlimited_releases_default_on(test_settings):
    test_settings.aria_gem_crush_premium_mode = True
    from aria_core.skills.gem_crush_skill import unlimited_releases_enabled

    assert unlimited_releases_enabled()


def test_validate_release_requires_min_10_items(test_settings):
    test_settings.aria_gem_crush_premium_mode = True
    tiny = release_for_version(8)
    assert tiny is not None
    assert validate_release_quality(tiny) is not None
    from aria_core.skills.gem_crush_synthesizer import ensure_min_release_items

    padded = ensure_min_release_items(tiny, 99)
    assert len(padded.items) >= MIN_RELEASE_ITEMS
    assert validate_release_quality(padded) is None


def test_validate_release_quality_rejects_small_bundle(test_settings):
    test_settings.aria_gem_crush_premium_mode = True
    test_settings.aria_gem_crush_incremental_mode = False
    tiny = release_for_version(8)
    assert tiny is not None
    assert validate_release_quality(tiny) is not None


def test_dry_run_detects_missing_anchor():
    patches = {
        "fake.tsx": [FilePatch("fake.tsx", "ANCHOR_MISSING_XYZ", "new")],
    }
    dry = dry_run_patches({"fake.tsx": "content"}, patches)
    assert not dry.ok
    assert dry.missing


def test_incremental_mode_default_on(test_settings):
    test_settings.aria_gem_crush_premium_mode = True
    assert incremental_mode_enabled()


def test_improve_interval_premium_default(test_settings):
    test_settings.aria_gem_crush_premium_mode = True
    test_settings.aria_gem_crush_improve_minutes = 30
    assert premium_mode_enabled()
    assert improve_interval_minutes() == 30


def test_improve_interval_premium_floor_30(test_settings):
    test_settings.aria_gem_crush_premium_mode = True
    test_settings.aria_gem_crush_improve_minutes = 5
    assert improve_interval_minutes() == 30


def test_minutes_since_last_ship():
    from aria_core.skills.gem_crush_skill import minutes_since_last_ship

    text = "export const GEM_CRUSH_UPDATED_AT = '2020-01-01 00:00 UTC'\n"
    mins = minutes_since_last_ship(text)
    assert mins is not None
    assert mins > 1_000_000


def test_format_ship_telegram_fr_bundle():
    msg = format_ship_telegram(
        version=25,
        title="Pack scoring & combos",
        repo="GoldenFarFR/aria-vanguard",
        commit_url="https://github.com/x/commit/abc",
        items=("Score +20%", "Combo ×1.5", "Bonus longs", "Score pop XL"),
        lang="fr",
    )
    assert "v25" in msg
    assert "expédiée" in msg
    assert "recherche concurrence" in msg
    assert "4 améliorations groupées" in msg
    assert "abc" in msg


def test_build_version_ts():
    out = _build_version_ts(27, "Pack audio", "2026-06-20 13:27 UTC")
    assert "GEM_CRUSH_VERSION = 27" in out
    assert "Pack audio" in out


def test_append_changelog_bundle():
    out = _append_changelog(
        "",
        version=25,
        title="Pack scoring",
        ts="2026-06-20",
        items=("A", "B", "C"),
    )
    assert "v25" in out
    assert "A; B; C" in out