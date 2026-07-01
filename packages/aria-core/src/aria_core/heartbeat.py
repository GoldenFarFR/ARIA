from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aria_core.memory import append_memory
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
        description="Automatic DEXPulse watchlist analysis",
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
        interval_minutes=360,
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
        interval_minutes=360,
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
        interval_minutes=480,
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
        id="gem_crush_daily",
        name="Gem Crush premium improve",
        description="Recherche Candy Crush / Clash Royale / Royal Match → brief → release massive (30 min)",
        interval_minutes=30,
        enabled=True,
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
        description="Grok Imagine — nouveau style depuis l'ancre (7/14 jours, validation opérateur)",
        interval_minutes=720,
        enabled=True,
    ),
    HeartbeatTask(
        id="visual_autonomy",
        name="Visual identity autonomy",
        description="Ancre opérateur → Imagine avatar + bannière X (vérif 24h, style 7j)",
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
        if task.id == "gem_crush_daily":
            from aria_core.skills.gem_crush_skill import improve_interval_minutes

            task.interval_minutes = improve_interval_minutes()


class AriaHeartbeat:
    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_runs: dict[str, datetime] = {}

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
        now = datetime.now(timezone.utc)
        _sync_x_curiosity_enabled()

        for hb_task in HEARTBEAT_TASKS:
            if not hb_task.enabled:
                continue
            last = self._last_runs.get(hb_task.id)
            if last and (now - last).total_seconds() < hb_task.interval_minutes * 60:
                continue

            await self._run_task(hb_task.id)
            self._last_runs[hb_task.id] = now
            hb_task.last_run = now

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
            await self._notify_telegram(
                f"📁 Repertoire\n{suggestions[0] if suggestions else 'No suggestions'}"
            )

        elif task_id == "entrepreneur_cultivate":
            from aria_core.skills.entrepreneur_skill import execute_entrepreneur
            from aria_core.revenue_goals import progress_summary

            summary, data = await execute_entrepreneur("cultivation cycle", lang="en")
            prog = progress_summary("en")
            append_memory("entrepreneur", f"[heartbeat] {prog}")
            if not data.get("progress", {}).get("on_track"):
                await self._notify_telegram(
                    f"🎯 Entrepreneur cultivation\n{prog}\n\n"
                    f"Next: ship first paid app v0 (Kelly — web or Play Store)."
                )

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

        elif task_id == "gem_crush_daily":
            from aria_core.skills.gem_crush_skill import run_daily_gem_crush_improve

            result = await run_daily_gem_crush_improve(lang="fr")
            status = result.get("status")
            # applied → notify_gem_crush_ship() dans gem_crush_skill (1 msg / version)
            if status in ("queue_empty", "write_denied", "local_only", "error"):
                await self._notify_telegram(
                    f"🎮 ARIA Gem Crush — {status}\n\n{result.get('message', '')[:1200]}"
                )
            append_memory("entrepreneur", f"[gem_crush_daily] {status} v={result.get('version')}")

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

        elif task_id == "self_banner_curiosity":
            from aria_core.self_maintenance import run_curiosity_x_banner_cycle

            summary = await run_curiosity_x_banner_cycle(lang="fr")
            append_memory("self-improve", f"[banner_curiosity] {summary[:250]}")
            notify_markers = ("Action bloquee", "Echec", "publiee", "bloquee")
            if any(m in summary for m in notify_markers):
                await self._notify_telegram(f"Banniere X (curiosite 6h)\n\n{summary[:1500]}")

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