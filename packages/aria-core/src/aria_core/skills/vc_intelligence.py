"""Veille des thèses VC crypto -- inspiration + proposition de calibration stratégique.

Suit un petit nombre de VC crypto reconnus (X, comptes vérifiés -- cf.
`x_watchlist.yaml::vc_handles`) pour repérer les signaux de thèse/conviction publics, et --
SEULEMENT si un LLM juge le constat durable -- PROPOSE (jamais n'impose) une piste de
calibration stratégique via une issue GitHub. Jamais un commit ni une fusion autonome sur
les fichiers de stratégie (`docs/protocole-argent-reel.md`,
`docs/strategie-aria-investissement.md` restent verrouillés, validation opérateur explicite
requise -- périmètre confirmé par l'opérateur le 09/07 : "observer + proposer").

Volet wallets (analyse on-chain des VC connus, « la face cachée ») : SEAM VOLONTAIREMENT
VIDE. Aucune adresse de wallet n'est branchée tant qu'elle n'est pas vérifiée par une source
fiable -- jamais une adresse devinée (cf. le HANDOFF le plus récent pour l'état de la
vérification en cours).

Réutilise le MÊME fetch que le cycle de curiosité existant (`fetch_curiosity_feed()`, appelé
une seule fois par `curiosity.run_curiosity_cycle()`) -- aucun appel X supplémentaire, même
doctrine que `opportunity_radar.mine_curiosity_items`.
"""
from __future__ import annotations

import json
import os
from typing import Any

TARGET_REPO = "ARIA"

_VC_SYNTHESIS_SYSTEM = (
    "Tu es ARIA, analyste on-chain de Aria Vanguard ZHC. On te montre des tweets récents de "
    "VC crypto reconnus (a16z, Paradigm, Dragonfly, Variant, Coinbase Ventures, Electric "
    "Capital, IOSG) -- lecture seule, jamais une source de vérité en soi. Synthétise en 2-4 "
    "phrases ce qu'ils signalent comme conviction/thèse en ce moment (secteur, narratif, "
    "type de projet qu'ils semblent privilégier). Si le contenu est trop faible ou générique "
    "pour en tirer un signal réel, dis-le honnêtement plutôt que d'inventer une tendance. "
    "Réponds STRICTEMENT en JSON : "
    '{"summary": "<synthèse courte, français>", "durable": true|false, '
    '"proposal_title": "<titre court si durable, sinon vide>", '
    '"proposal_body": "<piste de calibration structurée en markdown si durable -- jamais une '
    "réécriture directe des fichiers de stratégie, une PISTE à évaluer par l'opérateur -- "
    'sinon vide>"}. `durable` = true SEULEMENT si le signal est assez fort et cohérent pour '
    "mériter une vraie réflexion stratégique, jamais pour un tweet isolé ou du bruit."
)


def vc_intelligence_enabled() -> bool:
    return os.environ.get("ARIA_VC_INTELLIGENCE_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _format_vc_items_for_prompt(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items[:20]:
        topic = str(item.get("topic") or "?")
        text = str(item.get("text") or "").strip()[:280]
        if text:
            lines.append(f"- {topic} : {text}")
    return "\n".join(lines)


async def _propose_strategy_issue(title: str, body: str, *, github_client=None) -> str | None:
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
        + "\n\n---\n*Piste générée depuis la veille des thèses VC (lecture seule, X public) "
        "-- jamais une réécriture des fichiers de stratégie. Revue et décision opérateur "
        "requises avant toute intégration.*"
    )
    try:
        issue = await github_client.create_issue(
            owner, TARGET_REPO, f"[stratégie] {title}", body_full,
            labels=["aria-strategy-proposal"],
        )
    except Exception:  # noqa: BLE001 -- une panne GitHub ne doit jamais casser le cycle
        return None
    return issue.get("html_url")


async def run_vc_intelligence_cycle(
    *,
    items: list[dict[str, Any]],
    llm=None,
    notifier=None,
    github_client=None,
) -> dict:
    """Un tour : synthétise les items VC déjà filtrés (cf. `curiosity.py`), pousse un digest
    lecture-seule à l'opérateur, propose une issue SEULEMENT si jugé durable."""
    if not vc_intelligence_enabled():
        return {"outcome": "skipped_disabled"}
    if not items:
        return {"outcome": "no_items"}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    from aria_core.runtime import settings
    from aria_core.spark_config import DEFAULT_MODEL_DEVELOP

    develop_model = (
        getattr(settings, "aria_llm_model_develop", None) or ""
    ).strip() or DEFAULT_MODEL_DEVELOP

    prompt = _format_vc_items_for_prompt(items)
    raw = await llm(
        prompt, _VC_SYNTHESIS_SYSTEM, max_tokens=500, model=develop_model, depth="vc_intelligence",
    )
    if not raw:
        return {"outcome": "llm_unavailable"}

    try:
        data = json.loads(raw)
        summary = str(data.get("summary", "")).strip()
        durable = bool(data.get("durable", False))
        proposal_title = str(data.get("proposal_title", "")).strip()
        proposal_body = str(data.get("proposal_body", "")).strip()
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return {"outcome": "parse_failed"}

    if not summary:
        return {"outcome": "empty_summary"}

    if notifier is not None:
        try:
            await notifier(f"🧠 Veille VC\n\n{summary}")
        except Exception:  # noqa: BLE001 -- un envoi raté ne bloque jamais le cycle
            pass

    issue_url = None
    if durable and proposal_title and proposal_body:
        issue_url = await _propose_strategy_issue(
            proposal_title, proposal_body, github_client=github_client,
        )

    return {"outcome": "ok", "summary": summary, "durable": durable, "issue_url": issue_url}
