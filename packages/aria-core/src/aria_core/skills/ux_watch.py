"""ux_watch — ARIA observes the real site herself (Playwright screenshots,
desktop + mobile) and PROPOSES her own UX micro-improvement details, beyond
what Claude Code builds manually (task #155, 07/13, explicit operator request).

Capture -> visual reading via ``llm_vision.vision_analyze`` (a piece already
wired for avatar consistency, reused as-is, no duplicate) -> comparison
against the luxury-grade UX reference set by CLAUDE.md ("Permanent
Standards," not invented here). Same strict doctrine as
``knowledge_inbox.py``/``claude_mentor.py``/``pump_dump_autopsy.py``:
PROPOSES a GitHub ISSUE (label ``aria-ux-proposal``) -- never a redesign,
never a direct code change, never an autonomous commit or merge.

Gated OFF by default (``ARIA_UX_WATCH_ENABLED``) -- NOT
``ARIA_VISION_ENABLED``: that gate is specific to the admin-only Telegram
photo feature (``gateway/telegram_bot.py``), unrelated to this heartbeat
cycle. One cycle per day maximum (deduplicated by date) -- vision/LLM cost +
Playwright/Chromium weight in the Docker image (+300-500 MB measured, same
doctrine as ffmpeg/#23) accepted as-is (operator decision, 07/13).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
TARGET_REPO = "ARIA"

# Deliberate mirror of the private `release_pipeline._SITE_URL` constant --
# cross-module import of a private name avoided, duplicating a simple string
# is more robust than coupling to another module's implementation detail.
SITE_URL = "https://ariavanguardzhc.com"

# Fixed viewports -- desktop + mobile, priority to what the operator actually
# sees, not an exhaustive device matrix.
VIEWPORTS = [
    ("desktop", 1440, 900),
    ("mobile", 375, 844),
]

_UX_REFERENCE = (
    "Référentiel UX gamme luxe (CLAUDE.md, Normes permanentes -- ne pas en inventer "
    "d'autre) :\n"
    "- Cohérence système de design : palette/typo/espacements alignés sur l'existant "
    "du site (charcoal/#d4d0c8, pas de couleurs hors palette).\n"
    "- Contrastes lisibles (WCAG AA minimum) sur texte et éléments interactifs.\n"
    "- Focus clavier visible sur tout contrôle interactif (boutons, liens, champ de "
    "saisie).\n"
    "- prefers-reduced-motion respecté partout où il y a animation.\n"
    "- Responsive mobile-first : aucun élément coupé/superposé/débordant sur 375px "
    "et 1440px.\n"
    "- Zéro trace IA visuelle (pas de générique/template, cohérence avec le ton "
    "gamme luxe).\n"
    "- États de chargement/erreur jamais silencieux (pas de bouton mort, pas "
    "d'attente bloquante).\n"
    "- Accessibilité de base : labels ARIA sur les contrôles non textuels."
)

_UX_SYSTEM = (
    "Tu es un observateur externe UX pour Aria Vanguard ZHC (produit gamme luxe). "
    "On te montre une capture d'écran RÉELLE du site en production, à un viewport "
    "donné.\n\n" + _UX_REFERENCE + "\n\n"
    "Propose UNIQUEMENT des micro-détails concrets et actionnables observés SUR "
    "CETTE capture -- jamais une refonte, jamais un changement de code direct, "
    "jamais une invention au-delà de ce qui est visible. Si rien de concret ne "
    "ressort, dis-le clairement plutôt que d'inventer un détail creux. Réponds "
    'STRICTEMENT en JSON : {"findings": ["<micro-détail concret 1>", ...], '
    '"actionable": true|false}. `actionable` = false si `findings` est vide ou si '
    "rien n'est assez concret pour justifier une issue."
)


def ux_watch_enabled() -> bool:
    from aria_core.skills.github_skill import github_configured

    if not github_configured():
        return False
    return os.environ.get("ARIA_UX_WATCH_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ux_watch_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL UNIQUE,
                run_at TEXT NOT NULL,
                outcome TEXT NOT NULL,
                findings_count INTEGER NOT NULL DEFAULT 0,
                issue_url TEXT
            )
            """
        )
        await db.commit()


async def _already_ran_today() -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM ux_watch_log WHERE run_date = ?", (_today(),)
        )
        return (await cursor.fetchone()) is not None


async def _record_run(outcome: str, *, findings_count: int = 0, issue_url: str | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO ux_watch_log "
            "(run_date, run_at, outcome, findings_count, issue_url) VALUES (?, ?, ?, ?, ?)",
            (_today(), _now(), outcome, findings_count, issue_url),
        )
        await db.commit()


async def _default_screenshot(url: str, width: int, height: int) -> bytes | None:
    """Real capture via Playwright (headless Chromium) -- local import: heavy
    dependency (+Chromium binary), never loaded if the cycle is disabled or in tests."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("ux_watch: playwright not installed")
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": width, "height": height})
                await page.goto(url, wait_until="networkidle", timeout=30_000)
                return await page.screenshot(type="jpeg", quality=80, full_page=False)
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 -- a down site/timeout never breaks the cycle
        logger.info("ux_watch: capture failed (%s)", exc)
        return None


def _format_findings_body(findings_by_viewport: dict[str, list[str]]) -> str:
    lines = [
        "Micro-détails UX observés par ARIA sur des captures d'écran réelles du "
        "site (vision LLM, référentiel gamme luxe CLAUDE.md).",
        "",
    ]
    for viewport, findings in findings_by_viewport.items():
        if not findings:
            continue
        lines.append(f"### {viewport}")
        for finding in findings:
            lines.append(f"- {finding}")
        lines.append("")
    return "\n".join(lines)


async def _propose_ux_issue(findings_by_viewport: dict[str, list[str]], *, github_client=None) -> str | None:
    from aria_core.runtime import settings

    if github_client is None:
        token = (settings.github_token or "").strip()
        if not token:
            return None
        from aria_core.github_client import GitHubClient

        github_client = GitHubClient(token)

    owner = settings.github_owner
    total = sum(len(findings) for findings in findings_by_viewport.values())
    title = f"[ux_watch] {total} micro-détail(s) UX observé(s) — {_today()}"
    body = (
        _format_findings_body(findings_by_viewport)
        + "\n\n---\n*Proposition générée par ux_watch (captures d'écran réelles + "
        "lecture visuelle LLM) -- revue humaine requise avant toute intégration. "
        "Jamais un commit ni une fusion autonome, jamais une refonte.*"
    )
    try:
        issue = await github_client.create_issue(
            owner, TARGET_REPO, title, body, labels=["aria-ux-proposal"],
        )
    except Exception:  # noqa: BLE001 -- a GitHub outage must never break the cycle
        return None
    return issue.get("html_url")


async def run_ux_watch_cycle(
    *, site_url: str | None = None, screenshot_fn=None, vision_fn=None, github_client=None,
) -> dict:
    """One cycle: desktop+mobile capture of the real site -> per-viewport LLM
    visual reading -> a single grouped GitHub issue if concrete details come
    up. Fail-closed at every stage, never more than one cycle per day (deduplicated)."""
    if not ux_watch_enabled():
        return {"outcome": "skipped_disabled"}

    await _ensure_table()

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}

    if await _already_ran_today():
        return {"outcome": "skipped_already_ran_today"}

    if screenshot_fn is None:
        screenshot_fn = _default_screenshot
    if vision_fn is None:
        from aria_core.llm_vision import vision_analyze as vision_fn

    findings_by_viewport: dict[str, list[str]] = {}
    per_viewport_outcome: dict[str, str] = {}

    for name, width, height in VIEWPORTS:
        try:
            jpeg = await screenshot_fn(site_url or SITE_URL, width, height)
        except Exception as exc:  # noqa: BLE001 -- a failed capture never breaks the other viewports
            logger.warning("ux_watch: capture %s failed -- %s", name, exc)
            per_viewport_outcome[name] = "capture_failed"
            continue
        if not jpeg:
            per_viewport_outcome[name] = "capture_failed"
            continue

        try:
            raw = await vision_fn(jpeg, _UX_SYSTEM)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ux_watch: vision %s failed -- %s", name, exc)
            per_viewport_outcome[name] = "vision_failed"
            continue
        if not raw:
            per_viewport_outcome[name] = "vision_unavailable"
            continue

        try:
            data = json.loads(raw)
            actionable = bool(data.get("actionable", False))
            findings = [str(f).strip() for f in data.get("findings", []) if str(f).strip()]
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            per_viewport_outcome[name] = "parse_failed"
            continue

        per_viewport_outcome[name] = "ok"
        if actionable and findings:
            findings_by_viewport[name] = findings

    total_findings = sum(len(findings) for findings in findings_by_viewport.values())
    if total_findings == 0:
        await _record_run("no_findings")
        return {"outcome": "no_findings", "per_viewport": per_viewport_outcome}

    issue_url = await _propose_ux_issue(findings_by_viewport, github_client=github_client)
    await _record_run(
        "proposed" if issue_url else "proposal_failed",
        findings_count=total_findings,
        issue_url=issue_url,
    )
    return {
        "outcome": "proposed" if issue_url else "proposal_failed",
        "findings_count": total_findings,
        "issue_url": issue_url,
        "per_viewport": per_viewport_outcome,
    }
