from pathlib import Path

from aria_core.knowledge.gem_crush_backlog import backlog_items, pending_items_for_aria
from aria_core.skills.gem_crush_critic import (
    item_done_in_prod,
    pick_top_gap,
    plan_micro_release,
    run_critic,
    score_axes,
)
from aria_core.skills.gem_crush_premium import PREMIUM_RELEASE_BUNDLES
from aria_core.skills.gem_crush_skill import GAME_UI_PATH, dry_run_patches, _patches_by_path

VANGUARD_ROOT = Path(__file__).resolve().parents[5] / "aria-vanguard"


def test_backlog_has_aria_pending_items():
    pending = pending_items_for_aria()
    assert len(pending) >= 3
    assert any(i.id == "juice_burst" for i in pending)


def test_score_axes_detects_sprint_assets():
    ui = VANGUARD_ROOT / "src/games/aria-gem-crush/components/GemCrushGame.tsx"
    css = VANGUARD_ROOT / "src/games/aria-gem-crush/gem-crush.css"
    if not ui.is_file() or not css.is_file():
        return
    scores = score_axes(css=css.read_text(encoding="utf-8"), tsx=ui.read_text(encoding="utf-8"), version=38)
    assert scores["polish"] >= 40
    assert scores["progression"] >= 40


def test_pick_top_gap_prefers_low_axis():
    scores = {"juice": 20, "progression": 80, "polish": 90, "audio": 10, "obstacles": 0, "tutorial": 5}
    gap = pick_top_gap(css="", tsx="", scores=scores)
    assert gap is not None
    assert gap.axis in ("juice", "audio", "obstacles", "tutorial")


def test_item_done_detects_combo_trail():
    item = next(i for i in backlog_items() if i.id == "combo_trail")
    tsx = '<div data-combo={comboLabel ? "1" : undefined}>'
    css = "/* v41 combo */ @keyframes sparkle-burst {}"
    assert item_done_in_prod(item, css=css, tsx=tsx) is True


def test_plan_micro_release_max_two_items():
    full = PREMIUM_RELEASE_BUNDLES[41]
    gap = next(i for i in backlog_items() if i.id == "juice_burst")
    micro = plan_micro_release(full, gap, max_items=2)
    assert 1 <= len(micro.items) <= 2


def test_dry_run_patches_v41_ui():
    ui = VANGUARD_ROOT / "src/games/aria-gem-crush/components/GemCrushGame.tsx"
    if not ui.is_file():
        return
    release = PREMIUM_RELEASE_BUNDLES[41]
    micro = plan_micro_release(release, next(i for i in backlog_items() if i.id == "combo_trail"), max_items=2)
    patches = _patches_by_path(micro)
    dry = dry_run_patches({GAME_UI_PATH: ui.read_text(encoding="utf-8")}, patches)
    assert dry.ok


def test_run_critic_returns_gap_and_dry_run():
    ui = VANGUARD_ROOT / "src/games/aria-gem-crush/components/GemCrushGame.tsx"
    css = VANGUARD_ROOT / "src/games/aria-gem-crush/gem-crush.css"
    if not ui.is_file() or not css.is_file():
        return
    css_text = css.read_text(encoding="utf-8")
    ui_text = ui.read_text(encoding="utf-8")
    report = run_critic(
        version=38,
        css=css_text,
        tsx=ui_text,
        file_contents={"src/games/aria-gem-crush/gem-crush.css": css_text, GAME_UI_PATH: ui_text},
        release=PREMIUM_RELEASE_BUNDLES[41],
    )
    assert report.top_gap is not None
    assert report.scores
    assert isinstance(report.dry_run_ok, bool)