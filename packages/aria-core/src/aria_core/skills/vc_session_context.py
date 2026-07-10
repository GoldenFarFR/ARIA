"""Mémoire courte du dernier rapport /vc opérateur — suivi en chat Telegram.

Un ``/vc`` ne passait pas par ``repertoire_db`` : l'ordre Telegram partait au bot
mais n'entrait pas dans l'historique LLM. Résultat vécu : « +515 pourquoi ? » après
une analyse → ARIA cherchait ailleurs (web/GitHub) au lieu de s'ancrer sur SON rapport.

Ce module persiste un résumé structuré du dernier /vc admin (TTL 4 h) et fournit un
bloc factuel injecté dans le prompt quand l'opérateur pose une question de suivi.
Lecture/écriture locales uniquement — jamais d'appel réseau.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.skills.vc_analysis import VCResult

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
_ROW_ID = 1
TTL_SECONDS = 4 * 3600


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS vc_operator_last (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def record_operator_vc(
    result: VCResult,
    *,
    prediction_id: int | None = None,
    telegram_summary: str = "",
) -> None:
    """Mémorise le dernier /vc opérateur (écrase toujours la ligne unique)."""
    await _ensure_table()
    payload = {
        "contract": result.contract,
        "symbol": result.symbol or "",
        "prediction_id": prediction_id,
        "recommandation": result.recommandation,
        "potentiel": result.potentiel,
        "risque": result.risque,
        "upside_pct": result.upside_pct,
        "downside_pct": result.downside_pct,
        "rr": result.rr,
        "entree": result.entree,
        "invalidation": result.invalidation,
        "cible": result.cible,
        "these": result.these,
        "security_score": result.security_score,
        "lite_verdict": result.lite_verdict,
        "telegram_summary": telegram_summary,
    }
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO vc_operator_last (id, payload, recorded_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, recorded_at = excluded.recorded_at
            """,
            (_ROW_ID, json.dumps(payload, ensure_ascii=False), now),
        )
        await db.commit()


async def load_operator_vc() -> dict | None:
    """Charge le dernier /vc si encore dans la fenêtre TTL."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT payload, recorded_at FROM vc_operator_last WHERE id = ?",
                (_ROW_ID,),
            )
        ).fetchone()
    if not row:
        return None
    try:
        recorded_at = datetime.fromisoformat(row[1])
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - recorded_at).total_seconds()
        if age > TTL_SECONDS:
            return None
        data = json.loads(row[0])
        data["recorded_at"] = row[1]
        return data
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning("vc_session_context: payload illisible")
        return None


_FOLLOWUP_RE = re.compile(
    r"(?:"
    r"pourquoi|explique|comment|justif|d['']?où|d ou tu|de ou tu|"
    r"énorme|excessif|trop\s+(?:haut|élevé|eleve|gros)|"
    r"(?:ton|ta|mon|cette|cet|ces)\s+(?:analyse|rapport|reco|verdict|chiffre|valeur)"
    r")",
    re.I,
)
_VC_TOPIC_RE = re.compile(
    r"(?:"
    r"\+?\d{2,4}\s*%?|ratio|r(?:/|isque).?récompense|\brr\b|"
    r"\b(avoid|buy|watch|sell|analyse|rapport|reco|verdict|token|cible|entrée|invalidation|risque)\b|"
    r"valeur"
    r")",
    re.I,
)


def is_vc_followup_question(message: str) -> bool:
    """Détecte une question de suivi sur le dernier rapport /vc (opérateur)."""
    text = (message or "").strip()
    if not text or len(text) > 500:
        return False
    if not _FOLLOWUP_RE.search(text):
        return False
    return bool(_VC_TOPIC_RE.search(text))


def build_followup_context_block(data: dict, *, lang: str = "fr") -> str:
    """Bloc factuel pour le prompt LLM — jamais inventé, uniquement le payload persisté."""
    sym = data.get("symbol") or data.get("contract", "")[:10]
    lines_fr = [
        "DERNIER RAPPORT /vc (il y a quelques minutes — ancre-toi UNIQUEMENT sur ces faits) :",
        f"- Token : {sym} ({data.get('contract', '')})",
        f"- Recommandation : {data.get('recommandation')} · Potentiel {data.get('potentiel')}/10 · "
        f"Risque {data.get('risque')}",
        f"- Score on-chain : {data.get('security_score')} · Verdict scan : {data.get('lite_verdict')}",
    ]
    if data.get("upside_pct") is not None and data.get("downside_pct") is not None:
        lines_fr.append(
            f"- R/R mécanique : +{data['upside_pct']:.0f}% gain théorique pour "
            f"{data['downside_pct']:.0f}% risqué (ratio {data.get('rr')}) — "
            f"entrée {data.get('entree')}, invalidation {data.get('invalidation')}, "
            f"cible {data.get('cible')}"
        )
        lines_fr.append(
            "- Ce % vient des niveaux TA réels (support → résistance/plus-haut), pas d'une "
            "promesse. Si la reco est AVOID, le ratio est informatif seulement."
        )
    if data.get("these"):
        lines_fr.append(f"- Thèse : {data['these']}")
    if data.get("prediction_id"):
        lines_fr.append(f"- Prédiction track-record : #{data['prediction_id']}")
    lines_fr.append(
        "Si l'opérateur demande pourquoi un chiffre (+515%, ratio, etc.), explique-le "
        "depuis ces niveaux. Ne cherche pas sur le web ni GitHub pour ce sujet."
    )

    if lang == "en":
        return "\n".join(
            line.replace("DERNIER RAPPORT", "LAST /vc REPORT")
            for line in lines_fr
        )
    return "\n".join(lines_fr)


async def get_followup_context_block(*, lang: str = "fr") -> str | None:
    data = await load_operator_vc()
    if not data:
        return None
    return build_followup_context_block(data, lang=lang)
