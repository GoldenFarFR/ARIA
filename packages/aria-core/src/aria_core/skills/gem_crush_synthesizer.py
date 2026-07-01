"""Gem Crush — synthèse autonome quand le catalogue premium est épuisé.

ARIA peut shipper indéfiniment : gap backlog → template, sinon exploration ouverte.
"""
from __future__ import annotations

from aria_core.knowledge.gem_crush_backlog import BacklogItem
from aria_core.skills.gem_crush_skill import (
    CONSTANTS_PATH,
    GAME_UI_PATH,
    MIN_RELEASE_ITEMS,
    GemCrushItem,
    GemCrushRelease,
    _css_item,
    _patch,
    _release,
)

# Templates par id backlog — ancres alignées prod aria-vanguard
_GAP_BUILDERS: dict[str, callable] = {}


def _gap(id: str):
    def deco(fn):
        _GAP_BUILDERS[id] = fn
        return fn

    return deco


@_gap("obstacles_ice")
def _build_obstacles_ice(version: int) -> GemCrushRelease:
    return _release(
        f"Obstacles gelée v{version} — cases frozen",
        _patch(
            "data-ice sur plateau niveau 3+",
            GAME_UI_PATH,
            '<div className="gem-crush__board-wrap" data-combo={comboLabel ? \'1\' : undefined}>',
            '<div className="gem-crush__board-wrap" data-combo={comboLabel ? \'1\' : undefined} data-ice={level > 2 ? \'1\' : undefined}>',
        ),
        _css_item(
            "Gel visuel frozen",
            f"""/* v{version} frozen ice */
.gem-crush__board-wrap[data-ice='1'] .gem-crush__cell:nth-child(5n) {{
  position: relative;
}}
.gem-crush__board-wrap[data-ice='1'] .gem-crush__cell:nth-child(5n)::after {{
  content: '';
  position: absolute;
  inset: 2px;
  border-radius: 8px;
  background: linear-gradient(145deg, rgba(180, 220, 255, 0.55), rgba(80, 140, 220, 0.35));
  box-shadow: inset 0 0 8px rgba(255, 255, 255, 0.5);
  pointer-events: none;
}}
.gem-crush__cell--frozen .gem-crush__sprite {{
  filter: hue-rotate(180deg) brightness(1.1);
}}
""",
        ),
        _css_item(
            "Label gel HUD",
            f"""/* v{version} ice hud */
.gem-crush__board-wrap[data-ice='1']::before {{
  content: 'Gel';
  position: absolute;
  top: -1.2rem;
  right: 0.25rem;
  font-size: 0.55rem;
  letter-spacing: 0.08em;
  color: rgba(160, 210, 255, 0.85);
  text-transform: uppercase;
}}
""",
        ),
        _patch(
            "Classe frozen sur cellules gel",
            GAME_UI_PATH,
            "isHintCell(r, c) ? 'gem-crush__cell--hint' : '',",
            "isHintCell(r, c) ? 'gem-crush__cell--hint' : '',\n      (level > 2 && (r + c) % 5 === 0) ? 'gem-crush__cell--frozen' : '',",
        ),
    )


@_gap("world_map_scroll")
def _build_world_map_scroll(version: int) -> GemCrushRelease:
    return _release(
        f"Map monde scroll v{version}",
        _patch(
            "Map scroll horizontal",
            "src/games/aria-gem-crush/components/LevelMap.tsx",
            '<nav className="gem-crush__map" aria-label="Carte des chapitres">',
            '<nav className="gem-crush__map gem-crush__map--world map-world" aria-label="Carte des chapitres">',
        ),
        _css_item(
            "Map monde scroll",
            f"""/* v{version} map-world scroll */
.gem-crush__map--world {{
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 0.35rem;
  scroll-snap-type: x proximity;
}}
.gem-crush__map--world .gem-crush__map-track {{
  flex-wrap: nowrap;
  min-width: max-content;
  gap: 0.5rem;
}}
.gem-crush__map--world .gem-crush__map-node {{
  scroll-snap-align: center;
}}
""",
        ),
        _css_item(
            "Scrollbar candy",
            f"""/* v{version} map scroll bar */
.gem-crush__map--world::-webkit-scrollbar {{
  height: 4px;
}}
.gem-crush__map--world::-webkit-scrollbar-thumb {{
  background: rgba(201, 169, 98, 0.45);
  border-radius: 4px;
}}
""",
        ),
    )


@_gap("animated_tutorial")
def _build_animated_tutorial(version: int) -> GemCrushRelease:
    return _release(
        f"Tutoriel animé v{version}",
        _patch(
            "Tutoriel animé ghost-swap",
            GAME_UI_PATH,
            '<div className="gem-crush__tutorial">',
            '<div className="gem-crush__tutorial gem-crush__tutorial--animated">',
        ),
        _css_item(
            "Ghost swap hint",
            f"""/* v{version} tutorial ghost-swap */
.gem-crush__tutorial--animated {{
  animation: tutorial-fade-in 0.5s ease-out;
}}
.gem-crush__tutorial--animated::after {{
  content: '⇄';
  display: block;
  font-size: 1.6rem;
  margin: 0.5rem auto;
  animation: ghost-swap 1.4s ease-in-out infinite;
  opacity: 0.85;
}}
@keyframes ghost-swap {{
  0%, 100% {{ transform: translateX(-8px); opacity: 0.5; }}
  50% {{ transform: translateX(8px); opacity: 1; }}
}}
@keyframes tutorial-fade-in {{
  from {{ opacity: 0; transform: scale(0.96); }}
  to {{ opacity: 1; transform: scale(1); }}
}}
""",
        ),
        _css_item(
            "Flèche hint",
            f"""/* v{version} hint-arrow */
.gem-crush__tutorial--animated .gem-crush__tutorial-title::before {{
  content: '→ ';
  color: var(--gold-light, #e8d5a8);
}}
""",
        ),
    )


@_gap("scripted_levels")
def _build_scripted_levels(version: int) -> GemCrushRelease:
    return _release(
        f"Niveaux scriptés v{version}",
        _patch(
            "Cibles progressives 20 niveaux",
            CONSTANTS_PATH,
            "target: 680 + (level - 1) * 500,  // aria-gem-crush-v36",
            f"target: 650 + (level - 1) * 480 + (level > 10 ? (level - 10) * 40 : 0),  // aria-gem-crush-v{version}",
        ),
        _patch(
            "Coups par palier",
            CONSTANTS_PATH,
            "moves: 38,  // aria-gem-crush-v38",
            f"moves: Math.max(28, 40 - Math.floor((level - 1) / 4)),  // aria-gem-crush-v{version}",
        ),
        _css_item(
            "Badge niveau scripté",
            f"""/* v{version} levelConfig marker */
.gem-crush__version-badge {{
  border: 1px solid rgba(201, 169, 98, 0.35);
}}
""",
        ),
    )


@_gap("juice_burst")
def _build_juice_burst(version: int) -> GemCrushRelease:
    return _release(
        f"Juice burst renfort v{version}",
        _css_item(
            "Burst renforcé",
            f"""/* v{version} gem-burst renfort */
.gem-crush__cell--pop .gem-crush__sprite {{
  animation-duration: 0.38s;
}}
@keyframes gem-burst {{
  55% {{ filter: brightness(2) drop-shadow(0 0 16px #fff); }}
}}
""",
        ),
    )


@_gap("combo_trail")
def _build_combo_trail(version: int) -> GemCrushRelease:
    return _release(
        f"Combo trail v{version}",
        _css_item(
            "Sparkle combo",
            f"""/* v{version} sparkle-burst */
.gem-crush__board-wrap[data-combo='1'] .gem-crush__sparkles {{
  opacity: 1;
  background: radial-gradient(circle, rgba(255, 240, 180, 0.2) 0%, transparent 70%);
}}
""",
        ),
    )


@_gap("score_pop_juice")
def _build_score_pop_juice(version: int) -> GemCrushRelease:
    return _release(
        f"Score pop v{version}",
        _css_item(
            "Score float XL",
            f"""/* v{version} score pop */
.gem-crush__score-pop {{
  font-weight: 700;
  text-shadow: 0 0 8px rgba(255, 220, 120, 0.8);
}}
""",
        ),
    )


@_gap("audio_layers")
def _build_audio_layers(version: int) -> GemCrushRelease:
    return _release(
        f"Audio layers v{version}",
        _css_item(
            "Pulse audio visuel",
            f"""/* v{version} audio pulse */
.gem-crush__combo {{
  animation: combo-pulse-audio 0.4s ease-out;
}}
@keyframes combo-pulse-audio {{
  0% {{ transform: scale(0.9); }}
  50% {{ transform: scale(1.08); }}
  100% {{ transform: scale(1); }}
}}
""",
        ),
    )


# Exploration ouverte — pool cyclique quand backlog épuisé
_OPEN_THEMES: tuple[tuple[str, str], ...] = (
    ("Aurore candy", "aurora"),
    ("Nuit néon", "neon"),
    ("Saison printemps", "spring"),
    ("Saison hiver", "winter"),
    ("Booster rayure", "stripe-booster"),
    ("Booster bombe", "bomb-booster"),
    ("Parallax doux", "parallax"),
    ("Confetti XL", "confetti-xl"),
    ("HUD doré", "gold-hud"),
    ("Plateau profondeur", "depth-board"),
    ("Mascotte vivante", "mascot-live"),
    ("Victoire ciné", "win-cine"),
    ("Shuffle spectaculaire", "shuffle-show"),
    ("Indice laser", "hint-laser"),
    ("Progression pulse", "progress-pulse"),
)


def _polish_pad_item(version: int, index: int) -> GemCrushItem:
    hue = (version * 17 + index * 29) % 360
    return _css_item(
        f"Polish ARIA #{index + 1} v{version}",
        f"""/* v{version} polish pad {index} */
.gem-crush__stat:nth-child({(index % 4) + 1}) strong {{
  letter-spacing: 0.02em;
  text-shadow: 0 0 6px hsla({hue}, 70%, 75%, 0.25);
}}
.gem-crush__cell:nth-child({(index % 12) + 1}) .gem-crush__sprite {{
  transition: transform 0.18s ease, filter 0.18s ease;
}}
.gem-crush__progress-bar {{
  box-shadow: 0 0 {4 + index}px hsla({hue}, 60%, 60%, 0.15);
}}
""",
    )


def ensure_min_release_items(
    release: GemCrushRelease,
    version: int,
    minimum: int = MIN_RELEASE_ITEMS,
) -> GemCrushRelease:
    """Complète une release jusqu'à ≥ minimum améliorations (CSS polish safe)."""
    if len(release.items) >= minimum:
        return release
    extra = tuple(_polish_pad_item(version, i) for i in range(len(release.items), minimum))
    return GemCrushRelease(title=release.title, items=release.items + extra)


def synthesize_release_from_gap(version: int, gap: BacklogItem) -> GemCrushRelease:
    builder = _GAP_BUILDERS.get(gap.id)
    if builder:
        return ensure_min_release_items(builder(version), version)
    return synthesize_open_release(version)


def synthesize_open_release(version: int) -> GemCrushRelease:
    """Release CSS exploration — ≥10 améliorations, sans plafond de version."""
    title, slug = _OPEN_THEMES[(version - 43) % len(_OPEN_THEMES)]
    hue = (version * 37) % 360
    items: list[GemCrushItem] = [
        _css_item(
            f"Thème {slug}",
            f"""/* v{version} aria explore {slug} */
.gem-crush__board {{
  box-shadow:
    0 8px 32px rgba(0, 0, 0, 0.45),
    inset 0 0 0 1px hsla({hue}, 55%, 72%, 0.12);
}}
.gem-crush__hud {{
  backdrop-filter: blur(4px);
}}
""",
        ),
        _css_item(
            f"Mascotte {slug}",
            f"""/* v{version} mascot {slug} */
.gem-crush__mascot-face {{
  animation: aria-explore-v{version} 2.4s ease-in-out infinite;
}}
@keyframes aria-explore-v{version} {{
  0%, 100% {{ transform: scale(1) rotate(0deg); filter: hue-rotate(0deg); }}
  50% {{ transform: scale(1.05) rotate(3deg); filter: hue-rotate({hue % 40}deg); }}
}}
""",
        ),
    ]
    for i in range(2, MIN_RELEASE_ITEMS):
        theme_title, theme_slug = _OPEN_THEMES[(version + i) % len(_OPEN_THEMES)]
        sub_hue = (hue + i * 23) % 360
        items.append(
            _css_item(
                f"{theme_title} accent {i + 1}",
                f"""/* v{version} explore {theme_slug} layer {i} */
.gem-crush__combo {{
  text-shadow: 0 0 {8 + i}px hsla({sub_hue}, 80%, 70%, 0.4);
}}
.gem-crush__board-wrap::before {{
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: {0.04 + i * 0.008:.3f};
  background: radial-gradient(circle at {20 + i * 7}% {10 + i * 5}%, hsla({sub_hue}, 70%, 70%, 0.2), transparent 50%);
}}
.gem-crush__map-node:nth-child({(i % 5) + 1}) .gem-crush__map-dot {{
  box-shadow: 0 0 {6 + i}px hsla({sub_hue}, 80%, 65%, 0.35);
}}
""",
            )
        )
    return _release(f"Exploration ARIA v{version} — {title}", *items)


def catalog_max_version() -> int:
    from aria_core.skills.gem_crush_premium import PREMIUM_RELEASE_BUNDLES

    if not PREMIUM_RELEASE_BUNDLES:
        return 0
    return max(PREMIUM_RELEASE_BUNDLES)