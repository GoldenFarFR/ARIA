import re

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from aria_core import repertoire_db
from aria_core.brain import aria_brain
from aria_core.heartbeat import aria_heartbeat, HEARTBEAT_TASKS, _START_TIME
from aria_core.knowledge.web_verify import is_live_info_question
from aria_core.locale import LANG_FR, detect_lang
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

# Widget site (/aria/chat) SEULEMENT -- pas le chat public Telegram (généraliste par
# doctrine, même brain.process mais route distincte). Une question qui ressemble à de
# l'actu générale (is_live_info_question) et ne porte AUCUN mot-clé du périmètre
# Vanguard/ARIA/ZHC/BASE ne doit pas déclencher de recherche web ici -- réponse de
# recadrage immédiate à la place.
_SITE_SCOPE_RE = re.compile(
    r"\bvanguard\b|\baria\b|\bzhc\b|\bbase\b|\btoken\b|\blaunchpad\b|"
    r"m[ée]thodolog|track[\s-]?record",
    re.IGNORECASE,
)


def _is_out_of_scope_live_info(message: str) -> bool:
    return is_live_info_question(message) and not _SITE_SCOPE_RE.search(message)


def _scope_redirect_reply(message: str) -> str:
    if detect_lang(message) == LANG_FR:
        return (
            "Ce chat est dédié à Vanguard, ARIA, ZHC et BASE — pas à l'actualité générale "
            "(sport, cours, news...). Pose-moi une question sur le holding, ARIA, le token, "
            "le launchpad, ou notre méthodologie/track record."
        )
    return (
        "This chat is scoped to Vanguard, ARIA, ZHC and BASE — not general news (sports, "
        "prices, headlines...). Ask me about the holding, ARIA, the token, the launchpad, "
        "or our methodology/track record."
    )


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

    message = body.message.strip()
    if _is_out_of_scope_live_info(message):
        reply = _scope_redirect_reply(message)
        await repertoire_db.save_message("user", message, visitor_id=visitor_id)
        await repertoire_db.save_message("agent", reply, visitor_id=visitor_id)
        return ChatResponse(
            reply=reply,
            skill_used=None,
            actions_taken=["Chat scope filter (site widget) — recadrage sans recherche web"],
            data={"scope_redirect": True, "skip_web": True},
        )

    return await aria_brain.process(
        message,
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

    Chiffres agrégés seulement — le détail des positions, les contrats candidats et les
    hashes de verdict restent réservés aux abonnés/opérateur (jamais exposés ici, ça
    donnerait l'alpha gratuitement). Facts-only : si rien n'est encore valorisé, l'indice
    vaut 100 (+0 %), jamais un chiffre gonflé. Calibration/ventilation 85-15 ajoutées le
    10/07 (centre de commandement) : « est-ce qu'un 8/10 bat vraiment un 5/10 ? » --
    la vraie question d'un investisseur, répondue avec les vrais chiffres ou explicitement
    absente (buckets vides) si l'échantillon est encore trop jeune.
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
        "calibration": m.get("calibration", []),
        "by_strategy": m.get("by_strategy", {}),
        "pool_active": pool,
        "pool_rejected": pool_rejected,
        "disclaimer": (
            "Track-record de suivi (paper) valorisé aux prix on-chain réels. "
            "Informationnel, pas un conseil. Aucun rendement garanti."
        ),
    }


@router.get("/paper-wallet")
async def paper_wallet():
    """Portefeuille paper-trading PUBLIC (#76) : preuve de track-record, jamais l'alpha.

    Même doctrine que ``track_record()`` : chiffres agrégés seulement. Positions
    OUVERTES = agrégat uniquement (nombre + P&L latent total, jamais une ligne par
    position — c'est l'alpha la plus sensible, en temps réel, exposer le détail
    permettrait de copy-trader). Historique (trades CLÔTURÉS) = symbole visible
    (narratif crédible) mais jamais le contrat, ni le prix d'entrée/sortie, ni la
    raison de sortie (fuiterait la méthode stop suiveur / TP échelonné) — date
    tronquée au jour (pas l'heure précise, pour ne pas corréler avec un event
    on-chain). Facts-only comme ``track_record()`` : 0 position -> rendement 0 %,
    jamais un chiffre gonflé.
    """
    from aria_core import paper_trader

    summary = await paper_trader.portfolio_summary()
    closed = await paper_trader.get_closed_positions(limit=50)
    history = [
        {
            "symbol": p.get("symbol") or "",
            "closed_at": (p.get("closed_at") or "")[:10],
            "pnl_pct": p.get("pnl_pct"),
            "outcome": "win" if (p.get("pnl_usd") or 0.0) > 0 else "loss",
        }
        for p in closed
    ]
    return {
        "starting": summary["starting"],
        "equity": summary["equity"],
        "return_pct": summary["return_pct"],
        "realized_pnl": summary["realized_pnl"],
        "unrealized_pnl": summary["unrealized_pnl"],
        "open_positions": summary["open_positions"],
        "closed_trades": summary["closed_trades"],
        "win_rate": summary["win_rate"],
        "history": history,
        "disclaimer": (
            "Portefeuille de suivi (paper), 1 000 000 $ fictifs, prix on-chain réels. "
            "Informationnel, pas un conseil. Aucun rendement garanti."
        ),
    }


@router.get("/market-cycle")
async def market_cycle():
    """Cycle macro Bitcoin PUBLIC (halving à halving, pluri-annuel) : phase actuelle
    seulement (déterministe, aucun LLM, cache 1h côté aria-core). Contexte, jamais un
    signal d'achat/vente -- même cadre que la section « Contexte marché » des rapports /vc.
    """
    from aria_core.skills.btc_cycles import fetch_current_macro_phase

    phase = await fetch_current_macro_phase()
    return {
        "available": phase is not None,
        "phase": phase,
        "disclaimer": (
            "Cadre de lecture répandu (théorie des cycles de 4 ans liée au halving), "
            "pas une loi de marché prouvée."
        ),
    }


@router.get("/sentiment")
async def market_sentiment_public():
    """Sentiment de marché PUBLIC (RSI/Bollinger/momentum/retracement -> régimes,
    vocabulaire Wall St Cheat Sheet) des paires principales (BTC, ETH). Lit UNIQUEMENT
    la dernière lecture déjà calculée par le cycle heartbeat `market_sentiment_cycle`
    (gate ARIA_MARKET_SENTIMENT_ENABLED) -- ne recalcule rien ici, jamais d'appel
    réseau synchrone sur une requête publique.
    """
    from aria_core.skills.market_sentiment import REGIME_LABELS, latest_readings

    readings = await latest_readings()
    return {
        "readings": readings,
        "regime_labels": REGIME_LABELS,
        "disclaimer": (
            "Cadre de lecture inspiré du Wall St Cheat Sheet (psychologie du cycle de "
            "marché), simplifié en régimes mesurables -- pas une loi de marché prouvée."
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


@router.get("/sepolia-status")
async def sepolia_status():
    """Statut PUBLIC du rehearsal Sepolia autonome — chiffres agrégés + dernière décision
    seulement (jamais une clé, jamais un montant réel : testnet, aucune valeur réelle).
    """
    from aria_core.onchain.sepolia_autonomous import autonomous_status

    return await autonomous_status()


@router.get("/sepolia/code")
async def sepolia_code_check(address: str, request: Request):
    """Opérateur SEULEMENT : vérifie qu'un contrat a réellement du bytecode déployé sur
    Base Sepolia (lecture RPC seule, aucune clé, aucun signing) — étape de vérification
    avant de configurer ARIA_SEPOLIA_SWAP_ROUTER/_TOKEN_OUT sur un contrat non confirmé.
    """
    require_operator(request)
    from aria_core.onchain.sepolia_wallet import get_code

    result = get_code(address)
    if result is None:
        raise HTTPException(status_code=502, detail="Lecture RPC Sepolia indisponible.")
    return result


@router.get("/bonding-pool")
async def bonding_pool(request: Request, status: str = "active", limit: int = 100):
    """Opérateur SEULEMENT (#60, marché « Jetons d'agent ») : expose en lecture seule
    le pool `screened_token` réseau `base-bonding` déjà filtré par `bonding_screen.py`.

    Existe pour que le wrapper `bondv5-trader` (process TypeScript séparé) lise les
    candidats déjà analysés via HTTP plutôt que d'ouvrir `aria.db` en SQLite directement
    — deux langages/process ne doivent jamais taper le même fichier SQLite en continu.
    Aucune analyse ici, juste une relecture de `screened_pool.list_pool`.
    """
    require_operator(request)
    from aria_core import screened_pool

    limit = max(1, min(limit, 1000))
    items = await screened_pool.list_pool(status=status, network="base-bonding", limit=limit)
    return {"items": items, "status": status, "count": len(items)}


@router.post("/bonding-pool/trade-log")
async def bonding_pool_trade_log(body: dict, request: Request):
    """Opérateur SEULEMENT : enregistre le résultat d'une exécution `bondv5-trader`
    (#60). Écriture strictement dans `bonding_trade_log`, jamais dans `screened_token`
    — la séparation lecture-analyse / écriture-exécution est volontaire."""
    require_operator(request)
    from aria_core import bonding_trade_log

    contract = str(body.get("contract") or "").strip()
    side = str(body.get("side") or "").strip()
    status = str(body.get("status") or "").strip()
    if not contract or side not in ("buy", "sell") or status not in ("ok", "failed", "blocked"):
        raise HTTPException(status_code=422, detail="contract, side (buy/sell) et status (ok/failed/blocked) requis.")

    await bonding_trade_log.record_trade(
        contract=contract,
        symbol=str(body.get("symbol") or ""),
        side=side,
        amount_usdc=float(body.get("amount_usdc") or 0.0),
        amount_token=float(body.get("amount_token") or 0.0),
        min_out_wei=str(body.get("min_out_wei") or ""),
        slippage_bps=int(body.get("slippage_bps") or 0),
        tx_hash=str(body.get("tx_hash") or ""),
        status=status,
        reason=str(body.get("reason") or ""),
    )
    return {"recorded": True}


@router.get("/relay/recent")
async def relay_recent(request: Request, since_id: int = 0):
    """Historique récent du relais Claude/opérateur/ARIA (canal Telegram existant).

    Gaté par un accès DÉDIÉ (`ARIA_RELAY_ACCESS_TOKEN`), distinct du secret admin —
    ne donne accès qu'à ce relais, rien d'autre. Fail-closed : token absent/invalide
    -> 403, jamais un historique renvoyé par erreur.
    """
    from aria_core.relay_chat import recent_messages, verify_relay_access

    if not verify_relay_access(request.headers.get("X-Relay-Access")):
        raise HTTPException(status_code=403, detail="Relay access required")
    return {"messages": await recent_messages(since_id=since_id)}


class RelayReplyRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@router.post("/relay/reply")
async def relay_reply(body: RelayReplyRequest, request: Request):
    """Poste un message à l'opérateur à travers le bot ARIA existant (préfixé "Claude —"),
    et le journalise. Même gate dédié que `/relay/recent`."""
    from aria_core.relay_chat import send_relay_reply, verify_relay_access

    if not verify_relay_access(request.headers.get("X-Relay-Access")):
        raise HTTPException(status_code=403, detail="Relay access required")
    sent = await send_relay_reply(body.text)
    if not sent:
        raise HTTPException(status_code=503, detail="Relay disabled or send failed")
    return {"ok": True}


@router.get("/diagnostics/pool-status")
async def diagnostics_pool_status(request: Request):
    """Diagnostic lecture seule du pool de sourcing (`screened_token`) — pensé pour
    être appelé directement depuis une session Claude Code (y compris depuis le
    cloud, qui n'a pas d'accès VPS/base direct), sans dispatcher vers une session
    VPS ouverte à chaque fois. Gaté par un accès DÉDIÉ (`ARIA_DIAGNOSTIC_TOKEN`),
    distinct du secret admin et du token relay — ne donne accès qu'à ce diagnostic,
    rien d'autre.
    """
    from aria_core import screened_pool
    from aria_core.diagnostics_access import verify_diagnostic_access

    if not verify_diagnostic_access(request.headers.get("X-Diagnostic-Access")):
        raise HTTPException(status_code=403, detail="Diagnostic access required")

    result: dict = {}
    for network in ("base", "base-bonding"):
        counts = {
            status: await screened_pool.count_pool(status=status, network=network)
            for status in ("active", "pending", "rejected")
        }
        closest = await screened_pool.list_closest_to_passing(network=network, limit=3)
        result[network] = {
            "counts": counts,
            "closest_to_passing": [
                {
                    "contract": c.get("contract"),
                    "symbol": c.get("symbol"),
                    "security_score": c.get("security_score"),
                    "liquidity_usd": c.get("liquidity_usd"),
                    "verdict": c.get("verdict"),
                    "screen_reason": c.get("screen_reason"),
                    "retry_count": c.get("retry_count"),
                }
                for c in closest
            ],
        }
    return result


@router.get("/diagnostics/agent-wallet-ledger")
async def diagnostics_agent_wallet_ledger(request: Request, limit: int = 100):
    """Journal des transactions du futur pilote agent-wallet (seam — reste vide tant
    qu'aucun produit n'est choisi/câblé, cf. CLAUDE.md 15/07). Même gate dédié que
    `/diagnostics/pool-status`.
    """
    from aria_core import agent_wallet_log
    from aria_core.diagnostics_access import verify_diagnostic_access

    if not verify_diagnostic_access(request.headers.get("X-Diagnostic-Access")):
        raise HTTPException(status_code=403, detail="Diagnostic access required")

    limit = max(1, min(limit, 1000))
    return {"transactions": await agent_wallet_log.list_transactions(limit=limit)}


@router.get("/diagnostics/paper-ledger")
async def diagnostics_paper_ledger(request: Request, closed_limit: int = 100):
    """Registre du paper-trading 1M$ (#194) : positions ouvertes ET clôturées, avec
    le plan d'entrée/sortie complet (thèse, cible, invalidation) — pensé pour qu'une
    session Claude Code (y compris cloud, sans accès VPS/base direct) puisse suivre
    le test sans dépendre d'un relais manuel (capture Telegram/cockpit). Même gate
    dédié que `/diagnostics/pool-status`/`/diagnostics/agent-wallet-ledger`.
    """
    from aria_core import paper_trader
    from aria_core.diagnostics_access import verify_diagnostic_access

    if not verify_diagnostic_access(request.headers.get("X-Diagnostic-Access")):
        raise HTTPException(status_code=403, detail="Diagnostic access required")

    closed_limit = max(1, min(closed_limit, 1000))

    def _fmt(p: dict) -> dict:
        return {
            "contract": p.get("contract"),
            "symbol": p.get("symbol"),
            "chain": p.get("chain"),
            "status": p.get("status"),
            "entry_price": p.get("entry_price"),
            "target_price": p.get("target_price"),
            "invalidation_price": p.get("invalidation_price"),
            "cost_usd": p.get("cost_usd"),
            "opened_at": p.get("opened_at"),
            "exit_price": p.get("exit_price"),
            "closed_at": p.get("closed_at"),
            "pnl_usd": p.get("pnl_usd"),
            "pnl_pct": p.get("pnl_pct"),
            "close_reason": p.get("close_reason"),
            # 19/07 -- trouvé en investiguant une fausse alerte du watchdog paper-trading
            # ("close_notes vide" sur des positions dont la vraie ligne DB est bien
            # remplie) : cet endpoint omettait close_notes/realized_pnl_partial, la
            # seule vraie justification chiffrée de sortie (cf. paper_trader.close_position/
            # reduce_position) restait invisible pour toute session sans accès direct à
            # aria.db (watchdog inclus).
            "close_notes": p.get("close_notes"),
            "realized_pnl_partial": p.get("realized_pnl_partial"),
            "thesis": p.get("thesis"),
            # 19/07 -- revue croisée Gemini : ATR en % du prix d'entrée (paper_trader.
            # _effective_trail_pct), la vraie largeur de stop suiveur appliquée à CETTE
            # position -- ``None`` = stop fixe (TRAIL_STOP_PCT), sinon adaptatif.
            "entry_atr_pct": p.get("entry_atr_pct"),
        }

    open_positions = await paper_trader.get_open_positions()
    closed_positions = await paper_trader.get_closed_positions(limit=closed_limit)
    starting_capital = await paper_trader.starting_capital()

    return {
        # 20/07 -- extraction directe de la thèse écrite par ARIA elle-même
        # (aria-brain, chapitre 1) : le risque qu'elle nomme n'est pas de mentir,
        # c'est qu'un résultat simulé ressemble structurellement à un résultat
        # réel. Ce payload JSON était identique, champ pour champ, à ce que
        # rendrait un registre réel -- rien dans la DONNÉE elle-même (seulement
        # l'URL + un header opaque) ne le distinguait. Marqueur explicite ajouté
        # directement dans la réponse, pas seulement dans le chemin d'accès.
        "simulated": True,
        "disclaimer": "Portefeuille papier fictif -- aucun capital réel, aucune position réelle.",
        "starting_capital": starting_capital,
        "open_positions": [_fmt(p) for p in open_positions],
        "closed_positions": [_fmt(p) for p in closed_positions],
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