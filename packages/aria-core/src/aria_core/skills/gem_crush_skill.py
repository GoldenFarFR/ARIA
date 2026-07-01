"""ARIA Gem Crush — amélioration quotidienne autonome sur aria-vanguard (GitHub)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from aria_core.github_client import GitHubClient
from aria_core.memory import append_memory
from aria_core.runtime import settings
from aria_core.skills.github_skill import github_configured, repo_read_allowed, repo_write_allowed

REPO = "aria-vanguard"
CSS_PATH = "src/games/aria-gem-crush/gem-crush.css"
CHANGELOG_PATH = "docs/gem-crush-changelog.md"
ENGINE_PATH = "src/games/aria-gem-crush/game/engine.ts"
CONSTANTS_PATH = "src/games/aria-gem-crush/game/constants.ts"
AUDIO_PATH = "src/games/aria-gem-crush/hooks/useGemAudio.ts"
GAME_UI_PATH = "src/games/aria-gem-crush/components/GemCrushGame.tsx"
VERSION_PATH = "src/games/aria-gem-crush/version.ts"
IMPROVE_RE = re.compile(r"aria-gem-crush-improve:\s*(\d+)", re.I)
RELEASE_TAG_RE = re.compile(r"aria-gem-crush-v(\d+)", re.I)


@dataclass(frozen=True)
class FilePatch:
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class GemCrushItem:
    name: str
    css: str = ""
    patches: tuple[FilePatch, ...] = ()


@dataclass(frozen=True)
class GemCrushRelease:
    """Une version = un lot d'améliorations (CSS + gameplay TS)."""

    title: str
    items: tuple[GemCrushItem, ...]


def _patch(name: str, path: str, old: str, new: str) -> GemCrushItem:
    return GemCrushItem(name=name, patches=(FilePatch(path=path, old=old, new=new),))


def _css_item(name: str, css: str) -> GemCrushItem:
    return GemCrushItem(name=name, css=css)


def _bundle(title: str, *items: tuple[str, str]) -> GemCrushRelease:
    return GemCrushRelease(title=title, items=tuple(GemCrushItem(name=n, css=c) for n, c in items))


def _release(title: str, *items: GemCrushItem) -> GemCrushRelease:
    return GemCrushRelease(title=title, items=items)


# Legacy v8–v20 : micro-patches (déjà expédiés en prod). Conservés pour relecture / reprise.
DAILY_CSS_PATCHES: dict[int, tuple[str, str]] = {
    8: (
        "Chute gemmes — easing rebond colonne",
        """
/* aria-gem-crush-improve: 8 — rebond chute */
.gem-crush__cell--pop {
  animation: gem-pop 0.32s cubic-bezier(0.34, 1.4, 0.64, 1) forwards;
}
""",
    ),
    9: (
        "Fond plateau — motif bonbons",
        """
/* aria-gem-crush-improve: 9 — motif bonbons */
.gem-crush__board-wrap {
  background-image:
    linear-gradient(145deg, rgba(30, 28, 38, 0.92), rgba(8, 8, 14, 0.95)),
    radial-gradient(circle at 25% 25%, rgba(201, 169, 98, 0.06) 0 2px, transparent 3px);
  background-size: auto, 18px 18px;
}
""",
    ),
    2: (
        "Grille plus lisible — rainures entre gemmes",
        """
/* aria-gem-crush-improve: 2 — rainures plateau */
.gem-crush__board {
  gap: 5px;
  padding: 6px;
  background: rgba(0, 0, 0, 0.35);
  border-radius: 8px;
}
""",
    ),
    3: (
        "Barre objectif lumineuse près du but",
        """
/* aria-gem-crush-improve: 3 — glow objectif */
.gem-crush__progress-bar {
  box-shadow: 0 0 14px rgba(201, 169, 98, 0.55);
}
.gem-crush__progress[data-near="1"] .gem-crush__progress-bar {
  animation: target-pulse 1.2s ease-in-out infinite;
}
@keyframes target-pulse {
  0%, 100% { filter: brightness(1); }
  50% { filter: brightness(1.25); }
}
""",
    ),
    4: (
        "Gemmes sélectionnées — halo doré renforcé",
        """
/* aria-gem-crush-improve: 4 — halo sélection */
.gem-crush__cell--selected {
  animation: gem-selected-pulse 0.9s ease-in-out infinite !important;
}
@keyframes gem-selected-pulse {
  0%, 100% { box-shadow: 0 0 0 2px var(--gold), 0 0 18px rgba(201, 169, 98, 0.5); }
  50% { box-shadow: 0 0 0 3px var(--gold-light), 0 0 28px rgba(232, 213, 168, 0.65); }
}
""",
    ),
    5: (
        "Combo — texte plus festif",
        """
/* aria-gem-crush-improve: 5 — combo festif */
.gem-crush__combo {
  font-size: 1.05rem;
  text-shadow: 0 0 20px rgba(201, 169, 98, 0.75);
  letter-spacing: 0.06em;
}
""",
    ),
    6: (
        "Cadre jeu — bordure animée subtile",
        """
/* aria-gem-crush-improve: 6 — cadre animé */
.gem-crush {
  background-size: 200% 200%;
  animation: gem-frame-shimmer 8s ease infinite;
}
@keyframes gem-frame-shimmer {
  0%, 100% { background-position: 0% 50%; }
  50% { background-position: 100% 50%; }
}
""",
    ),
    10: ("HUD — stats plus contrastées", "/* v10 */ .gem-crush__stat { border-color: rgba(201,169,98,0.28); }\n"),
    11: ("Combo — couleur champagne", "/* v11 */ .gem-crush__combo { color: #e8d5a8; }\n"),
    12: ("Plateau — ombre intérieure", "/* v12 */ .gem-crush__board-wrap { box-shadow: inset 0 4px 24px rgba(0,0,0,0.5); }\n"),
    13: ("Gemmes — reflet renforcé", "/* v13 */ .gem-crush__cell::after { inset: 14%; opacity: 0.85; }\n"),
    14: ("Progress — hauteur 8px", "/* v14 */ .gem-crush__progress { height: 8px; }\n"),
    15: ("Mascotte — bulle dorée", "/* v15 */ .gem-crush__mascot-bubble { border-color: rgba(201,169,98,0.35); }\n"),
    16: ("Bouton indice — hover glow", "/* v16 */ .gem-crush__btn--ghost:hover { box-shadow: 0 0 12px rgba(201,169,98,0.35); }\n"),
    17: ("Grille — gap 6px", "/* v17 */ .gem-crush__board { gap: 6px; }\n"),
    18: ("Overlay victoire — blur", "/* v18 */ .gem-crush__overlay { backdrop-filter: blur(4px); }\n"),
    19: ("Score pop — plus grand", "/* v19 */ .gem-crush__score-pop { font-size: 1.25rem; }\n"),
    20: ("Crédit ARIA — visible", "/* v20 */ .gem-crush__aria-credit { color: rgba(201,169,98,0.65); }\n"),
}

# v21+ : releases groupées — plusieurs améliorations visibles par version.
RELEASE_BUNDLES: dict[int, GemCrushRelease] = {
    21: _bundle(
        "Pack juice visuel — animations, glow, profondeur",
        (
            "Chute gemmes — rebond élastique",
            """
/* v21.1 — rebond chute */
.gem-crush__cell--fall {
  animation: gem-fall-bounce 0.42s cubic-bezier(0.34, 1.45, 0.64, 1) forwards;
}
@keyframes gem-fall-bounce {
  0% { transform: translateY(calc(var(--fall-rows, 1) * -100%)); opacity: 0.85; }
  70% { transform: translateY(4px); }
  100% { transform: translateY(0); opacity: 1; }
}
""",
        ),
        (
            "Match — flash doré sur pop",
            """
/* v21.2 — flash match */
.gem-crush__cell--pop {
  animation: gem-pop 0.32s cubic-bezier(0.34, 1.4, 0.64, 1) forwards,
    gem-match-flash 0.5s ease-out forwards;
}
@keyframes gem-match-flash {
  0% { box-shadow: 0 0 0 0 rgba(232, 213, 168, 0.9); }
  100% { box-shadow: 0 0 24px 6px rgba(201, 169, 98, 0); }
}
""",
        ),
        (
            "Plateau — motif bonbons + profondeur",
            """
/* v21.3 — motif plateau */
.gem-crush__board-wrap {
  background-image:
    linear-gradient(145deg, rgba(30, 28, 38, 0.92), rgba(8, 8, 14, 0.95)),
    radial-gradient(circle at 25% 25%, rgba(201, 169, 98, 0.07) 0 2px, transparent 3px);
  background-size: auto, 18px 18px;
  box-shadow: inset 0 4px 28px rgba(0, 0, 0, 0.45);
}
""",
        ),
        (
            "HUD — stats contrastées + labels dorés",
            """
/* v21.4 — HUD contrast */
.gem-crush__stat {
  border-color: rgba(201, 169, 98, 0.32);
  background: rgba(0, 0, 0, 0.45);
}
.gem-crush__label {
  color: rgba(201, 169, 98, 0.55);
}
""",
        ),
        (
            "Combo — champagne + ombre portée",
            """
/* v21.5 — combo champagne */
.gem-crush__combo {
  color: #e8d5a8;
  font-size: 1.08rem;
  text-shadow: 0 2px 12px rgba(201, 169, 98, 0.65), 0 0 24px rgba(232, 213, 168, 0.4);
  letter-spacing: 0.05em;
}
""",
        ),
        (
            "Cadre — shimmer doré lent",
            """
/* v21.6 — cadre shimmer */
.gem-crush {
  background-size: 220% 220%;
  animation: gem-frame-shimmer 10s ease infinite;
}
@keyframes gem-frame-shimmer {
  0%, 100% { background-position: 0% 40%; }
  50% { background-position: 100% 60%; }
}
""",
        ),
    ),
    22: _bundle(
        "Pack feedback jeu — sélection, erreurs, objectif, indices",
        (
            "Sélection — halo pulsé renforcé",
            """
/* v22.1 — halo sélection */
.gem-crush__cell--selected {
  animation: gem-selected-pulse 0.85s ease-in-out infinite !important;
}
@keyframes gem-selected-pulse {
  0%, 100% { box-shadow: 0 0 0 2px var(--gold), 0 0 20px rgba(201, 169, 98, 0.55); transform: scale(1.08); }
  50% { box-shadow: 0 0 0 3px var(--gold-light), 0 0 32px rgba(232, 213, 168, 0.7); transform: scale(1.1); }
}
""",
        ),
        (
            "Coup invalide — flash rouge bref",
            """
/* v22.2 — invalide flash */
.gem-crush--invalid-flash .gem-crush__board-wrap {
  animation: invalid-flash 0.35s ease;
}
@keyframes invalid-flash {
  0%, 100% { box-shadow: inset 0 0 0 0 transparent; }
  50% { box-shadow: inset 0 0 0 3px rgba(220, 80, 80, 0.75); }
}
""",
        ),
        (
            "Objectif proche — barre pulse",
            """
/* v22.3 — objectif pulse */
.gem-crush__progress-bar {
  box-shadow: 0 0 16px rgba(201, 169, 98, 0.5);
}
.gem-crush__progress[data-near="1"] .gem-crush__progress-bar {
  animation: target-pulse 1s ease-in-out infinite;
}
@keyframes target-pulse {
  0%, 100% { filter: brightness(1); }
  50% { filter: brightness(1.3); }
}
""",
        ),
        (
            "Indice — bouton pulse discret",
            """
/* v22.4 — indice pulse */
.gem-crush__btn--ghost[data-hint="1"] {
  animation: hint-pulse 1.6s ease-in-out infinite;
}
@keyframes hint-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(201, 169, 98, 0); }
  50% { box-shadow: 0 0 14px 2px rgba(201, 169, 98, 0.45); }
}
.gem-crush__btn--ghost:hover {
  box-shadow: 0 0 16px rgba(201, 169, 98, 0.4);
}
""",
        ),
        (
            "Score flottant — plus lisible",
            """
/* v22.5 — score pop */
.gem-crush__score-pop {
  font-size: 1.28rem;
  font-weight: 800;
  letter-spacing: 0.02em;
}
""",
        ),
    ),
    23: _bundle(
        "Pack victoire & mascotte — célébration, crédit ARIA",
        (
            "Overlay victoire — blur + assombrissement",
            """
/* v23.1 — overlay victoire */
.gem-crush__overlay {
  backdrop-filter: blur(6px);
  background: rgba(4, 4, 8, 0.72);
}
""",
        ),
        (
            "Mascotte — bulle dorée + léger flottement",
            """
/* v23.2 — mascotte */
.gem-crush__mascot-bubble {
  border-color: rgba(201, 169, 98, 0.4);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
}
.gem-crush__mascot {
  animation: mascot-float 3s ease-in-out infinite;
}
@keyframes mascot-float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-4px); }
}
""",
        ),
        (
            "Grille — espacement confort mobile",
            """
/* v23.3 — grille gap */
.gem-crush__board {
  gap: 6px;
  padding: 6px;
}
""",
        ),
        (
            "Gemmes — reflet jelly renforcé",
            """
/* v23.4 — reflet gemmes */
.gem-crush__cell::after {
  inset: 12%;
  opacity: 0.9;
  background: radial-gradient(circle at 32% 28%, rgba(255, 255, 255, 0.65), transparent 58%);
}
""",
        ),
        (
            "Crédit ARIA — signature visible",
            """
/* v23.5 — crédit ARIA */
.gem-crush__aria-credit {
  color: rgba(201, 169, 98, 0.72);
  font-size: 0.72rem;
  letter-spacing: 0.08em;
}
""",
        ),
    ),
    24: _bundle(
        "Pack spéciaux & rayures — bombes, lignes, sparkles",
        (
            "Rayures — glow blanc pulsé",
            """
/* v24.1 — rayures glow */
.gem-crush__special-line-h::before,
.gem-crush__special-line-v::before {
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 0 10px rgba(255, 255, 255, 0.8);
  animation: stripe-glow 1.2s ease-in-out infinite;
}
@keyframes stripe-glow {
  0%, 100% { opacity: 0.85; }
  50% { opacity: 1; filter: brightness(1.4); }
}
""",
        ),
        (
            "Bombe — halo radial doré",
            """
/* v24.2 — bombe halo */
.gem-crush__special-bomb {
  box-shadow: 0 0 18px rgba(232, 213, 168, 0.65), inset 0 0 12px rgba(255, 255, 255, 0.25);
}
""",
        ),
        (
            "Sparkles plateau — drift plus rapide",
            """
/* v24.3 — sparkles */
.gem-crush__sparkles {
  opacity: 0.75;
  animation: sparkle-drift 8s linear infinite;
}
""",
        ),
        (
            "Progress — barre 8px + coins arrondis",
            """
/* v24.4 — progress */
.gem-crush__progress {
  height: 8px;
  border-radius: 4px;
}
.gem-crush__progress-bar {
  border-radius: 4px;
}
""",
        ),
    ),
    25: _release(
        "Pack scoring & combos — gameplay engine",
        _patch(
            "Score base +20%",
            ENGINE_PATH,
            "const base = matched.size * 10",
            "const base = matched.size * 12  // aria-gem-crush-v25",
        ),
        _patch(
            "Bonus alignements longs",
            ENGINE_PATH,
            "const bonus = groups.reduce((s, g) => s + Math.max(0, g.length - 3) * 15, 0)",
            "const bonus = groups.reduce((s, g) => s + Math.max(0, g.length - 3) * 20, 0)  // aria-gem-crush-v25",
        ),
        _patch(
            "Multiplicateur combo ×1.5",
            ENGINE_PATH,
            "(1 + (combo - 1) * 0.35))",
            "(1 + (combo - 1) * 0.5))  // aria-gem-crush-v25",
        ),
        _css_item(
            "Score pop XL",
            "/* v25 */\n.gem-crush__score-pop { font-size: 1.35rem; font-weight: 900; }\n",
        ),
    ),
    26: _release(
        "Pack rythme & niveaux — coups, objectifs, cascades",
        _patch(
            "Coups par niveau +4",
            CONSTANTS_PATH,
            "moves: 28,",
            "moves: 32,  // aria-gem-crush-v26",
        ),
        _patch(
            "Objectifs légèrement assouplis",
            CONSTANTS_PATH,
            "target: 800 + (level - 1) * 600,",
            "target: 750 + (level - 1) * 550,  // aria-gem-crush-v26",
        ),
        _patch(
            "Cascade plus rapide",
            GAME_UI_PATH,
            "await new Promise((r) => setTimeout(r, 340))",
            "await new Promise((r) => setTimeout(r, 280))  // aria-gem-crush-v26",
        ),
        _patch(
            "Indice après 7s d'inactivité",
            GAME_UI_PATH,
            "}, 9000)",
            "}, 7000)  // aria-gem-crush-v26",
        ),
    ),
    27: _release(
        "Pack audio — sons plus riches, victoire prolongée",
        _patch(
            "Fréquences swap / match / combo boostées",
            AUDIO_PATH,
            "  swap: 420,\n  match: 620,\n  combo: 780,",
            "  swap: 440,\n  match: 680,\n  combo: 860,  // aria-gem-crush-v27",
        ),
        _patch(
            "Volume général + victoire",
            AUDIO_PATH,
            "gain.gain.value = kind === 'win' ? 0.14 : 0.09",
            "gain.gain.value = kind === 'win' ? 0.18 : 0.1  // aria-gem-crush-v27",
        ),
        _patch(
            "Note victoire plus longue",
            AUDIO_PATH,
            "gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18)",
            "gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + (kind === 'win' ? 0.45 : 0.2))  // aria-gem-crush-v27",
        ),
        _patch(
            "Oscillateur combo = triangle",
            AUDIO_PATH,
            "osc.type = kind === 'invalid' ? 'sawtooth' : 'sine'",
            "osc.type = kind === 'invalid' ? 'sawtooth' : kind === 'combo' ? 'triangle' : 'sine'  // aria-gem-crush-v27",
        ),
    ),
    28: _release(
        "Pack combos FR — paliers Délicieux / Savoureux / Incroyable",
        _patch(
            "Helper comboLabelFor",
            GAME_UI_PATH,
            "const TUTORIAL_KEY = 'aria-gem-crush-tutorial-v1'",
            "const TUTORIAL_KEY = 'aria-gem-crush-tutorial-v1'\n\n"
            "function comboLabelFor(combo: number): string {\n"
            "  if (combo >= 4) return `Incroyable ×${combo} !`\n"
            "  if (combo === 3) return 'Savoureux ×3 !'\n"
            "  if (combo > 1) return `Délicieux ×${combo} !`\n"
            "  return 'Délicieux !'\n"
            "}  // aria-gem-crush-v28",
        ),
        _patch(
            "Utiliser comboLabelFor en cascade",
            GAME_UI_PATH,
            "setComboLabel(step.combo > 1 ? `Délicieux ×${step.combo} !` : 'Délicieux !')",
            "setComboLabel(comboLabelFor(step.combo))  // aria-gem-crush-v28",
        ),
        _css_item(
            "Animation combo par palier",
            """/* v28 combo tier */
.gem-crush__combo {
  animation: combo-pop 0.5s ease, combo-tier-pulse 0.6s ease;
}
@keyframes combo-tier-pulse {
  0% { transform: scale(0.92); }
  50% { transform: scale(1.06); }
  100% { transform: scale(1); }
}
""",
        ),
    ),
    29: _release(
        "Pack visuel branché — CSS relié au code TS",
        _patch(
            "Flash rouge coup invalide",
            GAME_UI_PATH,
            "const [shake, setShake] = useState(false)",
            "const [shake, setShake] = useState(false)\n"
            "  const [invalidFlash, setInvalidFlash] = useState(false)  // aria-gem-crush-v29",
        ),
        _patch(
            "Activer invalid-flash + shake",
            GAME_UI_PATH,
            "setShake(true)\n        play('invalid')\n        window.setTimeout(() => setShake(false), 420)",
            "setShake(true)\n"
            "        setInvalidFlash(true)\n"
            "        play('invalid')\n"
            "        window.setTimeout(() => {\n"
            "          setShake(false)\n"
            "          setInvalidFlash(false)\n"
            "        }, 420)  // aria-gem-crush-v29",
        ),
        _patch(
            "Classe invalid-flash sur root",
            GAME_UI_PATH,
            "${shake ? 'gem-crush--shake' : ''}`}",
            "${shake ? 'gem-crush--shake' : ''} ${invalidFlash ? 'gem-crush--invalid-flash' : ''}`}  // aria-gem-crush-v29",
        ),
        _patch(
            "Indice bouton pulse (data-hint)",
            GAME_UI_PATH,
            'className="gem-crush__btn gem-crush__btn--ghost"\n          onClick={showHintNow}',
            'className="gem-crush__btn gem-crush__btn--ghost"\n'
            '          data-hint={hint ? \'1\' : undefined}\n'
            "          onClick={showHintNow}  // aria-gem-crush-v29",
        ),
        _css_item(
            "Gemmes saturées + plateau glow",
            """/* v29 visuel branché */
.gem-crush__gem-0, .gem-crush__gem-1, .gem-crush__gem-2,
.gem-crush__gem-3, .gem-crush__gem-4, .gem-crush__gem-5 {
  filter: saturate(1.4) brightness(1.1);
}
.gem-crush__board-wrap {
  animation: board-glow-pulse 3.2s ease-in-out infinite;
}
@keyframes board-glow-pulse {
  0%, 100% { box-shadow: 0 0 0 2px rgba(201,169,98,0.35), 0 0 28px rgba(201,169,98,0.12); }
  50% { box-shadow: 0 0 0 3px rgba(232,213,168,0.55), 0 0 48px rgba(201,169,98,0.28); }
}
""",
        ),
    ),
    30: _release(
        "Pack tension finale — objectif proche, coups bas, juice Candy Crush",
        _patch(
            "Classe near-win sur root",
            GAME_UI_PATH,
            "${invalidFlash ? 'gem-crush--invalid-flash' : ''}`}",
            "${invalidFlash ? 'gem-crush--invalid-flash' : ''} ${nearTarget ? 'gem-crush--near-win' : ''}`}  // aria-gem-crush-v30",
        ),
        _patch(
            "Mascotte — tension objectif proche",
            GAME_UI_PATH,
            "{comboLabel || (nearTarget ? 'Presque !' : 'Échange deux gemmes')}",
            "{comboLabel || (nearTarget ? 'Objectif en vue — continue !' : 'Échange deux gemmes')}  // aria-gem-crush-v30",
        ),
        _patch(
            "Alerte coups bas à 3 restants",
            GAME_UI_PATH,
            "movesLeft <= 5 ? 'gem-crush__warn' : ''",
            "movesLeft <= 3 ? 'gem-crush__warn' : ''  // aria-gem-crush-v30",
        ),
        _patch(
            "Cascade plus nerveuse (rythme Candy Crush)",
            GAME_UI_PATH,
            "await new Promise((r) => setTimeout(r, 280))  // aria-gem-crush-v26",
            "await new Promise((r) => setTimeout(r, 240))  // aria-gem-crush-v30",
        ),
        _patch(
            "Score victoire combo label prolongé",
            GAME_UI_PATH,
            "window.setTimeout(() => setComboLabel(null), 700)",
            "window.setTimeout(() => setComboLabel(null), 900)  // aria-gem-crush-v30",
        ),
        _css_item(
            "Vignette objectif + gemmes XL + barre shine",
            """/* v30 near-win */
.gem-crush--near-win .gem-crush__board-wrap {
  outline: 3px solid rgba(232, 213, 168, 0.55);
  outline-offset: 2px;
}
.gem-crush--near-win .gem-crush__cell {
  transform: scale(1.02);
}
.gem-crush--near-win .gem-crush__progress-bar {
  background: linear-gradient(90deg, #c9a962, #fff8e0, #c9a962);
  background-size: 200% 100%;
  animation: progress-shine 1.5s linear infinite;
}
@keyframes progress-shine {
  0% { background-position: 0% 50%; }
  100% { background-position: 200% 50%; }
}
.gem-crush__warn {
  color: #ffb4b4;
  animation: moves-warn-pulse 0.8s ease-in-out infinite;
}
@keyframes moves-warn-pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.08); }
}
""",
        ),
        _css_item(
            "HUD objectif — glow doré quand proche",
            """/* v30 hud near */
.gem-crush--near-win .gem-crush__progress-wrap .gem-crush__label {
  color: rgba(232, 213, 168, 0.95);
  text-shadow: 0 0 12px rgba(201, 169, 98, 0.6);
}
.gem-crush--near-win .gem-crush__combo {
  color: #fff8e0;
}
""",
        ),
    ),
}


def premium_mode_enabled() -> bool:
    return bool(getattr(settings, "aria_gem_crush_premium_mode", True))


def incremental_mode_enabled() -> bool:
    """Boucle Spec → Critic → micro-patch → dry-run → ship."""
    if not premium_mode_enabled():
        return False
    return bool(getattr(settings, "aria_gem_crush_incremental_mode", True))


def unlimited_releases_enabled() -> bool:
    """Pas de plafond catalogue — synthèse backlog + exploration ouverte (défaut: oui)."""
    if not premium_mode_enabled():
        return False
    return bool(getattr(settings, "aria_gem_crush_unlimited_releases", True))


def release_for_version(version: int) -> GemCrushRelease | None:
    """Release groupée (v21+) — premium v31+ — ou legacy micro-patch (v8–v20)."""
    from aria_core.skills.gem_crush_premium import PREMIUM_RELEASE_BUNDLES

    bundle = PREMIUM_RELEASE_BUNDLES.get(version) or RELEASE_BUNDLES.get(version)
    if bundle:
        return bundle
    legacy = DAILY_CSS_PATCHES.get(version)
    if legacy:
        title, css = legacy
        return GemCrushRelease(title=title, items=(GemCrushItem(name=title, css=css),))
    return None


MIN_RELEASE_ITEMS = 10  # lot massif — minimum 10 améliorations par heartbeat
MIN_PREMIUM_ITEMS = MIN_RELEASE_ITEMS  # alias tests / worker queue
MIN_MICRO_TS_PATCHES = 0  # CSS-only OK si gap juice


def improve_interval_minutes() -> int:
    """
    Minutes entre deux releases Gem Crush (ARIA_GEM_CRUSH_IMPROVE_MINUTES).
    Premium : défaut et plancher 30 min (recherche concurrence + ship massif).
    """
    premium = premium_mode_enabled()
    default = 30 if premium else 5
    floor = 30 if premium else 1
    raw = int(getattr(settings, "aria_gem_crush_improve_minutes", default) or default)
    return max(floor, min(raw, 10080))


def minutes_since_last_ship(version_text: str) -> float | None:
    """Minutes depuis GEM_CRUSH_UPDATED_AT sur GitHub (survit aux redeploy Render)."""
    m = re.search(r"GEM_CRUSH_UPDATED_AT\s*=\s*['\"]([^'\"]+)['\"]", version_text or "")
    if not m:
        return None
    raw = m.group(1).strip().removesuffix(" UTC").strip()
    try:
        shipped_at = datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - shipped_at).total_seconds() / 60.0
    except ValueError:
        return None


def validate_release_quality(release: GemCrushRelease) -> str | None:
    """Premium : refuse les releases < 10 améliorations (tous modes)."""
    if not premium_mode_enabled():
        return None
    if len(release.items) < MIN_RELEASE_ITEMS:
        return (
            f"release trop petite ({len(release.items)} items, min {MIN_RELEASE_ITEMS}) — "
            "ARIA doit shipper un lot massif (≥10 améliorations)."
        )
    if not incremental_mode_enabled():
        ts_patches = sum(len(item.patches) for item in release.items)
        if ts_patches < 1:
            return (
                f"release sans patch TS branché ({ts_patches} patch, min 1) — "
                "CSS seul = invisible en prod."
            )
    return None


def validate_micro_release_quality(release: GemCrushRelease) -> str | None:
    """Alias — même seuil minimum 10 (plus de micro-lots 1–2)."""
    return validate_release_quality(release)


def format_ship_telegram(
    *,
    version: int,
    title: str,
    repo: str,
    commit_url: str = "",
    items: tuple[str, ...] = (),
    lang: str = "fr",
) -> str:
    item_lines: list[str] = []
    if items:
        preview = items[:10]
        for name in preview:
            item_lines.append(f"  • {name}")
        if len(items) > 10:
            item_lines.append(f"  • … +{len(items) - 10} autres")

    if lang == "en":
        lines = [
            "ARIA Gem Crush — version shipped",
            f"v{version} — {title}",
        ]
        if items:
            lines.append(f"{len(items)} improvements bundled:")
            lines.extend(item_lines)
        lines.append(f"Repo: {repo}")
        if commit_url:
            lines.append(f"Commit: {commit_url}")
        lines.append("Live on Vanguard after Render redeploy (~5–15 min).")
        return "\n".join(lines)

    lines = [
        "ARIA Gem Crush — version expédiée",
        f"v{version} — {title}",
    ]
    if lang == "fr":
        lines.append("Méthode : idée → recherche concurrence (web) → brief → ship groupé")
    else:
        lines.append("Method: idea → competitor research (web) → brief → bundled ship")
    if items:
        lines.append(f"{len(items)} améliorations groupées :")
        lines.extend(item_lines)
    lines.append(f"Repo : {repo}")
    if commit_url:
        lines.append(f"Commit : {commit_url}")
    lines.append("Visible sur Vanguard après redeploy Render (~5–15 min).")
    lines.append(f"Prochaine version dans {improve_interval_minutes()} min.")
    return "\n".join(lines)


async def notify_gem_crush_ship(result: dict, lang: str = "fr") -> bool:
    """Telegram FYI à chaque version expédiée (status applied)."""
    if result.get("status") != "applied":
        return False
    try:
        from aria_core.gateway.telegram_bot import send_message

        items = tuple(result.get("items") or ())
        text = format_ship_telegram(
            version=int(result["version"]),
            title=str(result.get("title", "")),
            repo=str(result.get("repo", REPO)),
            commit_url=str(result.get("commit_url", "")),
            items=items,
            lang=lang,
        )
        await send_message(f"🎮 {text}")
        return True
    except Exception:
        return False


def parse_improve_version(css_text: str) -> int:
    m = IMPROVE_RE.search(css_text)
    return int(m.group(1)) if m else 0


def _bump_marker(css_text: str, new_version: int) -> str:
    if IMPROVE_RE.search(css_text):
        return IMPROVE_RE.sub(f"aria-gem-crush-improve: {new_version}", css_text, count=1)
    return f"/* aria-gem-crush-improve: {new_version} */\n" + css_text


def _patches_by_path(release: GemCrushRelease) -> dict[str, list[FilePatch]]:
    grouped: dict[str, list[FilePatch]] = {}
    for item in release.items:
        for patch in item.patches:
            grouped.setdefault(patch.path, []).append(patch)
    return grouped


@dataclass(frozen=True)
class DryRunResult:
    ok: bool
    missing: tuple[tuple[str, str], ...] = ()


def dry_run_patches(
    file_contents: dict[str, str],
    patches_by_path: dict[str, list[FilePatch]],
) -> DryRunResult:
    """Valide toutes les ancres avant écriture GitHub."""
    missing: list[tuple[str, str]] = []
    for path, patches in patches_by_path.items():
        content = file_contents.get(path, "")
        for patch in patches:
            if patch.old not in content:
                snippet = patch.old[:80].replace("\n", " ")
                missing.append((path, snippet))
    return DryRunResult(ok=not missing, missing=tuple(missing))


def apply_patches(content: str, patches: list[FilePatch]) -> str:
    out = content
    for patch in patches:
        if patch.old not in out:
            raise ValueError(f"patch anchor missing: {patch.path!r}")
        out = out.replace(patch.old, patch.new, 1)
    return out


def _build_version_ts(version: int, title: str, ts: str) -> str:
    title_esc = title.replace("\\", "\\\\").replace("'", "\\'")
    ts_esc = ts.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "/** Auto-updated by ARIA gem_crush_skill — do not edit manually. */\n"
        f"export const GEM_CRUSH_VERSION = {version}\n"
        f"export const GEM_CRUSH_RELEASE_TITLE = '{title_esc}'\n"
        f"export const GEM_CRUSH_UPDATED_AT = '{ts_esc}'\n"
    )


def _build_release_css(release: GemCrushRelease, version: int) -> str:
    blocks = [f"/* aria-gem-crush-release: v{version} — {release.title} */"]
    for item in release.items:
        if item.css.strip():
            blocks.append(item.css.strip())
    if len(blocks) == 1:
        blocks.append(f"/* aria-gem-crush-v{version} — gameplay marker */")
    return "\n\n".join(blocks) + "\n"


def _release_already_applied(css_text: str, version: int, extra_texts: list[str]) -> bool:
    markers = (
        f"aria-gem-crush-improve: {version}",
        f"aria-gem-crush-release: v{version}",
        f"aria-gem-crush-v{version}",
    )
    haystacks = [css_text, *extra_texts]
    return any(m in text for text in haystacks for m in markers)


def _append_changelog(existing: str, *, version: int, title: str, ts: str, items: tuple[str, ...] = ()) -> str:
    if items and len(items) > 1:
        subs = "; ".join(items[:8])
        if len(items) > 8:
            subs += f" (+{len(items) - 8})"
        line = f"- **v{version}** ({ts}) — {title} — _{subs}_ — _ARIA autonome_\n"
    else:
        line = f"- **v{version}** ({ts}) — {title} — _ARIA autonome_\n"
    if not existing.strip():
        return f"# ARIA Gem Crush — changelog\n\n{line}"
    if line.strip() in existing:
        return existing
    return existing.rstrip() + "\n" + line


def _finalize_heartbeat_release(
    release: GemCrushRelease,
    next_ver: int,
    critic: "CriticReport | None",
    source: str,
) -> tuple[GemCrushRelease, "CriticReport | None", str]:
    from aria_core.skills.gem_crush_synthesizer import ensure_min_release_items

    return ensure_min_release_items(release, next_ver), critic, source


def resolve_heartbeat_release(
    next_ver: int,
    *,
    live_version: int,
    css: str,
    tsx: str,
    file_contents: dict[str, str],
) -> tuple[GemCrushRelease | None, "CriticReport | None", str]:
    """
    Résout la prochaine release : catalogue complet (≥10 items) → synthèse → exploration.
    Retourne (release, critic_report, source).
    """
    from aria_core.skills.gem_crush_critic import CriticReport, run_critic

    catalog = release_for_version(next_ver)
    critic: CriticReport | None = None

    if catalog:
        if incremental_mode_enabled():
            critic = run_critic(
                version=live_version,
                css=css,
                tsx=tsx,
                file_contents=file_contents,
                release=catalog,
            )
        return _finalize_heartbeat_release(catalog, next_ver, critic, "catalog")

    if not unlimited_releases_enabled():
        return None, None, "none"

    critic = run_critic(
        version=live_version,
        css=css,
        tsx=tsx,
        file_contents=file_contents,
        release=None,
    )
    from aria_core.skills.gem_crush_synthesizer import (
        synthesize_open_release,
        synthesize_release_from_gap,
    )

    if critic.top_gap:
        raw = synthesize_release_from_gap(next_ver, critic.top_gap)
        return _finalize_heartbeat_release(raw, next_ver, critic, "synthesis")
    raw = synthesize_open_release(next_ver)
    return _finalize_heartbeat_release(raw, next_ver, critic, "open")


async def _enqueue_gem_crush_worker(result: dict, *, lang: str = "fr") -> dict:
    """ARIA bloquée sur Gem Crush → file ouvrier Cursor."""
    status = str(result.get("status") or "")
    if status not in ("queue_empty", "quality_gate", "write_denied", "error", "missing", "local_only"):
        return result
    try:
        from aria_core.aria_worker_queue import enqueue_from_gem_crush_block

        worker = await enqueue_from_gem_crush_block(
            status=status,
            version=result.get("version"),
            message=str(result.get("message") or ""),
            title=str(result.get("title") or ""),
            lang=lang,
        )
        result["worker_queue"] = worker
    except Exception:
        pass
    return result


async def run_daily_gem_crush_improve(lang: str = "fr") -> dict:
    """
    Heartbeat périodique — mode premium :
    1. Idée / release en file
    2. Recherche web concurrence (DuckDuckGo) + brief stratégique
    3. Ship groupé (CSS + TS branchés) — peu de versions, gros impact
    Intervalle : aria_gem_crush_improve_minutes (défaut 30 min en premium).
    """
    owner = settings.github_owner.strip()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not github_configured():
        msg = "Gem Crush — amélioration locale (GITHUB_TOKEN absent)."
        append_memory("entrepreneur", f"[gem-crush] {msg}")
        return await _enqueue_gem_crush_worker({"status": "local_only", "message": msg}, lang=lang)

    if not repo_read_allowed(owner, REPO):
        msg = f"Gem Crush — lecture {owner}/{REPO} refusée."
        append_memory("entrepreneur", f"[gem-crush] {msg}")
        return {"status": "read_denied", "message": msg}

    client = GitHubClient(settings.github_token.strip())
    css_text, css_sha = await client.get_file_text(owner, REPO, CSS_PATH)
    if not css_text.strip():
        msg = f"Gem Crush — {CSS_PATH} introuvable sur GitHub (pousser le POC d'abord)."
        append_memory("entrepreneur", f"[gem-crush] {msg}")
        return await _enqueue_gem_crush_worker({"status": "missing", "message": msg}, lang=lang)

    current = parse_improve_version(css_text)
    next_ver = current + 1

    ui_text, _ = await client.get_file_text(owner, REPO, GAME_UI_PATH)
    version_text, _ = await client.get_file_text(owner, REPO, VERSION_PATH)

    interval = improve_interval_minutes()
    since = minutes_since_last_ship(version_text)
    if since is not None and since < interval:
        wait = max(1, int(interval - since))
        msg = (
            f"Gem Crush v{current} — cooldown actif ({wait} min restantes, intervalle {interval} min)."
            if lang == "fr"
            else f"Gem Crush v{current} — cooldown ({wait} min left, interval {interval} min)."
        )
        append_memory("entrepreneur", f"[gem-crush] cooldown v{current} wait={wait}m")
        return {"status": "cooldown", "version": current, "message": msg, "wait_minutes": wait}

    live_version = current
    if version_text.strip():
        m = re.search(r"GEM_CRUSH_VERSION\s*=\s*(\d+)", version_text)
        if m:
            live_version = int(m.group(1))

    file_contents: dict[str, str] = {CSS_PATH: css_text, GAME_UI_PATH: ui_text}
    release, critic_report, release_source = resolve_heartbeat_release(
        next_ver,
        live_version=live_version,
        css=css_text,
        tsx=ui_text,
        file_contents=file_contents,
    )

    if not release:
        msg = (
            f"Gem Crush v{current} — aucune release en file d'attente. "
            f"ARIA planifiera la prochaine amélioration."
            if lang == "fr"
            else f"Gem Crush v{current} — no queued release."
        )
        append_memory("entrepreneur", f"[gem-crush] queue empty v{current}")
        return await _enqueue_gem_crush_worker(
            {"status": "queue_empty", "version": current, "message": msg}, lang=lang
        )

    if critic_report and getattr(critic_report, "top_gap", None):
        append_memory(
            "entrepreneur",
            f"[gem-crush] critic v{next_ver} ({release_source}): gap={critic_report.top_gap.id} "
            f"scores={critic_report.scores}",
        )
    else:
        append_memory(
            "entrepreneur",
            f"[gem-crush] release v{next_ver} source={release_source}: {release.title}",
        )

    quality_err = validate_micro_release_quality(release) or validate_release_quality(release)
    if quality_err:
        msg = f"Gem Crush v{next_ver} — {quality_err}"
        append_memory("entrepreneur", f"[gem-crush] quality gate v{next_ver}: {quality_err}")
        return await _enqueue_gem_crush_worker(
            {"status": "quality_gate", "version": next_ver, "message": msg}, lang=lang
        )

    item_names = tuple(item.name for item in release.items)
    research_brief_md = ""
    if premium_mode_enabled() and not incremental_mode_enabled():
        from aria_core.skills.gem_crush_research import brief_path_for, run_match3_research

        research = await run_match3_research(
            version=next_ver,
            release_title=release.title,
            planned_items=item_names,
            lang=lang,
        )
        research_brief_md = research.markdown
        append_memory(
            "entrepreneur",
            f"[gem-crush] research v{next_ver}: {len(research.sources)} sources — {release.title}",
        )

    patch_paths = _patches_by_path(release)
    file_contents = {CSS_PATH: css_text, GAME_UI_PATH: ui_text}
    for path in patch_paths:
        if path not in file_contents:
            text, _ = await client.get_file_text(owner, REPO, path)
            file_contents[path] = text
    extra_texts = list(file_contents.values())

    if _release_already_applied(css_text, next_ver, extra_texts):
        msg = f"Gem Crush v{next_ver} déjà appliqué."
        return {"status": "already_applied", "version": next_ver, "message": msg}

    dry = dry_run_patches(file_contents, patch_paths)
    if not dry.ok:
        miss = "; ".join(f"{p}: {a[:40]}…" for p, a in dry.missing[:3])
        msg = f"Gem Crush v{next_ver} — dry-run échoué (ancres): {miss}"
        append_memory("entrepreneur", f"[gem-crush] dry-run fail v{next_ver}: {miss}")
        return await _enqueue_gem_crush_worker(
            {"status": "error", "version": next_ver, "message": msg}, lang=lang
        )

    css_block = _build_release_css(release, next_ver)
    new_css = _bump_marker(css_text.rstrip() + "\n" + css_block, next_ver)

    file_updates: dict[str, str] = {
        CSS_PATH: new_css,
        VERSION_PATH: _build_version_ts(next_ver, release.title, ts),
    }
    if research_brief_md:
        from aria_core.skills.gem_crush_research import brief_path_for

        file_updates[brief_path_for(next_ver)] = research_brief_md
    for path, patches in patch_paths.items():
        raw = file_contents.get(path, "")
        if not raw.strip():
            msg = f"Gem Crush — {path} introuvable pour v{next_ver}."
            return await _enqueue_gem_crush_worker({"status": "missing", "message": msg}, lang=lang)
        try:
            file_updates[path] = apply_patches(raw, patches)
        except ValueError as exc:
            msg = f"Gem Crush v{next_ver} — patch impossible sur {path} : {exc}"
            append_memory("entrepreneur", f"[gem-crush] patch fail v{next_ver}: {exc}")
            return await _enqueue_gem_crush_worker(
                {"status": "error", "version": next_ver, "message": msg}, lang=lang
            )

    if not repo_write_allowed(owner, REPO):
        msg = (
            f"Gem Crush v{next_ver} prêt : {release.title} ({len(release.items)} items) — "
            f"écriture GitHub refusée (GITHUB_WRITE_REPOS)."
            if lang == "fr"
            else f"Gem Crush v{next_ver} ready — write denied."
        )
        append_memory("entrepreneur", f"[gem-crush] would apply v{next_ver}: {release.title}")
        return await _enqueue_gem_crush_worker(
            {
                "status": "write_denied",
                "version": next_ver,
                "title": release.title,
                "items": item_names,
                "message": msg,
            },
            lang=lang,
        )

    changelog_text, changelog_sha = await client.get_file_text(owner, REPO, CHANGELOG_PATH)
    new_changelog = _append_changelog(
        changelog_text or "",
        version=next_ver,
        title=release.title,
        ts=ts,
        items=item_names,
    )

    commit_url = ""
    try:
        file_updates[CHANGELOG_PATH] = new_changelog
        gameplay_note = ""
        if patch_paths:
            gameplay_note = f" + {len(patch_paths)} fichier(s) gameplay"
        batch = await client.put_files_batch(
            owner,
            REPO,
            list(file_updates.items()),
            message=(
                f"feat(gem-crush): ARIA release v{next_ver} — {release.title} "
                f"({len(release.items)} items{gameplay_note})"
            ),
        )
        sha = batch.get("commit_sha", "")
        if sha:
            commit_url = f"https://github.com/{owner}/{REPO}/commit/{sha}"
    except Exception as exc:
        msg = f"Gem Crush release v{next_ver} échouée : {str(exc)[:180]}"
        append_memory("entrepreneur", f"[gem-crush] error v{next_ver}: {exc}")
        return {"status": "error", "version": next_ver, "message": msg}

    repo_slug = f"{owner}/{REPO}"
    msg = format_ship_telegram(
        version=next_ver,
        title=release.title,
        repo=repo_slug,
        commit_url=commit_url,
        items=item_names,
        lang=lang,
    )
    append_memory(
        "entrepreneur",
        f"[gem-crush] applied v{next_ver}: {release.title} ({len(release.items)} items)",
    )
    result = {
        "status": "applied",
        "version": next_ver,
        "title": release.title,
        "items": item_names,
        "message": msg,
        "repo": repo_slug,
        "commit_url": commit_url,
        "premium_mode": premium_mode_enabled(),
        "incremental_mode": incremental_mode_enabled(),
        "release_source": release_source,
        "critic_gap": critic_report.top_gap.id if critic_report and critic_report.top_gap else None,
        "research_brief": bool(research_brief_md),
    }
    await notify_gem_crush_ship(result, lang=lang)
    return result