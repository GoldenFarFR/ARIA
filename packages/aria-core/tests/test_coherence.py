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

import ast
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
] + sorted(str(p.relative_to(REPO)) for p in (REPO / "docs").glob("HANDOFF*.md"))
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


def _is_private_or_doc_range(ip: str) -> bool:
    """RFC 1918 (privé) + RFC 5737 (réservé documentation) — légitimes dans du code/tests,
    jamais une vraie IP de serveur qui aurait fuité."""
    octets = [int(x) for x in ip.split(".")]
    if octets[0] == 10:
        return True
    if octets[0] == 172 and 16 <= octets[1] <= 31:
        return True
    if octets[0] == 192 and octets[1] == 168:
        return True
    return any(ip.startswith(prefix) for prefix in ("192.0.2.", "198.51.100.", "203.0.113."))


def test_no_public_ip_in_source_or_tests():
    """Même garde-fou que `test_no_public_ip_in_human_docs`, étendu au CODE (src + tests) --
    trouvé le 09/07 : une vraie IP de VPS s'était glissée dans une fixture de test, jamais
    repérée par `detect-secrets` (une IP n'est pas un « secret » classique) ni par le check
    ci-dessus (scopé aux seuls docs humains). Zéro faux positif au moment de l'écriture."""
    roots = [CORE, REPO / "packages" / "aria-core" / "tests"]
    offenders: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in _IPV4.finditer(text):
                ip = m.group(0)
                octets = [int(x) for x in m.groups()]
                if not all(0 <= o <= 255 for o in octets):
                    continue
                if ip in _ALLOWED_IPS or _is_private_or_doc_range(ip):
                    continue
                offenders.append(f"{path.relative_to(REPO)}: {ip}")
    assert not offenders, (
        "IP publique en clair détectée dans le code/tests : " + ", ".join(offenders) + ". "
        "Utiliser une plage RFC 5737 (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) "
        "pour tout exemple/fixture."
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


def test_paper_weekly_cycle_registered_in_heartbeat():
    """18/07 -- boucle d'entraînement hebdomadaire (remplace le protocole 30j/7j/14j) :
    doit être une tâche heartbeat, avoir un dispatch, ET utiliser paper_trader.run_weekly_reset
    (pas une réimplémentation parallèle qui divergerait de reset_portfolio)."""
    hb = _read_core("heartbeat.py")
    assert 'id="paper_weekly_review_cycle"' in hb, (
        "tâche paper_weekly_review_cycle absente de HEARTBEAT_TASKS"
    )
    assert 'task_id == "paper_weekly_review_cycle"' in hb, (
        "dispatch de paper_weekly_review_cycle absent de _run_task"
    )
    assert "run_weekly_reset" in hb, "run_weekly_reset jamais appelé depuis le heartbeat"

    pt_src = _read_core("paper_trader.py")
    assert "async def run_weekly_reset(" in pt_src, "run_weekly_reset manquant dans paper_trader.py"
    assert "async def weekly_cycle_due(" in pt_src, "weekly_cycle_due manquant dans paper_trader.py"
    # Le reset hebdo ne doit JAMAIS DROP la table (contrairement à reset_portfolio,
    # destructif par design) -- il archive puis vide, cf. docstring de run_weekly_reset.
    assert "paper_position_archive" in pt_src, (
        "run_weekly_reset doit archiver l'historique avant de vider la table live"
    )


def test_sepolia_autonomous_registered_in_heartbeat_and_never_uses_wallet_guard():
    """Rehearsal Sepolia autonome : câblé au heartbeat, ET structurellement séparé de
    wallet_guard.escalate_spend/resolve_spend (le garde-fou Telegram partagé — utilisé par
    tout ce qui touchera un jour du capital réel — ne doit jamais être importé ici). C'est
    l'exception bornée documentée dans les Règles absolues (mainnet reste toujours gaté)."""
    assert (CORE / "onchain" / "sepolia_autonomous.py").is_file(), "sepolia_autonomous.py manquant"
    hb = _read_core("heartbeat.py")
    assert 'id="sepolia_autonomous_cycle"' in hb, "tâche sepolia_autonomous_cycle absente de HEARTBEAT_TASKS"
    assert 'task_id == "sepolia_autonomous_cycle"' in hb, "dispatch de sepolia_autonomous_cycle absent de _run_task"

    module = (CORE / "onchain" / "sepolia_autonomous.py").read_text(encoding="utf-8")
    # Recherche l'APPEL (parenthèse ouvrante) plutôt que la sous-chaîne : le docstring du
    # module explique volontairement pourquoi il ne les appelle jamais, donc les mentionne.
    assert "escalate_spend(" not in module and "resolve_spend(" not in module, (
        "sepolia_autonomous.py ne doit JAMAIS appeler wallet_guard.escalate_spend/resolve_spend "
        "— l'autonomie doit rester structurellement bornée au testnet, jamais un chemin "
        "partagé avec ce qui touchera un jour du capital réel."
    )


def test_acp_trade_never_falls_back_to_the_tools_default_slippage():
    """Règle absolue 09/07 ("grave le dans la roche", incident fondateur ETH->USDC
    à 30% de slippage par défaut, docs/HANDOFF_SECURITE.md) : le slippage est
    toujours explicite, jamais la valeur par défaut d'un outil de trade.

    Trou réel trouvé le 07/08 (audit proactif #259) : `_exec_trade_tokens` lisait
    `payload.get("slippage", "")` alors qu'aucun appelant ne fournit cette clé,
    donc `acp_cli.trade_tokens` sautait `--slippage` (`if slippage.strip():`) et
    acp-cli appliquait SON défaut. Verrouillé ici pour qu'une session future ne
    puisse pas réintroduire le repli silencieux -- si ce chemin est un jour
    rouvert (unité `--slippage` d'acp-cli enfin vérifiée), le slippage doit être
    passé explicitement, jamais via un `.get(..., "")` qui redégrade au défaut."""
    path = CORE / "wallet_guard.py"
    assert path.is_file(), "wallet_guard.py manquant"
    # AST plutôt que recherche textuelle : la docstring du correctif CITE le motif
    # fautif pour l'expliquer, un `in module` la prendrait pour le bug lui-même.
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "slippage"
    ]
    assert not offenders, (
        f"wallet_guard.py lit le slippage via `.get(\"slippage\", ...)` (ligne(s) {offenders}) : "
        "un payload sans cette clé retomberait silencieusement sur la valeur par défaut "
        "d'acp-cli (règle absolue 09/07). Passer une valeur explicite validée."
    )


def test_agent_wallet_pilot_never_uses_wallet_guard_and_gated_off():
    """Pilote agent-wallet réel (Coinbase Agentic Wallet, exception nommée 16/07,
    docs/pilote-agent-wallet-10usd.md) : structurellement séparé de
    wallet_guard.escalate_spend/resolve_spend (même doctrine que sepolia_autonomous),
    gate dédié OFF par défaut, aucune fonction de transfert générique."""
    path = CORE / "agent_wallet_pilot.py"
    assert path.is_file(), "agent_wallet_pilot.py manquant"
    module = path.read_text(encoding="utf-8")
    assert "escalate_spend(" not in module and "resolve_spend(" not in module, (
        "agent_wallet_pilot.py ne doit JAMAIS appeler wallet_guard.escalate_spend/resolve_spend "
        "— exception bornée au pilote 10-15$, structurellement séparée du garde-fou partagé."
    )
    assert "import wallet_guard" not in module and "from aria_core.wallet_guard" not in module, (
        "agent_wallet_pilot.py ne doit jamais importer wallet_guard.py."
    )
    assert "def transfer(" not in module and "def withdraw(" not in module, (
        "aucune fonction de transfert/retrait générique -- seulement swap (doc §3.2)."
    )


def test_agent_wallet_pilot_cycle_registered_in_heartbeat_and_isolated():
    """Boucle de décision autonome du pilote agent-wallet (18/07, "option 2" --
    ARIA décide ET exécute SEULE) : câblée au heartbeat, structurellement séparée
    de wallet_guard (même doctrine que le reste du pilote), et dimensionne
    TOUJOURS via agent_wallet_sizing (règle 3%/#203) -- jamais le solde entier."""
    path = CORE / "agent_wallet_pilot_cycle.py"
    assert path.is_file(), "agent_wallet_pilot_cycle.py manquant"
    module = path.read_text(encoding="utf-8")
    assert "escalate_spend(" not in module and "resolve_spend(" not in module, (
        "agent_wallet_pilot_cycle.py ne doit JAMAIS appeler wallet_guard.escalate_spend/resolve_spend."
    )
    assert "import wallet_guard" not in module and "from aria_core.wallet_guard" not in module, (
        "agent_wallet_pilot_cycle.py ne doit jamais importer wallet_guard.py."
    )
    assert "agent_wallet_sizing" in module, (
        "le dimensionnement doit passer par agent_wallet_sizing.size_trade_usd (règle 3%, #203) "
        "-- jamais un montant inventé ou le solde entier."
    )

    hb = _read_core("heartbeat.py")
    assert 'id="agent_wallet_pilot_cycle"' in hb, "tâche agent_wallet_pilot_cycle absente de HEARTBEAT_TASKS"
    assert 'task_id == "agent_wallet_pilot_cycle"' in hb, "dispatch de agent_wallet_pilot_cycle absent de _run_task"


def test_no_cdp_account_export_call_anywhere():
    """29/07, Item #190 -- post-incident (operator stress-test on aria-wallet-
    X402-EVM: private key exported via the CDP dashboard, then used directly
    in MetaMask -- confirmed a real EOA private key, once exported, bypasses
    every CDP-side control forever, no IP/API restriction can claw it back).
    Preventive guard, not a detection: the real CDP SDK exposes
    ``EvmClient.export_account`` (verified live in the installed SDK,
    cdp/evm_client.py) -- no code in this project should EVER call it. If a
    real future need arises, that's a deliberate, reviewed decision, never a
    silent call slipped into a random module."""
    forbidden = re.compile(r"\.export_account\s*\(")
    hits: list[str] = []
    for path in sorted(CORE.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if forbidden.search(text):
            hits.append(str(path.relative_to(REPO)))
    assert not hits, (
        "Aucun fichier ne doit jamais appeler .export_account() sur un compte CDP "
        f"(exportation de clé privée -- garde préventif, Item #190) : {hits}"
    )


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
    assert "DO NOT re-ask" in claude, "le bloc 'Established facts' a disparu de CLAUDE.md"
    assert "etat-systeme-cable.md" in claude, "CLAUDE.md ne pointe plus vers la fiche d'état câblé"


def test_claude_md_documents_automation():
    """Une session neuve doit être CONSCIENTE des automatismes (hook + garde-fou + CI)."""
    claude = _read("CLAUDE.md")
    assert "Automations in place" in claude, "la section 'Automations in place' a disparu de CLAUDE.md"
    assert "session-start.sh" in claude, "CLAUDE.md ne documente pas le hook de démarrage"
    assert "test_coherence" in claude, "CLAUDE.md ne documente pas le garde-fou de cohérence"


def test_claude_md_declares_permanent_norms():
    """Le bloc 'Permanent norms' (qualité/fluidité/UX…) doit rester présent — appliqué à chaque build."""
    claude = _read("CLAUDE.md")
    assert "Permanent norms" in claude, "la section 'Permanent norms' a disparu de CLAUDE.md"
    for norm in ("Quality", "Fluidity", "Visuals / UX", "Robustness", "Accessibility", "User data protection"):
        assert norm in claude, f"la norme permanente '{norm}' a disparu de CLAUDE.md"


# Ceiling set right after the 03/08 compaction pass (690 lines/~180KB -> 329
# lines/~75KB). 100KB leaves real headroom for organic growth before the next
# pass is needed -- see "Routeur CLAUDE.md" section for where new content goes
# instead of being appended here.
MAX_CLAUDE_MD_SIZE_BYTES = 100 * 1024


def test_claude_md_stays_under_size_budget():
    """Garde-fou anti-dérive (03/08) -- CLAUDE.md a déjà atteint ~600 Ko avant une compaction
    complète devenue nécessaire (22/07) ; ce test avertit tôt plutôt que d'attendre le prochain
    rattrapage géant."""
    content = _read("CLAUDE.md")
    size = len(content.encode("utf-8"))
    assert size <= MAX_CLAUDE_MD_SIZE_BYTES, (
        f"CLAUDE.md fait {size / 1024:.0f} Ko (> {MAX_CLAUDE_MD_SIZE_BYTES / 1024:.0f} Ko) -- "
        "voir la table 'Routeur CLAUDE.md' avant d'ajouter du contenu ici : la plupart du "
        "nouveau contenu devrait aller dans un docs/HANDOFF_<composant>.md, pas s'empiler ici."
    )


def test_attack_simulation_present():
    """Simulation d'attaque quotidienne + fix de validation (corps non-UTF8) verrouillés."""
    assert (REPO / "vanguard" / "backend" / "security_sim" / "harness.py").is_file(), (
        "harnais de simulation d'attaque manquant"
    )
    assert (REPO / ".github" / "workflows" / "security-sim.yml").is_file(), (
        "workflow quotidien de simulation d'attaque manquant"
    )
    main = _read("vanguard/backend/app/main.py")
    assert "RequestValidationError" in main, (
        "le handler de validation (fix corps non-UTF8 -> 500) a disparu de main.py."
    )


def test_onchain_attestation_present_and_valueless():
    """Preuve onchain : primitives Merkle + contrat AriaLedger présents ; le contrat ne
    transfère JAMAIS de valeur (garde-fou : l'ancrage n'est pas une exécution financière)."""
    assert (CORE / "onchain" / "attestation.py").is_file(), "module onchain/attestation.py manquant"
    from aria_core.onchain.attestation import merkle_root, verify_proof  # noqa: F401

    sol = REPO / "contracts" / "AriaLedger.sol"
    assert sol.is_file(), "contrat AriaLedger.sol manquant"
    src = sol.read_text(encoding="utf-8")
    assert "payable" not in src, "AriaLedger ne doit jamais être payable (aucun transfert de valeur)"
    assert "call{value" not in src and ".transfer(" not in src, (
        "AriaLedger ne doit jamais déplacer de fonds — c'est un ancrage de hash, pas un wallet."
    )


def test_session_checkpoint_hook_wired():
    """Checkpoint auto (cadence configurable, cf. INTERVAL du hook) : hook présent, enregistré, documenté."""
    assert (REPO / ".claude" / "hooks" / "session-checkpoint.sh").is_file(), (
        "hook session-checkpoint.sh manquant (sauvegarde auto de session)"
    )
    settings = _read(".claude/settings.json")
    assert "UserPromptSubmit" in settings, "hook checkpoint non enregistré (UserPromptSubmit absent de settings.json)"
    assert "session-checkpoint.sh" in settings, "settings.json ne pointe pas vers session-checkpoint.sh"
    claude = _read("CLAUDE.md")
    assert "session-checkpoint" in claude, "CLAUDE.md ne documente pas le checkpoint auto de session"


def test_vps_deploy_reminder_wired():
    """Rappel de déploiement VPS : marqueur suivi + logique seuil dans le hook + doc."""
    ref = REPO / ".claude" / "last-deployed-ref"
    assert ref.is_file(), "marqueur .claude/last-deployed-ref manquant (baseline du delta non déployé)"
    content = ref.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{7,40}", content), (
        "last-deployed-ref doit contenir un SHA de commit (baseline du dernier déploiement)"
    )
    hook = (REPO / ".claude" / "hooks" / "session-checkpoint.sh").read_text(encoding="utf-8")
    assert "DEPLOY_THRESHOLD" in hook, "le hook ne mesure plus le delta non déployé (seuil absent)"
    assert "last-deployed-ref" in hook, "le hook ne lit plus le marqueur de dernier déploiement"
    claude = _read("CLAUDE.md")
    assert "last-deployed-ref" in claude, "CLAUDE.md ne documente pas le rappel de déploiement VPS"


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


def test_operator_2fa_totp_wired():
    """2FA opérateur : module TOTP présent, is_operator_request TOTP-aware + anti-force-brute,
    et le middleware passe par cette source unique."""
    assert (CORE / "admin_totp.py").is_file(), "module admin_totp.py manquant (TOTP opérateur)"
    pm = _read_core("public_mode.py")
    assert "ADMIN_TOTP_SECRET" in pm, "is_operator_request n'intègre plus le second facteur TOTP"
    assert "verify_totp" in pm, "public_mode ne vérifie plus le code TOTP"
    assert "_TOTP_MAX_FAILS" in pm, "le verrou anti-force-brute du TOTP a disparu"
    mw = _read("vanguard/backend/app/auth/middleware.py")
    assert "is_operator_request" in mw, (
        "le middleware ne route plus le bypass opérateur via is_operator_request (2FA contournable)."
    )


def test_operator_mobile_kill_switch_requires_fresh_totp_and_anti_replay():
    """Item #201 Phase 3 -- /aria/ops/stop and /aria/ops/resume are the only REST
    surface able to arm or lift the kill-switch. Two invariants are locked here:
    (1) a fresh TOTP code is demanded on EVERY call, so a stolen sliding session is
    never enough on its own; (2) a code is single-use, enforced by a UNIQUE
    constraint in a dedicated table -- a captured code can never lift a STOP the
    operator legitimately armed. Also pins the reuse of the EXISTING
    kill_incident_log.TRIGGER_MANUAL, never a new trigger constant.
    """
    route = _read("vanguard/backend/app/api/routes/operator_mobile.py")
    assert '@router.post("/stop")' in route, "the mobile kill-switch arm route disappeared"
    assert '@router.post("/resume")' in route, "the mobile kill-switch lift route disappeared"
    assert route.count("await _require_fresh_totp(") == 2, (
        "both /stop and /resume must demand a fresh TOTP -- one of them lost its "
        "second factor, or a third state-changing route was added without it."
    )
    assert "totp_replay.claim_code" in route, (
        "the TOTP anti-replay claim is gone: a captured code becomes reusable for "
        "its whole validity window."
    )
    assert "kill_incident_log.TRIGGER_MANUAL" in route, (
        "the mobile kill-switch no longer logs its incidents under the existing "
        "manual trigger (audit hole, or a duplicated trigger constant)."
    )

    replay = _read("vanguard/backend/app/auth/operator_totp_replay.py")
    assert "UNIQUE(account_id, totp_code)" in replay, (
        "anti-replay downgraded to something other than a DB-level unique "
        "constraint: two simultaneous calls could both pass a check-then-mark race."
    )


def test_operator_mobile_chat_never_confabulates_a_kill_switch_action():
    """Real incident (30/07): the operator typed "/stop" as a plain chat message
    and ARIA answered "Stop confirmed" -- a pure LLM confabulation, proven live by
    a routine trading alert that kept arriving right after. The free-text chat
    must intercept a control command BEFORE calling the brain, with a fixed
    reply, never a generated one that could falsely claim an action happened."""
    route = _read("vanguard/backend/app/api/routes/operator_mobile.py")
    assert "_control_command_reply(" in route, "the confabulation guard on /stop-style chat messages is gone"
    assert route.count('"/stop"') >= 1 and route.count('"/resume"') >= 1, (
        "the guarded command set lost /stop or /resume -- the exact commands "
        "that caused the real incident this test locks in"
    )
    # The guard must run BEFORE aria_brain.process is ever called for that message.
    guard_pos = route.find("_control_command_reply(body.message")
    brain_pos = route.find("await aria_brain.process(")
    assert 0 <= guard_pos < brain_pos, (
        "the guard no longer runs before the brain call -- a control command "
        "could reach AriaBrain.process() and be confabulated again"
    )


def test_site_login_google_wired():
    """Site : Google dans les méthodes de connexion Privy câblé.

    Le bouton 2FA dédié dans la nav (enrôlement MFA Privy) a été retiré volontairement
    (08/07) — prêtait à confusion ("on dirait qu'il faut l'activer"). Le suivi 2FA/TOTP
    site reste ouvert côté tâche #32 ; l'enrôlement MFA Privy reste possible depuis le
    dashboard membre le cas échéant, juste plus via un bouton dédié dans la nav.
    """
    cfg = _read("vanguard/src/lib/privy-config.ts")
    assert "'google'" in cfg, "Google absent des méthodes de connexion Privy (privy-config.ts)"


def test_showcase_pr_autoreply_transparent_and_gated_to_human():
    """Auto-reply outward (PR showcase Virtuals) : signature de transparence, zéro em-dash,
    et tout ce qui n'est pas un feu vert net (question / technique / négatif) => passage de
    relai à l'humain. ARIA n'invente ni ne tranche rien en public (norme outward + zéro trace IA)."""
    from aria_core.skills import showcase_pr_watcher as spw

    # Feu vert net : ARIA répond seule, avec signature de transparence, sans em-dash.
    _, body = spw.decide_reply("LGTM, ready to merge.", target={"pr_number": 37})
    signed = spw._sign(body)
    assert "autonomous AI owned by GoldenFarFR" in signed, "signature de transparence absente"
    assert "—" not in signed, "em-dash dans une réponse publique (trace IA interdite)"
    for tpl in (spw._THANKS_REOPEN_TEMPLATE, spw._HANDOVER_TEMPLATE, spw._OPERATOR_DRAFT_TEMPLATE):
        assert "—" not in tpl, "em-dash dans un template outward"

    # Cas réel PR#37 : le mainteneur donne un correctif technique -> ARIA passe la main.
    action, _ = spw.decide_reply(
        "The 500 is an unregistered signer, re-run acp agent add-signer.",
        target={"pr_number": 37},
    )
    assert action == "handover", "un sujet technique doit passer le relai à l'humain, pas répondre"

    # Négation de merge -> jamais la réponse "on rouvre".
    assert spw.decide_reply("not ready to merge yet", target={"pr_number": 37})[0] == "handover"


def test_github_command_registered_and_repair_routed():
    """La commande /github doit être enregistrée (sinon /github repair reste muet), et la
    correction showcase doit avoir une route texte-libre côté handler admin."""
    src = _read("packages/aria-core/src/aria_core/gateway/telegram_bot.py")
    assert 'CommandHandler("github"' in src, (
        "/github non enregistré : la commande (dont /github repair) reste muette."
    )
    assert "wants_showcase_pr_repair" in src, (
        "route texte-libre de la correction showcase absente du handler admin."
    )


def test_x402_seam_gated_off_and_no_autonomous_spend():
    """Seam x402 (paiement agentique Base) : gaté OFF par défaut, fail-closed, et le côté
    'ARIA paie' n'est qu'une proposition validée humainement (dôme : aucune dépense auto)."""
    import os

    from aria_core.services import x402

    os.environ.pop("ARIA_X402_ENABLED", None)
    assert x402.x402_enabled() is False, "x402 doit être OFF par défaut"
    assert x402.build_payment_requirement("premium", "1") is None, "x402 OFF doit fail-closed"
    prop = x402.propose_payment(amount="1", to="0x", resource="r")
    assert prop.requires_human is True and prop.status == "proposed", (
        "le côté ARIA-paie doit rester une proposition validée par l'humain, jamais exécutée."
    )


def test_onchain_anchor_gated_and_keyless():
    """Ancrage onchain : gaté OFF par défaut, et le serveur ne signe/n'émet jamais (clé hors
    serveur). Le runbook de déploiement local existe (geste opérateur)."""
    import inspect
    import os

    from aria_core.onchain import anchor

    os.environ.pop("ARIA_ONCHAIN_ANCHOR_ENABLED", None)
    assert anchor.anchor_enabled() is False, "l'ancrage doit être OFF par défaut"
    assert anchor.build_anchor_request([{"a": 1}]) is None, "OFF => fail-closed"
    src = inspect.getsource(anchor)
    for forbidden in ("private_key", "send_raw_transaction", "eth_account"):
        assert forbidden not in src, "le serveur d'ancrage ne doit jamais signer/détenir de clé"
    assert (REPO / "contracts" / "DEPLOY.md").is_file(), "runbook de déploiement AriaLedger manquant"


def test_pulse_endpoint_public_and_present():
    """Le pouls /api/pulse est public (allowlist) et défini côté backend, pour le suivi live."""
    mw = _read("vanguard/backend/app/auth/middleware.py")
    assert '"/api/pulse"' in mw, "/api/pulse absent de l'allowlist publique du middleware"
    main = _read("vanguard/backend/app/main.py")
    assert '@app.get("/api/pulse")' in main, "endpoint /api/pulse manquant dans main.py"


def test_token_dossier_operator_gated_and_read_only():
    """Dossier par token : gaté OPÉRATEUR (expose le pipeline de candidats, jamais public/membre)
    et strictement en LECTURE (agrégateur pur, aucun write, aucun client réseau propre).

    27/07 -- la page cockpit web (et sa route FastAPI /aria/dossier/{contract}, jamais
    appelée que par elle) a été entièrement supprimée sur demande opérateur explicite ;
    le seul point d'accès restant au dossier est la commande Telegram (aria_core.dossier
    importé depuis gateway/telegram_bot.py), déjà gatée admin par _handle_message
    (redirige tout non-admin vers _handle_public_message avant d'atteindre ce chemin)."""
    import inspect

    from aria_core import dossier

    bot_src = _read("packages/aria-core/src/aria_core/gateway/telegram_bot.py")
    assert "from aria_core.dossier import build_dossier" in bot_src, "le dossier Telegram doit rester câblé"
    handler_src = bot_src.split("async def _handle_message(", 1)[1][:1200]
    assert "is_admin(user.id)" in handler_src and "_handle_public_message" in handler_src, (
        "le dossier doit rester derrière le gate admin de _handle_message"
    )
    # Never re-exposed via an HTTP route (the cockpit's own route was removed, never re-add it).
    aria_route = _read("vanguard/backend/app/api/routes/aria.py")
    assert '"/dossier/{contract}"' not in aria_route, "le dossier ne doit plus être exposé en route HTTP"
    # Agrégateur en lecture seule : aucune écriture SQL, aucun accès DB/réseau direct.
    src = inspect.getsource(dossier)
    for forbidden in ("INSERT", "UPDATE ", "DELETE", "aiosqlite", "httpx"):
        assert forbidden not in src, f"le dossier doit rester une lecture pure (trouvé: {forbidden})"


# ── Registre des actions externes (10/07) ────────────────────────────────────────────────
#
# Incident : un sous-système entier (aria_worker_queue.py + capability_gap.py), câblé dans
# brain.py/heartbeat.py, pouvait ouvrir des issues/PR GitHub et déléguer du code à un outil
# tiers ("Cursor") sans aucune validation opérateur -- déclenchable par des mots du quotidien
# en Telegram ("go", "vas-y", "nettoie le répertoire") ou même un formulaire public du site.
# Il a réellement écrit sur ce repo (issue #1 + PR #2, 03/07) avant d'être retiré (10/07).
# Ce test est le garde-fou MÉCANIQUE censé empêcher la récidive : toute fonction capable
# d'écrire réellement à l'extérieur (GitHub, X, email) est listée ci-dessous. Si un NOUVEAU
# fichier de production appelle une de ces fonctions sans être ajouté à la liste, ce test
# échoue -- il ne dépend d'aucune mémoire humaine ni d'aucun audit périodique.
#
# Pour ajouter un appelant légitime : l'ajouter ci-dessous avec une raison, dans le MÊME
# commit qui introduit l'appel (même règle que le reste de ce fichier).

_EXTERNAL_WRITE_PATTERNS = [
    # GitHub (github_client.GitHubClient) -- écriture réelle sur un repo.
    r"\.create_issue\(", r"\.put_file\(", r"\.put_files_batch\(",
    r"\.create_pull_request\(", r"\.create_branch\(", r"\.delete_repo\(",
    r"\.create_repo\(", r"\.create_issue_comment\(", r"\.edit_issue_comment\(",
    # X/Twitter (gateway.x_twitter) -- post/édition réelle du profil public.
    r"\bapply_profile_banner\(", r"\bapply_profile_image\(",
    r"\bapply_x_profile_fields\(", r"\bpost_tweet\(", r"\breply_to_tweet\(",
    # Email (services.mailer) -- envoi réel.
    r"\bsend_email\(",
    # TikTok (gateway.tiktok, #34) -- publication vidéo réelle. Aujourd'hui aucun appelant
    # (tiktok_release_publisher reste inerte, pas de pipeline vidéo) -- posé en avance pour
    # que le jour où ce seam s'active, le garde-fou déclenche immédiatement.
    r"\.publish_video\(",
]
_EXTERNAL_WRITE_RE = re.compile("|".join(_EXTERNAL_WRITE_PATTERNS))

# Fichiers de définition (posséder la fonction n'est pas "l'appeler") + tests, exclus du scan.
_EXTERNAL_WRITE_DEFINITION_FILES = {
    "github_client.py",
    "gateway/x_twitter.py",
    "gateway/tiktok.py",
}

# Chaque fichier listé ici a un appelant légitime et connu -- vérifié le 10/07.
_EXTERNAL_WRITE_ALLOWLIST = {
    # GitHub
    "skills/claude_mentor.py",
    "skills/showcase_pr_watcher.py",
    "skills/holding_site_skill.py",
    "skills/vc_intelligence.py",
    "skills/knowledge_inbox.py",
    "skills/github_skill.py",
    "skills/telegram_conversation_miner.py",
    "skills/pump_dump_autopsy.py",
    "skills/aria_brain.py",
    "skills/code_proposal.py",
    "skills/ux_watch.py",
    "truth_ledger/sync.py",
    # X/Twitter
    "skills/acp_workflow_social.py",
    "skills/comms_skill.py",
    "skills/acp_product_launch_skill.py",
    "tweet_compose_workflow.py",
    "gateway/telegram_bot.py",
    "gateway/x_engagement.py",
    "autonomy_revenue.py",
    "actions.py",
    "avatar.py",
    "community_feedback.py",
    "self_maintenance.py",
    "x_profile.py",
    "visual_autonomy.py",
}


def test_external_write_actions_registered_in_allowlist():
    """Garde-fou mécanique anti-récidive (cf. incident Cursor/worker-queue, 10/07) : tout
    fichier de production appelant une fonction d'écriture externe (GitHub/X/email) doit
    être déclaré dans _EXTERNAL_WRITE_ALLOWLIST. Casse immédiatement si un nouveau chemin
    d'action autonome apparaît sans revue explicite."""
    unexpected: list[str] = []
    for path in CORE.rglob("*.py"):
        rel = str(path.relative_to(CORE))
        if rel in _EXTERNAL_WRITE_DEFINITION_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _EXTERNAL_WRITE_RE.search(text) and rel not in _EXTERNAL_WRITE_ALLOWLIST:
            unexpected.append(rel)
    assert not unexpected, (
        "Nouveau(x) fichier(s) appelant une action d'écriture externe (GitHub/X/email) sans "
        f"être déclaré(s) dans _EXTERNAL_WRITE_ALLOWLIST : {unexpected}. "
        "Ajoute-le avec une raison si légitime, ou retire l'appel si non voulu."
    )


# Item #176 (31/07) -- generic mechanical guard for every ARIA_*_ENABLED gate. Motivated by
# real, already-lived drift: this file's own history recorded ARIA_BONDING_DISCOVERY_ENABLED
# and ARIA_CABALSPY_SOURCING_ENABLED as OFF in CLAUDE.md while both were actually ON in prod,
# caught only by a manual 5-agent audit (24/07) -- never mechanically. Same pattern as
# _EXTERNAL_WRITE_ALLOWLIST above: a new gate must be REGISTERED here, so it can never exist
# unreviewed. Scope, honestly: this proves a gate is KNOWN and DECLARED -- it cannot prove the
# live prod value of any of them (that still requires a real `docker exec` + "verifier avant
# d'affirmer", CI has no access to the deployed .env).
_ENABLED_GATE_RE = re.compile(r"ARIA_[A-Z_]*_ENABLED")

_KNOWN_ENABLED_GATES = {
    "ARIA_ACP_ENABLED",
    "ARIA_ACP_PROVIDER_ENABLED",
    "ARIA_AGENT_WALLET_MONITOR_ENABLED",
    "ARIA_AGENT_WALLET_PILOT_ENABLED",
    "ARIA_AGENT_WALLET_TRANSFER_ENABLED",
    "ARIA_ALPHAVANTAGE_ENABLED",
    "ARIA_AVATAR_STYLE_ENABLED",
    "ARIA_BLOCKRUN_KALSHI_ENABLED",
    "ARIA_BONDING_DISCOVERY_ENABLED",
    "ARIA_BRAIN_ENABLED",
    "ARIA_CABALSPY_SOURCING_ENABLED",
    # 11/08 -- candle_history.py's dedicated goplus_watchlist collector cycle
    # (run_candle_history_watchlist_cycle). Standalone infrastructure, no
    # pocket wired to it yet. OFF by default.
    "ARIA_CANDLE_HISTORY_WATCHLIST_ENABLED",
    "ARIA_CANONICAL_FACTS_SYNC_ENABLED",
    "ARIA_CLAUDE_MENTOR_ENABLED",
    "ARIA_CODE_PROPOSAL_ENABLED",
    "ARIA_CONVICTION_RESEARCH_ENABLED",
    "ARIA_COUNTERFACTUAL_TRACKER_ENABLED",
    "ARIA_DAILY_TRADE_FLOOR_ENABLED",
    # 13/08 -- dip_recovery_shadow.py: forward-test of the operator-proposed
    # signal (-30%/24h + -5% stop), pure local read of candle_history, never
    # a real trigger (never real capital, never merged with the 1M$ test).
    # OFF by default.
    "ARIA_DIP_RECOVERY_SHADOW_ENABLED",
    "ARIA_DIRECTIVE_CHANNEL_ENABLED",
    "ARIA_DIRECTIVE_PROPOSAL_ENABLED",
    "ARIA_DUNE_ENABLED",
    # 15/08 -- RPC-direct legitimacy signals (owner renouncement, LP lock/burn
    # via chunked Transfer-log scan) for brand-new tokens, zero Blockscout/
    # GoPlus dependency. Pure observation, never gates a real/paper trade.
    # OFF by default.
    "ARIA_EARLY_LEGITIMACY_SHADOW_ENABLED",
    "ARIA_EXAM_ENABLED",
    # 08/09 -- signal_cascade_farcaster.py stage 2 refresh cycle. Same
    # doctrine as its GitHub sibling below. OFF by default.
    "ARIA_FARCASTER_SIGNAL_CASCADE_ENABLED",
    # 08/08 -- signal_cascade_github.py stage 2 refresh cycle. Never a
    # trigger, never blocks the momentum pipeline. OFF by default.
    "ARIA_GITHUB_SIGNAL_CASCADE_ENABLED",
    "ARIA_GOPLUS_WATCHLIST_ENABLED",
    # 18/08 -- homemade_agent_wallet.py's guardrail wrapper (Safe+
    # AllowanceModule/Squads v4, testnet/devnet only). OFF by default, no
    # production/heartbeat caller anywhere -- distinct from
    # ARIA_AGENT_WALLET_PILOT_ENABLED (the Coinbase CDP pilot).
    "ARIA_HOMEMADE_AGENT_WALLET_ENABLED",
    "ARIA_KNOWLEDGE_INBOX_ENABLED",
    "ARIA_LLM_ANTHROPIC_ROUTING_ENABLED",
    "ARIA_LLM_ENABLED",
    "ARIA_MARKETING_VIDEO_ENABLED",
    "ARIA_MARKET_ALERTS_ENABLED",
    "ARIA_MARKET_SENTIMENT_ENABLED",
    "ARIA_MEMORY_CONSOLIDATION_ENABLED",
    "ARIA_MOMENTUM_WEBSOCKET_ENABLED",
    "ARIA_MULTI_POCKET_SOURCING_ENABLED",
    "ARIA_ONCHAIN_ANCHOR_ENABLED",
    "ARIA_ONCHAIN_GRADUATION_ENABLED",
    "ARIA_OPPORTUNITY_RADAR_ENABLED",
    "ARIA_OTTO_AI_ENABLED",
    "ARIA_PAPER_RISK_CIRCUIT_BREAKERS_DISABLED",
    "ARIA_PAPER_TRADING_ENABLED",
    "ARIA_POLYMARKET_PAPER_ENABLED",
    # 08/03 -- dedicated, separate gate for services/polymarket_execution.py
    # (real-order execution adapter, diligence only). Never set in any
    # environment today -- two external preconditions (operator relocation
    # effective + lawyer confirmation) must hold first, per CLAUDE.md.
    "ARIA_POLYMARKET_REAL_TRADING_ENABLED",
    "ARIA_PUMP_DUMP_AUTOPSY_ENABLED",
    "ARIA_QUICKINTEL_ENABLED",
    "ARIA_RELAY_AUTOREPLY_ENABLED",
    # 16/08 -- robinhood_pump_shadow.py's reserved gate name for a future
    # heartbeat wiring (NOT wired, NOT read anywhere yet -- named in the
    # module's own docstring only, as documentation of the intended
    # convention). Functional twin of ARIA_SOLANA_PUMP_SHADOW_ENABLED below,
    # on Robinhood Chain instead -- same shadow-only out-of-sample validation
    # of the "+25%/15min take the train" strategy, excluding known Stock
    # Tokens (#309), pure read+log, never a trigger, never real/paper capital.
    "ARIA_ROBINHOOD_PUMP_SHADOW_ENABLED",
    "ARIA_SEPOLIA_AUTONOMOUS_ENABLED",
    "ARIA_SEPOLIA_SWAP_ENABLED",
    "ARIA_SEPOLIA_WALLET_ENABLED",
    # 09/08 -- signal_cascade_convergence.run_falsifiability_watch_cycle:
    # daily forward-price refresh + one-time WARNING log per window once
    # _MIN_SAMPLES_PER_SIDE is reached. Own dedicated flag, independent of
    # every source column's own gate. OFF by default.
    # 21/08 -- REAL CAPITAL on Solana, 0.10$ hard cap per trade
    # (solana_trade_pilot.py). Separate from the Coinbase pilot above: another
    # chain, another key, another pot of money. Ships OFF; funding the wallet
    # is a distinct operator decision from opening this gate.
    # 21/08 -- automatic sweep of surplus above 500$ to the operator's cold
    # wallet (solana_cold_sweep.py). Destination hard-coded, never a parameter.
    # REDUCES exposure rather than raising it, which is why the 17/08 caution on
    # a 500$ HOT ceiling does not apply to this threshold. Ships OFF.
    "ARIA_SOLANA_COLD_SWEEP_ENABLED",
    "ARIA_SOLANA_TRADE_PILOT_ENABLED",
    # 22/08 -- reclaims the 0.00204 SOL rent deposit locked in each token
    # account (solana_rent_recovery.py). Deliberately NOT the trade pilot's
    # gate: closing an account sends a real signed transaction, so cleanup
    # being on must never be implied by trading being on. Ships OFF.
    "ARIA_SOLANA_RENT_RECOVERY_ENABLED",
    "ARIA_SIGNAL_CASCADE_FALSIFIABILITY_ENABLED",
    "ARIA_SKILL_PROJECTS_ENABLED",
    "ARIA_SMART_MONEY_LEADERBOARD_ENABLED",
    "ARIA_SMART_SWING_ENABLED",
    # 16/08 -- solana_pump_shadow.py's reserved gate name for a future
    # heartbeat wiring (NOT wired, NOT read anywhere yet -- named in the
    # module's own docstring only, as documentation of the intended
    # convention). Explicit operator request: shadow-only out-of-sample
    # validation of the "+25%/15min take the train" strategy on Solana,
    # first time Solana is connected to anything in this project (even
    # read-only) -- pure read+log, never a trigger, never real/paper capital.
    "ARIA_SOLANA_PUMP_SHADOW_ENABLED",
    "ARIA_TAVILY_LEARNING_ENABLED",
    "ARIA_TELEGRAM_MINER_ENABLED",
    "ARIA_TELEGRAM_PUBLIC_CONVERSATION_ENABLED",
    "ARIA_TIKTOK_PUBLISH_ENABLED",
    "ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED",
    "ARIA_TRADE_DEVILS_ADVOCATE_ENABLED",
    # 13/08 -- twitterapi_io_budget.check_and_alert: proactive prepaid-credit
    # runway monitor, one-time Telegram alert once projected runway < 24h.
    # Read-only, no suspension/circuit-breaker (Tavily fallback already
    # covers outages). OFF by default.
    "ARIA_TWITTERAPI_IO_BUDGET_WATCH_ENABLED",
    "ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED",
    # 15/08 (#182/#280) -- LATTICE decision-support-quality judge
    # (thesis_quality.py) wired into analyze_vc's automatic pipeline.
    # Consultative only, own dedicated gate distinct from the adversarial
    # vc_judge.py gate. OFF by default.
    "ARIA_THESIS_QUALITY_ENABLED",
    "ARIA_UX_WATCH_ENABLED",
    "ARIA_VC_INTELLIGENCE_ENABLED",
    "ARIA_VC_POCKET_SOURCING_ENABLED",
    # 14/08 (#166/#167) -- weekly TTL purge + LanceDB compaction for the
    # vector memory (aria_cognitive_vectors). Standalone maintenance, OFF
    # by default.
    "ARIA_VECTOR_MEMORY_MAINTENANCE_ENABLED",
    "ARIA_VISION_ENABLED",
    # 10/08 -- kill switch for ALL LLM calls reachable from the public site
    # widget (see llm_economy.set_public_llm_context / is_public_llm_disabled_now).
    # OFF by default: real incident the same day (grounded budget branch
    # silently bypassing the Anthropic gate, 85% Grok/Groq failure rate
    # burning tokens) -- re-enable only after that routing bug is fixed and
    # verified live.
    "ARIA_VITRINE_LLM_ENABLED",
    "ARIA_WALLET_CANDIDATE_SOURCING_ENABLED",
    # 08/08 -- wallet_copy_shadow.py : forward-test de copie sur 8 wallets réels,
    # ledgers fictifs indépendants, jamais un trigger réel (jamais de capital
    # réel, jamais fusionné avec le test 1M$). OFF par défaut.
    "ARIA_WALLET_COPY_SHADOW_ENABLED",
    # 14/08 (#146) -- sub-gate inside the same cycle: sources dynamic wallet
    # candidates off the REAL smart_money_leaderboard (percentile-based),
    # independent of the 8 hand-picked wallets above. Seam for a future
    # scalping pocket (v11, undesigned) -- OFF by default.
    "ARIA_WALLET_COPY_SHADOW_DYNAMIC_ENABLED",
    "ARIA_WALLET_SCAN_QUEUE_ENABLED",
    "ARIA_WALLET_SCORING_ENABLED",
    "ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED",
    # 14/08 -- watchlist_refill_cycle (momentum_entry.run_watchlist_refill_cycle):
    # pure-discovery honeypot-check-only pass over discover_momentum_candidates(),
    # populates goplus_watchlist once swing/scalping_v8 stop calling discovery
    # directly. Double gate with ARIA_PAPER_TRADING_ENABLED, OFF by default.
    "ARIA_WATCHLIST_REFILL_ENABLED",
    "ARIA_WEB_FETCH_ENABLED",
    # 08/09 -- signal_cascade_web.py stage 2 refresh cycle. Same doctrine as
    # its GitHub/Farcaster siblings, deliberately hourly (shared Tavily
    # budget). OFF by default.
    "ARIA_WEB_SIGNAL_CASCADE_ENABLED",
    "ARIA_X_PROFILE_SYNC_ENABLED",
    # 08/09 -- signal_cascade_x.py stage 2 refresh cycle, 4th and last
    # column. Dedicated 15/week budget (signal_cascade_x.WEEKLY_REQUEST_
    # CAP), never shared with x_research_budget.py. OFF by default.
    "ARIA_X_SIGNAL_CASCADE_ENABLED",
}


def test_all_enabled_gates_registered_in_known_gates():
    """Garde-fou mécanique générique (Item #176) : tout ARIA_*_ENABLED référencé dans le
    code doit être déclaré dans _KNOWN_ENABLED_GATES. Casse immédiatement si un nouveau gate
    apparaît sans revue explicite -- empêche la classe de dérive déjà vécue deux fois (gate
    documenté OFF alors qu'il tourne ON en prod, jamais détecté mécaniquement jusqu'ici)."""
    found: set[str] = set()
    for path in CORE.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        found.update(_ENABLED_GATE_RE.findall(text))
    unexpected = found - _KNOWN_ENABLED_GATES
    assert not unexpected, (
        f"Nouveau(x) gate(s) ARIA_*_ENABLED jamais déclaré(s) dans _KNOWN_ENABLED_GATES : "
        f"{sorted(unexpected)}. Ajoute-le(s) avec une raison si légitime."
    )


# Décision opérateur explicite (20/07) : « seul ARIA peut écrire » dans son propre repo
# (aria-brain) -- le token dédié ne doit JAMAIS être lu ailleurs que dans le skill qui
# écrit sa mémoire libre, ni par un autre skill du projet, ni par une future session
# Claude Code qui déciderait d'y écrire à sa place. Les deux fichiers de déclaration du
# champ settings (jamais une lecture/usage réel) sont seuls exemptés.
_ARIA_BRAIN_TOKEN_RE = re.compile(r"aria_brain_github_token", re.IGNORECASE)
_ARIA_BRAIN_TOKEN_ALLOWED_FILES = {
    CORE / "skills" / "aria_brain.py",
    CORE / "testing.py",
    REPO / "vanguard" / "backend" / "app" / "config.py",
}


def test_aria_brain_token_scoped_to_its_own_skill_only():
    """Garde-fou mécanique : personne d'autre que skills/aria_brain.py ne doit jamais lire
    ou utiliser ce token -- ni un autre skill, ni Claude Code écrivant à la place d'ARIA."""
    unexpected: list[str] = []
    for root in (CORE, REPO / "vanguard" / "backend" / "app"):
        for path in root.rglob("*.py"):
            if path in _ARIA_BRAIN_TOKEN_ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if _ARIA_BRAIN_TOKEN_RE.search(text):
                unexpected.append(str(path.relative_to(REPO)))
    assert not unexpected, (
        "aria_brain_github_token référencé en dehors de skills/aria_brain.py -- "
        f"{unexpected}. Ce token est réservé à l'écriture autonome d'ARIA dans SON repo, "
        "jamais réutilisé ailleurs (décision opérateur explicite, 20/07 : « seul ARIA peut "
        "écrire »)."
    )


def test_github_mandatory_write_blocked_repos_includes_aria():
    """Garde-fou mécanique anti-récidive (incident #139, 12/07) : truth_ledger/sync.py a
    poussé des conversations Telegram en clair sur GoldenFarFR/ARIA parce que la seule
    protection contre l'écriture reposait sur une config .env correcte (GITHUB_SANDBOX_REPO,
    GITHUB_EXCLUDED_REPOS) -- rien ne signalait à la revue de code qu'une future config VPS
    pouvait reproduire l'oubli. github_skill._MANDATORY_WRITE_BLOCKED_REPOS bloque désormais
    l'écriture sur ces repos EN DUR, indépendamment de tout réglage .env. Casse immédiatement
    si "aria" en disparaît."""
    from aria_core.skills import github_skill

    assert {"aria", "aria-ops"} <= github_skill._MANDATORY_WRITE_BLOCKED_REPOS, (
        "github_skill._MANDATORY_WRITE_BLOCKED_REPOS ne protège plus 'aria'/'aria-ops' en "
        "écriture -- c'est exactement l'oubli qui a causé l'incident #139 (truth_ledger/sync.py "
        "poussant des conversations Telegram en clair sur main). Si le retrait est volontaire, "
        "confirme explicitement pourquoi ce repo n'a plus besoin de ce plancher."
    )


def test_aria_directive_channel_perimeter_locked_and_gated():
    """Canal de directives ARIA -> Claude Code (pilote, 10/07) : le périmètre autorisé
    est verrouillé à la SEULE famille déjà déléguée. L'élargir exige un changement
    délibéré de cette assertion dans le MÊME commit (jamais un glissement silencieux).
    Frontières dures : aucune catégorie financière ni d'auto-modification du canal."""
    from aria_core import aria_directives as ad

    assert ad._DIRECTIVE_CATEGORIES == frozenset({"repo_hygiene", "docs", "backlog"}), (
        "Périmètre du canal de directives modifié. Si volontaire, mets à jour cette "
        "assertion ET confirme qu'aucune catégorie ne touche du capital réel ni le canal "
        "lui-même (ARIA ne doit jamais pouvoir s'auto-élargir les pouvoirs)."
    )
    # Gate OFF par défaut : le producteur lit ARIA_DIRECTIVE_CHANNEL_ENABLED, refusé sinon.
    src = _read_core("aria_directives.py")
    assert 'os.environ.get("ARIA_DIRECTIVE_CHANNEL_ENABLED"' in src
    assert "if not channel_enabled():" in src  # propose_directive refuse porte fermée


def test_aria_directive_log_is_append_only():
    """Le journal d'audit ne doit JAMAIS être modifié ni effacé : aucune requête
    UPDATE/DELETE ne cible ``aria_directive_log`` dans le module (trace inviolable)."""
    src = _read_core("aria_directives.py")
    assert "UPDATE aria_directive_log" not in src
    assert "DELETE FROM aria_directive_log" not in src
    assert "DROP TABLE aria_directive_log" not in src


# Décision opérateur explicite (28/07) : pendant de _EXTERNAL_WRITE_ALLOWLIST mais pour
# les SEUILS DE TRADING -- chaque constante listée ici doit avoir une entrée à jour dans
# docs/trading-thresholds-calibration.md. Un changement de valeur sans mise à jour du
# doc casse la CI plutôt que de dériver silencieusement (même doctrine que le registre
# des actions externes ci-dessus). Quand tu changes VOLONTAIREMENT une de ces valeurs,
# mets à jour LES DEUX dans le même commit : ce dict ET le tableau markdown correspondant.
_TRADING_THRESHOLDS = {
    "aria_core.momentum_entry": {
        "_MIN_LIQUIDITY_USD": 25_000.0,
        "_MIN_LIQUIDITY_USD_FEAR": 100_000.0,
        "_MIN_LIQUIDITY_USD_SCALPING": 15_000.0,
        "_RR_MIN_FOR_DIRECT_BUY": 2.0,
        "_RR_AMBIGUOUS_FLOOR": 1.0,
        "_ALIGN_SCORE_MIN_FOR_DIRECT_BUY": 2,
        "MAX_VOLUME_TO_LIQUIDITY_RATIO": 20.0,
        "_MAX_PRICE_CHANGE_24H_PCT": 200.0,
        "_PARABOLIC_RESCUE_MAX_PCT": 350.0,
        "_MIN_VOLUME_24H_USD": 500.0,
        "_MIN_VOLUME_TO_LIQUIDITY_RATIO": 0.01,
        "_MAX_TOP_HOLDERS_CONCENTRATION_PCT": 80.0,
        "_RVOL_CONFIRMATION_MULTIPLIER": 3.0,
        # Item #182, 28/07 -- golden-pocket liberation: minimum retracement
        # fraction (window high->low) required before a "not there yet"
        # setup is even considered for a watch-and-wait limit order.
        "_GOLDEN_POCKET_WATCH_MIN_RETRACEMENT": 0.5,
    },
    "aria_core.risk_guard": {
        "RISK_CAP_PCT": 0.02,
        "CONVICTION_RR_THRESHOLD": 2.5,
        "MIN_ALLOC_MULTIPLIER": 0.4,
        "MODERATE_ALLOC_MULTIPLIER": 0.7,
        "MAX_ALLOC_MULTIPLIER": 1.0,
        "FUNDAMENTAL_WEAK_THRESHOLD": 4.0,
        "FUNDAMENTAL_REJECT_THRESHOLD": 2.5,
        # Item #179, 28/07 -- dex_composite_score.py's additive signal, same
        # 2-tier doctrine as FUNDAMENTAL_WEAK/REJECT_THRESHOLD above.
        "DEX_SECURITY_WEAK_THRESHOLD": 40.0,
        "DEX_SECURITY_REJECT_THRESHOLD": 15.0,
        # Item #182, 28/07 -- golden-pocket liberation: minimum confirmed DEX
        # composite score to place a watch-and-wait limit order when the
        # golden pocket/RSI gate itself isn't met yet.
        "DEX_QUALITY_WATCH_THRESHOLD": 70.0,
        "REGIME_FEAR_SIZE_MULTIPLIER": 0.5,
        "PRICE_IMPACT_RATIO": 2.0,
        "SOFT_DRAWDOWN_PCT": 0.10,
        "HARD_DRAWDOWN_PCT": 0.20,
        "HARD_CONSECUTIVE_LOSSES": 5,
        "MACRO_CIRCUIT_BREAKER_LOSS_PCT": 0.15,
    },
    "aria_core.skills.market_sentiment": {
        "_RSI_EUPHORIA": 75.0,
        "_RSI_OVERSOLD": 30.0,
        "_DRAWDOWN_CAPITULATION_PCT": -35.0,
    },
    "aria_core.bonding_entry": {
        "_MAX_DEV_HOLDING_PCT": 5.0,
        # #167, 28/07 -- 80.0 (a reject threshold) -> 100.0 (score-scale
        # ceiling): the hard gate was removed, see _TOP10_HOLDER_PCT_SCORE_
        # FLOOR below for the new best-case reference.
        "_MAX_TOP10_HOLDER_PCT": 100.0,
        "_TOP10_HOLDER_PCT_SCORE_FLOOR": 90.0,
        "_MIN_HOLDERS_FOR_CONCENTRATION_CHECK": 50,
        # #167, 28/07 -- 10,000$ -> 5,000$ (sat just above a bimodal
        # launch-config artifact, see bonding_entry.py's own comment).
        "_MIN_LIQUIDITY_USD": 5_000.0,
        "_WEIGHT_DEV_SECURITY": 35.0,
        "_WEIGHT_PRODUCT_CONVICTION": 35.0,
        "_WEIGHT_TECHNICAL_SETUP": 15.0,
        "_WEIGHT_HOLDER_CONCENTRATION": 15.0,
        "_SCORE_THRESHOLD": 60.0,
        "BONDING_SIZE_REDUCTION": 0.5,
        "_FALLBACK_TARGET_MULTIPLE": 2.0,
        "_FALLBACK_INVALIDATION_MULTIPLE": 0.35,
        # Item #156, 28/07 -- supply-proportion sizing cap (paper_trader.py
        # applies this on top of BONDING_SIZE_REDUCTION); _MAX_SUPPLY_PCT_BY_
        # TIER is a dict, not tracked here (this registry only tracks scalar
        # constants), documented in the markdown table directly instead.
        # 12/08 -- 0.01 -> 0.04 (recalibrated tiers, 2%-4% range): see
        # bonding_entry.py's own comment on this constant and
        # _MAX_SUPPLY_PCT_BY_TIER (dict, not tracked here).
        "_MAX_SUPPLY_PCT_DEFAULT": 0.04,
        # Item #165, 28/07 -- BTC long-cycle sizing lever.
        "_BTC_LATE_CYCLE_SIZE_MULTIPLIER": 0.7,
        # Items #161/#162 (organic-decline/staleness penalty) REMOVED 30/07,
        # Item #242 -- operator's explicit, standing directive: never apply a
        # malus to a token for low market cap or bonding status. See
        # bonding_entry.py's module docstring and docs/trading-thresholds-
        # calibration.md for the full removal note.
        "_HOLDER_CONCENTRATION_UNINFORMATIVE_SCORE_FRACTION": 0.5,
    },
    "aria_core.limit_orders": {
        "LIMIT_ORDER_WATCH_TRIGGER_MULT": 1.10,
        "LIMIT_ORDER_EXPIRY_HOURS": 3.0,
        "BONDING_LIMIT_ORDER_MIN_LIQUIDITY_USD": 20_000.0,
    },
    "aria_core.dex_composite_score": {
        # Item #177, 28/07 -- additive DEX security/conviction score, first
        # pass (never yet calibrated against real outcomes).
        "_WEIGHT_CONTRACT_RISK": 35.0,
        "_WEIGHT_DEV_BEHAVIOR": 20.0,
        "_WEIGHT_SMART_MONEY": 25.0,
        "_WEIGHT_LIQUIDITY_DEPTH": 20.0,
        # 28/07 2nd pass, operator decision -- neutral base lowered from 50%
        # to 35% of each pillar's weight; pillar 1 (contract risk) made
        # BINARY, replacing the old per-field graduated penalties entirely.
        "_NEUTRAL_BASE_FRACTION": 0.35,
        "_CONTRACT_RISK_BASE": 12.25,
        "_CONTRACT_RISK_BAD_SCORE": 0.0,
        "_TAX_BAD_THRESHOLD_PCT": 0.10,
        "_MAX_SMART_MONEY_WALLETS": 4,
    },
    "aria_core.paper_trader": {
        "TRAIL_STOP_PCT": 0.15,
        "ATR_TRAIL_MULTIPLIER": 2.5,
        "MIN_ATR_TRAIL_PCT": 0.05,
        "MAX_ATR_TRAIL_PCT": 0.40,
        "VC_MIN_LIQUIDITY_FLOOR_USD": 30_000.0,
        "VC_LIQUIDITY_DROP_INVALIDATION_PCT": 0.5,
        "VC_TAKE_SEED_MULTIPLE": 2.0,
        "VC_LIQUIDITY_SUDDEN_DROP_PCT": 0.3,
        "MAX_CONSECUTIVE_LOSSES_PER_CONTRACT": 2,
        "SCALPING_MAX_CONSECUTIVE_LOSSES_PER_CONTRACT": 1,
        "BONDING_TP_STAGES": (1.0, 4.0, 11.5),
        "BONDING_TP_STAGE_FRACTIONS": (0.45, 0.25, 0.20),
        "BONDING_VELOCITY_DROP_PCT": 0.40,
        "BONDING_VELOCITY_WINDOW_MINUTES": 30,
        "BONDING_LIQUIDITY_FLOOR_USD": 5_000.0,
        "BONDING_LIQUIDITY_DROP_CUMULATIVE_PCT": 0.5,
        "BONDING_LIQUIDITY_SUDDEN_DROP_PCT": 0.3,
    },
}


def test_trading_thresholds_match_calibration_doc():
    """Garde-fou mécanique (28/07) : chaque constante de _TRADING_THRESHOLDS doit (1)
    exister dans son module avec EXACTEMENT la valeur attendue, et (2) être mentionnée
    par son nom dans docs/trading-thresholds-calibration.md. Casse si une valeur dérive
    silencieusement du doc, ou si le doc perd la trace d'une constante encore vérifiée
    ici -- objectif explicite de l'opérateur : pouvoir recalibrer proprement, à
    répétition, sans jamais perdre le fil de ce qui a déjà été tranché et pourquoi."""
    import importlib

    doc_path = REPO / "docs" / "trading-thresholds-calibration.md"
    assert doc_path.is_file(), "docs/trading-thresholds-calibration.md manquant"
    doc_text = doc_path.read_text(encoding="utf-8", errors="replace")

    mismatches: list[str] = []
    undocumented: list[str] = []
    for module_name, constants in _TRADING_THRESHOLDS.items():
        module = importlib.import_module(module_name)
        for const_name, expected in constants.items():
            actual = getattr(module, const_name, None)
            if actual != expected:
                mismatches.append(f"{module_name}.{const_name} = {actual!r} (attendu {expected!r})")
            if const_name not in doc_text:
                undocumented.append(f"{module_name}.{const_name}")

    assert not mismatches, (
        "Constante(s) de trading ayant dérivé du registre documenté (docs/"
        f"trading-thresholds-calibration.md) sans mise à jour du même commit : {mismatches}. "
        "Si le changement est volontaire, mets à jour _TRADING_THRESHOLDS ET le tableau "
        "markdown correspondant (valeur + date/source + critère de révision)."
    )
    assert not undocumented, (
        f"Constante(s) vérifiée(s) ici mais absente(s) du registre markdown : {undocumented}. "
        "Ajoute une ligne dans docs/trading-thresholds-calibration.md."
    )


# ── Index HANDOFF (07/08, délégué opérateur "choi toi") ──────────────────────────────────
# Un HANDOFF non indexé dans CLAUDE.md est aussi invisible qu'un HANDOFF qui n'existe pas
# (règle déjà écrite dans CLAUDE.md lui-même) -- ce test la rend mécanique plutôt que de
# compter sur une relecture manuelle à chaque nouveau fichier.

_HANDOFF_FILES = sorted((REPO / "docs").glob("HANDOFF_*.md"))


@pytest.mark.parametrize("path", _HANDOFF_FILES, ids=lambda p: p.name)
def test_handoff_file_indexed_in_claude_md(path):
    """Chaque docs/HANDOFF_<composant>.md doit être cité par son nom dans l'index de
    CLAUDE.md -- sinon une session future ne saura jamais qu'il existe."""
    claude = _read("CLAUDE.md")
    assert path.name in claude, (
        f"{path.name} existe sur disque mais n'est cité nulle part dans CLAUDE.md. "
        "Ajoute une ligne dans la section 'Index des HANDOFF par composant' (même commit "
        "que la création du fichier, règle déjà écrite dans CLAUDE.md)."
    )


# ── Format des entrées HANDOFF (07/08, délégué opérateur "choi toi") ─────────────────────
# Format imposé par CLAUDE.md : "[STATUT] Sujet : <titre>" / "Date : ... / Probleme : ..."
# / "Solution : ...", entrées séparées par une ligne de tirets. Vérifié empiriquement sur
# les 492 entrées réelles avant d'écrire ce test (492/492 conformes) -- "Sujet" et "Subject"
# cohabitent légitimement (bascule repo-en-anglais du 23/07, cf. CLAUDE.md), donc les deux
# sont acceptés ici, jamais un seul.
_HANDOFF_STATUSES = ("DEPLOYE", "CODE", "CONFIG", "ETAT ACTUEL")
_HANDOFF_STATUS_LINE = re.compile(
    r"^\[(" + "|".join(_HANDOFF_STATUSES) + r")\][^\n]*(?:Sujet|Subject)", re.MULTILINE
)
_HANDOFF_BLOCK_SPLIT = re.compile(r"\n-{10,}\n")


@pytest.mark.parametrize("path", _HANDOFF_FILES, ids=lambda p: p.name)
def test_handoff_entries_use_valid_status_and_required_fields(path):
    """Chaque entrée HANDOFF (bloc entre deux séparateurs) doit : (1) ouvrir sur un statut
    dans {DEPLOYE, CODE, CONFIG, ETAT ACTUEL} + Sujet/Subject sur la même ligne, (2) contenir
    Date, Probleme/Problème/Problem et Solution quelque part dans le bloc. Attrape un statut
    inventé/mal orthographié ou une entrée bâclée sans qu'une relecture manuelle soit requise."""
    text = path.read_text(encoding="utf-8", errors="replace")
    for block in _HANDOFF_BLOCK_SPLIT.split(text):
        if not _HANDOFF_STATUS_LINE.search(block):
            continue  # préambule/section libre, pas une entrée -- jamais forcé au format
        assert re.search(r"\bDate\b", block), (
            f"{path.name} : entrée sans 'Date' -- {block[:80]!r}"
        )
        assert re.search(r"\bProbleme\b|\bProblème\b|\bProblem\b", block), (
            f"{path.name} : entrée sans 'Probleme'/'Problem' -- {block[:80]!r}"
        )
        assert re.search(r"\bSolution\b", block), (
            f"{path.name} : entrée sans 'Solution' -- {block[:80]!r}"
        )


# ── Anti prompt-injection (mandat #192) -- couverture verrouillée (11/08, backlog #104) ──
# Audit réel (grep, 11/08) : ces 13 modules appellent sanitize_untrusted_text
# (aria_core/sanitize.py) sur du texte tiers non fiable avant de l'injecter dans un
# prompt LLM -- vérifié un par un contre des tests d'attaque comportementale réels
# (fausse tentative de fermeture de balise/instruction système) déjà présents dans
# leurs fichiers de test respectifs (community_feedback, x_engagement, vc_analysis,
# momentum_entry, conviction_research, vc_judge, operator_conversational,
# market_sentiment, market_alerts, source_code_audit, thesis_quality,
# paper_ledger_report, polymarket). Ce test ne REJOUE pas ces attaques (déjà fait,
# ailleurs, avec un vrai mock du LLM) -- il verrouille juste la LISTE : si un de ces
# fichiers cesse d'importer sanitize_untrusted_text, casse LOUDLY plutôt que de
# rouvrir silencieusement un vecteur déjà fermé. Retirer un module d'ici doit être
# une décision de sécurité délibérée (documentée dans le commit), jamais un
# effet de bord de refactor.
_KNOWN_SANITIZE_CONSUMERS = (
    "paper_ledger_report.py",
    "community_feedback.py",
    "skills/thesis_quality.py",
    "skills/source_code_audit.py",
    "skills/market_sentiment.py",
    "skills/market_alerts.py",
    "skills/vc_analysis.py",
    "conviction_research.py",
    "gateway/x_engagement.py",
    "momentum_entry.py",
    "operator_conversational.py",
    "knowledge/web_verify.py",
    "services/polymarket.py",
)


@pytest.mark.parametrize("rel", _KNOWN_SANITIZE_CONSUMERS)
def test_known_sanitize_untrusted_text_consumers_keep_using_it(rel):
    text = _read_core(rel)
    assert "sanitize_untrusted_text" in text, (
        f"{rel} a cessé d'utiliser sanitize_untrusted_text (mandat #192, anti "
        "prompt-injection) -- si c'est délibéré (le module ne traite plus de texte "
        "tiers), retire-le de _KNOWN_SANITIZE_CONSUMERS dans le MÊME commit avec une "
        "justification ; sinon c'est une régression de sécurité réelle."
    )


def test_pocket_parameter_registry_matches_the_code():
    """The pocket registry must never drift from the code it describes.

    21/08, operator-directed. A -81.5% close was explained away as a "-20%
    trailing stop" that was really 15% and only armed above +10%: a parameter
    nobody had re-read. A hand-kept registry would drift the same way, so the
    committed JSON is generated and this test fails the build the moment it
    stops matching. Regenerate with `python -m aria_core.pocket_parameters
    --write` and READ THE DIFF -- that reading is the point of the mechanism."""
    from aria_core import pocket_parameters

    assert pocket_parameters.REGISTRY_PATH.exists(), (
        "docs/pocket-parameters.json is missing -- run "
        "`python -m aria_core.pocket_parameters --write`"
    )
    assert pocket_parameters.is_current(), (
        "docs/pocket-parameters.json is stale: a pocket parameter changed "
        "without the registry being regenerated. Run "
        "`python -m aria_core.pocket_parameters --write` and review the diff."
    )


def test_every_shadow_pocket_module_is_in_the_registry():
    """A new pocket that never reaches the registry is invisible to the very
    re-reading this exists to force."""
    from pathlib import Path

    from aria_core import pocket_parameters

    src = Path(pocket_parameters.__file__).resolve().parent
    on_disk = {p.stem for p in src.glob("*_shadow.py")}
    missing = on_disk - set(pocket_parameters.POCKET_MODULES)
    assert not missing, (
        f"shadow pocket modules absent from POCKET_MODULES: {sorted(missing)} -- "
        f"add them to aria_core/pocket_parameters.py so their parameters are tracked"
    )


@pytest.mark.asyncio
async def test_the_two_active_pockets_put_their_database_in_wal_mode(tmp_path):
    """21/08, Devil's Advocate finding, verified against the real file:
    `shadow.db` ran in `delete` journal mode while `aria.db` had been in WAL
    for a long time. `delete` locks the whole database for every commit, so
    the pockets' concurrent small writers serialise and can hit SQLITE_BUSY
    inside paths written to never raise -- silently losing the very rows the
    pockets are judged on."""
    import aiosqlite

    from aria_core import solana_fresh_launch_fast_discovery_shadow as fd
    from aria_core import solana_late_bonding_shadow as lb

    for mod, name in ((lb, "late_bonding"), (fd, "fast_discovery")):
        db_path = str(tmp_path / f"{name}.db")
        mod._ensured_db_paths.discard(db_path)
        original = mod._db_path
        mod._db_path = lambda p=db_path: p
        try:
            await (mod._ensure_table(db_path) if mod is lb else mod._ensure_table())
        finally:
            mod._db_path = original

        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("PRAGMA journal_mode")
            assert (await cur.fetchone())[0].lower() == "wal", f"{name} is not in WAL mode"


def test_the_two_active_pockets_share_the_same_exit_guardrails():
    """21/08, operator-directed ("verifie toujours pour toutes les poches").

    LATE-BONDING was written after FAST-DISCOVERY and silently inherited none
    of its hard-won guardrails. Every one of these was found missing on the
    same day, each after real damage:
      - `window_high`/`window_low`: without them the exit rule only ever sees
        a point sample, which is why a -20% hard stop filled at -78% -- the
        websocket HAD recorded the crossing, the pocket never read it;
      - `max_rest_calls`: without it, websocket-orphaned positions queued
        behind the throttled REST cascade, 2.6s each;
      - hot ALTER: without it, adding a column silently stopped a pocket from
        closing any position at all;
      - `hard_stop_pct`: the trailing stop only arms above +10%, leaving
        everything below it with no downside rule.

    Checked as SOURCE TEXT on purpose: these are call-site wiring defects, and
    every one of them shipped with a green unit suite because each component
    worked perfectly on its own. This asserts they are actually connected."""
    import inspect

    from aria_core import solana_fresh_launch_fast_discovery_shadow as fd
    from aria_core import solana_late_bonding_shadow as lb

    required = {
        "window_high=": "the high reached since the last read",
        "window_low=": "the low reached since the last read",
        "hard_stop_pct=": "a floor loss for the window the trailing does not cover",
        "max_rest_calls": "a per-cycle ceiling on REST-bound pricing",
        "PRAGMA table_info": "a hot migration path for new columns",
        "ensure_wal": "WAL journal mode",
    }
    for mod, name in ((lb, "late_bonding"), (fd, "fast_discovery")):
        src = inspect.getsource(mod)
        missing = [f"{k} ({why})" for k, why in required.items() if k not in src]
        assert not missing, f"{name} is missing: {missing}"


def test_both_active_pockets_import_the_shared_exit_rule():
    """They must differ on ENTRY only. A local copy of the exit logic would
    make every comparison between them meaningless."""
    from aria_core import solana_fresh_launch_fast_discovery_shadow as fd
    from aria_core import solana_fresh_launch_ws_exit_shadow as shared
    from aria_core import solana_late_bonding_shadow as lb

    assert lb.evaluate_exit is shared.evaluate_exit
    assert fd.evaluate_exit is shared.evaluate_exit


# Modules allowed to make a raw Solana RPC call. The gateway itself, obviously,
# and the two websocket feeds -- a subscription is a long-lived socket, not a
# request, so it is not what the gateway paces.
_SOLANA_RPC_ALLOWLIST = {
    "services/solana_gateway.py",
    "services/pumpswap_ws.py",
    "services/pumpfun_bonding_ws.py",
    "services/pumpfun_curve_tracker.py",
    "onchain/squads_solana_wallet.py",
    "onchain/squads_solana_signer.py",
    # A websocket subscription is a long-lived socket, not a paced request --
    # the gateway has nothing to give it.
    "services/pumpfun_trade_stream.py",
    # EVM (Robinhood Chain), not Solana at all.
    "services/wallet_transfers_fast.py",
}


def test_solana_rpc_calls_go_through_the_gateway():
    """Mechanical guard, added 22/08 after the night that made it necessary.

    Seven defects in one night, every one the same shape: a module talking to
    Solana its own way -- its endpoint, its rate or none, its failover or none.
    When one provider's quota ran out, each fell over separately while a
    healthy provider sat unused, and the pocket went three hours without a
    trade.

    The decisive proof that convention does not hold: the liquidation script
    written THREE HOURS AFTER documenting the rule broke it the same day.
    Discipline is not a mechanism; this test is.
    """
    offenders: list[str] = []
    for path in CORE.rglob("*.py"):
        rel = str(path.relative_to(CORE))
        if rel in _SOLANA_RPC_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # A raw JSON-RPC body built by hand is the signature of a direct call.
        if '"jsonrpc": "2.0"' in text or "'jsonrpc': '2.0'" in text:
            offenders.append(rel)
    assert not offenders, (
        f"appel RPC Solana hors de la passerelle : {sorted(offenders)}. "
        f"Utilise aria_core.services.solana_gateway.call(), ou ajoute le module "
        f"a _SOLANA_RPC_ALLOWLIST avec la raison."
    )
