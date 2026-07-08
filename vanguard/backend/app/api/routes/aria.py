from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from aria_core import repertoire_db
from aria_core.brain import aria_brain
from aria_core.heartbeat import aria_heartbeat, HEARTBEAT_TASKS, _START_TIME
from aria_core.memory import count_memory_entries
from aria_core.holding import (
    DEFAULT_ARIA_TITLE,
    DEFAULT_HOLDING_TAGLINE,
    GOVERNANCE_RULE,
    SUBSIDIARY_OF_LABEL,
    holding_name,
)
from aria_core.narrative import one_liner, x_juno_intent_url
from pydantic import BaseModel, Field

from aria_core.models import (
    AgentStatus,
    ChatRequest,
    ChatResponse,
    HoldingStructure,
    RepertoireCreateRequest,
    RepertoireItem,
)
from aria_core.content.content_db import list_drafts
from aria_core.content.service import list_faq, search_faq
from aria_core.content.site_copy import public_site_payload
from aria_core.public_mode import (
    is_operator_request,
    is_public_mode,
    require_operator,
    resolve_visitor_id,
)
from app.auth.rate_limit import check_rate_limit
from app.auth.visitor import client_ip
from app.config import settings

from app.database import get_watchlist

router = APIRouter(prefix="/aria", tags=["aria"])


class KnowledgeCreateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=64)
    content: str = Field(..., min_length=1, max_length=2000)
    source: str = "manual"
    approved: bool = True


class CommunityFeedbackRequest(BaseModel):
    message: str = Field(..., min_length=8, max_length=500)
    handle: str = Field(default="", max_length=64)
    lang: str = Field(default="en", max_length=8)


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request):
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    visitor_id = resolve_visitor_id(request, body.visitor_id)
    if is_public_mode():
        allowed = check_rate_limit(
            f"aria_chat:{visitor_id}",
            max_attempts=settings.aria_chat_rate_limit_per_hour,
            window_seconds=3600,
        )
        # Plafond par IP réelle : X-Visitor-Id est fourni par le client, donc le faire
        # tourner contournerait la limite par-visiteur. Le plafond IP (plus large) borde
        # cet abus. client_ip() renvoie None hors proxy (pas de régression).
        ip = client_ip(request)
        ip_allowed = True
        if ip is not None:
            ip_allowed = check_rate_limit(
                f"aria_chat_ip:{ip}",
                max_attempts=max(settings.aria_chat_rate_limit_per_hour * 3, 60),
                window_seconds=3600,
            )
        if not allowed or not ip_allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit reached. Try again in an hour.",
            )

    return await aria_brain.process(
        body.message.strip(),
        visitor_id=visitor_id,
        public_mode=is_public_mode(),
    )


@router.post("/community-feedback")
async def community_feedback(body: CommunityFeedbackRequest, request: Request):
    """Avis communauté site — ARIA trie et file l'ouvrier si l'idée vaut le coup."""
    from aria_core.community_feedback import (
        is_trusted_feedback_handle,
        is_trusted_operator_publish,
        submit_community_feedback,
    )

    visitor_id = resolve_visitor_id(request)

    # Anti-usurpation : ce endpoint est PUBLIC (visiteur anonyme). Le champ `handle` n'est
    # qu'une revendication non prouvée. Un handle de confiance (opérateur) revendiqué SANS
    # le secret admin ne doit PAS débloquer les privilèges opérateur (pas de modération, pas
    # de rate-limit, publication INSTANTANÉE et autonome sur @Aria_ZHC — ce qui violerait le
    # garde-fou « jamais X autonome »). On le neutralise en le traitant comme anonyme.
    raw_handle = body.handle.strip()
    handle = raw_handle
    if raw_handle and is_trusted_feedback_handle(raw_handle) and not is_operator_request(request):
        handle = ""

    if not is_trusted_operator_publish(handle):
        allowed = check_rate_limit(
            f"community_fb:{visitor_id}",
            max_attempts=8,
            window_seconds=3600,
        )
        if not allowed:
            raise HTTPException(status_code=429, detail="Rate limit — réessaie dans une heure.")

    lang = "fr" if body.lang.lower().startswith("fr") else "en"
    return await submit_community_feedback(
        body.message.strip(),
        handle=handle,
        visitor_id=visitor_id,
        source="vanguard_site",
        lang=lang,
    )


@router.get("/status", response_model=AgentStatus)
async def agent_status():
    from aria_core.llm import is_llm_configured, is_llm_provider_configured

    watchlist = await get_watchlist()
    repertoire = await repertoire_db.get_all()
    hb = aria_heartbeat.get_status()

    return AgentStatus(
        holding_name=holding_name(),
        uptime_since=_START_TIME,
        memory_entries=count_memory_entries(),
        repertoire_count=len(repertoire),
        watchlist_count=len(watchlist),
        heartbeat_tasks=HEARTBEAT_TASKS,
        last_heartbeat=hb.get("last_heartbeat"),
        zhc_connection="public_api" if is_public_mode() else "operator",
        llm_configured=is_llm_configured(),
        aria_llm_enabled=settings.aria_llm_enabled,
        llm_provider_configured=is_llm_provider_configured(),
        grounded_mode=settings.aria_grounded_mode,
        long_term_memory=True,
    )


@router.get("/messages")
async def get_chat_history(request: Request, limit: int = 50):
    visitor_id = resolve_visitor_id(request) if is_public_mode() else None
    return await repertoire_db.get_messages(limit, visitor_id=visitor_id)


@router.get("/track-record")
async def track_record():
    """Track-record PUBLIC (teaser FOMO) : valeur du wallet suivi + calibration synthétique.

    Chiffres agrégés seulement — le détail des positions et les hashes de verdict sont
    réservés aux abonnés (endpoint gaté à venir). Facts-only : si rien n'est encore
    valorisé, l'indice vaut 100 (+0 %), jamais un chiffre gonflé.
    """
    from aria_core import screened_pool, vc_predictions

    wallet = await vc_predictions.live_wallet()
    m = await vc_predictions.metrics()
    pool = await screened_pool.count_pool("active")
    pool_rejected = await screened_pool.count_pool("rejected")
    return {
        "wallet_index": wallet["index"],
        "wallet_return_pct": wallet["total_return_pct"],
        "vc_return_pct": wallet["vc_return_pct"],
        "spec_return_pct": wallet["spec_return_pct"],
        "positions": wallet["positions_valued"],
        "verdicts_total": m["total"],
        "verdicts_closed": m["closed"],
        "hit_rate": m["hit_rate"],
        "avoid_count": m.get("avoid_count", 0),
        "pool_active": pool,
        "pool_rejected": pool_rejected,
        "disclaimer": (
            "Track-record de suivi (paper) valorisé aux prix on-chain réels. "
            "Informationnel, pas un conseil. Aucun rendement garanti."
        ),
    }


@router.get("/exam-status")
async def exam_status():
    """Statut PUBLIC du rehearsal pédagogique (examen trading, 20 jours) — chiffres
    agrégés seulement. Jamais une action financière : uniquement mesurer et consigner."""
    from aria_core import exam

    day = min(await exam.current_exam_day(), exam.EXAM_PROGRAM_DAYS)
    today = await exam.daily_summary(day)
    cumulative = await exam.cumulative_summary()
    return {
        "enabled": exam.exam_enabled(),
        "program_days": exam.EXAM_PROGRAM_DAYS,
        "current_day": day,
        "today": today,
        "cumulative": cumulative,
    }


@router.get("/dossier/{contract}")
async def token_dossier(contract: str, request: Request):
    """Dossier par token (opérateur) : chronologie de TOUT ce qu'ARIA a consigné sur un CA.

    Réservé opérateur — expose le détail du pipeline de candidats (analyses,
    thèses, suivis, paper), donc jamais public ni membre. Lecture seule, facts-only :
    un token jamais analysé renvoie un dossier vide (aucune donnée inventée).
    """
    require_operator(request)
    from aria_core.dossier import build_dossier

    return await build_dossier(contract)


@router.get("/repertoire", response_model=list[RepertoireItem])
async def list_repertoire():
    return await repertoire_db.get_all()


@router.get("/holding", response_model=HoldingStructure)
async def get_holding():
    structure = await repertoire_db.get_holding_structure()
    if not structure:
        raise HTTPException(status_code=404, detail="Holding not initialized")
    structure.aria_title = DEFAULT_ARIA_TITLE
    structure.holding_tagline = DEFAULT_HOLDING_TAGLINE
    structure.governance_rule = GOVERNANCE_RULE
    structure.subsidiary_label = SUBSIDIARY_OF_LABEL
    return structure


@router.post("/repertoire", response_model=RepertoireItem)
async def add_repertoire_item(body: RepertoireCreateRequest, request: Request):
    if is_public_mode():
        require_operator(request)
    try:
        return await repertoire_db.create_from_request(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/repertoire/{item_id}")
async def delete_repertoire_item(item_id: str, request: Request):
    require_operator(request)
    ok, reason, item = await repertoire_db.delete_item(item_id)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    return {"ok": True, "message": reason, "deleted": item.name if item else item_id}


@router.post("/repertoire/{item_id}/archive")
async def archive_repertoire_item(item_id: str, request: Request):
    require_operator(request)
    ok, reason, item = await repertoire_db.archive_item(item_id)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    return {"ok": True, "message": reason, "archived": item.name if item else item_id}


@router.get("/setup")
async def aria_setup_guide(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.holding import holding_structure_text
    from aria_core.identity import ARIA_BIO, ARIA_DISPLAY_NAME, ARIA_HANDLE, ARIA_TITLE, SETUP_STEPS
    from aria_core.gateway.x_twitter import is_x_configured, x_status

    return {
        "identity": ARIA_DISPLAY_NAME,
        "holding": holding_name(),
        "aria_title": ARIA_TITLE,
        "holding_structure": holding_structure_text(),
        "governance_rule": GOVERNANCE_RULE,
        "one_liner": one_liner("en"),
        "public_url": settings.public_site_url,
        "holding_domain": settings.holding_domain,
        "x_handle": f"@{settings.aria_x_handle or ARIA_HANDLE}",
        "email": settings.aria_email or "not configured",
        "bio_suggestion": ARIA_BIO,
        "setup_steps": SETUP_STEPS,
        "x_api_configured": is_x_configured(),
        "x_status": x_status(),
        "telegram_configured": bool(settings.telegram_bot_token),
    }


@router.get("/knowledge")
async def list_knowledge(approved_only: bool = True):
    from aria_core.knowledge.cognitive import get_approved, get_pending
    items = await get_approved() if approved_only else await get_pending()
    return [
        {
            "id": k.id,
            "source": k.source,
            "topic": k.topic,
            "content": k.content,
            "confidence": k.confidence,
            "approved": k.approved,
            "created_at": k.created_at.isoformat(),
        }
        for k in items
    ]


@router.post("/knowledge")
async def add_knowledge_item(body: KnowledgeCreateRequest, request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.knowledge.cognitive import add_knowledge
    from aria_core.memory import append_memory

    item = await add_knowledge(
        source=body.source,
        topic=body.topic,
        content=body.content,
        confidence=0.9,
        approved=body.approved,
    )
    append_memory("curiosity", f"[{body.source}] [{body.topic}] {body.content[:120]}")
    return {
        "id": item.id,
        "topic": item.topic,
        "content": item.content,
        "approved": item.approved,
    }


@router.get("/capability")
async def get_capability(lang: str = "fr"):
    from aria_core.capability_levels import check_auto_completions, full_status

    check_auto_completions()
    return full_status("fr" if lang.startswith("fr") else "en")


class CapabilityLevelUpRequest(BaseModel):
    category: str = Field(..., min_length=3, max_length=32)
    note: str = Field(default="", max_length=300)


@router.post("/capability/level-up")
async def capability_level_up(body: CapabilityLevelUpRequest, request: Request):
    require_operator(request)
    from aria_core.capability_levels import complete_level

    cat = body.category.lower().replace("fiabilité", "fiabilite")
    return complete_level(cat, note=body.note or "operator API")


@router.get("/directives")
async def get_directives(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.directives import get_directives_text

    return {"directives": get_directives_text()}


@router.get("/launchpads")
async def launchpad_rankings(holding: bool = True):
    from aria_core.knowledge.base_launchpads import rank_launchpads, primary_pick

    ranked = rank_launchpads(holding_context=holding)
    pick = primary_pick(holding_context=holding)
    return {
        "pick": pick.id,
        "pick_name": pick.name,
        "holding_context": holding,
        "rankings": [
            {
                "id": lp.id,
                "name": lp.name,
                "url": lp.url,
                "score": score,
                "volume": lp.volume,
                "builders": lp.builders,
                "community": lp.community,
                "exposure": lp.exposure,
                "holding_fit": lp.holding_fit,
                "best_for": lp.best_for,
            }
            for lp, score in ranked
        ],
    }


@router.get("/avatar")
async def get_avatar_image():
    from aria_core.avatar import current_avatar_path

    path = current_avatar_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Avatar not set")
    return FileResponse(path, media_type="image/jpeg", filename="aria-avatar.jpg")


@router.get("/avatar/status")
async def avatar_status(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar import get_avatar_status

    return get_avatar_status()


@router.post("/avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...), note: str = ""):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar_identity import set_profile_with_identity

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        entry = await set_profile_with_identity(
            data,
            source="api_upload",
            note=note or "Operator API upload",
            force_establish=not note or "identity" in note.lower(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "current": entry}


@router.post("/avatar/pick/{avatar_id}")
async def pick_avatar(avatar_id: str, request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar import pick_gallery_avatar

    try:
        entry = await pick_gallery_avatar(avatar_id, note=f"API pick {avatar_id}")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "current": entry}


@router.post("/avatar/apply")
async def apply_avatar(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar import apply_avatar_sync

    sync = await apply_avatar_sync()
    return {"ok": True, "sync": sync}


class AvatarStyleConfigRequest(BaseModel):
    enabled: bool | None = None
    interval_days: int | None = Field(default=None, description="14 jours (minimum)")


class AvatarStyleGenerateRequest(BaseModel):
    style: str = Field(default="", max_length=600)


@router.get("/avatar/style")
async def avatar_style_status(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar_style_refresh import get_refresh_status

    return get_refresh_status()


@router.patch("/avatar/style/config")
async def avatar_style_config(body: AvatarStyleConfigRequest, request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar_style_refresh import update_config

    try:
        return update_config(enabled=body.enabled, interval_days=body.interval_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/avatar/style/propose")
async def avatar_style_propose(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar_style_refresh import propose_style

    style = await propose_style(force_new=True)
    return {"style": style}


@router.post("/avatar/style/generate")
async def avatar_style_generate(body: AvatarStyleGenerateRequest, request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar_style_refresh import generate_pending_style

    try:
        return await generate_pending_style(style=body.style or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/avatar/style/preview")
async def avatar_style_preview(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar_style_refresh import get_refresh_status, pending_preview_path

    path = pending_preview_path()
    if not path:
        raise HTTPException(status_code=404, detail="No pending style preview")
    return FileResponse(path, media_type="image/jpeg", filename="aria-style-preview.jpg")


@router.post("/avatar/style/apply")
async def avatar_style_apply(request: Request, note: str = ""):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar_style_refresh import apply_pending_style

    try:
        return await apply_pending_style(note=note or "Operator API")
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/avatar/style/discard")
async def avatar_style_discard(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar_style_refresh import discard_pending

    return {"ok": True, "message": discard_pending()}


@router.post("/avatar/style/run")
async def avatar_style_run(request: Request, force: bool = False):
    """Cycle complet : propose + génère aperçu + notifie Telegram (sans appliquer)."""
    if is_public_mode():
        require_operator(request)
    from aria_core.avatar_style_refresh import run_refresh_cycle

    result = await run_refresh_cycle(notify=True, force=force)
    if result.get("skipped"):
        raise HTTPException(status_code=400, detail=result.get("reason", "skipped"))
    return result


@router.post("/avatar/visual/run")
async def avatar_visual_run(request: Request, force: bool = True):
    """Cycle visuel autonome : ancre → Imagine avatar + bannière X (force=true par défaut)."""
    if is_public_mode():
        require_operator(request)
    from aria_core.visual_autonomy import run_visual_autonomy_cycle

    result = await run_visual_autonomy_cycle(lang="fr", notify=True, force=force)
    if result.get("skipped"):
        raise HTTPException(status_code=400, detail=result.get("reason", "skipped"))
    return result


class XPostRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=280)


@router.get("/x/status")
async def x_connection_status(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.gateway.x_twitter import verify_x_connection, x_status

    st = x_status()
    ok, message = await verify_x_connection() if st["post"] else (False, "Post keys not set")
    return {**st, "verified": ok, "message": message}


@router.get("/x/handles")
async def x_handles_status(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.handle_registry import registry_status

    return registry_status()


class XHandleAddRequest(BaseModel):
    handle: str = Field(..., min_length=1, max_length=50)
    role: str = Field(default="custom", max_length=64)


class XHandleAliasRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)
    handles: list[str] = Field(..., min_length=1)


@router.post("/x/handles")
async def x_handles_add(body: XHandleAddRequest, request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.handle_registry import add_handle

    try:
        return {"ok": True, "message": add_handle(body.handle, role=body.role)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/x/handles/{handle}")
async def x_handles_remove(handle: str, request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.handle_registry import remove_handle

    return {"ok": True, "message": remove_handle(handle)}


@router.post("/x/handles/alias")
async def x_handles_alias(body: XHandleAliasRequest, request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.handle_registry import set_alias

    try:
        return {"ok": True, "message": set_alias(body.name, body.handles)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/x/post")
async def x_post_tweet(body: XPostRequest, request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.gateway.x_twitter import post_tweet

    _, note = await post_tweet(body.text.strip(), approval_id="api")
    return {"ok": True, "detail": note}


@router.get("/github")
async def github_status(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.skills.github_skill import (
        allowed_read_repos,
        allowed_write_repos,
        github_configured,
        github_unlimited_access,
    )

    return {
        "configured": github_configured(),
        "unlimited": github_unlimited_access(),
        "owner": settings.github_owner,
        "sandbox_repo": settings.github_sandbox_repo,
        "token_repo": settings.github_token_repo,
        "excluded_repos": settings.github_excluded_repos,
        "write_repos": allowed_write_repos(),
        "read_repos": allowed_read_repos(),
    }


@router.get("/exchanges")
async def list_exchanges(request: Request, limit: int = 20):
    if is_public_mode():
        require_operator(request)
    from aria_core.exchanges import get_all
    exchanges = await get_all(limit)
    return [
        {
            "id": ex.id,
            "target": ex.target_agent,
            "status": ex.status.value,
            "channel": ex.channel,
            "message_preview": ex.message_body[:200],
            "published_at": ex.published_at.isoformat() if ex.published_at else None,
            "reply_at": ex.reply_at.isoformat() if ex.reply_at else None,
            "created_at": ex.created_at.isoformat(),
        }
        for ex in exchanges
    ]


@router.get("/content/site")
async def get_public_site_content():
    return public_site_payload()


@router.get("/content/faq")
async def get_faq_content(tag: str | None = None, q: str | None = None):
    if q:
        return {"items": search_faq(q, limit=10), "query": q}
    return {"items": list_faq(tag)}


@router.get("/truth-ledger/stats")
async def truth_ledger_stats():
    from aria_core.truth_ledger.store import ledger_stats
    return await ledger_stats()


@router.post("/truth-ledger/{entry_id}/verify")
async def approve_truth_entry(entry_id: str, request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.truth_ledger.store import verify_entry
    ok = await verify_entry(entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"id": entry_id, "status": "verified"}


@router.post("/truth-ledger/sync-canonical")
async def sync_canonical_truth(request: Request):
    """Re-sync canonical_facts.yaml → ledger + faq.yaml (operator only)."""
    if is_public_mode():
        require_operator(request)
    from aria_core.truth_ledger.canonical import sync_canonical_facts
    return await sync_canonical_facts()


@router.get("/content/drafts")
async def get_content_drafts(request: Request, limit: int = 20, kind: str | None = None):
    if is_public_mode():
        require_operator(request)
    return await list_drafts(limit=limit, kind=kind)


class TotpRequestBody(BaseModel):
    machine: str = Field(default="", max_length=64)
    purpose: str = Field(default="vault-sync", max_length=64)


class OperatorNotifyBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    source: str = Field(default="operator", max_length=64)


class OperatorFileGapBody(BaseModel):
    capability_id: str = Field(..., min_length=1, max_length=80)
    context: str = Field(default="", max_length=4000)
    open_pr: bool = True
    lang: str = Field(default="fr", max_length=8)


@router.post("/operator/notify")
async def operator_notify(body: OperatorNotifyBody, request: Request):
    """Envoie un message informatif a l'admin Telegram (scripts PC operateur)."""
    require_operator(request)
    from aria_core.gateway.telegram_bot import notify_admin

    prefix = f"[{body.source.strip() or 'operator'}] "
    sent = await notify_admin(prefix + body.text.strip())
    return {"ok": sent, "telegram_notified": sent}


@router.post("/operator/file-gap")
async def operator_file_gap(body: OperatorFileGapBody, request: Request):
    """Ouvre issue/PR self-improve (scripts PC operateur — audit, health, session)."""
    require_operator(request)
    from aria_core.capability_gap import (
        file_audit_security_gaps,
        file_capability_gap,
        file_operator_incident,
        file_post_session_bump,
        file_skill_gap,
        SECURITY_RULE_TO_GAP,
    )

    cap = body.capability_id.strip()
    ctx = body.context.strip()
    lang = body.lang.strip() or "fr"

    if cap == "audit_security_batch":
        import json

        try:
            findings = json.loads(ctx) if ctx.startswith("[") else []
        except json.JSONDecodeError:
            findings = []
        results = await file_audit_security_gaps(findings, lang=lang)
        return {"ok": True, "results": results}

    if cap.startswith("security_") or cap in SECURITY_RULE_TO_GAP.values():
        result = await file_capability_gap(cap, context=ctx, lang=lang, open_pr=body.open_pr)
        return {"ok": True, "result": result}

    if cap.startswith("operator_"):
        result = await file_operator_incident(cap, ctx, lang=lang)
        return {"ok": True, "result": result}

    if cap == "skill_missing" or cap.startswith("skill_"):
        slug = ctx.split("=", 1)[-1].strip() if "skill=" in ctx else cap.removeprefix("skill_")
        result = await file_skill_gap(slug, context=ctx, lang=lang)
        return {"ok": True, "result": result}

    if cap == "post_session_aria_core_bump":
        result = await file_post_session_bump(ctx, lang=lang)
        return {"ok": True, "result": result}

    result = await file_capability_gap(cap, context=ctx, lang=lang, open_pr=body.open_pr)
    return {"ok": True, "result": result}


@router.post("/totp/request")
async def totp_create_request(body: TotpRequestBody, request: Request):
    """Desactive — TOTP via agent IDE uniquement (plus Telegram)."""
    require_operator(request)
    raise HTTPException(
        status_code=410,
        detail="TOTP Telegram desactive — code dans chat Grok/Cursor (-TotpCode).",
    )


@router.get("/totp/poll/{request_id}")
async def totp_poll_request(request_id: str, request: Request):
    """Desactive — TOTP via agent IDE uniquement."""
    require_operator(request)
    raise HTTPException(
        status_code=410,
        detail="TOTP Telegram desactive — code dans chat Grok/Cursor (-TotpCode).",
    )


@router.get("/zhc/message/intro")
async def get_zhc_intro_message(request: Request):
    if is_public_mode():
        require_operator(request)
    from aria_core.skills.repertoire_skill import get_repertoire_summary
    from aria_core.skills.zhc_bridge import build_intro_message

    summary = await get_repertoire_summary()
    msg = build_intro_message(summary)
    return {
        "message": msg,
        "publish_hint": (
            "Publiez ce JSON en réponse à @JunoAgent sur X, "
            "ou envoyez-le sur le Telegram JUNO une fois le gateway configuré."
        ),
        "x_intent_url": x_juno_intent_url(),
    }