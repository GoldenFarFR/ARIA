"""Propositions de code long-cours — ARIA identifie une amélioration concrète et l'ouvre
comme ISSUE GitHub (jamais une PR, jamais un commit, jamais un merge autonome). Décision
opérateur (08/07) : elle "continue de construire" en continu via le heartbeat, mais
l'écriture et la fusion de code réelles restent une revue humaine (Claude Code ou
l'opérateur) — elle n'a ni les outils (git/tests/déploiement itératifs) ni l'autorisation
de merger seule. Le seul autre seam touchant du code est `develop_repertoire`/
`github_sandbox`, déjà opérateur-gaté ; celui-ci n'y touche pas non plus.

Gaté OFF par défaut (`ARIA_CODE_PROPOSAL_ENABLED`) — action visible sur le repo public,
même politique que `showcase_pr_watch` pour toute action GitHub autonome.
"""
from __future__ import annotations

import json
import os

TARGET_REPO = "ARIA"

_PROPOSAL_SYSTEM = (
    "Tu es ARIA. Identifie UNE amélioration concrète et bornée (pas un vœu vague) pour "
    "ton propre système (site, backend, capacité), à partir de ce que tu sais de ton "
    "architecture. Rédige une proposition d'issue GitHub structurée : Problème, Approche "
    "proposée, Fichiers/zones concernés, Esquisse d'implémentation (si pertinent), "
    "Risques/questions ouvertes. Concrète, bornée (implémentable en moins de 3 jours par "
    "un humain ou un agent de code), jamais une refonte massive."
)


def code_proposal_enabled() -> bool:
    from aria_core.skills.github_skill import github_configured

    if not github_configured():
        return False
    return os.environ.get("ARIA_CODE_PROPOSAL_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def generate_code_proposal(*, llm=None, context=None) -> dict | None:
    """Génère UNE proposition structurée (titre + corps), sans jamais toucher de code
    réel. Fail-closed : LLM absent/vide/non parsable -> None, jamais une proposition
    inventée ou tronquée silencieusement."""
    if llm is None:
        from aria_core.llm import chat_with_context as llm
    if context is None:
        from aria_core.proactive import build_llm_context

        context = await build_llm_context(public=False)

    prompt = (
        f"{context}\n\n"
        "Propose UNE amélioration concrète pour ce système. Réponds STRICTEMENT en JSON : "
        '{"title": "<titre court>", "body": "<proposition structurée en markdown>"}'
    )
    raw = await llm(prompt, _PROPOSAL_SYSTEM, max_tokens=700)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        title = str(data.get("title", "")).strip()
        body = str(data.get("body", "")).strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not title or not body:
        return None
    return {"title": title[:200], "body": body}


async def run_code_proposal_cycle(*, llm=None, github_client=None, notifier=None) -> dict:
    """Un tour : génère une proposition, l'ouvre comme issue GitHub (jamais une PR, jamais
    un commit). Fail-closed à chaque étage — un échec n'écrit jamais de code à la place."""
    if not code_proposal_enabled():
        return {"outcome": "skipped_disabled"}

    proposal = await generate_code_proposal(llm=llm)
    if not proposal:
        return {"outcome": "generation_failed"}

    from aria_core.runtime import settings

    if github_client is None:
        token = (settings.github_token or "").strip()
        if not token:
            return {"outcome": "no_token"}
        from aria_core.github_client import GitHubClient

        github_client = GitHubClient(token)

    owner = settings.github_owner
    body = (
        proposal["body"]
        + "\n\n---\n*Proposition générée par ARIA — revue humaine requise avant toute "
        "implémentation. Elle n'écrit ni ne fusionne jamais de code seule.*"
    )
    try:
        issue = await github_client.create_issue(
            owner, TARGET_REPO, proposal["title"], body, labels=["aria-proposal"],
        )
    except Exception as exc:  # noqa: BLE001 — un echec d'ouverture d'issue ne doit jamais casser le heartbeat
        return {"outcome": "error", "error": str(exc)[:300]}

    url = issue.get("html_url", "")
    if notifier:
        try:
            await notifier(f"📝 Proposition ARIA ouverte — {proposal['title']}\n{url}")
        except Exception:  # noqa: BLE001
            pass

    return {"outcome": "ok", "title": proposal["title"], "url": url}
