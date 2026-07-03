"""Trace verbose ouvrier — affichage raisonnement/étapes (stderr, style Cursor)."""
from __future__ import annotations

import os
import sys
import time

_PHASE_COLORS = {
    "pensee": "\033[96m",
    "moteur": "\033[93m",
    "bootstrap": "\033[90m",
    "preflight": "\033[92m",
    "outil": "\033[95m",
    "resultat": "\033[37m",
    "fallback": "\033[33m",
    "final": "\033[97m",
}
_RESET = "\033[0m"


def is_verbose() -> bool:
    return os.environ.get("ARIA_OUVRIER_VERBOSE", "").strip().lower() in ("1", "true", "yes", "on")


def set_verbose(enabled: bool) -> None:
    os.environ["ARIA_OUVRIER_VERBOSE"] = "1" if enabled else ""


def trace(phase: str, message: str) -> None:
    if not is_verbose():
        return
    color = _PHASE_COLORS.get(phase, "")
    prefix = f"{color}[{phase}]{_RESET}" if color else f"[{phase}]"
    # stdout : ordre stable dans KART PowerShell (stderr arrive souvent après)
    for line in message.splitlines() or [""]:
        print(f"{prefix} {line}", flush=True)


def trace_block(phase: str, title: str, body: str, *, max_lines: int = 12) -> None:
    if not is_verbose():
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
        trace("pensee", f"▶ {self.label}…")
        return self

    def __exit__(self, *_) -> None:
        sec = time.perf_counter() - self.t0
        trace("pensee", f"✓ {self.label} ({sec:.1f}s)")