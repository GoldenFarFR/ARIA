import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.integrations.aria_host import register_aria_host_integrations

register_aria_host_integrations()

from aria_core import repertoire_db
from aria_core.gateway.telegram_bot import start_telegram_bot, stop_telegram_bot
from aria_core.heartbeat import aria_heartbeat
from app.api.routes import (
    alerts,
    analysis,
    aria,
    auth,
    games,
    holding_member,
    pairs,
    pot,
    telegram_route,
    watchlist,
    websocket,
)
from aria_core.gateway import telegram_bot
from app.auth.access_code import init_auth_db, purge_expired
from app.auth.middleware import AccessCodeMiddleware
from app.config import settings
from app.database import init_db
from app.realtime.pair_indexer import pair_indexer
from app.realtime.scanner import realtime_scanner

logger = logging.getLogger(__name__)


async def _background_startup() -> None:
    """Non-blocking boot — Render health check must pass before slow init (Telegram API, seed)."""
    from aria_core.knowledge.seed import (
        seed_builder_knowledge_if_empty,
        seed_launchpad_knowledge,
        seed_zhc_identity_knowledge,
    )

    try:
        n = await seed_builder_knowledge_if_empty()
        if n:
            logger.info("Seeded %d builder knowledge entries", n)
        zhc = await seed_zhc_identity_knowledge()
        if zhc:
            logger.info("Upserted %d ZHC identity doctrine entries", zhc)
        lp = await seed_launchpad_knowledge()
        if lp:
            logger.info("Upserted %d launchpad knowledge entries", lp)
    except Exception as exc:
        logger.warning("Knowledge seed skipped: %s", exc)

    try:
        await init_db()
        await init_auth_db()
        await purge_expired()
        await repertoire_db.init_repertoire_db()
        from aria_core.content.content_db import init_content_db
        from aria_core.truth_ledger.canonical import sync_canonical_facts
        from aria_core.truth_ledger.store import init_truth_ledger
        from aria_core.truth_ledger.sync import (
            ensure_github_sync_scheduler,
            flush_pending_github_sync,
        )
        await init_content_db()
        await init_truth_ledger()
        await ensure_github_sync_scheduler()
        flushed = await flush_pending_github_sync()
        if flushed:
            logger.info("Truth ledger startup flush: %d entries synced to GitHub", flushed)
        canon = await sync_canonical_facts()
        logger.info(
            "Canonical facts synced: %d new, %d superseded, %d unchanged / %d total",
            canon["synced"],
            canon["superseded"],
            canon["unchanged"],
            canon["total_facts"],
        )
        websocket.setup_scanner_broadcast()
        await realtime_scanner.start()
        await pair_indexer.start()
        from aria_core.avatar_style_refresh import bootstrap_style_schedule

        boot = bootstrap_style_schedule()
        logger.info("Avatar style schedule: %s", boot.get("action"))
        await aria_heartbeat.start()
        logger.info("Aria Vanguard core services started")
    except Exception as exc:
        logger.exception("Core startup failed: %s", exc)
        return

    try:
        await start_telegram_bot()
    except Exception as exc:
        logger.exception("Telegram bot startup failed (app continues): %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup_task = asyncio.create_task(_background_startup())
    yield
    if not startup_task.done():
        startup_task.cancel()
        try:
            await startup_task
        except asyncio.CancelledError:
            pass
    try:
        await stop_telegram_bot()
    except Exception as exc:
        logger.warning("Telegram shutdown: %s", exc)
    await aria_heartbeat.stop()
    await pair_indexer.stop()
    await realtime_scanner.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def _sanitized_validation_handler(request, exc: RequestValidationError):
    """Renvoie un 422 propre SANS jamais réémettre le corps brut de la requête.

    Sécurité/robustesse : le handler par défaut inclut le champ `input` (le corps reçu) ;
    un corps binaire (non-UTF8) faisait alors planter la sérialisation JSON -> 500 sur tout
    POST. On préserve loc/msg/type (utiles au front) mais on retire `input`/`ctx`
    (octets/objets non sérialisables, et évite de renvoyer une entrée hostile en écho).
    """
    clean = []
    for err in exc.errors():
        err = dict(err)
        err.pop("input", None)
        err.pop("ctx", None)
        clean.append(err)
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(clean)})


app.add_middleware(AccessCodeMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(games.router, prefix="/api")
app.include_router(pot.router, prefix="/api")
app.include_router(telegram_route.router, prefix="/api")
app.include_router(pairs.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(aria.router, prefix="/api")
app.include_router(holding_member.router, prefix="/api")
app.include_router(websocket.router)


@app.get("/api/pulse")
async def pulse():
    """Pouls public read-only : signaux COARSE (heartbeat, mode), AUCUN secret / candidat / PII.
    Alimente le suivi live et le cockpit sans exposer la surface opérateur."""
    from aria_core.heartbeat import heartbeat_pulse
    from aria_core.onchain.anchor import anchor_enabled

    commit = (os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "unknown")[:12]
    paper = os.environ.get("ARIA_PAPER_TRADING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    return {
        "status": "ok",
        "commit": commit,
        "heartbeat": heartbeat_pulse(),
        "paper_trading": paper,
        "real_execution": False,
        "onchain": {"anchor_ready": anchor_enabled(), "anchored": False},
    }


@app.get("/api/aria/arena-signal/btc")
async def arena_signal_btc():
    """Signal BTC public, lecture seule, sans auth (seam #60 — contrat "Custom Data
    Endpoint" de Shekel/agents tiers). Réutilise les analyses BTC existantes d'ARIA
    (cycle macro, RSI) ; aucun secret, aucun candidat, aucune PII. Dormant tant
    qu'aucun agent externe n'est configuré pour l'interroger."""
    from aria_core.skills.arena_signal import fetch_btc_arena_signal

    return await fetch_btc_arena_signal()


@app.get("/api/health")
async def health():
    """Public liveness probe — no sensitive details."""
    from aria_core.gateway.x_twitter import is_x_post_configured, is_x_read_configured
    from aria_core.llm import is_llm_provider_configured
    from aria_core.skills.github_skill import github_configured, github_unlimited_access

    commit = (
        os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("GIT_COMMIT")
        or "unknown"
    )[:12]
    payload = {
        "status": "ok",
        "app": settings.app_name,
        "commit": commit,
        "access_gate": settings.access_code_enabled,
        "auth_routes": {
            "handoff": True,
            "session_cookie": True,
        },
        "member_access": "vanguard_handoff",
        "aria_x": {
            "post_configured": is_x_post_configured(),
            "read_configured": is_x_read_configured(),
        },
        "aria_github": {
            "configured": github_configured(),
            "unlimited": github_unlimited_access(),
        },
        "aria_telegram": {"configured": bool(settings.telegram_bot_token.strip())},
        "aria_llm": {
            "enabled": settings.aria_llm_enabled,
            # Reflète le routage RÉEL : pour provider=virtuals la clé d'auth est
            # virtuals_api_key, pas llm_api_key (qui restait souvent vide -> faux négatif).
            "provider_configured": is_llm_provider_configured(),
        },
        "aria_acp": {
            "cli_available": __import__("shutil").which("acp") is not None,
            "provider_enabled": settings.aria_acp_provider_enabled,
            "events_file_configured": bool((settings.aria_acp_events_file or "").strip()),
        },
        "aria_web": {
            # Fournisseur de recherche web ACTIF + si la clé Tavily est bien câblée.
            # Permet de vérifier d'un coup d'œil que Tavily est branché (sans exposer la clé).
            "provider": str(getattr(settings, "aria_web_search_provider", "ddg") or "ddg"),
            "tavily_key_present": bool(os.getenv("TAVILY_API_KEY", "").strip()),
        },
    }
    try:
        from aria_core._build import ARIA_CORE_BUILD

        payload["aria_core_build"] = ARIA_CORE_BUILD
    except ImportError:
        payload["aria_core_build"] = None
    return payload


def _mount_frontend() -> None:
    static = settings.static_dir
    if not settings.serve_frontend or not static.is_dir():
        return

    assets = static / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    async def spa_index():
        return FileResponse(
            static / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    def _file_handler(file_path):
        async def handler():
            return FileResponse(file_path)
        return handler

    for name in ("favicon.svg", "icons.svg"):
        file_path = static / name
        if file_path.is_file():
            app.add_api_route(f"/{name}", _file_handler(file_path), methods=["GET"])

    static_root = static.resolve()

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        if path.startswith("api") or path == "ws":
            raise HTTPException(status_code=404)
        # Anti path traversal : le fichier servi DOIT rester sous le dossier statique.
        # Sans cela, "../../etc/passwd" sortait de static et était servi (lecture arbitraire).
        candidate = (static_root / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(static_root):
            return FileResponse(candidate)
        return FileResponse(
            static / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


_mount_frontend()