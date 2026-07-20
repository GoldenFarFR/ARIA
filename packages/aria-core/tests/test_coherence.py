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
] + sorted(str(p.relative_to(REPO)) for p in (REPO / "docs").glob("HANDOFF-*.md"))
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


def test_claude_md_declares_permanent_norms():
    """Le bloc 'Normes permanentes' (qualité/fluidité/UX…) doit rester présent — appliqué à chaque build."""
    claude = _read("CLAUDE.md")
    assert "Normes permanentes" in claude, "la section 'Normes permanentes' a disparu de CLAUDE.md"
    for norm in ("Qualité", "Fluidité", "Graphique", "Robustesse", "Accessibilité", "Protection des données"):
        assert norm in claude, f"la norme permanente '{norm}' a disparu de CLAUDE.md"


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
    et strictement en LECTURE (agrégateur pur, aucun write, aucun client réseau propre)."""
    import inspect

    from aria_core import dossier

    aria_route = _read("vanguard/backend/app/api/routes/aria.py")
    assert '@router.get("/dossier/{contract}")' in aria_route, "route dossier manquante"
    # La route doit exiger l'opérateur (le détail des candidats n'est jamais public ni membre).
    route_seg = aria_route.split('@router.get("/dossier/{contract}")', 1)[1][:600]
    assert "require_operator(request)" in route_seg, "le dossier doit être gaté opérateur"
    # Jamais dans les allowlists publiques du middleware.
    mw = _read("vanguard/backend/app/auth/middleware.py")
    assert "/api/aria/dossier" not in mw, "le dossier ne doit JAMAIS être exposé en public"
    # Agrégateur en lecture seule : aucune écriture SQL, aucun accès DB/réseau direct.
    src = inspect.getsource(dossier)
    for forbidden in ("INSERT", "UPDATE ", "DELETE", "aiosqlite", "httpx"):
        assert forbidden not in src, f"le dossier doit rester une lecture pure (trouvé: {forbidden})"


def test_cockpit_operator_secret_never_persistent():
    """Le cockpit web (secret opérateur saisi côté navigateur) doit rester SESSION-only :
    jamais localStorage (persisterait le secret sur l'appareil), jamais dans une URL."""
    auth = _read("vanguard/src/lib/operator-auth.ts")
    for forbidden in ("localStorage.setItem", "localStorage.getItem"):
        assert forbidden not in auth, "le secret opérateur ne doit JAMAIS toucher localStorage"
    assert "sessionStorage" in auth, "le secret opérateur doit être session-only (sessionStorage)"

    api = _read("vanguard/src/api.ts")
    dossier_call = api.split("export async function getDossier", 1)[1][:400]
    assert "operatorHeaders()" in dossier_call, "le dossier doit envoyer le secret en HEADER, jamais en query-string"


def test_vc_report_pdf_secured_and_email_body_never_leaks_full_report():
    """PDF sécurisé joint au rapport /vc : (a) permissions anti-copie posées avant
    envoi, jamais un mot de passe propriétaire codé en dur (généré à la volée par
    l'appelant) ; (b) le corps de l'email (teaser) ne contient JAMAIS la thèse ni
    le rapport détaillé — sinon la protection PDF anti-copie serait sans objet."""
    import inspect

    from aria_core.skills import vc_delivery, vc_report_pdf

    delivery_src = inspect.getsource(vc_delivery)
    assert "secure_pdf_bytes" in delivery_src, "le PDF joint doit être sécurisé avant envoi"
    assert "secrets.token_urlsafe" in delivery_src, (
        "le mot de passe propriétaire doit être généré à la volée (jamais codé en dur/réutilisé)"
    )
    assert "render_email_teaser_html" in delivery_src and "email_teaser_text" in delivery_src, (
        "le corps de l'email doit être le teaser court, jamais le rapport complet"
    )
    assert "render_html_report" not in delivery_src, (
        "le rapport HTML complet ne doit plus être envoyé comme corps d'email"
    )

    pdf_src = inspect.getsource(vc_report_pdf)
    assert "pas inviolable" in pdf_src or "jamais un chiffrement inviolable" in pdf_src, (
        "le module doit documenter honnêtement la limite de la protection PDF (dissuasif, pas absolu)"
    )


def test_vc_language_choice_never_asks_confirmation_or_email_address():
    """/vc (chemin réel) demande la LANGUE avant envoi — jamais une confirmation
    d'envoi séparée, jamais l'adresse email (destinataire toujours fixe, dôme)."""
    src = _read_core("gateway/telegram_bot.py")
    assert "vclang:" in src, "callback de choix de langue manquant"
    # Le destinataire reste résolu par vc_delivery (ARIA_VC_REPORT_TO / ARIA_SMTP_USER),
    # jamais saisi depuis Telegram.
    handler_seg = src.split("async def _handle_vc(", 1)[1][:3000]
    for forbidden in ("input(", "quelle adresse", "quel email", "confirmer l'envoi"):
        assert forbidden not in handler_seg.lower(), f"jamais de prompt interdit : {forbidden}"


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
    "services/mailer.py",
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
    # Email
    "skills/vc_delivery.py",
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
