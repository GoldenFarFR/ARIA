"""Trace verbose ouvrier — affichage raisonnement/étapes (stderr, style Cursor)."""
from __future__ import annotations

import os
import re
import time

_PHASE_COLORS = {
    "pensee": "\033[96m",
    "moteur": "\033[93m",
    "bootstrap": "\033[90m",
    "preflight": "\033[92m",
    "outil": "\033[95m",
    "resultat": "\033[37m",
    "fallback": "\033[33m",
}
_RESET = "\033[0m"
_FINAL_COLOR = "\033[1;92m"
_PROOF_COLOR = "\033[90m"
_ALWAYS_PHASES = frozenset({"moteur", "outil", "fallback", "resultat"})


def is_verbose() -> bool:
    return os.environ.get("ARIA_OUVRIER_VERBOSE", "").strip().lower() in ("1", "true", "yes", "on")


def set_verbose(enabled: bool) -> None:
    os.environ["ARIA_OUVRIER_VERBOSE"] = "1" if enabled else ""


def trace(phase: str, message: str) -> None:
    if not is_verbose() and phase not in _ALWAYS_PHASES:
        return
    color = _PHASE_COLORS.get(phase, "")
    prefix = f"{color}[{phase}]{_RESET}" if color else f"[{phase}]"
    # stdout : ordre stable dans KART PowerShell (stderr arrive souvent après)
    for line in message.splitlines() or [""]:
        print(f"{prefix} {line}", flush=True)


def trace_block(phase: str, title: str, body: str, *, max_lines: int = 12) -> None:
    if not is_verbose() and phase not in _ALWAYS_PHASES:
        return
    lines = body.splitlines()
    if len(lines) > max_lines:
        head = lines[:max_lines]
        omitted = len(lines) - max_lines
        body_show = "\n".join(head) + f"\n… ({omitted} lignes omises)"
    else:
        body_show = body
    trace(phase, f"── {title} ──\n{body_show}")


class StepTimer:
    def __init__(self, label: str) -> None:
        self.label = label
        self.t0 = 0.0

    def __enter__(self) -> StepTimer:
        self.t0 = time.perf_counter()
        if is_verbose():
            trace("pensee", f"▶ {self.label}…")
        return self

    def __exit__(self, *_) -> None:
        if is_verbose():
            sec = time.perf_counter() - self.t0
            trace("pensee", f"✓ {self.label} ({sec:.1f}s)")


def emit_final(text: str) -> None:
    """Réponse ARIA — une ligne distincte (pas une trace [pensee]/[final])."""
    body = (text or "").strip()
    if not body:
        return
    one_line = re.sub(r"\s+", " ", body)
    print("", flush=True)
    print(f"{_FINAL_COLOR}› {one_line}{_RESET}", flush=True)
    print("", flush=True)


def emit_proof(text: str) -> None:
    """Preuve runtime — grisée, sous la réponse."""
    block = (text or "").strip()
    if not block:
        return
    for line in block.splitlines():
        print(f"{_PROOF_COLOR}  {line}{_RESET}", flush=True)