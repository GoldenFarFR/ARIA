"""Single, always-current registry of every shadow pocket's real parameters.

**Why this exists (21/08, operator-directed).** Operator, verbatim: "tu me
saoul a jamais verifier chaque parametre en profondeur de chaque poche, tu as
qu'a tenir un json pour chaque poche a jour". He was right, and the failure was
real: on 21/08 a -81.5% close was blamed on a "-20% trailing stop" that was
actually 15%, and only armed above +10% -- a parameter nobody had re-read since
it was introduced the day before.

**Why it is GENERATED, not hand-written.** A hand-maintained JSON is a second
source of truth, and a second source of truth drifts -- which would leave us
exactly where we started, but with false confidence. So the code stays the only
source: this module reads it, and `test_coherence` fails the build whenever the
committed JSON no longer matches. The registry therefore cannot be stale; the
only way to change a parameter is to regenerate and re-read the diff.

Regenerate with::

    python -m aria_core.pocket_parameters --write
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

# Pocket module -> human label. Explicit rather than globbed: a pocket that
# stops being listed here should be a visible deletion in review, not a silent
# disappearance from the registry.
POCKET_MODULES = {
    "solana_late_bonding_shadow": "LATE-BONDING (70-98.5% of the bonding curve)",
    "solana_fresh_launch_fast_discovery_shadow": "FAST-DISCOVERY (control pocket)",
    "solana_fresh_launch_ws_exit_shadow": "WS-EXIT (stopped as a pocket -- still the SHARED exit rule)",
    "solana_fresh_launch_shadow": "FRESH-LAUNCH",
    "solana_support_bounce_shadow": "SUPPORT-BOUNCE v1",
    "solana_support_bounce_v2_shadow": "SUPPORT-BOUNCE v2",
    "solana_pump_shadow": "PUMP (Solana)",
    "robinhood_pump_shadow": "PUMP (Robinhood Chain)",
    "robinhood_pump_v2_shadow": "PUMP v2 (Robinhood Chain, aggressive scale-out)",
    "base_momentum_shadow": "MOMENTUM (Base, shadow-only -- real pipeline paused since 17/08)",
    "solana_variant_shadow": "SOLANA VARIANTS (3 parallel entry thresholds)",
    "dip_recovery_shadow": "DIP-RECOVERY (operator-proposed entry signal)",
    "narrative_signal_shadow": "NARRATIVE (trade the news, not the chart)",
    "wallet_copy_shadow": "WALLET-COPY (8 verified Base wallets)",
    "ath_shadow": "ATH persistence",
    # Observers rather than pockets: they log a judgment and never open a
    # position. Listed all the same -- their thresholds decide what the real
    # pockets are allowed to see, so an unread one is just as costly.
    "early_legitimacy_shadow": "OBSERVER -- early on-chain legitimacy",
    "chasing_filter_shadow": "OBSERVER -- anti-chasing filter",
    "candle_staleness_shadow": "OBSERVER -- candle freshness (#261)",
}

REGISTRY_PATH = Path(__file__).resolve().parents[4] / "docs" / "pocket-parameters.json"

_SCALARS = (int, float, str, bool, type(None))

# A tuple longer than this is a lookup table, not a knob.
_MAX_TUPLE_LEN = 8


def _is_tunable(value) -> bool:
    if isinstance(value, _SCALARS):
        return True
    if isinstance(value, tuple) and 0 < len(value) <= _MAX_TUPLE_LEN:
        return all(
            isinstance(v, _SCALARS)
            or (isinstance(v, tuple) and len(v) <= 3 and all(isinstance(x, _SCALARS) for x in v))
            for v in value
        )
    return False


def _leading_comment(lines: list[str], lineno: int) -> str | None:
    """The comment block directly above a constant, flattened to one line.

    The comment is where the JUSTIFICATION lives (sample size, measured PnL,
    why this value and not another), so a registry without it would list
    numbers with no way to judge them -- the exact reading failure it exists
    to prevent."""
    out: list[str] = []
    i = lineno - 2  # 0-indexed line above the assignment
    while i >= 0:
        stripped = lines[i].strip()
        if not stripped.startswith("#"):
            break
        out.append(stripped.lstrip("#").strip())
        i -= 1
    if not out:
        return None
    return " ".join(reversed(out)) or None


def extract_parameters(source: str) -> dict:
    """Every module-level UPPER_CASE tuning constant, with its value, line and
    justification.

    Scalars, plus TUPLES of scalars or of scalar pairs. The original rule was
    scalars only, on the reasoning that a collection constant is a lookup
    table rather than a knob. That was too broad and it cost us: 21/08, the
    single most important setting of the new exit -- `PROFIT_LADDER =
    ((50, .25), (100, .25), (200, .25))` -- was silently absent from the
    registry built precisely so no setting could go unread. Dicts and long
    lists stay excluded: those really are lookup tables."""
    tree = ast.parse(source)
    lines = source.splitlines()
    params: dict = {}
    for node in tree.body:
        # 23/08 -- ANNOTATED assignments count too. `FIXED_STOP_PCT = 5.0` was
        # read, but the moment the same knob was turned off as
        # `FIXED_STOP_PCT: float | None = None` it VANISHED from the registry
        # instead of showing up as disabled. A registry whose whole purpose is
        # that no setting goes unread must not lose a setting exactly when
        # someone changes it -- a disabled knob is a knob, and its `why`
        # comment is the most valuable one there is. `None` was already an
        # accepted value (`_SCALARS`); only the annotated form was missed.
        if isinstance(node, ast.AnnAssign):
            target, value_node = node.target, node.value
            if value_node is None:  # a bare declaration holds no setting
                continue
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value_node = node.targets[0], node.value
        else:
            continue
        if not isinstance(target, ast.Name) or not target.id.isupper():
            continue
        try:
            value = ast.literal_eval(value_node)
        except (ValueError, SyntaxError):
            continue
        if not _is_tunable(value):
            continue
        params[target.id] = {
            "value": value,
            "line": node.lineno,
            "why": _leading_comment(lines, node.lineno),
        }
    return params


def build_registry() -> dict:
    here = Path(__file__).resolve().parent
    registry: dict = {}
    for module, label in POCKET_MODULES.items():
        path = here / f"{module}.py"
        if not path.exists():
            # A retired pocket is reported as such rather than dropped, so the
            # registry never quietly loses a line.
            registry[module] = {"label": label, "status": "module absent", "parameters": {}}
            continue
        registry[module] = {
            "label": label,
            "status": "present",
            "parameters": extract_parameters(path.read_text()),
        }
    return registry


def render() -> str:
    return json.dumps(build_registry(), indent=2, sort_keys=True) + "\n"


def is_current() -> bool:
    """False when the committed registry no longer matches the code."""
    if not REGISTRY_PATH.exists():
        return False
    return REGISTRY_PATH.read_text() == render()


if __name__ == "__main__":  # pragma: no cover -- operator/CI entry point
    import sys

    if "--write" in sys.argv:
        REGISTRY_PATH.write_text(render())
        print(f"wrote {REGISTRY_PATH}")
    else:
        print("current" if is_current() else "STALE -- rerun with --write")
