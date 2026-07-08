"""Garde-fou de COHÉRENCE — impose que les affirmations sur le système collent au code réel.

Pourquoi ce fichier existe : la description d'ARIA (CLAUDE.md, docs, capacités) était écrite
à la main et dérivait du code (capacités annoncées mais orphelines/stubs/absentes, secrets
qui réapparaissent). Chaque session lisait un instantané faux → incohérences. Ces tests
CODIFIENT les invariants qui doivent TOUJOURS tenir ; s'ils cassent, la CI passe au rouge et
la dérive est bloquée avant d'atteindre une nouvelle session.

Tout est statique/hors-ligne (lecture de fichiers), aucun secret, aucun réseau. Quand tu
changes volontairement un invariant, mets À JOUR ce fichier dans le MÊME commit : c'est le
contrat de cohérence.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CORE = REPO / "packages" / "aria-core" / "src" / "aria_core"


def _read(rel: str) -> str:
    p = REPO / rel
    assert p.is_file(), f"Fichier attendu manquant : {rel}"
    return p.read_text(encoding="utf-8", errors="replace")


def _read_core(rel: str) -> str:
    p = CORE / rel
    assert p.is_file(), f"Module aria_core attendu manquant : {rel}"
    return p.read_text(encoding="utf-8", errors="replace")


# ── 1. Sécurité : le repo public ne doit contenir NI IP serveur NI email perso ───────────
# (On ne hardcode PAS le secret ici — on détecte la CLASSE de fuite par motif générique.)

_HUMAN_DOCS = [
    "AGENTS.md",
    "CLAUDE.md",
    "docs/deploy-ionos.md",
    "docs/etat-systeme-cable.md",
    "docs/HANDOFF-2026-07-07-nuit.md",
]
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
_ALLOWED_IPS = {"127.0.0.1", "0.0.0.0", "255.255.255.255"}
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@(?:gmail|outlook|yahoo|hotmail|proton(?:mail)?)\.[A-Za-z]{2,}")


@pytest.mark.parametrize("rel", _HUMAN_DOCS)
def test_no_public_ip_in_human_docs(rel):
    """Aucune IP de serveur en clair dans les docs humaines (127.0.0.1/0.0.0.0 tolérés)."""
    if not (REPO / rel).is_file():
        pytest.skip(f"{rel} absent")
    for m in _IPV4.finditer(_read(rel)):
        ip = m.group(0)
        octets = [int(x) for x in m.groups()]
        if ip in _ALLOWED_IPS:
            continue
        if all(0 <= o <= 255 for o in octets):  # ressemble à une vraie IP
            pytest.fail(
                f"IP en clair détectée dans {rel} : '{ip}'. "
                "Rien d'infra/IP ne doit vivre dans le repo public (→ aria-ops privé)."
            )


@pytest.mark.parametrize("rel", _HUMAN_DOCS)
def test_no_personal_email_in_human_docs(rel):
    """Aucun email personnel (fournisseur grand public) dans les docs publiques."""
    if not (REPO / rel).is_file():
        pytest.skip(f"{rel} absent")
    found = _EMAIL.findall(_read(rel))
    assert not found, (
        f"Email personnel détecté dans {rel} : {found}. "
        "La PII opérateur vit dans aria-ops (privé), pas ici."
    )


# ── 2. Câblage : les capacités ANNONCÉES doivent être réellement branchées ────────────────

def test_honeypot_service_exists_and_wired():
    """GoPlus (anti-scam) : service présent + drapeau include_honeypot dans le hub de scan."""
    assert (CORE / "services" / "goplus.py").is_file(), "services/goplus.py manquant"
    scan = _read_core("skills/acp_onchain_scan.py")
    assert "include_honeypot" in scan, "include_honeypot absent de scan_base_token"


def test_honeypot_active_on_vc_path():
    """L'analyse VC doit VRAIMENT activer le honeypot (sinon la capacité est inerte)."""
    vc = _read_core("skills/vc_analysis.py")
    assert "include_honeypot=True" in vc, (
        "vc_analysis n'active pas include_honeypot=True : la détection honeypot serait dormante."
    )


def test_honeypot_active_on_pool_screening():
    """Le filtre d'entrée du pool (token_absorber) doit AUSSI activer le honeypot."""
    ta = _read_core("token_absorber.py")
    assert "include_honeypot=True" in ta, (
        "token_absorber n'active pas include_honeypot : un honeypot pourrait entrer dans le pool."
    )


def test_paper_trader_registered_in_heartbeat():
    """Le paper-trading 1M$ doit être une tâche heartbeat ET avoir un dispatch (pas orphelin)."""
    assert (CORE / "paper_trader.py").is_file(), "paper_trader.py manquant"
    hb = _read_core("heartbeat.py")
    assert 'id="paper_trade_cycle"' in hb, "tâche paper_trade_cycle absente de HEARTBEAT_TASKS"
    assert 'task_id == "paper_trade_cycle"' in hb, "dispatch de paper_trade_cycle absent de _run_task"


def test_acp_conversational_routing_gated_off():
    """L'ACP (abandonné) ne doit PAS détourner la conversation libre par défaut."""
    brain = _read_core("brain.py")
    assert "_acp_intent_enabled" in brain, (
        "le garde d'intention ACP a disparu : la conversation libre risque de repartir vers l'ACP."
    )


def test_candidate_ranking_available():
    from aria_core.skills.candidate_ranking import rank_candidates, top_candidates  # noqa: F401


def test_paper_trader_importable():
    from aria_core.paper_trader import run_paper_cycle, portfolio_summary  # noqa: F401


# ── 3. Intégrité documentaire : ce que CLAUDE.md dit de lire doit exister ─────────────────

def test_referenced_docs_exist():
    """CLAUDE.md renvoie vers des docs de référence : elles doivent exister (pas de lien mort)."""
    for rel in (
        "docs/etat-systeme-cable.md",
        "docs/architecture-extensibilite.md",
        "docs/protocole-argent-reel.md",
    ):
        assert (REPO / rel).is_file(), f"Doc référencé dans CLAUDE.md manquant : {rel}"


def test_claude_md_declares_established_facts_block():
    """Le bloc 'faits établis' (anti-questions répétées) doit rester présent dans CLAUDE.md."""
    claude = _read("CLAUDE.md")
    assert "NE PAS re-demander" in claude, "le bloc 'Faits établis' a disparu de CLAUDE.md"
    assert "etat-systeme-cable.md" in claude, "CLAUDE.md ne pointe plus vers la fiche d'état câblé"


def test_claude_md_documents_automation():
    """Une session neuve doit être CONSCIENTE des automatismes (hook + garde-fou + CI)."""
    claude = _read("CLAUDE.md")
    assert "Automatismes en place" in claude, "la section 'Automatismes en place' a disparu de CLAUDE.md"
    assert "session-start.sh" in claude, "CLAUDE.md ne documente pas le hook de démarrage"
    assert "test_coherence" in claude, "CLAUDE.md ne documente pas le garde-fou de cohérence"


def test_session_checkpoint_hook_wired():
    """Checkpoint auto (tous les 20 messages) : hook présent, enregistré, documenté."""
    assert (REPO / ".claude" / "hooks" / "session-checkpoint.sh").is_file(), (
        "hook session-checkpoint.sh manquant (sauvegarde auto de session)"
    )
    settings = _read(".claude/settings.json")
    assert "UserPromptSubmit" in settings, "hook checkpoint non enregistré (UserPromptSubmit absent de settings.json)"
    assert "session-checkpoint.sh" in settings, "settings.json ne pointe pas vers session-checkpoint.sh"
    claude = _read("CLAUDE.md")
    assert "session-checkpoint" in claude, "CLAUDE.md ne documente pas le checkpoint auto de session"


# ── 4. Sécurité — invariants d'auth (failles fermées, ne pas rouvrir) ─────────────────────

def test_operator_secret_header_only_not_query_string():
    """Le secret opérateur s'authentifie par header SEUL — jamais en query-string (fuite logs)."""
    pm = _read_core("public_mode.py")
    assert "is_operator_request" in pm, "helper is_operator_request absent de public_mode"
    assert "compare_digest" in pm, "comparaison du secret opérateur non à temps constant"
    assert 'query_params.get("secret")' not in pm, (
        "public_mode accepte encore le secret en query-string : fuite dans logs/historique/Referer."
    )


def test_telegram_webhook_fail_closed():
    """Le webhook Telegram doit être fail-CLOSED (secret absent => refus) + compare à temps constant."""
    route = _read("vanguard/backend/app/api/routes/telegram_route.py")
    assert "compare_digest" in route, "comparaison du secret webhook non à temps constant"
    assert "Webhook secret not configured" in route, (
        "le webhook Telegram n'est plus fail-closed : sans secret, il accepterait des updates forgés."
    )


def test_community_feedback_handle_impersonation_guarded():
    """Un handle opérateur revendiqué sans le secret admin ne doit pas donner de privilège."""
    route = _read("vanguard/backend/app/api/routes/aria.py")
    assert "is_operator_request" in route, "la route ne vérifie pas l'authentification opérateur réelle"
    assert "is_trusted_feedback_handle" in route, (
        "l'anti-usurpation du champ handle a disparu de la route community-feedback."
    )


def test_uvicorn_proxy_headers_enabled():
    """Le conteneur doit lancer uvicorn avec --proxy-headers (IP réelle => plafond anti-abus)."""
    dockerfile = _read("vanguard/Dockerfile")
    assert "--proxy-headers" in dockerfile, (
        "uvicorn sans --proxy-headers : l'IP client reste le loopback, le plafond par IP est inerte."
    )
