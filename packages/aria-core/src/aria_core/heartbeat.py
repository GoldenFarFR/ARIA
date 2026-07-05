from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from aria_core.knowledge.curriculum_cooldown import cooldown_minutes_remaining
from aria_core.memory import append_memory
from aria_core.paths import data_dir
from aria_core.models import HeartbeatTask
from aria_core.skills.portfolio_skill import execute_portfolio_analysis
from aria_core.skills.repertoire_skill import execute_develop_repertoire
from aria_core.skills.zhc_bridge import execute_zhc_bridge
from aria_core.runtime import settings

logger = logging.getLogger(__name__)

_START_TIME = datetime.now(timezone.utc)
_LAST_HEARTBEAT: datetime | None = None

HEARTBEAT_TASKS = [
    HeartbeatTask(
        id="portfolio_scan",
        name="Portfolio scan",
        description="Automatic Aria Market watchlist analysis",
        interval_minutes=30,
    ),
    HeartbeatTask(
        id="zhc_watch",
        name="ZHC/JUNO watch",
        description="ZHC Institute benchmark metrics",
        interval_minutes=120,
        enabled=False,
    ),
    HeartbeatTask(
        id="repertoire_grow",
        name="Repertoire growth",
        description="Strategic repertoire suggestions",
        interval_minutes=1440,
    ),
    HeartbeatTask(
        id="x_curiosity",
        name="X curiosity learning",
        description="Scan ZHC peer agents on X (requires X API keys)",
        interval_minutes=180,
        enabled=False,
    ),
    HeartbeatTask(
        id="x_mentions_learn",
        name="X mentions auto-reply",
        description="Reply to @Aria_ZHC mentions (X_ALLOW_REPLIES; learn opt-in)",
        interval_minutes=90,
        enabled=False,
    ),
    HeartbeatTask(
        id="entrepreneur_cultivate",
        name="Entrepreneur cultivation",
        description="Study ZHC peers + track $50/mo revenue goal",
        interval_minutes=1440,
        enabled=True,
    ),
    HeartbeatTask(
        id="launchpad_watch",
        name="BASE launchpad watch",
        description="Refresh launchpad pick (volume, builders, community, exposure)",
        interval_minutes=1440,
    ),
    HeartbeatTask(
        id="founder_ping",
        name="Founder initiative ping",
        description="Spontaneous LLM idea + optional /directive for operator (Telegram)",
        interval_minutes=1440,
        enabled=False,
    ),
    HeartbeatTask(
        id="epistemic_replay",
        name="Epistemic replay",
        description="Re-verify uncertain calibrated answers via web",
        interval_minutes=1440,
        enabled=True,
    ),
    HeartbeatTask(
        id="exposure_curriculum",
        name="Exposure curriculum",
        description="Daily epistemic training questions for operator",
        interval_minutes=1440,
        enabled=True,
    ),
    HeartbeatTask(
        id="cultivation_curriculum",
        name="Broad cultivation",
        description="Geo, macro, regulation, product — study then ship (Kelly model)",
        interval_minutes=1440,
        enabled=True,
    ),
    HeartbeatTask(
        id="app_idea_poll",
        name="App factory poll",
        description="Weekly 3 app ideas — vote app 1/2/3",
        interval_minutes=10080,
        enabled=False,
    ),
    HeartbeatTask(
        id="tweet_schedule",
        name="Scheduled X posts",
        description="Publish /x compose tweets at operator local time",
        interval_minutes=1,
        enabled=True,
    ),
    HeartbeatTask(
        id="avatar_style_refresh",
        name="Avatar style refresh",
        description="Grok Imagine — nouveau style depuis l'ancre (14 jours min, validation opérateur)",
        interval_minutes=720,
        enabled=True,
    ),
    HeartbeatTask(
        id="visual_autonomy",
        name="Visual identity autonomy",
        description="Ancre opérateur → Imagine avatar + bannière X (vérif 24h, style 14j)",
        interval_minutes=1440,
        enabled=True,
    ),
    HeartbeatTask(
        id="self_banner_curiosity",
        name="Self banner curiosity",
        description="Boucle curiosite banniere X proactive (6h)",
        interval_minutes=360,
        enabled=True,
    ),
    HeartbeatTask(
        id="x_profile_sync",
        name="X profile sync",
        description="Bio, site web et nom @Aria_ZHC alignés sur narrative Vanguard",
        interval_minutes=1440,
        enabled=True,
    ),
    HeartbeatTask(
        id="acp_provider_poll",
        name="ACP provider poll",
        description="Drain ACP events file and fulfill marketplace jobs (local acp-cli)",
        interval_minutes=5,
        enabled=False,
    ),
    HeartbeatTask(
        id="acp_market_scan",
        name="ACP market intelligence",
        description="Browse marketplace — offre/demande, gaps, suggestions workflows",
        interval_minutes=1440,
        enabled=False,
    ),
    HeartbeatTask(
        id="acp_email_watch",
        name="ACP email job watch",
        description="Poll agents.world inbox — job alerts (degraded mode when Virtuals Privy 500)",
        interval_minutes=10,
        enabled=False,
    ),
    HeartbeatTask(
        id="showcase_pr_watch",
        name="Showcase PR auto-reply",
        description="Watch Virtual-Protocol/acp-cli-demos#37 — auto-reply to reviewer comments",
        interval_minutes=15,
        enabled=False,
    ),
    HeartbeatTask(
        id="revenue_autonomy",
        name="Revenue autonomy cycle",
        description="Poll ACP, scan marché, promo X, initiative — sans relance opérateur",
        interval_minutes=360,
        enabled=False,
    ),
    HeartbeatTask(
        id="health_watch",
        name="Health regression watch",
        description="Ping /api/health — issue apres 3 echecs",
        interval_minutes=15,
        enabled=True,
    ),
    HeartbeatTask(
        id="qi_promote",
        name="QI promotion check",
        description="Propose palier suivant si gaps resolus / metriques OK",
        interval_minutes=1440,
        enabled=True,
    ),
]


def _sync_x_curiosity_enabled() -> None:
    for task in HEARTBEAT_TASKS:
        if task.id == "x_curiosity":
            task.enabled = bool(
                getattr(settings, "x_curiosity_enabled", False)
                and (settings.x_bearer_token or settings.x_api_key)
            )
        if task.id == "x_mentions_learn":
            from aria_core.gateway.x_engagement import mentions_reply_enabled

            task.enabled = mentions_reply_enabled()
        if task.id == "zhc_watch":
            task.enabled = bool(settings.aria_juno_outreach)
        if task.id == "founder_ping":
            from aria_core.proactive import proactive_ideas_enabled

            task.enabled = proactive_ideas_enabled()
            if task.enabled and settings.aria_autonomous:
                task.interval_minutes = max(
                    240,
                    int(os.environ.get("ARIA_AUTONOMY_INITIATIVE_HOURS", "8") or 8) * 60,
                )
        if task.id == "avatar_style_refresh":
            from aria_core.avatar_style_refresh import _enabled, is_image_generation_available
            from aria_core.visual_autonomy import visual_autonomy_enabled

            task.enabled = (
                _enabled()
                and is_image_generation_available()
                and not visual_autonomy_enabled()
            )
        if task.id == "visual_autonomy":
            from aria_core.avatar_style_refresh import is_image_generation_available
            from aria_core.visual_autonomy import visual_autonomy_enabled

            task.enabled = visual_autonomy_enabled() and is_image_generation_available()
            raw_iv = int(getattr(settings, "aria_visual_autonomy_interval_minutes", 1440) or 1440)
            task.interval_minutes = max(360, raw_iv)
        if task.id == "self_banner_curiosity":
            from aria_core.visual_autonomy import visual_autonomy_enabled

            task.enabled = not visual_autonomy_enabled()
        if task.id == "x_profile_sync":
            from aria_core.gateway.x_twitter import is_x_post_configured

            task.enabled = is_x_post_configured()
        if task.id == "acp_provider_poll":
            from aria_core.skills.acp_cli import is_acp_available

            task.enabled = (
                bool(getattr(settings, "aria_acp_provider_enabled", False))
                and is_acp_available()
                and bool((getattr(settings, "aria_acp_events_file", None) or "").strip())
            )
        if task.id == "acp_market_scan":
            from aria_core.skills.acp_cli import is_acp_available

            task.enabled = is_acp_available()
        if task.id == "acp_email_watch":
            from aria_core.skills.acp_cli import is_acp_available

            task.enabled = is_acp_available()
        if task.id == "showcase_pr_watch":
            from aria_core.skills.github_skill import github_configured
            from aria_core.skills.showcase_pr_watcher import load_watch_targets

            task.enabled = github_configured() and bool(load_watch_targets())
        if task.id == "revenue_autonomy":
            from aria_core.autonomy_revenue import revenue_autonomy_enabled
            from aria_core.skills.acp_cli import is_acp_available

            task.enabled = revenue_autonomy_enabled() and is_acp_available()
            if task.enabled:
                task.interval_minutes = max(
                    60,
                    int(os.environ.get("ARIA_AUTONOMY_CYCLE_MINUTES", "360") or 360),
                )

_HEARTBEAT_STATE_PATH = data_dir() / "heartbeat_state.json"


def _load_heartbeat_state() -> dict[str, str]:
    if not _HEARTBEAT_STATE_PATH.exists():
        return {}
    try:
        raw = json.loads(_HEARTBEAT_STATE_PATH.read_text(encoding="utf-8"))
        last_runs = raw.get("last_runs") or {}
        return {k: v for k, v in last_runs.items() if isinstance(k, str) and isinstance(v, str)}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_heartbeat_state(last_runs: dict[str, datetime]) -> None:
    payload = {
        "last_runs": {
            task_id: dt.astimezone(timezone.utc).isoformat()
            for task_id, dt in last_runs.items()
        }
    }
    _HEARTBEAT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HEARTBEAT_STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _task_due(task_id: str, interval_minutes: int, last_runs: dict[str, datetime]) -> bool:
    last = last_runs.get(task_id)
    if last is not None:
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60.0
        if elapsed < interval_minutes:
            return False
    persisted = _load_heartbeat_state().get(task_id)
    if cooldown_minutes_remaining(persisted, interval_minutes=interval_minutes) > 0:
        return False
    return True


class AriaHeartbeat:
    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_runs: dict[str, datetime] = {}
        self._hydrate_last_runs()

    def _hydrate_last_runs(self) -> None:
        for task_id, iso_ts in _load_heartbeat_state().items():
            try:
                dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                self._last_runs[task_id] = dt
            except (ValueError, TypeError):
                continue

    async def start(self) -> None:
        if self._running:
            return
        _sync_x_curiosity_enabled()
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        await asyncio.sleep(30)
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("Heartbeat tick failed: %s", exc)
                append_memory("heartbeat", f"Heartbeat error: {exc}")
            await asyncio.sleep(60)

    async def _tick(self) -> None:
        global _LAST_HEARTBEAT
        # Kill-switch : en pause, aucun job planifié ne tourne (tweets programmés, ACP,
        # revenue, mentions, profile/visual sync, health watch…). La boucle reste vivante
        # et reprend telle quelle au /start. _LAST_HEARTBEAT n'est pas touché : /status
        # affiche l'état de pause explicitement.
        from aria_core import outgoing_pause

        if outgoing_pause.is_paused():
            return
        now = datetime.now(timezone.utc)
        _sync_x_curiosity_enabled()

        for hb_task in HEARTBEAT_TASKS:
            if not hb_task.enabled:
                continue
            if not _task_due(hb_task.id, hb_task.interval_minutes, self._last_runs):
                continue

            await self._run_task(hb_task.id)
            self._last_runs[hb_task.id] = now
            hb_task.last_run = now

        _save_heartbeat_state(self._last_runs)
        _LAST_HEARTBEAT = now

    async def _notify_telegram(self, text: str) -> None:
        try:
            from aria_core.gateway.telegram_bot import send_message
            await send_message(text)
        except Exception as exc:
            logger.warning("Telegram notify failed: %s", exc)

    async def _run_task(self, task_id: str) -> None:
        if task_id == "portfolio_scan":
            summary, data = await execute_portfolio_analysis(lang="en")
            if data.get("items", 0) > 0:
                score = data.get("avg_score", 0)
                append_memory("heartbeat", f"[portfolio_scan] avg score: {score:.1f}")
                await self._notify_telegram(
                    f"📊 Portfolio scan\nAverage score: {score:.1f}/100\n{summary[:500]}"
                )

        elif task_id == "zhc_watch":
            summary, _, _ = await execute_zhc_bridge("benchmark", lang="en")
            append_memory("heartbeat", f"[zhc_watch] ZHC benchmark\n{summary[:200]}")

        elif task_id == "x_curiosity":
            from aria_core.curiosity import run_curiosity_cycle
            result = await run_curiosity_cycle()
            if result.get("insights", 0) > 0:
                append_memory("heartbeat", f"[x_curiosity] {result['insights']} insights pending")

        elif task_id == "x_mentions_learn":
            from aria_core.gateway.x_engagement import run_mentions_learn_cycle
            result = await run_mentions_learn_cycle()
            if result.get("processed", 0) > 0:
                append_memory(
                    "heartbeat",
                    f"[x_mentions] {result['processed']} learned, "
                    f"{result.get('replied', 0)} replied, {result.get('liked', 0)} liked",
                )

        elif task_id == "repertoire_grow":
            summary, data = await execute_develop_repertoire(lang="en")
            suggestions = data.get("suggestions", [])
            append_memory("heartbeat", f"[repertoire_grow] {suggestions[:1]}")

        elif task_id == "entrepreneur_cultivate":
            from aria_core.skills.entrepreneur_skill import execute_entrepreneur
            from aria_core.revenue_goals import progress_summary

            summary, data = await execute_entrepreneur("cultivation cycle", lang="en")
            prog = progress_summary("en")
            append_memory("entrepreneur", f"[heartbeat] {prog}")

        elif task_id == "launchpad_watch":
            from aria_core.knowledge.seed import seed_launchpad_knowledge, seed_zhc_identity_knowledge
            from aria_core.knowledge.base_launchpads import primary_pick, touch_refresh

            await seed_zhc_identity_knowledge()
            await seed_launchpad_knowledge()
            pick = primary_pick(holding_context=True)
            touch_refresh()
            append_memory(
                "launchpad",
                f"[watch] Vanguard pick remains {pick.name} — vol {pick.volume} "
                f"builders {pick.builders} community {pick.community}",
            )

        elif task_id == "founder_ping":
            from aria_core.proactive import run_founder_ping

            msg = await run_founder_ping(lang="fr")
            if msg:
                await self._notify_telegram(f"💡 Initiative ARIA\n\n{msg}")

        elif task_id == "epistemic_replay":
            from aria_core.knowledge.epistemic_replay import run_epistemic_replay

            result = await run_epistemic_replay(limit=3)
            if result.get("replayed", 0) > 0:
                append_memory(
                    "epistemic",
                    f"[replay] {result['replayed']} answer(s) web-verified",
                )

        elif task_id == "exposure_curriculum":
            from aria_core.knowledge.exposure_curriculum import generate_curriculum_message

            msg = generate_curriculum_message("fr")
            if msg:
                append_memory("epistemic", f"[curriculum] {msg[:400]}")
                if bool(getattr(settings, "aria_curriculum_notify_operator", False)):
                    await self._notify_telegram(msg)

        elif task_id == "cultivation_curriculum":
            from aria_core.knowledge.cultivation_curriculum import generate_cultivation_message

            msg = generate_cultivation_message("fr")
            if msg:
                append_memory("entrepreneur", f"[cultivation] {msg[:400]}")
                if bool(getattr(settings, "aria_curriculum_notify_operator", False)):
                    await self._notify_telegram(msg)

        elif task_id == "app_idea_poll":
            from aria_core.knowledge.app_idea_poll import run_app_idea_poll_cycle

            result = await run_app_idea_poll_cycle(lang="fr")
            await self._notify_telegram(result["message"])
            append_memory("entrepreneur", "[app_poll] weekly 3-app poll sent")

        elif task_id == "tweet_schedule":
            from aria_core.tweet_compose_workflow import process_scheduled_tweets

            result = await process_scheduled_tweets()
            if result.get("published"):
                append_memory("x", "[compose] scheduled tweet published")

        elif task_id == "avatar_style_refresh":
            from aria_core.avatar_style_refresh import run_refresh_cycle

            result = await run_refresh_cycle(notify=True)
            if result.get("ok"):
                pending = result.get("pending") or {}
                append_memory(
                    "avatar",
                    f"[style_refresh] preview {pending.get('style_label', '')[:80]}",
                )

        elif task_id == "visual_autonomy":
            from aria_core.visual_autonomy import run_visual_autonomy_cycle

            result = await run_visual_autonomy_cycle(lang="fr", notify=True)
            if result.get("ok"):
                av = result.get("avatar") or {}
                bn = result.get("banner") or {}
                append_memory(
                    "avatar",
                    f"[visual_autonomy] avatar={av.get('applied', av.get('skipped'))} "
                    f"banner={bn.get('uploaded', bn.get('reason', '-'))}",
                )
            elif result.get("reason") == "no_identity_anchor":
                append_memory("avatar", "[visual_autonomy] en attente ancre — photo /avatar")

        elif task_id == "x_profile_sync":
            from aria_core.x_profile import sync_x_profile

            result = await sync_x_profile()
            if result.get("synced"):
                append_memory(
                    "comms",
                    f"[x_profile] heartbeat sync drift={result.get('drift')}",
                )

        elif task_id == "self_banner_curiosity":
            from aria_core.self_maintenance import run_curiosity_x_banner_cycle

            summary = await run_curiosity_x_banner_cycle(lang="fr")
            append_memory("self-improve", f"[banner_curiosity] {summary[:250]}")
            notify_markers = ("Action bloquee", "Echec", "publiee", "bloquee")
            if any(m in summary for m in notify_markers):
                await self._notify_telegram(f"Banniere X (curiosite 6h)\n\n{summary[:1500]}")

        elif task_id == "acp_provider_poll":
            from aria_core.skills.acp_provider_skill import run_provider_cycle
            from aria_core.skills.acp_cli import is_acp_available

            if not is_acp_available():
                return
            if not bool(getattr(settings, "aria_acp_provider_enabled", False)):
                return
            events_file = (getattr(settings, "aria_acp_events_file", None) or "").strip()
            result = await run_provider_cycle(events_file or None)
            if result.get("processed", 0) > 0:
                append_memory(
                    "acp",
                    f"[heartbeat] provider poll — {result.get('processed')} events",
                )
                await self._notify_telegram(
                    f"ACP provider — {result.get('processed')} job(s) traité(s)\n"
                    f"Actions : {', '.join(result.get('actions') or [])}"
                )

        elif task_id == "acp_email_watch":
            from aria_core.skills.acp_email_watcher import run_email_watch

            watch = await run_email_watch()
            alerts = watch.get("new_alerts") or []
            if alerts:
                append_memory(
                    "acp_email",
                    f"[heartbeat] {len(alerts)} email job alert(s)",
                )
                for alert in alerts[:3]:
                    jids = ", ".join(alert.get("job_ids") or []) or "?"
                    body = (
                        f"ACP email — job detected\n"
                        f"Subject: {alert.get('subject', '?')[:120]}\n"
                        f"Job(s): {jids}\n"
                        f"Command: prepare job acp {jids.split(',')[0] if jids != '?' else '<id>'} "
                        f"offering {alert.get('offering') or 'analyse_lite_x1'}"
                    )
                    await self._notify_telegram(body[:1500])

        elif task_id == "showcase_pr_watch":
            from aria_core.skills.showcase_pr_watcher import run_showcase_pr_watch

            scan = await run_showcase_pr_watch()
            replied = scan.get("replied") or []
            if replied:
                append_memory(
                    "github",
                    f"[heartbeat] showcase_pr_watch — {len(replied)} auto-repl(ies)",
                )
                for row in replied[:2]:
                    body = (
                        f"Showcase PR — auto-reply posted\n"
                        f"To: @{row.get('trigger_author')}\n"
                        f"URL: {row.get('reply_url') or row.get('trigger_url')}"
                    )
                    await self._notify_telegram(body[:1500])

        elif task_id == "acp_market_scan":
            from aria_core.skills.acp_market_intelligence import run_market_scan

            scan = await run_market_scan()
            gaps = (scan.get("market") or {}).get("categories") or {}
            top_gap = max(gaps.items(), key=lambda kv: kv[1], default=(None, 0))
            append_memory(
                "acp_market",
                f"[heartbeat] scan source={scan.get('source')} agents={scan.get('agent_count')} "
                f"top_cat={top_gap[0]}",
            )
            if scan.get("ok") and top_gap[0]:
                await self._notify_telegram(
                    f"ACP market scan — {scan.get('agent_count', 0)} agents\n"
                    f"Top demande : {top_gap[0]} (score {top_gap[1]})\n"
                    f"Commande : scan marché acp"
                )

        elif task_id == "revenue_autonomy":
            from aria_core.autonomy_revenue import run_revenue_autonomy_cycle

            cycle = await run_revenue_autonomy_cycle(lang="fr")
            actions = cycle.get("actions") or []
            if actions:
                append_memory("autonomy", f"[heartbeat] revenue_autonomy — {actions}")
                body = "Autonomie revenu — actions :\n" + "\n".join(f"• {a}" for a in actions)
                if cycle.get("initiative"):
                    body += f"\n\nInitiative :\n{cycle['initiative'][:800]}"
                await self._notify_telegram(body[:1500])

        elif task_id == "health_watch":
            from aria_core.health_watch import check_health_regression

            result = await check_health_regression()
            if not result.get("ok") and result.get("gap"):
                await self._notify_telegram(
                    "Health Render regression — issue ouverte\n\n"
                    f"{result.get('detail', '')[:500]}"
                )

        elif task_id == "qi_promote":
            from aria_core.qi_promote import run_qi_promotion_check

            await run_qi_promotion_check(lang="fr")

    def get_status(self) -> dict:
        return {
            "uptime_since": _START_TIME,
            "last_heartbeat": _LAST_HEARTBEAT,
            "tasks": HEARTBEAT_TASKS,
        }


aria_heartbeat = AriaHeartbeat()