"""Fictional training portfolio — operator learning loop (business simulation)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from aria_core.paths import memory_dir

PORTFOLIO_PATH = memory_dir() / "training_portfolio.md"
DEFAULT_BALANCE = 1000.0


def portfolio_path() -> Path:
    return PORTFOLIO_PATH


def _ensure_file() -> Path:
    path = portfolio_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# ARIA — Portefeuille d'entraînement (fictif)\n\n"
            f"**Solde actuel : {DEFAULT_BALANCE:.2f} $**\n",
            encoding="utf-8",
        )
    return path


def read_portfolio_text(limit: int = 6000) -> str:
    path = _ensure_file()
    return path.read_text(encoding="utf-8")[:limit]


def get_balance() -> float:
    text = read_portfolio_text(limit=800)
    m = re.search(r"Solde actuel\s*\|\s*\*\*([0-9]+(?:\.[0-9]+)?)\s*\$?\*\*", text)
    if m:
        return float(m.group(1))
    m = re.search(r"Solde actuel\s*:\s*\*\*([0-9]+(?:\.[0-9]+)?)", text)
    if m:
        return float(m.group(1))
    return DEFAULT_BALANCE


def append_entry(
    *,
    title: str,
    reasoning: str,
    action: str,
    result: str,
    lesson: str,
    balance: float | None = None,
) -> str:
    path = _ensure_file()
    content = path.read_text(encoding="utf-8")
    idx = len(re.findall(r"^### \[#\d+\]", content, re.MULTILINE)) + 1
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bal = balance if balance is not None else get_balance()
    entry = (
        f"\n### [#{idx:03d}] {ts} — {title}\n\n"
        f"**Raisonnement**\n{reasoning.strip()}\n\n"
        f"**Action**\n{action.strip()}\n\n"
        f"**Résultat**\n{result.strip()}\n\n"
        f"**Leçon**\n{lesson.strip()}\n"
    )
    if "## Historique des actions" not in content:
        content += "\n## Historique des actions\n"
    content += entry
    content = re.sub(
        r"(Solde actuel\s*\|\s*)\*\*[0-9]+(?:\.[0-9]+)?\s*\$?\*\*",
        rf"\g<1>**{bal:.2f} $**",
        content,
        count=1,
    )
    path.write_text(content, encoding="utf-8")
    return str(path)


def portfolio_summary() -> str:
    text = read_portfolio_text(limit=3500)
    balance = get_balance()
    return f"Solde fictif : {balance:.2f} $\n\n{text}"