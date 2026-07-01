"""Gem Crush Critic v1 — déterministe, sans LLM.

Spec → score axes → 1 gap prioritaire → dry-run ancres.
"""
from __future__ import annotations

from dataclasses import dataclass

from aria_core.knowledge.gem_crush_backlog import BacklogItem, backlog_axes, pending_items_for_aria
from aria_core.skills.gem_crush_skill import (
    CSS_PATH,
    GAME_UI_PATH,
    GemCrushItem,
    GemCrushRelease,
    FilePatch,
    _patches_by_path,
    dry_run_patches,
)


@dataclass(frozen=True)
class CriticReport:
    version: int
    scores: dict[str, int]
    top_gap: BacklogItem | None
    acceptance: tuple[str, ...] = ()
    suggested_files: tuple[str, ...] = ()
    anchor_checks: tuple[tuple[str, bool, str], ...] = ()
    dry_run_ok: bool = True
    dry_run_missing: tuple[tuple[str, str], ...] = ()


def _signal_present(haystack: str, signals: tuple[str, ...]) -> bool:
    if not signals:
        return False
    low = haystack.lower()
    return any(s.lower() in low for s in signals)


def item_done_in_prod(item: BacklogItem, *, css: str, tsx: str) -> bool:
    if item.status in ("done", "completed"):
        return True
    css_ok = _signal_present(css, item.detect_css) if item.detect_css else False
    tsx_ok = _signal_present(tsx, item.detect_tsx) if item.detect_tsx else False
    if item.detect_css and item.detect_tsx:
        return css_ok and tsx_ok
    if item.detect_css:
        return css_ok
    if item.detect_tsx:
        return tsx_ok
    return False


def score_axes(*, css: str, tsx: str, version: int) -> dict[str, int]:
    """Score 0–100 par axe (heuristiques déterministes)."""
    axes = backlog_axes()
    scores: dict[str, int] = {axis: 0 for axis in axes}

    juice_css = ("gem-burst", "sparkle-burst", "score-float", "gem-fall-in", "v41 burst", "v41 combo")
    juice_tsx = ("data-combo", "scorePops", "fallRows")
    scores["juice"] = min(
        100,
        sum(12 for s in juice_css if s.lower() in css.lower())
        + sum(15 for s in juice_tsx if s in tsx),
    )

    prog_css = ("gem-crush__map", "map-node", "map-world")
    prog_tsx = ("LevelMap", "chapterForLevel", "chapters")
    scores["progression"] = min(
        100,
        sum(20 for s in prog_css if s.lower() in css.lower())
        + sum(25 for s in prog_tsx if s in tsx),
    )

    polish_css = ("gem-crush__sprite", "candy", "v40 sprites")
    polish_tsx = ("GemSprite", "gem-crush__cell--sprite")
    scores["polish"] = min(
        100,
        sum(20 for s in polish_css if s.lower() in css.lower())
        + sum(30 for s in polish_tsx if s in tsx),
    )

    audio_tsx = ("useGemAudio", "kind === 'match'", "kind === 'combo'")
    scores["audio"] = min(100, sum(30 for s in audio_tsx if s in tsx))

    obs_css = ("frozen", "ice", "gel", "obstacle")
    obs_tsx = ("frozen", "ice", "obstacle")
    scores["obstacles"] = min(
        100,
        sum(25 for s in obs_css if s.lower() in css.lower())
        + sum(25 for s in obs_tsx if s in tsx),
    )

    tut_css = ("tutorial", "ghost-swap", "hint-arrow")
    tut_tsx = ("showTutorial", "TUTORIAL_KEY")
    scores["tutorial"] = min(
        100,
        sum(25 for s in tut_css if s.lower() in css.lower())
        + sum(35 for s in tut_tsx if s in tsx),
    )

    # Bonus version ship récente
    if version >= 40:
        scores["polish"] = min(100, scores["polish"] + 10)
    if version >= 41:
        scores["juice"] = min(100, scores["juice"] + 5)

    return scores


def pick_top_gap(
    *,
    css: str,
    tsx: str,
    scores: dict[str, int],
) -> BacklogItem | None:
    """Un seul gap : axe le plus faible + item pending aria/shared non déjà en prod."""
    pending = [
        item
        for item in pending_items_for_aria()
        if not item_done_in_prod(item, css=css, tsx=tsx)
    ]
    if not pending:
        return None

    def sort_key(item: BacklogItem) -> tuple[int, int, int]:
        axis_score = scores.get(item.axis, 50)
        return (axis_score, -item.priority, 0)

    return sorted(pending, key=sort_key)[0]


def _item_matches_gap(item: GemCrushItem, gap: BacklogItem) -> bool:
    hay = f"{item.name} {item.css}".lower()
    tokens = [
        gap.id.replace("_", " "),
        gap.title.lower(),
        gap.axis,
        *gap.detect_css,
        *gap.detect_tsx,
    ]
    return any(t.lower() in hay for t in tokens if t)


def plan_micro_release(
    full: GemCrushRelease,
    gap: BacklogItem,
    *,
    max_items: int = 2,
) -> GemCrushRelease:
    """Découpe une release catalogue en micro-lot (1–2 items) ciblant le gap."""
    matched = [item for item in full.items if _item_matches_gap(item, gap)]
    if not matched:
        matched = list(full.items[:max_items])
    else:
        matched = matched[:max_items]
    if not matched:
        matched = list(full.items[:1])
    title = f"{gap.title} — micro"
    return GemCrushRelease(title=title, items=tuple(matched))


def run_critic(
    *,
    version: int,
    css: str,
    tsx: str,
    file_contents: dict[str, str],
    release: GemCrushRelease | None = None,
) -> CriticReport:
    scores = score_axes(css=css, tsx=tsx, version=version)
    gap = pick_top_gap(css=css, tsx=tsx, scores=scores)

    anchor_checks: list[tuple[str, bool, str]] = []
    dry_missing: list[tuple[str, str]] = ()
    dry_ok = True
    suggested: list[str] = []

    if gap and release:
        micro = plan_micro_release(release, gap)
        patches = _patches_by_path(micro)
        suggested = sorted(patches.keys())
        dry = dry_run_patches(file_contents, patches)
        dry_ok = dry.ok
        dry_missing = dry.missing
        for path, patches_list in patches.items():
            content = file_contents.get(path, "")
            for patch in patches_list:
                ok = patch.old in content
                anchor_checks.append((path, ok, patch.old[:72]))

    return CriticReport(
        version=version,
        scores=scores,
        top_gap=gap,
        acceptance=gap.acceptance if gap else (),
        suggested_files=tuple(suggested),
        anchor_checks=tuple(anchor_checks),
        dry_run_ok=dry_ok,
        dry_run_missing=dry_missing,
    )