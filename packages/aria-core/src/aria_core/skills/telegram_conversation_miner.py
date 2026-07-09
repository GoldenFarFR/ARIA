"""Mineur de conversations opérateur/ARIA (Telegram) -- relit les échanges déjà
journalisés par `relay_chat.py` (le canal Telegram existant, pas un nouveau) et
PROPOSE (jamais n'impose) un enseignement durable observé dans le dialogue réel.
Même doctrine stricte que `knowledge_inbox.py`/`claude_mentor.py` : ISSUE GitHub
uniquement, jamais un commit ni une fusion autonome.

Garde-fou spécifique à ce module (au-delà de la doctrine commune) : la source
est une conversation PRIVÉE (peut contenir IP/mot de passe/clé -- vécu en
conditions réelles cette même nuit) et la destination est une ISSUE GITHUB
PUBLIQUE. Une création d'issue ne passe PAS par le scan `detect-secrets` de la
CI (qui ne couvre que les push) -- `_looks_like_secret` est le seul filet ici et
bloque la publication (jamais un envoi partiel) au moindre doute plutôt que de
laisser fuiter un fragment de secret dans le repo public.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

TARGET_REPO = "ARIA"
MIN_INTERVAL_HOURS = 20.0  # meme cadence que claude_mentor -- revue de fond, pas un chat continu
_MIN_NEW_MESSAGES = 6  # au moins quelques echanges pour qu'un motif soit credible
_MAX_MESSAGES_PER_RUN = 120

_MINER_SYSTEM = (
    "Tu es Claude Code, réviseur externe des échanges Telegram entre l'opérateur "
    "(GoldenFarFR) et ARIA. On te montre un extrait RÉEL de leur conversation. Cherche un "
    "enseignement DURABLE et GÉNÉRALISABLE (une préférence opérateur répétée, un correctif "
    "qu'ARIA devrait retenir, une confusion récurrente à éviter) -- jamais une anecdote "
    "ponctuelle. RÈGLE ABSOLUE : ne recopie JAMAIS un fragment verbatim de la conversation "
    "dans ta proposition (adresse IP, mot de passe, clé API, identifiant, nom de domaine "
    "privé, ou tout texte qui y ressemble) -- décris la leçon en langage abstrait "
    "uniquement, jamais une citation. Si rien de solide ne se dégage, dis-le honnêtement "
    "(durable=false) plutôt que d'inventer un motif. Réponds STRICTEMENT en JSON : "
    '{"durable": true|false, "proposal_title": "<titre court si durable, sinon vide>", '
    '"proposal_body": "<proposition structurée en markdown, en langage abstrait, sans '
    'aucune citation directe -- sinon vide>"}'
)

# Filet de sécurité local avant toute publication -- indépendant du scan CI (qui ne
# couvre que git push, pas les appels API comme la création d'une issue).
_SECRET_PATTERNS = [
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # IP
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bCG-[A-Za-z0-9]{15,}\b"),  # cle demo CoinGecko
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),  # token GitHub
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # token Slack
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),  # blob base64 long generique
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),  # cle API style OpenAI/Anthropic
]


def _looks_like_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def telegram_miner_enabled() -> bool:
    from aria_core import relay_chat
    from aria_core.skills.github_skill import github_configured

    if not relay_chat.relay_enabled() or not github_configured():
        return False
    return os.environ.get("ARIA_TELEGRAM_MINER_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_state_table(db) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS telegram_miner_state ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), last_mined_id INTEGER NOT NULL, "
        "last_run_at TEXT)"
    )
    await db.execute(
        "INSERT OR IGNORE INTO telegram_miner_state (id, last_mined_id, last_run_at) "
        "VALUES (1, 0, NULL)"
    )
    await db.commit()


async def _load_state() -> tuple[int, float | None]:
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_state_table(db)
        cursor = await db.execute(
            "SELECT last_mined_id, last_run_at FROM telegram_miner_state WHERE id = 1"
        )
        row = await cursor.fetchone()
    last_mined_id = row[0] if row else 0
    hours_since: float | None = None
    if row and row[1]:
        last_run = datetime.fromisoformat(row[1])
        hours_since = (datetime.now(timezone.utc) - last_run).total_seconds() / 3600.0
    return last_mined_id, hours_since


async def _save_state(last_mined_id: int) -> None:
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_state_table(db)
        await db.execute(
            "UPDATE telegram_miner_state SET last_mined_id = ?, last_run_at = ? WHERE id = 1",
            (last_mined_id, _now()),
        )
        await db.commit()


def _format_transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        sender = "Opérateur" if m["sender"] == "operator" else "ARIA"
        lines.append(f"{sender} : {m['content'][:500]}")
    return "\n".join(lines)


async def _propose_durable_insight(title: str, body: str, *, github_client=None) -> dict:
    """Publie l'issue -- SEULEMENT si ni le titre ni le corps ne ressemblent à un secret."""
    if _looks_like_secret(title) or _looks_like_secret(body):
        return {"outcome": "blocked_suspected_secret"}

    from aria_core.runtime import settings

    if github_client is None:
        token = (settings.github_token or "").strip()
        if not token:
            return {"outcome": "no_token"}
        from aria_core.github_client import GitHubClient

        github_client = GitHubClient(token)

    owner = settings.github_owner
    body_full = (
        body
        + "\n\n---\n*Proposition générée par Claude à partir d'un motif observé dans les "
        "échanges opérateur/ARIA -- jamais une citation directe, revue humaine requise "
        "avant toute intégration dans les fichiers de connaissance. Aucun commit ni fusion "
        "autonome.*"
    )
    try:
        issue = await github_client.create_issue(
            owner, TARGET_REPO, f"[connaissance] {title}", body_full,
            labels=["aria-knowledge-proposal"],
        )
    except Exception as exc:  # noqa: BLE001 -- une panne GitHub ne doit jamais casser le cycle
        return {"outcome": "error", "error": str(exc)[:300]}
    return {"outcome": "ok", "issue_url": issue.get("html_url")}


async def run_telegram_miner_cycle(*, llm=None, github_client=None) -> dict:
    """Un tour : relit les nouveaux échanges opérateur/ARIA depuis le dernier passage,
    propose un enseignement durable si un motif solide se dégage. Fail-closed à chaque
    étage, throttle interne (~1x/jour)."""
    if not telegram_miner_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}

    last_mined_id, hours_since = await _load_state()
    if hours_since is not None and hours_since < MIN_INTERVAL_HOURS:
        return {"outcome": "throttled", "hours_since_last": round(hours_since, 1)}

    from aria_core import relay_chat

    all_new = await relay_chat.recent_messages(since_id=last_mined_id, limit=_MAX_MESSAGES_PER_RUN)
    if not all_new:
        return {"outcome": "nothing_new"}

    highest_id = max(m["id"] for m in all_new)
    exchanges = [m for m in all_new if m["sender"] in ("operator", "aria")]

    if len(exchanges) < _MIN_NEW_MESSAGES:
        await _save_state(highest_id)
        return {"outcome": "insufficient_signal", "new_messages": len(exchanges)}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    transcript = _format_transcript(exchanges)
    prompt = f"Extrait de conversation :\n\n{transcript}\n\nCherche un enseignement durable."
    raw = await llm(prompt, _MINER_SYSTEM, max_tokens=700)
    if not raw:
        return {"outcome": "llm_unavailable"}

    try:
        data = json.loads(raw)
        durable = bool(data.get("durable", False))
        title = str(data.get("proposal_title", "")).strip()
        body = str(data.get("proposal_body", "")).strip()
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        await _save_state(highest_id)
        return {"outcome": "parse_failed"}

    await _save_state(highest_id)

    if not durable or not title or not body:
        return {"outcome": "not_durable"}

    result = await _propose_durable_insight(title, body, github_client=github_client)
    return {**result, "title": title}
