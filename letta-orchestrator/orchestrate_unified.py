"""
ARIA unifiée — un seul langage naturel : cerveau aria-core + outils ouvrier.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys

from aria_config import ARIA_REPO_ROOT
from ouvrier_acp_direct import try_acp_workflow_direct
from ouvrier_instant import instant_reply, is_simple_exchange
from ouvrier_memory import bootstrap_aria_core_runtime, memory_status_line, preflight_memory_context
from ouvrier_coding_mode import should_debranch, strip_coding_triggers
from ouvrier_runner import provider_label, run_ouvrier
from ouvrier_session import enrich_continuation, is_continuation, load_session, save_session, wants_opinion
from ouvrier_trace import is_verbose, set_verbose, trace
from ouvrier_vision import build_image_context, direct_image_reply, wants_image_context

# Réutilise preflights Telegram + affichage depuis orchestrate_ouvrier
from orchestrate_ouvrier import (  # noqa: E402
    CONFIG_PATH,
    _complete_turn,
    _needs_bootstrap,
    bootstrap,
    display_ouvrier_output,
    preflight_acp_context,
    preflight_notification_status,
    preflight_preuve,
    preflight_telegram_activate,
    preflight_telegram_notifications,
    preflight_telegram_ping,
)

_OUVRIER_OPS_RE = re.compile(
    r"(?i)\b(?:"
    r"implément|implement|fix|corrige|commit|push|pytest|handoff|pending|"
    r"aria-worker|download|inbox|patch_vault|build-local|écris|ecris|"
    r"modifie|exécute|execute|run_powershell|read_repo|write_repo|"
    r"déploie|deploy|git\s|fichier|code\s"
    r")\b"
)


def _needs_ouvrier_execution(message: str, *, brain_skill: str | None) -> bool:
    if brain_skill:
        return False
    if _OUVRIER_OPS_RE.search(message or ""):
        return True
    if _needs_bootstrap(message):
        return True
    return False


def _bootstrap_brain_runtime() -> None:
    """Cerveau complet — LLM + mémoire (≠ preflight ouvrier sans LLM)."""
    import os

    bootstrap_aria_core_runtime()
    os.environ.setdefault("ARIA_LLM_ENABLED", "true")
    os.environ.setdefault("LLM_PROVIDER", os.environ.get("LLM_PROVIDER", "grok"))
    os.environ.setdefault("LLM_MODEL", os.environ.get("LLM_MODEL", "grok-4.3"))
    groq = os.environ.get("GROQ_API_KEY") or os.environ.get("LLM_API_KEY") or ""
    if groq:
        os.environ.setdefault("LLM_API_KEY", groq)

    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime
    from pathlib import Path

    data_dir = Path(os.environ["DATA_DIR"])
    configure_test_runtime(
        data_dir=data_dir,
        settings=AriaRuntimeSettings(
            aria_vector_memory=True,
            aria_memory_arbitrator=True,
            aria_ddg_search_cache=True,
            aria_public_mode=False,
            aria_llm_enabled=True,
            llm_provider=os.environ.get("LLM_PROVIDER", "grok"),
            llm_model=os.environ.get("LLM_MODEL", "grok-4.3"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            aria_autonomous=True,
        ),
    )


async def _run_brain(message: str) -> tuple[str, str | None, list[str]]:
    _bootstrap_brain_runtime()
    from aria_core import repertoire_db
    from aria_core.brain import aria_brain
    from aria_core.locale import LANG_FR

    try:
        await repertoire_db.init_repertoire_db()
    except Exception:
        pass
    result = await aria_brain.process(
        message.strip(),
        lang=LANG_FR,
        visitor_id="shell-sylvain",
        public_mode=False,
    )
    skill = result.skill_used.value if result.skill_used else None
    actions = list(result.actions_taken or [])
    return (result.reply or "").strip(), skill, actions


def main() -> None:
    parser = argparse.ArgumentParser(description="ARIA unifiée (cerveau + ouvrier)")
    parser.add_argument("--message", required=True)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    if args.quiet:
        set_verbose(False)
    elif args.verbose:
        set_verbose(True)

    if not CONFIG_PATH.is_file():
        sys.exit("[Erreur] ouvrier_config.json absent — setup-ouvrier.py")

    user_msg = args.message
    session = load_session()
    effective = enrich_continuation(user_msg) if is_continuation(user_msg) else user_msg
    image_block, image_path, image_analysis = build_image_context(user_msg, session)
    if image_block:
        effective = f"{image_block}\n\n{effective}"

    direct_img = (
        direct_image_reply(user_msg, image_analysis or "", image_path or "")
        if image_analysis and image_path
        else None
    )
    if direct_img:
        _complete_turn(user_msg, direct_img, image_path=image_path)
        return

    if (
        is_simple_exchange(user_msg)
        and not is_continuation(user_msg)
        and not wants_image_context(user_msg, session)
    ):
        _complete_turn(user_msg, instant_reply(user_msg))
        return

    acp_direct = try_acp_workflow_direct(effective)
    if acp_direct:
        _complete_turn(user_msg, acp_direct, image_path=image_path)
        return

    from aria_core.gateway.local_commands import execute_local_command, is_local_command

    if is_local_command(user_msg):
        _bootstrap_brain_runtime()
        try:
            local_out = asyncio.run(execute_local_command(user_msg, lang="fr"))
        except Exception as exc:
            sys.exit(f"[Erreur] commande locale: {exc}")
        if local_out:
            reply, _data = local_out
            print("--- ARIA-UNIFIÉE (commande locale) ---", file=sys.stderr)
            _complete_turn(user_msg, reply, image_path=image_path)
            return

    for tag, handler in (
        ("mute", preflight_telegram_notifications),
        ("enable", preflight_telegram_activate),
        ("status", preflight_notification_status),
        ("ping", preflight_telegram_ping),
        ("preuve", preflight_preuve),
    ):
        direct = handler(user_msg)
        if direct and not re.search(r"(?i)^\s*(oui|ok|yes)\s*$", user_msg):
            print(f"--- ARIA-UNIFIÉE ({tag}) ---", file=sys.stderr)
            if tag in ("mute", "enable") and not is_verbose():
                _complete_turn(user_msg, direct.splitlines()[0] + "\n\n" + direct, image_path=image_path)
            else:
                _complete_turn(user_msg, direct, image_path=image_path)
            return

    skip_brain, coding_pure = should_debranch(
        effective, needs_bootstrap=_needs_bootstrap(user_msg)
    )
    brain_reply, brain_skill, brain_actions = "", None, []

    if not skip_brain:
        trace("moteur", "Cerveau aria-core (skills + mémoire)")
        trace("preflight", memory_status_line())
        try:
            brain_reply, brain_skill, brain_actions = asyncio.run(_run_brain(effective))
        except Exception as exc:
            trace("fallback", f"cerveau KO ({exc}) → ouvrier")
    else:
        trace("débranchement", "Cerveau sauté → ouvrier Grok coding pur")

    if (
        brain_reply
        and not skip_brain
        and not _needs_ouvrier_execution(user_msg, brain_skill=brain_skill)
    ):
        tag = f"cerveau/{brain_skill}" if brain_skill else "cerveau"
        print(f"--- ARIA-UNIFIÉE ({tag}) ---", file=sys.stderr)
        if brain_actions:
            trace("resultat", ", ".join(brain_actions[:4]))
        _complete_turn(user_msg, brain_reply, image_path=image_path)
        return

    needs_ops = _needs_ouvrier_execution(user_msg, brain_skill=brain_skill)
    if not skip_brain and not needs_ops:
        _complete_turn(user_msg, brain_reply or "(pas de réponse)", image_path=image_path)
        return

    trace_label = "débranchement Grok" if coding_pure else "Ops code/repo → ouvrier"
    trace("fallback", trace_label)
    prompt = strip_coding_triggers(effective) if coding_pure else effective
    memory_block = preflight_memory_context(user_msg)
    if memory_block:
        prompt = f"{memory_block}\n\n{prompt}"
    if brain_reply and not coding_pure:
        prompt = (
            f"[Contexte cerveau — skill={brain_skill or 'aucun'}]\n{brain_reply}\n\n"
            f"Exécute maintenant ce que Sylvain demande (outils repo).\n\n{prompt}"
        )
    acp_block = preflight_acp_context(effective)
    if acp_block:
        prompt = f"{acp_block}\n\n{prompt}"
    elif wants_opinion(effective):
        prompt = "Sylvain demande ton AVIS — lis le repo puis réponds.\n\n" + prompt
    if _needs_bootstrap(user_msg):
        prompt = bootstrap(user_msg, memory_block or "")

    engine = provider_label()
    tag = f"ouvrier-coding/{engine}" if coding_pure else f"ouvrier/{engine}"
    print(f"--- ARIA-UNIFIÉE ({tag}) ---", file=sys.stderr)
    try:
        reply = run_ouvrier(prompt, coding_pure=coding_pure)
        _complete_turn(user_msg, reply, image_path=image_path)
    except Exception as exc:
        if brain_reply:
            _complete_turn(user_msg, brain_reply, image_path=image_path)
        else:
            sys.exit(f"[Erreur] ARIA unifiée: {exc}")


if __name__ == "__main__":
    main()