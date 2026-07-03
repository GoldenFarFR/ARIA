"""ACP client — acheter, financer, approuver, rejeter, trader (langage naturel)."""
from __future__ import annotations

import json
import re
from typing import Any

from pathlib import Path

import yaml

from aria_core.skills.acp_cli import (
    client_complete_job,
    client_create_job,
    client_fund_job,
    client_reject_job,
    is_acp_available,
    list_offerings,
    trade_tokens,
)

_CLIENT_RE = re.compile(
    r"(?i)\b(?:"
    r"achet|command|commander|crée?r?\s+un\s+job|fund|financer|payer|"
    r"approuv|complét|complet|valid|rejett|refus|"
    r"négoci|negoce|trade|swap|vendre|buy|sell|souscri|abonnement"
    r")\b"
)
_JOB_ID_RE = re.compile(r"(?i)\b(?:job[- ]?id|job)\s*[:#]?\s*([0-9a-fx-]{6,})")
_OFFERING_RE = re.compile(
    r"(?i)\b(?:offre|offering|workflow|service)\s+([a-z][a-z0-9_]*)"
    r"|(?:template|offre)\s+([a-z][a-z0-9_]*)"
)
_AMOUNT_RE = re.compile(r"(?i)(\d+(?:[.,]\d+)?)\s*(?:usdc|\$|dollars?)")
_CONTRACT_RE = re.compile(r"(0x[a-fA-F0-9]{40})")
_TRADE_PAIR_RE = re.compile(
    r"(?i)(?:swap|trade|échange|echange)\s+(\w+)\s+(?:contre|for|→|->)\s+(\w+)"
    r"|(?:vendre|sell)\s+(\d+(?:[.,]\d+)?)\s*(\w+)"
    r"|(?:achet|buy)\s+(\d+(?:[.,]\d+)?)\s*(\w+)"
)


_CONVERSATIONAL_ACP_RE = re.compile(
    r"(?i)(?:"
    r"comment\s+(?:se\s+)?passe|"
    r"ça\s+va|ca\s+va|"
    r"gagn(?:é|er)\s+(?:de\s+)?l['']?argent|"
    r"tu\s+(?:as|a)\s+(?:gagn|fait)|"
    r"combien\s+(?:as[- ]tu|tu\s+as|de\s+)"
    r")",
)


def wants_acp_client_action(message: str) -> bool:
    text = (message or "").strip()
    if not text or not re.search(r"(?i)\bacp\b", text):
        return False
    if _CONVERSATIONAL_ACP_RE.search(text):
        return False
    return bool(_CLIENT_RE.search(text))


def _provider_wallet() -> str:
    path = Path(__file__).resolve().parents[1] / "knowledge" / "acp_config.yaml"
    if not path.is_file():
        return ""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    return str(doc.get("agent_wallet") or doc.get("wallet") or "").strip()


def _parse_offering_name(message: str) -> str | None:
    m = _OFFERING_RE.search(message or "")
    if not m:
        return None
    return (m.group(1) or m.group(2) or "").strip().lower() or None


def _parse_job_id(message: str) -> str | None:
    m = _JOB_ID_RE.search(message or "")
    return m.group(1).strip() if m else None


def _default_requirements(offering: str, message: str) -> dict[str, Any]:
    contract = _CONTRACT_RE.search(message or "")
    if offering == "analyse_lite_x1" and contract:
        return {
            "contractAddress": contract.group(1),
            "chainId": "8453",
            "context": "Scan demandé via ARIA client.",
        }
    if offering == "analyse_full_x1" and contract:
        return {
            "contractAddress": contract.group(1),
            "chainId": "8453",
            "concerns": "Audit complet demandé via ARIA.",
        }
    if offering == "veille_zhc_x1":
        return {"topic": "ZHC autonomous agents", "horizon_hours": 24}
    return {"brief": (message or "")[:500]}


async def execute_acp_client_action(message: str, lang: str) -> tuple[str, dict]:
    lang_key = "fr" if lang == "fr" else "en"
    if not is_acp_available():
        msg = "ACP — acp-cli introuvable." if lang_key == "fr" else "ACP — acp-cli missing."
        return msg, {"acp": "no_cli"}

    text = (message or "").strip().lower()
    job_id = _parse_job_id(message)

    if job_id and re.search(r"(?i)\b(?:approuv|complét|complet|valid|approve)\b", message):
        row, err = client_complete_job(job_id)
        if err:
            return f"Complete job : {err[:300]}", {"acp": "client_complete_error"}
        return (
            f"Job {job_id} approuvé et complété sur ACP.",
            {"acp": "client_complete", **(row or {})},
        )

    if job_id and re.search(r"(?i)\b(?:rejett|refus|reject)\b", message):
        row, err = client_reject_job(job_id)
        if err:
            return f"Reject job : {err[:300]}", {"acp": "client_reject_error"}
        return (
            f"Job {job_id} rejeté sur ACP.",
            {"acp": "client_reject", **(row or {})},
        )

    if job_id and re.search(r"(?i)\b(?:fund|financer|payer)\b", message):
        amount = None
        m = _AMOUNT_RE.search(message)
        if m:
            amount = float(m.group(1).replace(",", "."))
        row, err = client_fund_job(job_id, amount_usdc=amount)
        if err:
            return f"Fund job : {err[:300]}", {"acp": "client_fund_error"}
        amt = f" ({amount} USDC)" if amount else ""
        return (
            f"Job {job_id} financé sur ACP{amt}.",
            {"acp": "client_fund", **(row or {})},
        )

    if re.search(r"(?i)\b(?:trade|swap|vendre|sell|achet|buy)\b", message):
        m = _TRADE_PAIR_RE.search(message)
        if not m:
            if lang_key == "fr":
                return (
                    "Trade ACP — précise : « trade acp swap 10 USDC contre ETH » "
                    "ou « acheter 5 USDC de VIRTUAL sur acp ».",
                    {"acp": "client_trade_parse"},
                )
            return "Specify trade: swap X USDC to TOKEN on acp.", {"acp": "client_trade_parse"}
        groups = [g for g in m.groups() if g]
        if len(groups) >= 2 and groups[0].replace(".", "").replace(",", "").isdigit():
            amount_in, token_out = groups[0], groups[1]
            token_in = "USDC"
        elif len(groups) >= 2:
            token_in, token_out = groups[0], groups[1]
            amount_in = "1"
        else:
            token_in, token_out, amount_in = "USDC", "ETH", "1"
        row, err = trade_tokens(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in.replace(",", "."),
        )
        if err:
            return f"Trade ACP : {err[:350]}", {"acp": "client_trade_error"}
        return (
            f"Trade ACP exécuté : {amount_in} {token_in} → {token_out}.",
            {"acp": "client_trade", **(row or {})},
        )

    if re.search(r"(?i)\b(?:crée?r?\s+un\s+job|command|commander|achet)\b", message):
        offering = _parse_offering_name(message)
        if not offering:
            offerings, _ = list_offerings()
            names = [str(o.get("name")) for o in offerings or [] if o.get("name")]
            hint = ", ".join(names[:6]) if names else "analyse_lite_x1, analyse_full_x1, veille_zhc_x1"
            return (
                f"Précise l'offre : « créer job acp offre {hint.split(',')[0].strip()} ».\n"
                f"Disponibles : {hint}",
                {"acp": "client_create_parse"},
            )
        req = _default_requirements(offering, message)
        provider = _provider_wallet()
        row, err = client_create_job(
            offering_name=offering,
            requirements=req,
            provider=provider,
        )
        if err:
            return f"Create job : {err[:350]}", {"acp": "client_create_error"}
        jid = str((row or {}).get("jobId") or (row or {}).get("id") or "?")
        return (
            f"Job ACP créé — offre {offering}, ID {jid}.\n"
            f"Étape suivante : « financer job acp {jid} ».",
            {"acp": "client_create", "offering": offering, "job_id": jid, **(row or {})},
        )

    if re.search(r"(?i)\b(?:négoci|negoce|abonnement|souscri)\b", message):
        if lang_key == "fr":
            return (
                "Négociation / abonnement ACP :\n"
                "• Abonnement full-access : aria_full_access (19,99 $ / 30j) — lié à toutes les offres.\n"
                "• Job sur mesure : « créer job acp offre analyse_full_x1 » + contrat 0x…\n"
                "• Custom : acp client create-custom-job (CLI) — décris le scope et le prix cible.\n"
                "Dis-moi l'offre et le budget pour que je crée le job.",
                {"acp": "client_negotiate_help"},
            )
        return "Specify offering + budget for ACP custom job.", {"acp": "client_negotiate_help"}

    if lang_key == "fr":
        return (
            "ACP client — langage naturel :\n"
            "• créer job acp offre analyse_lite_x1 0x…\n"
            "• financer job acp <id>\n"
            "• approuver job acp <id>\n"
            "• rejeter job acp <id>\n"
            "• trade acp swap 10 USDC contre ETH\n"
            "• négocier abonnement acp",
            {"acp": "client_help"},
        )
    return "ACP client: create job | fund | complete | reject | trade", {"acp": "client_help"}