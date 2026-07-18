"""Autopsie pump/dump — brique "connaissance 24/7" (tâche #8, 09/07).

``weekly_training.resolve_due`` clôture chaque pronostic sur un point-à-point
entrée→prix courant à échéance — ça masque un pump-puis-crash survenu ENTRE
temps (ex. entrée $1, pic à $4 en cours de route, retombé à $1.10 à échéance :
le point-à-point dit "+10%", en réalité le token a pris 4x puis rendu presque
tout). Ce module relit la VRAIE série OHLCV parcourue pendant la détention
(``services/ohlcv``, déjà câblé ailleurs — pas de nouveau client), détecte
DÉTERMINISTIQUEMENT (aucun LLM, aucune invention) si un pattern pump/dump réel
a eu lieu, et si oui, demande au LLM une autopsie courte : qu'est-ce que la
thèse originale annonçait (recommandation/potentiel/risque déjà journalisés),
qu'est-ce que les vrais chiffres montrent, une leçon.

Deux sorties, jamais un troisième canal créé pour l'occasion :
  1. Log local (``pump_dump_autopsy_log``) — traçabilité complète, jamais publié.
  2. Si la leçon est jugée durable : proposition d'ISSUE GitHub (label
     ``aria-playbook-proposal``) — jamais un commit ni une fusion autonome,
     même doctrine stricte que ``knowledge_inbox.py`` / ``claude_mentor.py``.

Gaté OFF par défaut (``ARIA_PUMP_DUMP_AUTOPSY_ENABLED``). Un pronostic n'est
autopsié qu'une seule fois (dédoublonné par ``prediction_id``, contrainte
``UNIQUE`` en base).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
TARGET_REPO = "ARIA"

# Seuils de détection — déterministes, sur la vraie série OHLCV (jamais un LLM
# pour la détection elle-même). Un "pump" real doit avoir atteint au moins ce
# multiple de l'entrée ; le "dump" ensuite doit avoir rendu au moins cette part
# du pic. Les deux doivent être vrais pour qu'on parle de pump/dump.
PUMP_MULTIPLE_MIN = 1.5
DUMP_DRAWDOWN_MIN = 0.4
AUTOPSY_WINDOW_DAYS = 3  # ne relit que les pronostics clôturés récemment (fenêtre glissante)
MAX_PER_CYCLE = 5  # plafond de bon sens (coût LLM + GitHub), pas un plafond de risque

_AUTOPSY_SYSTEM = (
    "Tu es Claude Code, réviseur externe d'ARIA (pas ARIA elle-même). On te montre "
    "un pronostic VC qu'elle a réellement émis, et ce qui s'est RÉELLEMENT passé sur "
    "le prix (données OHLCV réelles, jamais inventées) : un pattern pump-puis-dump a "
    "été détecté. Rédige UNE autopsie courte et concrète : la thèse d'origine avait-"
    "elle déjà un signal qui annonçait ce risque (risque annoncé, potentiel, taille "
    "de position) ou est-ce un angle mort réel ? Ne spécule jamais au-delà des "
    "chiffres fournis. Réponds STRICTEMENT en JSON : "
    '{"lesson": "<leçon courte et concrète, une phrase>", "durable": true|false, '
    '"proposal_title": "<titre court si durable, sinon vide>", "proposal_body": '
    '"<proposition structurée en markdown si durable -- motif de pattern à ajouter '
    "à un playbook pump/dump, quel signal l'aurait annoncé plus tôt, sinon vide>\"}. "
    '`durable` = true SEULEMENT si le cas révèle un motif réutilisable pour de '
    "futures analyses, pas pour un cas isolé sans généralisation possible."
)


def pump_dump_autopsy_enabled() -> bool:
    return os.environ.get("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pump_dump_autopsy_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL UNIQUE,
                contract TEXT,
                run_at TEXT NOT NULL,
                outcome TEXT NOT NULL,
                peak_multiple REAL,
                drawdown_pct REAL,
                lesson TEXT,
                durable INTEGER NOT NULL DEFAULT 0,
                issue_url TEXT
            )
            """
        )
        await db.commit()


def detect_pump_dump(candles: list, entry_price: float | None, *, since_ts: int | None = None) -> dict | None:
    """Détection facts-only, déterministe, aucun LLM. ``candles`` = série OHLCV réelle
    (objets avec ``.ts``/``.high``/``.close``). ``since_ts`` filtre aux bougies de la
    période de détention réelle (sinon un pic pré-entrée pourrait fausser le pic).
    ``None`` si aucun pattern pump/dump réel n'est détecté."""
    if not candles or not entry_price or entry_price <= 0:
        return None
    window = [c for c in candles if since_ts is None or (getattr(c, "ts", 0) or 0) >= since_ts]
    if not window:
        return None
    highs = [c.high for c in window if getattr(c, "high", None) is not None]
    if not highs:
        return None
    peak = max(highs)
    peak_multiple = peak / entry_price
    if peak_multiple < PUMP_MULTIPLE_MIN:
        return None
    last_close = next((c.close for c in reversed(window) if getattr(c, "close", None) is not None), None)
    if last_close is None or peak <= 0:
        return None
    drawdown_pct = (peak - last_close) / peak
    if drawdown_pct < DUMP_DRAWDOWN_MIN:
        return None
    return {"peak_multiple": round(peak_multiple, 2), "drawdown_pct": round(drawdown_pct, 4)}


def _epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _format_case_for_prompt(prediction: dict, pattern: dict) -> str:
    return "\n".join([
        "Pronostic ARIA original (déjà journalisé, jamais réécrit) :",
        f"- Recommandation : {prediction.get('recommandation')}",
        f"- Potentiel annoncé : {prediction.get('potentiel')}/10",
        f"- Risque annoncé : {prediction.get('risque')}",
        f"- Taille suggérée : {prediction.get('taille_pct')}%",
        f"- Prix d'entrée réel : {prediction.get('entry_price')}",
        f"- Cible dérivée : {prediction.get('target_price')}",
        f"- Invalidation dérivée : {prediction.get('invalidation_price')}",
        "",
        "Ce qui s'est RÉELLEMENT passé (OHLCV réel, jamais inventé) :",
        f"- Pic atteint : {pattern['peak_multiple']}x le prix d'entrée",
        f"- Retombée depuis le pic à la clôture : {pattern['drawdown_pct']:.0%}",
        f"- Résultat point-à-point déjà journalisé : {prediction.get('outcome_pct')}%",
    ])


async def _propose_playbook(title: str, body: str, *, github_client=None) -> str | None:
    from aria_core.runtime import settings

    if github_client is None:
        token = (settings.github_token or "").strip()
        if not token:
            return None
        from aria_core.github_client import GitHubClient

        github_client = GitHubClient(token)

    owner = settings.github_owner
    body_full = (
        body
        + "\n\n---\n*Proposition générée par l'autopsie pump/dump (données OHLCV réelles) "
        "-- revue humaine requise avant toute intégration à un playbook. Aucun commit "
        "ni fusion autonome.*"
    )
    try:
        issue = await github_client.create_issue(
            owner, TARGET_REPO, f"[playbook pump/dump] {title}", body_full,
            labels=["aria-playbook-proposal"],
        )
    except Exception:  # noqa: BLE001 -- une panne GitHub ne doit jamais casser le cycle
        return None
    return issue.get("html_url")


async def _autopsy_one(prediction: dict, *, ohlcv_fetch=None, llm=None, github_client=None) -> dict:
    contract = prediction.get("contract") or ""
    pool = (prediction.get("pool_address") or "").strip()
    entry = prediction.get("entry_price")
    created_ts = _epoch(prediction.get("created_at"))

    if not pool or not entry:
        return {"outcome": "skipped_no_pool_or_entry", "prediction_id": prediction["id"]}

    if ohlcv_fetch is None:
        from aria_core.services.ohlcv import ohlcv_client

        async def ohlcv_fetch(pool_address: str, network: str):
            res = await ohlcv_client.get_ohlcv(pool_address, network=network)
            return res.candles if res.available else []

    candles = await ohlcv_fetch(pool, prediction.get("network") or "base")
    pattern = detect_pump_dump(candles, entry, since_ts=created_ts)
    if pattern is None:
        return {"outcome": "no_pattern", "prediction_id": prediction["id"]}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    prompt = _format_case_for_prompt(prediction, pattern)
    # 17/07 -- même bascule que claude_mentor.py (voir son commentaire pour le détail de
    # la revue) : Sonnet 5 via OpenRouter, secours explicite Haiku 4.5, puis repli global
    # existant (Grok/Groq). max_tokens porté à 900 -- 500 tronquait systématiquement les
    # autopsies Opus/Sonnet lors du test réel du 17/07 (finish_reason=length en plein mot).
    raw = await llm(
        prompt, _AUTOPSY_SYSTEM, max_tokens=900, depth="pump_dump_autopsy",
        provider="openrouter", model="anthropic/claude-sonnet-5",
        fallback_provider="openrouter", fallback_model="anthropic/claude-haiku-4.5",
    )

    lesson = ""
    durable = False
    issue_url = None
    if raw:
        try:
            data = json.loads(raw)
            lesson = str(data.get("lesson", "")).strip()
            durable = bool(data.get("durable", False))
            proposal_title = str(data.get("proposal_title", "")).strip()
            proposal_body = str(data.get("proposal_body", "")).strip()
            if durable and proposal_title and proposal_body:
                issue_url = await _propose_playbook(
                    proposal_title, proposal_body, github_client=github_client,
                )
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            lesson = ""

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO pump_dump_autopsy_log "
            "(prediction_id, contract, run_at, outcome, peak_multiple, drawdown_pct, "
            "lesson, durable, issue_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                prediction["id"], contract, _now(), "autopsied" if lesson else "llm_unavailable",
                pattern["peak_multiple"], pattern["drawdown_pct"], lesson, int(durable), issue_url,
            ),
        )
        await db.commit()

    return {
        "outcome": "autopsied" if lesson else "llm_unavailable",
        "prediction_id": prediction["id"],
        "contract": contract,
        "pattern": pattern,
        "lesson": lesson,
        "durable": durable,
        "issue_url": issue_url,
    }


async def run_pump_dump_autopsy_cycle(*, ohlcv_fetch=None, llm=None, github_client=None) -> dict:
    """Un tour de collecte + autopsie. Fail-closed si désactivé. Ne casse jamais le
    heartbeat (une panne par cas n'empêche pas les autres cas d'être traités)."""
    if not pump_dump_autopsy_enabled():
        return {"outcome": "skipped_disabled"}

    await _ensure_table()

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}

    from aria_core import vc_predictions

    async with aiosqlite.connect(DB_PATH) as db:
        already = {
            row[0]
            for row in await (
                await db.execute("SELECT prediction_id FROM pump_dump_autopsy_log")
            ).fetchall()
        }

    since = (datetime.now(timezone.utc) - timedelta(days=AUTOPSY_WINDOW_DAYS)).isoformat()
    closed = await vc_predictions.list_recently_closed(since, limit=50)
    candidates = [p for p in closed if p["id"] not in already][:MAX_PER_CYCLE]

    if not candidates:
        return {"outcome": "nothing_to_autopsy", "checked": len(closed)}

    results = []
    for prediction in candidates:
        try:
            result = await _autopsy_one(
                prediction, ohlcv_fetch=ohlcv_fetch, llm=llm, github_client=github_client,
            )
        except Exception as exc:  # noqa: BLE001 -- une autopsie ratée ne casse jamais le cycle
            logger.warning("pump_dump_autopsy: échec sur prédiction %s -- %s", prediction["id"], exc)
            result = {"outcome": "error", "prediction_id": prediction["id"], "error": str(exc)[:200]}
        results.append(result)

    autopsied = sum(1 for r in results if r["outcome"] == "autopsied")
    return {"outcome": "ok", "checked": len(closed), "autopsied": autopsied, "results": results}
