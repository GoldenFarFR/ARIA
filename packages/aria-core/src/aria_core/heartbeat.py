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
        description="Automatic portfolio watchlist analysis",
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
        description="Study ZHC peers + track VC/trading track-record progress toward the real-money pact (no paid product)",
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
    HeartbeatTask(
        id="vc_crawl",
        name="BASE token crawl",
        description="Decouvre les tokens Base -> filtre securite -> base propriataire",
        interval_minutes=360,
        enabled=True,
    ),
    HeartbeatTask(
        id="vc_resolve",
        name="VC predictions resolve",
        description="Cloture les pronostics a echeance via le prix OHLCV reel",
        interval_minutes=1440,
        enabled=True,
    ),
    HeartbeatTask(
        id="vc_weekly_forecast",
        name="VC forecast",
        description="Tire 20 tokens du pool -> analyse -> enregistre 20 pronostics dates (cadence 2j)",
        interval_minutes=2880,
        enabled=True,
    ),
    HeartbeatTask(
        id="vc_self_report",
        name="ARIA self report",
        description="Digest sante & reglages -> operateur (Telegram)",
        interval_minutes=10080,
        enabled=True,
    ),
    HeartbeatTask(
        id="vc_radar_x",
        name="Radar X social",
        description="Ecoute sociale -> sourcing/reveil de candidats, arbitre on-chain (jamais un declencheur)",
        interval_minutes=720,
        enabled=True,
    ),
    HeartbeatTask(
        id="vc_thesis_review",
        name="Thesis surveillance",
        description="Repasse sur chaque position ouverte (prix + activite projet) -> alerte si stagne/casse",
        interval_minutes=1440,
        enabled=True,
    ),
    HeartbeatTask(
        id="paper_trade_cycle",
        name="Paper trading 1M$ (simulation)",
        description="Applique les VRAIS rapports a un portefeuille FICTIF de 1M$ (mode trading) : ouvre/ferme des positions simulees, alertes achat/vente fictives. Preuve sur ~20 jours. Aucun argent reel, aucune signature.",
        interval_minutes=180,
        enabled=False,
    ),
    HeartbeatTask(
        id="aria_exam_cycle",
        name="Examen trading ARIA (rehearsal pedagogique)",
        description="Genere ~25 questions de trading/jour (curriculum 50 concepts), les pose au raisonnement d'ARIA, note via juge LLM. 20 jours, en parallele du paper-trading. Aucune action financiere.",
        interval_minutes=1440,
        enabled=False,
    ),
    HeartbeatTask(
        id="code_proposal_cycle",
        name="Proposition de code long-cours",
        description="Redige UNE amelioration concrete de son propre systeme et l'ouvre comme issue GitHub (jamais une PR, jamais un commit, jamais un merge autonome -- revue humaine requise). Gate OFF par defaut.",
        interval_minutes=1440,
        enabled=False,
    ),
    HeartbeatTask(
        id="skill_project_cycle",
        name="Projet d'apprentissage long-cours",
        description="Un increment reel par jour sur un projet de plusieurs jours (3-7j, curriculum trading) ; synthese finale soumise a l'operateur seulement a la fin. 100% analyse/ecriture, aucune action financiere ni changement de code.",
        interval_minutes=1440,
        enabled=True,
    ),
    HeartbeatTask(
        id="sepolia_autonomous_cycle",
        name="Rehearsal Sepolia autonome",
        description="Decide ET execute SEULE sur Base Sepolia (testnet, aucune valeur reelle) -- sans clic Telegram. Kelly sizing sur calibration reelle, ancrage onchain autonome, telemetrie complete (latence/hesitation/erreurs). Chain_id verrouille 84532 ; le mainnet garde la validation humaine.",
        interval_minutes=60,
        enabled=False,
    ),
    HeartbeatTask(
        id="relay_conversation_cycle",
        name="Conversation relay ARIA <-> Claude Code",
        description="Repond dans sa propre voix (LLM) quand le dernier message du relay Telegram vient de Claude Code -- jamais l'operateur. Aucune action/competence declenchable depuis cet echange, uniquement de la discussion. Plafond quotidien, respecte le kill-switch. Gate OFF par defaut.",
        interval_minutes=15,
        enabled=False,
    ),
    HeartbeatTask(
        id="knowledge_inbox_cycle",
        name="Boite de depot de connaissance",
        description="Lit une note non traitee dans docs/aria-learning-inbox/ et PROPOSE (jamais n'impose) son integration dans les vrais fichiers de connaissance (knowledge/*.yaml, canonical_facts.yaml) via une ISSUE GitHub -- jamais un commit ni une fusion autonome. Une note n'est proposee qu'une seule fois. Gate OFF par defaut.",
        interval_minutes=360,
        enabled=False,
    ),
    HeartbeatTask(
        id="claude_mentor_cycle",
        name="Revue de performance ARIA par Claude",
        description="Claude (Opus 4.8, profondeur develop via Virtuals -- pas de nouveau secret) lit les vraies donnees mesurees d'ARIA (calibration VC, paper-trading, telemetrie Sepolia) et poste UNE observation ancree sur les chiffres dans le relais Telegram (ARIA y repond en vrai). Si le constat merite d'etre durable, PROPOSE une issue GitHub connaissance -- jamais un commit ni une fusion autonome. Throttle interne ~1x/jour. Gate OFF par defaut.",
        interval_minutes=60,
        enabled=False,
    ),
    HeartbeatTask(
        id="telegram_miner_cycle",
        name="Mineur de conversations operateur/ARIA",
        description="Relit les nouveaux echanges du relais Telegram existant (relay_chat.py, rien duplique) et PROPOSE (jamais n'impose) un enseignement durable et generalisable observe dans le dialogue reel -- jamais une citation verbatim (filet de securite anti-secret local, une creation d'issue ne passe pas par le scan detect-secrets de la CI). PROPOSE via ISSUE GitHub -- jamais un commit ni une fusion autonome. Throttle interne ~1x/jour. Gate OFF par defaut.",
        interval_minutes=60,
        enabled=False,
    ),
    HeartbeatTask(
        id="high_conviction_alert_cycle",
        name="Alertes proactives haute-conviction",
        description="Pousse une alerte Telegram des que le pool screene fait remonter un candidat SAFE au-dessus du seuil de score compose (candidate_ranking, deja existant -- rien duplique). Signal de tri, jamais un ordre d'achat -- renvoie vers /vc <contrat> pour l'analyse complete. Un contrat n'est alerte qu'une seule fois. Gate OFF par defaut.",
        interval_minutes=60,
        enabled=False,
    ),
    HeartbeatTask(
        id="pump_dump_autopsy_cycle",
        name="Autopsie pump/dump",
        description="Relit la vraie serie OHLCV parcourue par chaque pronostic VC clos recemment (le point-a-point entree->echeance masque un pump-puis-crash intermediaire) ; si un pattern reel est detecte (deterministe, aucun LLM), demande une autopsie courte au LLM. Log local + proposition d'issue GitHub (aria-playbook-proposal) si la lecon est jugee durable -- jamais un commit ni une fusion autonome. Gate OFF par defaut.",
        interval_minutes=180,
        enabled=False,
    ),
    HeartbeatTask(
        id="market_sentiment_cycle",
        name="Sentiment de marche continu",
        description="Rafraichit SANS expiration la lecture de sentiment (RSI/Bollinger/momentum/retracement, deterministe, aucun LLM) des paires principales (BTC, ETH) -- vocabulaire aligne sur le Wall St Cheat Sheet, regroupe en regimes mesurables. Ecrase toujours la derniere lecture (aucun cache perime) ; une paire en echec de fetch n'interrompt pas les autres. Gate OFF par defaut.",
        interval_minutes=60,
        enabled=False,
    ),
]


def _sync_x_curiosity_enabled() -> None:
    for task in HEARTBEAT_TASKS:
        try:
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
                from aria_core.x_profile import x_profile_sync_enabled

                # Sync manuelle (commande admin /x profile sync) toujours disponible ;
                # la tâche AUTOMATIQUE (heartbeat, personne ne clique) reste en plus
                # gardée par ARIA_X_PROFILE_SYNC_ENABLED (outward-facing -> opt-in).
                task.enabled = is_x_post_configured() and x_profile_sync_enabled()
            if task.id == "paper_trade_cycle":
                # Simulation interne 1M$ : OFF par defaut. L'operateur demarre le run de preuve
                # (20 jours) en posant ARIA_PAPER_TRADING_ENABLED=1 dans le .env (cout LLM
                # deliberé). Aucun argent reel, aucune surface outward-facing.
                task.enabled = os.environ.get("ARIA_PAPER_TRADING_ENABLED", "").strip().lower() in (
                    "1", "true", "yes", "on",
                )
            if task.id == "aria_exam_cycle":
                from aria_core.exam import exam_enabled

                task.enabled = exam_enabled()
            if task.id == "code_proposal_cycle":
                from aria_core.skills.code_proposal import code_proposal_enabled

                task.enabled = code_proposal_enabled()
            if task.id == "skill_project_cycle":
                from aria_core.knowledge.skill_projects import skill_projects_enabled

                task.enabled = skill_projects_enabled()
            if task.id == "sepolia_autonomous_cycle":
                from aria_core.onchain.sepolia_autonomous import sepolia_autonomous_enabled

                task.enabled = sepolia_autonomous_enabled()
            if task.id == "relay_conversation_cycle":
                from aria_core.relay_chat import relay_autoreply_enabled

                task.enabled = relay_autoreply_enabled()
            if task.id == "knowledge_inbox_cycle":
                from aria_core.skills.knowledge_inbox import knowledge_inbox_enabled

                task.enabled = knowledge_inbox_enabled()
            if task.id == "claude_mentor_cycle":
                from aria_core.skills.claude_mentor import claude_mentor_enabled

                task.enabled = claude_mentor_enabled()
            if task.id == "telegram_miner_cycle":
                from aria_core.skills.telegram_conversation_miner import telegram_miner_enabled

                task.enabled = telegram_miner_enabled()
            if task.id == "high_conviction_alert_cycle":
                from aria_core.skills.high_conviction_alerts import high_conviction_alerts_enabled

                task.enabled = high_conviction_alerts_enabled()
            if task.id == "pump_dump_autopsy_cycle":
                from aria_core.skills.pump_dump_autopsy import pump_dump_autopsy_enabled

                task.enabled = pump_dump_autopsy_enabled()
            if task.id == "market_sentiment_cycle":
                from aria_core.skills.market_sentiment import market_sentiment_enabled

                task.enabled = market_sentiment_enabled()
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
        except Exception as exc:
            # Un gate de tâche cassé (import manquant, dépendance non déployée...) ne
            # doit jamais empêcher l'évaluation des AUTRES tâches ni, en amont, tout le
            # reste de _tick() (cette fonction tourne à CHAQUE tick, avant la boucle
            # d'exécution des tâches -- un throw ici gelait heartbeat entier). Fail-closed :
            # la tâche en échec reste désactivée pour ce cycle, les autres continuent.
            logger.warning("heartbeat gate check failed for task=%s: %s — désactivée ce cycle (fail-closed)", task.id, exc)
            task.enabled = False

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


def heartbeat_pulse() -> dict:
    """Pouls COARSE et NON sensible du heartbeat, pour un endpoint public / le cockpit.

    N'expose QUE des horodatages de cycles (cadence non sensible) : jamais un candidat, un
    verdict, un montant, un secret ni une PII. `alive` = au moins un cycle a tourné."""
    state = _load_heartbeat_state()  # {task_id: iso}
    times = sorted(v for v in state.values() if isinstance(v, str) and v)
    last_tick = times[-1] if times else None
    safe_keys = (
        "vc_crawl", "vc_weekly_forecast", "vc_radar_x", "vc_thesis_review", "paper_trade_cycle",
        "market_sentiment_cycle",
    )
    cycles = {k: state[k] for k in safe_keys if state.get(k)}
    return {"alive": last_tick is not None, "last_tick": last_tick, "cycles": cycles}


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
            result = await run_curiosity_cycle(notifier=self._notify_telegram)
            if result.get("insights", 0) > 0:
                append_memory("heartbeat", f"[x_curiosity] {result['insights']} insights pending")
            if result.get("opportunities", 0) > 0:
                append_memory(
                    "heartbeat", f"[x_curiosity] {result['opportunities']} opportunités surfacées"
                )

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

        elif task_id == "vc_crawl":
            from aria_core.base_crawler import crawl_and_absorb

            counts = await crawl_and_absorb(limit=100, max_age_days=182)
            append_memory("vc", f"[crawl] {counts} — {counts.get('kept', 0)} gardés")

        elif task_id == "vc_resolve":
            from aria_core.weekly_training import resolve_due

            summary = await resolve_due()
            if summary.get("resolved", 0) > 0:
                append_memory("vc", f"[resolve] {summary['resolved']} pronostics clôturés (OHLCV)")

        elif task_id == "vc_weekly_forecast":
            from aria_core.weekly_training import run_weekly_forecasts

            ids = await run_weekly_forecasts(n=20)
            append_memory("vc", f"[forecast] {len(ids)} pronostics enregistrés")
            if ids:
                await self._notify_telegram(
                    f"🎯 ARIA — {len(ids)} nouveaux pronostics enregistrés (walk-forward)."
                )

        elif task_id == "vc_self_report":
            from aria_core.weekly_training import self_report

            digest = await self_report()
            append_memory("vc", "[self_report] digest opérateur envoyé")
            await self._notify_telegram(digest)

        elif task_id == "vc_radar_x":
            from aria_core.radar_x import run_radar

            report = await run_radar(limit=40)
            if report.get("above_threshold", 0) > 0:
                append_memory(
                    "vc",
                    f"[radar] {report['above_threshold']} candidats bruyants — "
                    f"{report.get('kept', 0)} gardés, {report.get('resurrected', 0)} réveillés",
                )

        elif task_id == "vc_thesis_review":
            from aria_core.weekly_training import run_thesis_review

            review = await run_thesis_review()
            alerts = review.get("alerts", [])
            if alerts:
                append_memory("vc", f"[thesis] {len(alerts)} thèse(s) à revoir (stagne/casse)")
                lignes = "\n".join(
                    f"• {a['contract'][:10]} : {a['verdict']} — {a['note']}" for a in alerts[:8]
                )
                await self._notify_telegram(
                    f"🔎 ARIA — {len(alerts)} thèse(s) à revoir :\n{lignes}"
                )

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
            # Le module aria_core.x_profile n'est pas (encore) livré. Sans garde, l'import
            # levait ModuleNotFoundError, sortait de la boucle de _tick AVANT la sauvegarde
            # d'état -> la tâche restait « due » et re-crashait chaque tick, en sautant tous
            # les jobs suivants (landmine dès que X est configuré). On dégrade proprement,
            # comme visual_autonomy.py, en attendant que le module existe (surface X =
            # outward-facing -> à livrer sous validation opérateur).
            try:
                from aria_core.x_profile import sync_x_profile
            except ModuleNotFoundError:
                append_memory("comms", "[x_profile] module non livré — sync X ignorée")
                return

            result = await sync_x_profile()
            if result.get("synced"):
                append_memory(
                    "comms",
                    f"[x_profile] heartbeat sync drift={result.get('drift')}",
                )

        elif task_id == "paper_trade_cycle":
            from aria_core import paper_trader

            actions = await paper_trader.run_paper_cycle(notifier=self._notify_telegram)
            if actions.get("opened") or actions.get("closed"):
                append_memory(
                    "paper",
                    f"[paper_trade] fictif 1M$ : +{len(actions.get('opened', []))} achats / "
                    f"-{len(actions.get('closed', []))} ventes",
                )

        elif task_id == "aria_exam_cycle":
            from aria_core import exam

            day = await exam.current_exam_day()
            if day > exam.EXAM_PROGRAM_DAYS:
                return  # programme des 20 jours termine — plus de nouveau cycle
            questions = await exam.generate_daily_questions(day, n=25)
            for q in questions:
                await exam.administer_question(q)
            summary = await exam.daily_summary(day)
            if summary["answered"] > 0:
                append_memory(
                    "exam",
                    f"[exam] jour {day}/{exam.EXAM_PROGRAM_DAYS} — {summary['answered']} "
                    f"questions, score moyen {summary['avg_score']}/10",
                )
                await self._notify_telegram(
                    f"📚 Examen ARIA — jour {day}/{exam.EXAM_PROGRAM_DAYS} : "
                    f"{summary['answered']} questions, score moyen {summary['avg_score']}/10."
                )

        elif task_id == "code_proposal_cycle":
            from aria_core.skills.code_proposal import run_code_proposal_cycle

            result = await run_code_proposal_cycle(notifier=self._notify_telegram)
            if result.get("outcome") == "ok":
                append_memory("code_proposal", f"[proposal] {result.get('title', '?')} -> {result.get('url', '')}")

        elif task_id == "sepolia_autonomous_cycle":
            from aria_core.onchain import sepolia_autonomous

            result = await sepolia_autonomous.run_autonomous_cycle(notifier=self._notify_telegram)
            outcome = result.get("outcome")
            if outcome in ("ok", "error"):
                append_memory(
                    "sepolia_autonomous",
                    f"[rehearsal] {result.get('contract', '?')[:10]} -> {outcome} "
                    f"(hesitant={result.get('hesitant', False)})",
                )

        elif task_id == "relay_conversation_cycle":
            from aria_core.relay_conversation import run_relay_conversation_cycle

            result = await run_relay_conversation_cycle()
            if result.get("outcome") == "ok":
                append_memory("relay_conversation", "[relay] réponse envoyée à Claude Code")

        elif task_id == "knowledge_inbox_cycle":
            from aria_core.skills.knowledge_inbox import run_knowledge_inbox_cycle

            result = await run_knowledge_inbox_cycle(notifier=self._notify_telegram)
            if result.get("outcome") == "ok":
                append_memory(
                    "knowledge_inbox",
                    f"[inbox] {result.get('path', '?')} -> proposition {result.get('title', '?')}",
                )

        elif task_id == "claude_mentor_cycle":
            from aria_core.skills.claude_mentor import run_claude_mentor_cycle

            result = await run_claude_mentor_cycle()
            if result.get("outcome") == "ok":
                append_memory(
                    "claude_mentor",
                    f"[mentor] remarque postée (durable={result.get('durable', False)})",
                )

        elif task_id == "telegram_miner_cycle":
            from aria_core.skills.telegram_conversation_miner import run_telegram_miner_cycle

            result = await run_telegram_miner_cycle()
            if result.get("outcome") == "ok":
                append_memory(
                    "telegram_miner",
                    f"[mineur] proposition -- {result.get('title', '?')}",
                )

        elif task_id == "high_conviction_alert_cycle":
            from aria_core.skills.high_conviction_alerts import run_high_conviction_alert_cycle

            result = await run_high_conviction_alert_cycle(notifier=self._notify_telegram)
            if result.get("outcome") == "ok":
                append_memory(
                    "high_conviction_alert",
                    f"[alerte] {result.get('contract', '?')[:10]} -> score "
                    f"{result.get('rank_score', 0):.0f}",
                )

        elif task_id == "pump_dump_autopsy_cycle":
            from aria_core.skills.pump_dump_autopsy import run_pump_dump_autopsy_cycle

            result = await run_pump_dump_autopsy_cycle()
            if result.get("outcome") == "ok" and result.get("autopsied"):
                append_memory(
                    "pump_dump_autopsy",
                    f"[autopsie] {result['autopsied']} cas sur {result.get('checked', 0)} clotures verifiees",
                )

        elif task_id == "market_sentiment_cycle":
            from aria_core.skills.market_sentiment import run_market_sentiment_cycle

            result = await run_market_sentiment_cycle()
            if result.get("updated"):
                append_memory(
                    "market_sentiment",
                    f"[sentiment] {', '.join(result['updated'])} rafraichi(s)"
                    + (f" ; echec : {', '.join(result['failed'])}" if result.get("failed") else ""),
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
            handed = scan.get("handed_over") or []
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
            # Passage de relai : ARIA n'a pas tranche, elle te passe la main. Ping avec le
            # commentaire recu et un brouillon pret a copier (tu decides et tu reponds).
            if handed:
                append_memory(
                    "github",
                    f"[heartbeat] showcase_pr_watch — {len(handed)} passage(s) de relai operateur",
                )
                for row in handed[:2]:
                    body = (
                        f"Showcase PR — ARIA te passe la main (ta reponse requise)\n"
                        f"De: @{row.get('trigger_author')} ({row.get('reason')})\n"
                        f"URL: {row.get('reply_url') or row.get('trigger_url')}\n\n"
                        f"Il a ecrit:\n{row.get('comment_excerpt') or ''}\n\n"
                        f"Brouillon suggere (a editer):\n{row.get('suggested_draft') or ''}"
                    )
                    await self._notify_telegram(body[:1800])

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
