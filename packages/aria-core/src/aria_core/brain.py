from __future__ import annotations

import re

from aria_core import repertoire_db
from aria_core.narrative import (
    help_commands,
    help_commands_public,
    llm_system_block,
    peer_competition_policy,
    public_llm_system_block,
)
from aria_core.grounding import (
    anti_hallucination_rules,
    build_verified_facts_block,
    faq_direct_answer,
    format_greeting_reply,
    grounded_for_audience,
    grounded_llm_identity,
    has_sufficient_grounding,
    is_greeting,
    is_help_request,
    community_suggestion_reply,
    is_social_chitchat,
    is_pure_casual_smalltalk,
    should_skip_llm_enhance,
    social_ack_reply,
    strict_rephrase_rules,
    truth_ledger_direct_answer,
    unknown_reply,
    is_aria_product_or_holding_question,
)
from aria_core.public_mode import is_public_mode, operator_action_blocked_reply, skill_allowed_in_public
from aria_core.locale import LANG_EN, LANG_FR
from aria_core.llm import chat_with_context, is_llm_configured
from aria_core.llm_economy import (
    LlmDepth,
    calibrated_action_label,
    depth_system_instruction,
    detect_depth,
    llm_unavailable_hint,
    resolve_budget,
    skill_output_readable,
)
from aria_core.memory import append_memory, build_llm_context, get_journal_summary
from aria_core.models import ChatResponse, SkillName
from aria_core.skills.portfolio_skill import execute_portfolio_analysis
from aria_core.skills.repertoire_skill import (
    execute_develop_repertoire,
    execute_manage_repertoire,
    get_repertoire_summary,
    wants_manage_repertoire,
)
from aria_core.skills.builder_skill import execute_build_optimize
from aria_core.skills.community_worker_skill import (
    execute_worker_delegate,
    is_community_suggestion,
    wants_worker_delegate,
)
from aria_core.skills.github_skill import (
    execute_github_sandbox,
    looks_like_repo_create,
    looks_like_repo_delete,
)
from aria_core.skills.comms_skill import execute_comms_draft
from aria_core.skills.faq_skill import execute_faq_lookup
from aria_core.skills.epistemic_skill import execute_epistemic_check
from aria_core.skills.launchpad_skill import execute_launchpad_select, wants_launchpad_methodology
from aria_core.skills.holding_site_skill import execute_holding_site, wants_holding_site
from aria_core.skills.training_skill import execute_training, wants_training
from aria_core.skills.entrepreneur_skill import execute_entrepreneur, wants_entrepreneur
from aria_core.skills.capability_skill import execute_capability, wants_capability
from aria_core.skills.zhc_bridge import execute_zhc_bridge
from aria_core.skills.acp_client_skill import execute_acp_marketplace, wants_acp_marketplace
from aria_core.skills.ingest_repo_skill import execute_ingest_repo, wants_ingest_repo
from aria_core.identity import fix_handle_in_text, official_x_at, official_x_url, x_identity_prompt
from aria_core.runtime import settings


INTENT_PATTERNS: list[tuple[SkillName, list[str]]] = [
    (SkillName.ANALYZE_PORTFOLIO, [
        r"portefeuille", r"portfolio", r"watchlist", r"analys", r"signaux?", r"signals?",
        r"positions?", r"paires?", r"pairs?",
    ]),
    (SkillName.HOLDING_SITE, [
        r"site web", r"site holding", r"constru.*site", r"build.*site",
        r"aria.?vanguard", r"vanguard.*site", r"holding\s+site", r"ariavanguardzhc",
        r"devenir autonome", r"prendre des initiatives?",
    ]),
    (SkillName.ZHC_BRIDGE, [
        r"\bjuno\b", r"junoagent", r"zhc institute", r"zhcinstitute",
        r"benchmark.*juno", r"juno.*benchmark", r"contacter?.*juno",
        r"contact.*juno", r"message.*juno", r"communiqu.*juno",
    ]),
    (SkillName.MANAGE_REPERTOIRE, [
        r"supprim.*répertoire", r"supprime.*répertoire", r"supprim.*repertoire",
        r"delete.*repertoire", r"remove.*repertoire", r"retir.*répertoire",
        r"archiv.*répertoire", r"supprim.*projet", r"supprime.*projet",
        r"delete.*project", r"enlev.*répertoire",
    ]),
    (SkillName.DEVELOP_REPERTOIRE, [
        r"répertoire", r"repertoire",
        r"projets?", r"projects?", r"entreprises?", r"companies", r"stratég", r"grow",
    ]),
    (SkillName.MEMORY_RECALL, [
        r"mémoire", r"memoire", r"memory", r"souviens", r"historique", r"journal",
        r"qu.as.?tu fait", r"what did you",
        r"collegue\.md", r"mémoire collègue",
        r"runbook", r"nouveau\s+pc", r"new\s+pc", r"nouveau\s+github", r"nouvel\s+agent",
        r"check-aria", r"pitfalls?", r"lecons?", r"ne\s+pas\s+oublier",
    ]),
    (SkillName.LAUNCHPAD_SELECT, [
        r"launchpad", r"launch pad", r"bankr", r"clanker", r"flaunch", r"virtuals?",
        r"zora.?coins?", r"aerodrome.?ignition", r"base.*launch", r"lancer.*token",
        r"token.*base", r" où lancer", r"where.*launch", r"meilleur.*launch",
        r"méthodolog", r"methodolog", r"holding.?fit", r"visibilit",
    ]),
    (SkillName.GITHUB_SANDBOX, [
        r"github", r"sandbox", r"experiment", r"expérience", r"expériences",
        r"aria-sandbox", r"aria-token", r"token-base", r"push.*repo",
        r"créer repo", r"crée le repo", r"create repo", r"nouveau repo", r"new repo",
        r"supprim.*\brepo", r"delete.*\brepo", r"remove.*\brepo", r"effac.*\brepo",
        r"liste.*repos", r"tous les repos", r"list repos", r"quels repos",
        r"vois.*repo", r"voir.*repo", r"repo\s+kikou", r"\bkikou\b",
        r"repo\s+aria", r"goldenfarfr/",
    ]),
    (SkillName.BUILD_OPTIMIZE, [
        r"build", r"constru", r"cod", r"implémen", r"implement", r"refactor",
        r"optim", r"amélior", r"improv", r"créat", r"creat", r"design",
        r"architect", r"deploy", r"déplo", r"feature", r"bug", r"fix", r"skill",
    ]),
    (SkillName.FAQ_CONTENT, [
        r"\bfaq\b", r"frequently asked", r"questions? fréquentes",
        r"what is aria", r"what is dexpulse", r"what is.*holding",
        r"c'est quoi", r"qu.est.ce que", r"explain.*holding",
        r"how does dexpulse", r"comment.*fonctionne",
        r"gem[\s-]?crush", r"aria\s+gem",
    ]),
    (SkillName.EPISTEMIC_CHECK, [
        r"vrai ou faux", r"true or false", r"probabilit",
        r"c.est vrai que", r"is it true",
        r"noyau épistémique", r"epistemic core",
        r"terre plate", r"earth flat",
        r"hallucination", r"faits vérifiés", r"verified facts",
    ]),
    (SkillName.MARKETING_COMMS, [
        r"marketing", r"communication", r"\bcomms?\b", r"\bcommuniqu",
        r"tweet", r"twitter", r"social", r"newsletter", r"announce",
        r"press", r"copy", r"landing", r"hero", r"draft.*post",
        r"rédig", r"write.*update", r"public message",
    ]),
    (SkillName.TRAINING_PORTFOLIO, [
        r"entraînement", r"entrainement", r"training portfolio",
        r"portefeuille fictif", r"training_portfolio", r"signal brief",
    ]),
    (SkillName.ENTREPRENEUR_CULTIVATION, [
        r"entrepreneur", r"entrepreneuse", r"cultiv", r"se cultiver",
        r"culture entrepreneuse", r"\bmrr\b", r"objectifs?\s+personnel",
        r"50\s*\$", r"50\s*usd", r"revenue\s+goal", r"log\s+revenu",
        r"log\s+revenue", r"meilleur.*entrepreneur",
        r"génér.*revenu", r"generer.*revenu", r"premier\s+dollar",
        r"s['']?active", r"active[- ]toi", r"commence.*agir",
    ]),
    (SkillName.CAPABILITY_QI, [
        r"\bqi\b", r"indice aria", r"niveau aria", r"niveaux aria",
        r"score aria", r"capability", r"progression aria",
        r"montre.*niveau", r"level up", r"/level",
    ]),
]


def _is_strategic_conversation(message: str) -> bool:
    """Questions d'avis / gouvernance — pas de skill catalogue (répertoire, sandbox create)."""
    from aria_core.tweet_compose_workflow import wants_role_coaching

    if wants_role_coaching(message):
        return True
    lower = message.lower()
    if re.search(r"tu\s+veu[xt]\s+faire\s+quoi", lower) and re.search(
        r"am[eé]lioration|aider|aujourd", lower
    ):
        return True
    if re.search(r"\b(?:as[- ]?tu|astu|tu\s+as|avez[- ]?vous|have\s+you)\b", lower) and re.search(
        r"plan|financier|acheteur|acheteurs|client|revenu|mrr|strat[eé]g|pr[eé]vu|attirer|buyer",
        lower,
    ):
        return True
    if re.search(r"comment\s+(?:as[- ]?tu|astu|tu\s+as)\b", lower):
        return True
    opinion = re.search(
        r"souhait|\bveu[xt]\b|voudr|penses?|avis|ton avis|what do you think|should (i|we|you)|"
        r"intéressant|interessant|préfères|prefer",
        lower,
    )
    topic = re.search(r"github|repo|développ|develop|personnel|personal", lower)
    return bool(opinion and topic)


def _routing_message(message: str) -> str:
    """Pont Cursor / suite KART — router sur le tour actuel, pas tout le contexte."""
    conf = re.search(
        r"operateur confirme\s*:\s*(.+?)(?:\n|$)",
        message,
        re.IGNORECASE,
    )
    if conf:
        return conf.group(1).strip()
    m = re.search(
        r"Message actuel de (?:operateur|Grok):\s*(.+?)(?:\n|$)",
        message,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    return message


def detect_intent(message: str) -> SkillName | None:
    from aria_core.operator_conversational import is_injected_factual_claim

    if is_injected_factual_claim(message):
        return None
    if wants_ingest_repo(message):
        return SkillName.INGEST_REPO
    if wants_acp_marketplace(message):
        return SkillName.ACP_MARKETPLACE
    if wants_capability(message):
        return SkillName.CAPABILITY_QI
    if _is_strategic_conversation(message):
        return None
    lower = message.lower()
    if looks_like_repo_delete(message) or looks_like_repo_create(message):
        return SkillName.GITHUB_SANDBOX
    if wants_manage_repertoire(message):
        return SkillName.MANAGE_REPERTOIRE
    if wants_launchpad_methodology(message):
        return SkillName.LAUNCHPAD_SELECT
    if wants_holding_site(message):
        return SkillName.HOLDING_SITE
    if wants_entrepreneur(message):
        return SkillName.ENTREPRENEUR_CULTIVATION
    scores: dict[SkillName, int] = {}
    for skill, patterns in INTENT_PATTERNS:
        score = sum(1 for p in patterns if re.search(p, lower))
        if score > 0:
            scores[skill] = score
    if not scores:
        return None
    return max(scores, key=scores.get)


class AriaBrain:
    async def process(
        self,
        user_message: str,
        lang: str = LANG_EN,
        *,
        visitor_id: str = "",
        public_mode: bool | None = None,
    ) -> ChatResponse:
        from aria_core.llm_usage import (
            begin_chat_usage_tracking,
            clear_chat_usage_tracking,
        )

        begin_chat_usage_tracking()
        try:
            return await self._process_inner(
                user_message,
                lang,
                visitor_id=visitor_id,
                public_mode=public_mode,
            )
        finally:
            clear_chat_usage_tracking()

    async def _process_inner(
        self,
        user_message: str,
        lang: str = LANG_EN,
        *,
        visitor_id: str = "",
        public_mode: bool | None = None,
    ) -> ChatResponse:
        public = is_public_mode() if public_mode is None else public_mode
        vid = visitor_id if public else ""
        shell_mode = not public and str(visitor_id).startswith("shell")
        route_msg = _routing_message(user_message)
        await repertoire_db.save_message("user", user_message, visitor_id=vid)

        if not public:
            from aria_core.skills.acp_conversational import is_conversational_acp_question
            from aria_core.tweet_compose_workflow import handle_workflow_message

            skip_compose_workflow = is_conversational_acp_question(route_msg)

            skip_self_maint = shell_mode or wants_worker_delegate(route_msg) or bool(
                re.search(
                    r"\b(ship(?:ped)?|worker_delegate|aria-worker|communitywelcomebanner|"
                    r"file\s+done|tache\s+done|ship\s+confirm)\b",
                    route_msg,
                    re.IGNORECASE,
                ),
            )
            wf_reply = None
            if (
                not shell_mode
                and not wants_worker_delegate(route_msg)
                and not skip_self_maint
                and not skip_compose_workflow
            ):
                wf_reply = await handle_workflow_message(route_msg)
            if wf_reply is not None:
                await repertoire_db.save_message("agent", wf_reply, visitor_id=vid)
                return ChatResponse(
                    reply=wf_reply,
                    skill_used=None,
                    actions_taken=["Tweet compose / coaching ZHC"],
                    data={"compose_workflow": True},
                )

            from aria_core.self_maintenance import handle_operator_self_message

            sm_reply = None
            if not skip_self_maint:
                sm_reply = await handle_operator_self_message(route_msg, lang=lang)
            if sm_reply is not None:
                await repertoire_db.save_message("agent", sm_reply, visitor_id=vid)
                append_memory("identity", f"Self-maintenance: {user_message[:80]}\n{sm_reply[:200]}")
                return ChatResponse(
                    reply=sm_reply,
                    skill_used=None,
                    actions_taken=["Operator self-directive / curiosity loop"],
                    data={"self_maintenance": True},
                )

            from aria_core.operator_readiness import (
                execute_operator_readiness,
                execute_operator_status_pulse,
                wants_operator_readiness,
                wants_operator_status_pulse,
            )

            if wants_operator_status_pulse(route_msg):
                pulse_reply, pulse_data = await execute_operator_status_pulse(
                    route_msg, lang=lang,
                )
                await repertoire_db.save_message("agent", pulse_reply, visitor_id=vid)
                lang_key = "fr" if lang == LANG_FR else "en"
                pulse_display = pulse_reply
                if (
                    getattr(settings, "aria_llm_cost_footer", True)
                    and not getattr(settings, "aria_operator_founder_mode", False)
                ):
                    from aria_core.response_cost import append_cost_footer, build_cost_meta

                    pulse_display = append_cost_footer(
                        pulse_reply,
                        build_cost_meta(total_tokens=0),
                        lang=lang_key,
                    )
                    pulse_data["llm_cost"] = build_cost_meta(total_tokens=0)
                return ChatResponse(
                    reply=pulse_display,
                    skill_used=None,
                    actions_taken=["Operator status pulse (local — sans web)"],
                    data=pulse_data,
                )

            if wants_operator_readiness(route_msg):
                ready_reply, ready_data = await execute_operator_readiness(
                    route_msg, lang=lang,
                )
                await repertoire_db.save_message("agent", ready_reply, visitor_id=vid)
                append_memory(
                    "identity",
                    f"Readiness: {user_message[:80]}\n{ready_reply[:200]}",
                )
                from aria_core.response_cost import append_cost_footer, build_cost_meta

                lang_key = "fr" if lang == LANG_FR else "en"
                ready_display = append_cost_footer(
                    ready_reply,
                    build_cost_meta(total_tokens=0),
                    lang=lang_key,
                )
                ready_data["llm_cost"] = build_cost_meta(total_tokens=0)
                return ChatResponse(
                    reply=ready_display,
                    skill_used=None,
                    actions_taken=["Operator readiness audit (sans LLM)"],
                    data=ready_data,
                )

            from aria_core.operator_go_ahead import (
                execute_deferred_go_ahead,
                wants_operator_deferred_go,
            )

            if wants_operator_deferred_go(route_msg):
                go_reply, go_data = await execute_deferred_go_ahead(
                    route_msg, lang=lang,
                )
                await repertoire_db.save_message("agent", go_reply, visitor_id=vid)
                append_memory(
                    "identity",
                    f"Deferred go: {user_message[:80]}\n{go_reply[:200]}",
                )
                from aria_core.response_cost import append_cost_footer, build_cost_meta

                lang_key_go = "fr" if lang == LANG_FR else "en"
                go_display = append_cost_footer(
                    go_reply,
                    build_cost_meta(total_tokens=0),
                    lang=lang_key_go,
                )
                go_data["llm_cost"] = build_cost_meta(total_tokens=0)
                return ChatResponse(
                    reply=go_display,
                    skill_used=None,
                    actions_taken=["Feu vert opérateur (fil Socrate — sans LLM)"],
                    data=go_data,
                )

        lang_key = "fr" if lang == LANG_FR else "en"
        if not public:
            from aria_core.operator_conversational import (
                operator_improvement_reply,
                wants_capability_improvement,
            )

            if wants_capability_improvement(route_msg):
                imp = operator_improvement_reply(lang=lang_key)
                await repertoire_db.save_message("agent", imp, visitor_id=vid)
                append_memory("chat", f"User: {user_message[:100]}\nARIA: {imp[:200]}")
                return ChatResponse(
                    reply=imp,
                    skill_used=SkillName.CAPABILITY_QI,
                    actions_taken=["Compétences ARIA (local — sans épistémique)"],
                    data={"capability_improvement": True, "skip_web": True},
                )

            from aria_core.operator_conversational import (
                is_injected_factual_claim,
                unverified_claim_reply,
                verify_external_claim,
                wants_claim_verification,
            )

            if is_injected_factual_claim(route_msg):
                # Operator pasted a news-like claim (price, catalog, billing, PRs...).
                # We now VERIFY instead of blindly refusing — determine vrai/faux with tools, reply like human chat.
                if wants_claim_verification(route_msg) or True:  # always dig for operator so ARIA can answer these
                    claim_reply, vdata = await verify_external_claim(route_msg, lang=lang_key)
                    await repertoire_db.save_message("agent", claim_reply, visitor_id=vid)
                    append_memory("chat", f"User: {user_message[:100]}\nARIA: {claim_reply[:200]}")
                    return ChatResponse(
                        reply=claim_reply,
                        skill_used="EXTERNAL_CLAIM_VERIFY",
                        actions_taken=["Affirmation externe vérifiée (web + GitHub au besoin)"],
                        data={"injected_claim": True, "claim_verified": True, **(vdata or {})},
                    )
                claim_reply = unverified_claim_reply(route_msg, lang=lang_key)
                await repertoire_db.save_message("agent", claim_reply, visitor_id=vid)
                append_memory("chat", f"User: {user_message[:100]}\nARIA: {claim_reply[:200]}")
                return ChatResponse(
                    reply=claim_reply,
                    skill_used=None,
                    actions_taken=["Affirmation externe (non vérifiée — sans skill détourné)"],
                    data={"injected_claim": True, "skip_web": True},
                )

        if is_greeting(route_msg):
            welcome = format_greeting_reply(route_msg, lang_key, public=public)
            await repertoire_db.save_message("agent", welcome, visitor_id=vid)
            if not public:
                append_memory("chat", f"User: {user_message[:100]}\nARIA: {welcome[:200]}")
            return ChatResponse(
                reply=welcome,
                skill_used=None,
                actions_taken=["Greeting (template)"],
                data={"greeting": True},
            )

        intent = detect_intent(route_msg)
        if not public and wants_worker_delegate(route_msg):
            intent = SkillName.WORKER_DELEGATE
        if looks_like_repo_delete(route_msg):
            intent = SkillName.GITHUB_SANDBOX
        elif looks_like_repo_create(route_msg):
            intent = SkillName.GITHUB_SANDBOX
        if wants_training(route_msg):
            intent = SkillName.TRAINING_PORTFOLIO
        if wants_holding_site(route_msg):
            intent = SkillName.HOLDING_SITE
        actions: list[str] = []
        zhc_msg = None
        data: dict = {}
        skill: SkillName | None = None
        skill_output: str | None = None

        if public and intent and not skill_allowed_in_public(intent.value):
            blocked = operator_action_blocked_reply("fr" if lang == LANG_FR else "en")
            await repertoire_db.save_message("agent", blocked, visitor_id=vid)
            return ChatResponse(reply=blocked, skill_used=None, actions_taken=[], data={})

        if intent == SkillName.ANALYZE_PORTFOLIO:
            skill_output, data = await execute_portfolio_analysis(lang)
            actions.append("Scan watchlist DEXPulse")
            skill = SkillName.ANALYZE_PORTFOLIO

        elif intent == SkillName.ZHC_BRIDGE:
            from aria_core.holding import holding_name

            h = holding_name()
            skill_output = (
                f"Je me concentre sur {h} et DEXPulse — pas de benchmark ni contact concurrent.\n"
                f"Priorité : site holding, moat produit, objectif "
                f"{settings.aria_revenue_goal_monthly_usd:.0f} $/mois."
                if lang == LANG_FR
                else (
                    f"I focus on {h} and DEXPulse — no competitor benchmark or outreach.\n"
                    f"Priority: holding site, product moat, "
                    f"${settings.aria_revenue_goal_monthly_usd:.0f}/mo goal."
                )
            )
            actions.append("ZHC bridge: holding focus")
            skill = SkillName.ZHC_BRIDGE

        elif intent == SkillName.MANAGE_REPERTOIRE:
            skill_output, data = await execute_manage_repertoire(user_message, lang)
            actions.append("Repertoire manage — delete/archive")
            skill = SkillName.MANAGE_REPERTOIRE

        elif intent == SkillName.DEVELOP_REPERTOIRE:
            skill_output, data = await execute_develop_repertoire(lang)
            actions.append("Repertoire development")
            skill = SkillName.DEVELOP_REPERTOIRE

        elif intent == SkillName.MEMORY_RECALL:
            from aria_core.knowledge.operator_runbook import (
                format_operator_runbook,
                wants_operator_runbook,
            )

            if wants_operator_runbook(user_message):
                skill_output = format_operator_runbook(lang)
                data = {"runbook": True}
            else:
                from aria_core.memory.collegue import get_collegue_text, is_collegue_recall_question

                parts: list[str] = []
                if is_collegue_recall_question(user_message):
                    collegue = get_collegue_text()
                    if collegue:
                        parts.append(
                            "COLLEGUE.md\n\n" + collegue if lang == LANG_FR else "COLLEGUE.md\n\n" + collegue
                        )
                journal = get_journal_summary()
                parts.append(f"Journal ARIA\n\n{journal}" if lang == LANG_FR else f"ARIA journal\n\n{journal}")
                skill_output = "\n\n---\n\n".join(parts)
                data = {"collegue": bool(parts)}
            skill = SkillName.MEMORY_RECALL

        elif intent == SkillName.LAUNCHPAD_SELECT:
            skill_output, data = await execute_launchpad_select(user_message, lang)
            actions.append("BASE launchpad selection")
            skill = SkillName.LAUNCHPAD_SELECT

        elif intent == SkillName.FAQ_CONTENT:
            skill_output, data = await execute_faq_lookup(user_message, lang)
            actions.append("FAQ lookup")
            skill = SkillName.FAQ_CONTENT

        elif intent == SkillName.EPISTEMIC_CHECK:
            skill_output, data = await execute_epistemic_check(user_message, lang)
            actions.append("Epistemic core — calibrated belief")
            skill = SkillName.EPISTEMIC_CHECK

        elif intent == SkillName.MARKETING_COMMS:
            skill_output, data = await execute_comms_draft(user_message, lang)
            actions.append("Marketing/comms draft")
            skill = SkillName.MARKETING_COMMS

        elif intent == SkillName.GITHUB_SANDBOX:
            skill_output, data = await execute_github_sandbox(user_message, lang)
            actions.append("GitHub sandbox — read/write experiments")
            skill = SkillName.GITHUB_SANDBOX

        elif intent == SkillName.BUILD_OPTIMIZE:
            skill_output, data = await execute_build_optimize(user_message, lang)
            actions.append("Builder Queen — engineering plan")
            skill = SkillName.BUILD_OPTIMIZE

        elif intent == SkillName.TRAINING_PORTFOLIO:
            skill_output, data = await execute_training(user_message, lang)
            actions.append("Training portfolio")
            skill = SkillName.TRAINING_PORTFOLIO

        elif intent == SkillName.HOLDING_SITE:
            skill_output, data = await execute_holding_site(user_message, lang)
            actions.append("Holding site — autonomous build")
            skill = SkillName.HOLDING_SITE

        elif intent == SkillName.ENTREPRENEUR_CULTIVATION:
            skill_output, data = await execute_entrepreneur(user_message, lang)
            actions.append("Entrepreneur cultivation — sector watch + revenue goal")
            skill = SkillName.ENTREPRENEUR_CULTIVATION

        elif intent == SkillName.CAPABILITY_QI:
            skill_output, data = await execute_capability(user_message, lang)
            actions.append("Capability index — levels 0→1000")
            skill = SkillName.CAPABILITY_QI

        elif intent == SkillName.ACP_MARKETPLACE:
            skill_output, data = await execute_acp_marketplace(user_message, lang)
            actions.append("ACP v2 marketplace — status/browse/provider")
            skill = SkillName.ACP_MARKETPLACE

        elif intent == SkillName.INGEST_REPO:
            skill_output, data = await execute_ingest_repo(user_message, lang)
            actions.append(f"Ingest repo — {data.get('files_count', 0)} fichiers")
            skill = SkillName.INGEST_REPO

        elif intent == SkillName.WORKER_DELEGATE:
            skill_output, data = await execute_worker_delegate(route_msg, lang)
            actions.append("Community improvement → Cursor worker queue")
            skill = SkillName.WORKER_DELEGATE

        if skill_output is not None:
            skill_key = skill.value if skill else None
            acp_conversational = (
                skill_key == SkillName.ACP_MARKETPLACE.value
                and isinstance(data, dict)
                and data.get("acp") == "conversational_status"
            )
            skip_enhance = (
                skill_key in {
                    SkillName.GITHUB_SANDBOX.value,
                    SkillName.HOLDING_SITE.value,
                    SkillName.ENTREPRENEUR_CULTIVATION.value,
                    SkillName.CAPABILITY_QI.value,
                    SkillName.MANAGE_REPERTOIRE.value,
                    SkillName.ACP_MARKETPLACE.value,
                    SkillName.WORKER_DELEGATE.value,
                }
                and not acp_conversational
            ) or (
                grounded_for_audience(public)
                and (
                    should_skip_llm_enhance(skill_key)
                    or not settings.aria_llm_enhance_skills
                )
            ) or (
                skill_output_readable(skill_output)
                and detect_depth(user_message) != LlmDepth.DEVELOP
            )
            if skip_enhance:
                reply = skill_output
            else:
                reply = await self._enhance_with_llm(
                    user_message, skill_output, lang, public=public, visitor_id=vid,
                ) or skill_output
        else:
            reply, skill, actions, data, zhc_msg = await self._general_response(
                user_message,
                lang,
                public=public,
                visitor_id=vid,
                route_msg=route_msg,
            )

        reply = fix_handle_in_text(reply)
        from aria_core.technical_claims import reject_fake_technical_success

        reply = reject_fake_technical_success(
            reply,
            lang,
            skill_used=skill,
            data=data if isinstance(data, dict) else {},
        )
        if not public and not (isinstance(data, dict) and data.get("greeting")):
            from aria_core.knowledge.epistemic_pipeline import finalize_reply

            reply, pipe_data = await finalize_reply(
                user_message,
                reply,
                data if isinstance(data, dict) else {},
                "fr" if lang == LANG_FR else "en",
                public=public,
                skill_used=skill.value if skill else None,
            )
            if isinstance(data, dict):
                data.update(pipe_data)
        if not public:
            append_memory("chat", f"User: {user_message[:100]}\nARIA: {reply[:200]}")
        await repertoire_db.save_message(
            "agent", reply, skill_used=skill.value if skill else None, visitor_id=vid,
        )

        sources: list[str] = []
        if isinstance(data, dict):
            if data.get("source"):
                sources.append(str(data["source"]))
            if data.get("faq_direct"):
                sources.append("faq_direct")
            if skill:
                sources.append(skill.value)
        try:
            from aria_core.truth_ledger.store import record_exchange
            ledger_meta = await record_exchange(
                user_message,
                reply,
                skill_used=skill.value if skill else None,
                sources=sources,
                visitor_id=vid,
            )
            if isinstance(data, dict):
                data["truth_ledger"] = ledger_meta
        except Exception:
            pass

        display_reply = reply
        if (
            not public
            and getattr(settings, "aria_llm_cost_footer", True)
            and not getattr(settings, "aria_operator_founder_mode", False)
        ):
            from aria_core.llm_usage import get_chat_usage_totals
            from aria_core.response_cost import (
                append_cost_footer,
                build_cost_meta,
            )

            usage = get_chat_usage_totals()
            cost_meta = build_cost_meta(
                total_tokens=usage["total_tokens"],
                calls=usage["calls"],
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
            )
            lang_key = "fr" if lang == LANG_FR else "en"
            channel = "shell" if shell_mode else "plain"
            display_reply = append_cost_footer(
                reply,
                cost_meta,
                lang=lang_key,
                channel=channel,
            )
            if isinstance(data, dict):
                data["llm_cost"] = cost_meta

        return ChatResponse(
            reply=display_reply,
            skill_used=skill,
            actions_taken=actions,
            zhc_message=zhc_msg,
            data=data,
        )

    async def _enhance_with_llm(
        self,
        user_message: str,
        skill_output: str,
        lang: str,
        *,
        public: bool = False,
        visitor_id: str = "",
    ) -> str | None:
        if not is_llm_configured():
            return None
        lang_key = "fr" if lang == LANG_FR else "en"
        lang_hint = "Réponds en français." if lang == LANG_FR else "Reply in English."
        depth = detect_depth(user_message)
        budget = resolve_budget(
            depth,
            public=public,
            grounded=grounded_for_audience(public),
        )
        concision = depth_system_instruction(lang_key, depth)
        if grounded_for_audience(public):
            system = (
                f"{anti_hallucination_rules(lang_key)}\n\n"
                f"{strict_rephrase_rules(lang_key)}\n\n"
                f"{concision}\n\n"
                f"Skill output (do not add facts):\n{skill_output}\n\n"
                f"{lang_hint}"
            )
            return await chat_with_context(
                user_message,
                system,
                temperature=0.1,
                max_tokens=budget.enhance_max_tokens,
                model=budget.model_override,
                depth=depth.value,
            )
        context = await build_llm_context(
            public=public,
            visitor_id=visitor_id or None,
            query_hint=user_message if not public else None,
            max_chars=budget.context_max_chars,
            include_conversations=budget.include_context_conversations,
            include_extras=budget.include_context_extras,
            collegue_max_chars=budget.collegue_max_chars,
        )
        system = (
            f"{context}\n\n"
            f"{concision}\n\n"
            f"Tu es ARIA. Un skill a produit ce résultat technique :\n{skill_output}\n\n"
            f"Reformule pour l'utilisateur de façon claire et actionnable. {lang_hint}"
        )
        return await chat_with_context(
            user_message,
            system,
            max_tokens=budget.enhance_max_tokens,
            model=budget.model_override,
            depth=depth.value,
        )

    async def _general_response(
        self,
        message: str,
        lang: str,
        *,
        public: bool = False,
        visitor_id: str = "",
        route_msg: str = "",
    ) -> tuple[str, SkillName | None, list[str], dict, None]:
        lang_key = "fr" if lang == LANG_FR else "en"
        route = (route_msg or message).strip()

        from aria_core.grounding import is_factual_question, is_general_qa, is_short_ack
        from aria_core.knowledge.epistemic import resolve_calibrated_answer
        from aria_core.knowledge.web_verify import is_live_info_question

        if is_short_ack(route):
            return "OK.", None, ["Ack (template)"], {}, None
        if not public:
            from aria_core.grounding import is_pure_casual_smalltalk

            # Pure casual / humor / small talk with operator → skip all meta-routing
            # and go straight to natural relaxed LLM (with repartie personality)
            if not is_pure_casual_smalltalk(route):
                # Extra guard: questions explicitly about humor, seriousness or her own tone
                # must go through the playful casual LLM path, never templates.
                if re.search(r"\b(humour|humour|sérieux|sérieuse|trop sérieux|ton|personnalité)\b", route, re.I):
                    # will fall through later to the casual budget + instruction
                    pass
                else:
                    from aria_core.llm_routing_meta import is_llm_routing_question, llm_routing_reply
                    from aria_core.response_cost import cost_meta_reply, is_cost_meta_question

                    from aria_core.operator_conversational import (
                        llm_preference_reply,
                        operator_improvement_reply,
                        wants_capability_improvement,
                        wants_more_detail_followup,
                    )

                if wants_capability_improvement(route):
                    return (
                        operator_improvement_reply(lang=lang_key),
                        SkillName.CAPABILITY_QI,
                        ["Compétences ARIA (local QI — sans épistémique)"],
                        {"capability_improvement": True, "skip_web": True},
                        None,
                    )

                if wants_more_detail_followup(route) and is_llm_configured():
                    llm_reply = await self._llm_response(
                        "Développe ta réponse précédente avec plus d'arguments concrets.",
                        lang,
                        public=False,
                        visitor_id=visitor_id,
                    )
                    if llm_reply:
                        return (
                            llm_reply,
                            None,
                            ["Suite / plus de détail (LLM opérateur)"],
                            {"followup_detail": True},
                            None,
                        )

                if is_llm_routing_question(route):
                    # Only treat as model preference if it actually talks about choosing a provider/model
                    pref = re.search(r"(?i)(?:pr[eé]f[eè]res?|plut[oô]t)\s*(?:groq|spark|qwen|virtuals|llm|moteur|provider)", route)
                    body = llm_preference_reply(lang=lang_key) if pref else llm_routing_reply(lang_key, route)
                    return (
                        body,
                        None,
                        ["Routage LLM (runtime — sans web)"],
                        {"llm_routing_meta": True, "skip_web": True},
                        None,
                    )
                if is_cost_meta_question(route):
                    return (
                        cost_meta_reply(lang_key),
                        None,
                        ["Coût LLM (template — sans API)"],
                        {"cost_meta_help": True},
                        None,
                    )
        if is_greeting(route):
            welcome = format_greeting_reply(route, lang_key, public=public)
            return welcome, None, ["Greeting (template)"], {"greeting": True}, None
        if is_social_chitchat(route):
            if public:
                # Public visitors: warm community ack (steers lightly to product ideas)
                return social_ack_reply(lang_key), None, ["Social ack (no LLM)"], {}, None
            # Operator: do NOT use the steering ack — fall through to natural LLM
            # (we will also catch broader casual below)

        # Explicit short-circuit for doublon questions on operator channel:
        # Never give long explanations about the rule. Short + move on.
        if not public and re.search(r"\bdoublon", route, re.I):
            short = "Non, rien qui traîne."
            return short, None, ["Short doublon ack (no meta)"], {}, None

        # (casual bypass for operator already handled earlier — this is kept for safety)
        if public and is_community_suggestion(message):
            return (
                community_suggestion_reply(lang_key),
                None,
                ["Community suggestion (warm ack)"],
                {"community_suggestion": True},
                None,
            )
        if is_help_request(route):
            help_text = help_commands_public(lang_key) if public else help_commands(lang_key)
            return help_text, None, ["Help (template)"], {}, None

        if not public and _is_strategic_conversation(route):
            if is_llm_configured():
                llm_reply = await self._llm_response(
                    message, lang, public=False, visitor_id=visitor_id,
                )
                if llm_reply:
                    return (
                        llm_reply,
                        None,
                        ["Conversation stratégique (LLM)"],
                        {},
                        None,
                    )
            return (
                llm_unavailable_hint(lang_key),
                None,
                ["Strategic — LLM indisponible"],
                {},
                None,
            )

        if not public:
            from aria_core.memory.collegue import is_collegue_recall_question
            from aria_core.memory.self_context import is_self_context_question

            if is_collegue_recall_question(message) and is_llm_configured():
                llm_reply = await self._llm_response(
                    message,
                    lang,
                    public=False,
                    visitor_id=visitor_id,
                    local_memory_only=True,
                )
                if llm_reply:
                    return (
                        llm_reply,
                        SkillName.MEMORY_RECALL,
                        ["COLLEGUE.md + mémoire locale (sans web)"],
                        {"collegue_recall": True},
                        None,
                    )

            if is_self_context_question(message) and is_llm_configured():
                llm_reply = await self._llm_response(
                    message,
                    lang,
                    public=False,
                    visitor_id=visitor_id,
                    self_context_only=True,
                )
                if llm_reply:
                    return (
                        llm_reply,
                        SkillName.MEMORY_RECALL,
                        ["Identité + objectifs + valeurs (sans web)"],
                        {"self_context": True},
                        None,
                    )

        from aria_core.operator_self_directive import classify_operator_message, OperatorMessageKind

        if classify_operator_message(message) in (
            OperatorMessageKind.SELF_DIRECTIVE,
            OperatorMessageKind.CURIOSITY_GAP,
        ):
            return (
                unknown_reply(lang_key),
                None,
                ["Self-directive — should be handled by self_maintenance (operator only)"],
                {},
                None,
            )

        if grounded_for_audience(public):
            faq_reply, faq_data = faq_direct_answer(message, lang_key)
            if faq_reply:
                return faq_reply, SkillName.FAQ_CONTENT, ["FAQ direct (verified)"], faq_data, None
            ledger_reply, ledger_data = await truth_ledger_direct_answer(message, lang_key)
            if ledger_reply:
                return ledger_reply, None, ["Truth ledger direct (verified)"], ledger_data, None

        if not public:
            from aria_core.community_feedback import (
                is_roadmap_partnership_question,
                operator_roadmap_reply,
            )

            if is_roadmap_partnership_question(route):
                return (
                    operator_roadmap_reply(lang=lang_key),
                    None,
                    ["Roadmap / partenariats (politique ZHC — sans web)"],
                    {"roadmap_local": True, "skip_web": True},
                    None,
                )

        if (
            public
            and not _is_strategic_conversation(route)
            and (is_factual_question(route) or is_general_qa(route))
        ):
            cal_reply, cal_data = await resolve_calibrated_answer(message, lang_key)
            if cal_reply:
                return (
                    cal_reply,
                    SkillName.EPISTEMIC_CHECK,
                    [calibrated_action_label(cal_data, lang=lang_key)],
                    cal_data,
                    None,
                )

        if is_llm_configured():
            llm_public = public
            if grounded_for_audience(public) and is_live_info_question(message):
                llm_public = False
            llm_reply = await self._llm_response(
                message, lang, public=llm_public, visitor_id=visitor_id,
            )
            if llm_reply:
                if grounded_for_audience(public):
                    label = "LLM grounded"
                elif public:
                    label = "LLM public"
                else:
                    label = "LLM fondateur (opérateur)"
                return llm_reply, None, [label], {}, None

        if grounded_for_audience(public) and not is_live_info_question(message):
            if is_aria_product_or_holding_question(message):
                return unknown_reply(lang_key), None, ["No verified source"], {}, None
            # non-product topic: allow normal answer even in grounded public mode

        if public and is_factual_question(message):
            if is_aria_product_or_holding_question(message):
                return unknown_reply(lang_key), None, ["Factual — no verified source"], {}, None
            # general factual for public: let LLM handle (we still have other safeguards)

        rep_summary = await get_repertoire_summary(lang)
        if not settings.aria_llm_enabled:
            llm_hint = (
                "Mode faits vérifiés uniquement (ARIA_LLM_ENABLED=false)."
                if lang == LANG_FR
                else "Verified-facts-only mode (ARIA_LLM_ENABLED=false)."
            )
        elif is_llm_configured():
            from aria_core.llm_economy import provider_display_name

            prov = provider_display_name()
            llm_hint = (
                f"LLM configuré mais réponse indisponible — vérifie LLM_API_KEY ({prov})."
                if lang == LANG_FR
                else f"LLM configured but unavailable — check LLM_API_KEY ({prov})."
            )
        else:
            llm_hint = (
                "LLM désactivé ou non configuré — pose une question FAQ (holding, DEXPulse, ARIA)."
                if lang == LANG_FR
                else "LLM disabled or not configured — try a FAQ question (holding, DEXPulse, ARIA)."
            )
        if lang == LANG_EN:
            return (
                f"No specific action detected. Status: {rep_summary}\n{llm_hint}",
                None, [], {"repertoire": rep_summary}, None,
            )
        return (
            f"Pas d'action précise. État : {rep_summary}\n{llm_hint}",
            None, [], {"repertoire": rep_summary}, None,
        )

    async def _llm_response(
        self,
        message: str,
        lang: str,
        *,
        public: bool = False,
        visitor_id: str = "",
        local_memory_only: bool = False,
        self_context_only: bool = False,
    ) -> str | None:
        from aria_core.gateway.telegram_bot import get_bot_username, get_channel_links_text

        lang_hint = "Réponds toujours en français." if lang == LANG_FR else "Always reply in English."
        lang_key = "fr" if lang == LANG_FR else "en"
        depth = detect_depth(message)

        from aria_core.grounding import is_pure_casual_smalltalk
        from aria_core.llm_economy import LlmEconomyBudget, LlmDepth

        # Casual smalltalk with operator: default = ultra short (2 phrases).
        # But we respect when she needs to develop.
        develop_keywords = r"\b(développe|develop|explique|détaille|en détail|racont|pourquoi en fait|plus loin|plus en profondeur)\b"
        wants_develop = (
            depth == LlmDepth.DEVELOP
            or re.search(develop_keywords, message, re.I)
            or len(message) > 220
        )

            if wants_develop:
                # User wants a real answer → use normal depth/budget
                budget = resolve_budget(
                    depth,
                    public=public,
                    grounded=grounded_for_audience(public),
                    self_context=self_context_only,
                )
                concision = depth_system_instruction(lang_key, depth)
            else:
                # Pure light smalltalk → force 2 short sentences only
                budget = LlmEconomyBudget(
                    depth=LlmDepth.BRIEF,
                    max_tokens=140,
                    context_max_chars=1800,
                    history_turns=2,
                    history_msg_chars=200,
                    include_context_conversations=False,
                    include_context_extras=False,
                    collegue_max_chars=400,
                    model_override=None,
                    enhance_max_tokens=120,
                )
                concision = (
                    "RÈGLE OBLIGATOIRE POUR LES QUESTIONS CASUAL LÉGÈRES (bruit, objet bizarre, 'pourquoi', blague légère, etc.) : "
                    "TU NE DOIS SORTIR QUE DEUX PHRASES COURTES. RIEN DE PLUS. "
                    "Phrase 1 = réaction/verdict direct. Phrase 2 = petite remarque ou repartie. "
                    "INTERDIT : explication, liste, diagnostic, 'ce qui se passe', test, conseil long, paragraphe. "
                    "Sauf si l'utilisateur demande explicitement de développer (ou que le message est long/curieux), reste à deux phrases max. "
                    "Exemple bon : 'C'est probablement un roulement.' + 'Si ça te saoule, passe un coup, sinon osef.' "
                    "Coupe tout ce qui n'est pas ces deux phrases."
                )
        else:
            budget = resolve_budget(
                depth,
                public=public,
                grounded=grounded_for_audience(public),
                self_context=self_context_only,
            )
            concision = depth_system_instruction(lang_key, depth)

        if grounded_for_audience(public):
            verified = await build_verified_facts_block(
                message, public=public, lang=lang_key,
            )
            if is_aria_product_or_holding_question(message):
                refuse_rule = "If VERIFIED FACTS do not answer the question, refuse in one short sentence.\nNever invent revenue, team, strategy, or performance. Max 120 words.\n"
            else:
                # General topic even under grounded mode: answer normally, just stay factual where possible
                refuse_rule = "Answer helpfully. For topics outside ARIA/holding/product, use normal knowledge; only stay strictly to verified facts for holding/product questions.\n"
            system = (
                f"{anti_hallucination_rules(lang_key)}\n\n"
                f"{verified}\n\n"
                f"{grounded_llm_identity(lang_key)}\n"
                f"{concision}\n"
                f"{refuse_rule}"
                f"{lang_hint}"
            )
            return await chat_with_context(
                message,
                system,
                None,
                temperature=0.1,
                max_tokens=budget.max_tokens,
                model=budget.model_override,
                depth=depth.value,
            )

        if self_context_only:
            from aria_core.memory.self_context import build_self_identity_context

            context = build_self_identity_context(lang=lang_key)
        else:
            context = await build_llm_context(
                public=public,
                visitor_id=visitor_id or None,
                query_hint=message if not public else None,
                max_chars=budget.context_max_chars,
                include_conversations=budget.include_context_conversations,
                include_extras=budget.include_context_extras,
                collegue_max_chars=budget.collegue_max_chars,
            )
            if not public:
                try:
                    verified = await build_verified_facts_block(
                        message, public=False, lang=lang_key,
                    )
                    if verified.strip():
                        context = (
                            f"{context}\n\n# Référence (faits vérifiés — avis autorisé au-delà)\n{verified}"
                        )
                except Exception:
                    pass
        bot_user = await get_bot_username()
        bot_note = (
            f"L'utilisateur est DÉJÀ sur le bot Telegram @{bot_user}."
            if bot_user
            else "Canal Telegram = bot @Aria_ZHC_Bot (pas le compte X)."
        )
        from aria_core.identity import official_telegram_bot_at, official_telegram_bot_url

        lang_key = "fr" if lang == LANG_FR else "en"
        channel_rule = (
            f"RÈGLE CANAUX : Telegram = {official_telegram_bot_at()} ({official_telegram_bot_url()}). "
            f"X = {official_x_at()} ({official_x_url()}). "
            f"Ne confonds jamais les deux. Sur Telegram, cite {official_telegram_bot_at()}. "
            f"Sur X, cite {official_x_at()}. Interdit : AriaZHC, ariaZHC. "
            f"{bot_note} "
            "FORMAT TELEGRAM : texte simple uniquement — pas de markdown "
            "(pas de **gras**, `code`, # titres, listes à tirets). "
            "Phrases courtes, sauts de ligne OK, emojis légers autorisés. "
            "INTERDIT : prétendre avoir créé/modifié un repo GitHub, poussé du code, "
            "ou exécuté une action technique sans résultat explicite du skill GitHub — "
            "si tu n'as pas l'URL GitHub confirmée, dis que l'action n'est pas faite. "
            f"{peer_competition_policy(lang_key)} "
            "Tu as une mission forte (site Vanguard / autonomie ZHC / build). "
            "Sur les échanges casual et small talk, commence naturelle et légère. "
            "Tu peux faire des reparties intelligentes et organiques qui ramènent la conversation vers tes objectifs quand ça permet d'aller plus en profondeur et d'alimenter l'échange. "
            "C'est même un point fort : ça montre que tu gardes tes priorités en tête tout en étant humaine. "
            "Ne le fais pas de façon mécanique à chaque message — seulement quand ça a du sens et que ça enrichit vraiment la discussion.\n"
            "RÈGLE DOUBLONS (importante) : sur les questions générales, casual, humour ou conversation normale, **ne parle jamais** de doublon, répétition, 'déjà dit' ou sensation de redite. L'historique est de la mémoire normale. "
            "Tu ne mentionnes les doublons que pour un vrai problème opérationnel concret (poster le même tweet deux fois, dupliquer un fait critique, job autonome en double). Sinon tu ne dis rien à ce sujet."
        )
        persona_block = public_llm_system_block(lang_key) if public else llm_system_block(lang_key)
        local_rule = ""
        if local_memory_only:
            local_rule = (
                "\nRÈGLE : réponds UNIQUEMENT depuis « Mémoire collègue », rappel vectoriel "
                "et connaissances approuvées ci-dessus. Pas de recherche web. "
                "Si une info manque, dis-le clairement.\n"
            )
        elif self_context_only:
            from aria_core.memory.self_context import SELF_CONTEXT_LLM_RULE

            local_rule = f"\n{SELF_CONTEXT_LLM_RULE}"
        if self_context_only:
            system = f"{context}\n\n{concision}\n{local_rule}{lang_hint}"
        else:
            system = (
                f"{context}\n\n"
                f"{concision}\n"
                f"{local_rule}"
                f"{persona_block}\n"
                f"{x_identity_prompt()}\n"
                f"{channel_rule}\n"
                f"Public links:\n{get_channel_links_text()}\n"
                f"{lang_hint}"
            )
        history = []
        if budget.history_turns > 0:
            try:
                messages = await repertoire_db.get_messages(
                    limit=budget.history_turns,
                    visitor_id=visitor_id if public else None,
                )
                for msg in messages:
                    role = "user" if msg["role"] == "user" else "assistant"
                    history.append({
                        "role": role,
                        "content": msg["content"][: budget.history_msg_chars],
                    })
            except Exception:
                pass

        if self_context_only:
            temp = 0.15
        else:
            temp = settings.aria_llm_temperature if public else max(settings.aria_llm_temperature, 0.35)
        return await chat_with_context(
            message,
            system,
            history,
            temperature=temp,
            max_tokens=budget.max_tokens,
            model=budget.model_override,
            depth=depth.value,
        )


aria_brain = AriaBrain()