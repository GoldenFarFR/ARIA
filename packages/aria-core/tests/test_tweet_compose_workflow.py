import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from aria_core.tweet_compose_workflow import (
    TweetComposePhase,
    _append_handles_to_draft,
    _fallback_draft_text,
    _gather_compose_context,
    _is_handle_addition_request,
    _mark_follow_up_used,
    _normalize_draft_text,
    _operator_wants_tweet_content,
    _parse_schedule,
    _pick_fallback_question,
    _polish_english_tweet,
    _published_parent_key,
    _record_compose_intel,
    _rotation_index,
    _sync_published_intel,
    _wants_compose_start,
    _wants_draft,
    _wants_expand_thought,
    extract_operator_supplied_tweet,
    handle_workflow_message,
    is_tweet_operator_context,
    record_published_intel,
    reset_workflow,
    start_role_coaching_workflow,
    start_compose_workflow,
    wants_role_coaching,
)
from aria_core.x_publication_policy import check_tweet_content


@pytest.fixture(autouse=True)
def isolated_workflow(tmp_path, monkeypatch):
    path = tmp_path / "tweet_compose_workflow.json"
    monkeypatch.setattr("aria_core.tweet_compose_workflow.WORKFLOW_PATH", path)
    monkeypatch.setattr(
        "aria_core.tweet_compose_workflow.operator_tz",
        lambda: ZoneInfo("Europe/Paris"),
    )
    reset_workflow()


def test_extract_operator_supplied_tweet_built_in_public():
    msg = (
        "/x compose — texte exact: Built in public: autonomous ARIA CAO, aria-core, "
        "vector memory (Phases A-D), skills moat ship loop, multi-PC handoff, "
        "Cursor-ARIA 3-voice bridge, truth ledger, DDG-only brain, QI shadow judge, "
        "Grok/Cursor skills moat. Operator in the loop. @GoldenFarFR"
    )
    body = extract_operator_supplied_tweet(msg)
    assert body is not None
    assert body.startswith("Built in public:")
    assert "@GoldenFarFR" in body


def test_tweet_context_blocks_false_github_create():
    msg = (
        "marketing communication: valide tweet avec aria-core et stats GitHub sur capture"
    )
    assert is_tweet_operator_context(msg) is True
    from aria_core.skills.github_skill import looks_like_repo_create

    assert looks_like_repo_create(msg) is False


@pytest.mark.asyncio
async def test_start_compose_with_prevalidated_text(monkeypatch):
    msg = (
        "/x compose — texte exact: Built in public: autonomous ARIA CAO, aria-core, "
        "vector memory (Phases A-D), skills moat ship loop, multi-PC handoff, "
        "Cursor-ARIA 3-voice bridge, truth ledger, DDG-only brain, QI shadow judge, "
        "Grok/Cursor skills moat. Operator in the loop. @GoldenFarFR"
    )
    out = await start_compose_workflow(operator_context=msg)
    assert "validé par l'opérateur" in out.lower() or "Built in public" in out
    assert "sharpest question" not in out.lower()


def test_proposal_message_starts_compose_workflow():
    msg = (
        "propose moi un tweet a publier qui t'aiderai a mieux comprendre tes objectif"
    )
    assert _wants_compose_start(msg) is True


def test_role_coaching_operator_messages():
    assert wants_role_coaching("Concernant ton identité et ton travail comme zhc") is True
    assert (
        wants_role_coaching(
            "Es-ce que tu des questions concernant ton travail comme PDG de vanguard ?"
        )
        is True
    )


@pytest.mark.asyncio
async def test_compose_flow_to_approval(monkeypatch):
    async def fake_learn(_ctx: str = "") -> str:
        return "Apprendre le timing des signaux memecoin"

    async def fake_draft(state) -> str:
        return "Tweet test ARIA — signaux memecoin."

    monkeypatch.setattr(
        "aria_core.tweet_compose_workflow._propose_learning",
        fake_learn,
    )
    monkeypatch.setattr(
        "aria_core.tweet_compose_workflow._draft_tweet",
        fake_draft,
    )

    r1 = await start_compose_workflow()
    assert "apprendre" in r1.lower() or "Apprendre" in r1

    r2 = await handle_workflow_message("crée un tweet")
    assert "non publié" in r2.lower()
    assert "ajouter" in r2.lower()

    r3 = await handle_workflow_message("non")
    assert "publier" in r3.lower()
    assert "oui" in r3.lower()


def test_parse_schedule_maintenant():
    when = _parse_schedule("maintenant")
    assert when is not None
    assert when.tzinfo == timezone.utc


def test_wants_draft_propose_moi():
    assert _wants_draft("Mais propose moi un tweet") is True
    assert _wants_draft("propose un tweet") is True
    assert _operator_wants_tweet_content("Oui mais écrit moi le tweet") is True
    assert _operator_wants_tweet_content(
        "Si tu devais publié une pensée ou une question sa serait quoi ?"
    ) is True
    assert _operator_wants_tweet_content("Tu réponds pas à ma question la") is True


def test_wants_expand_thought():
    assert _wants_expand_thought("Ok développe ta pensée") is True


def test_normalize_draft_strips_preamble():
    raw = "Voici le tweet :\nJe suis ARIA, agente ZHC — et vous ?"
    assert "ARIA" in _normalize_draft_text(raw)


def test_fallback_draft_not_empty():
    text = _fallback_draft_text({"learn_topic": "autonomie ZHC"})
    assert len(text) > 40
    assert "?" in text
    assert check_tweet_content(text)[0]


def test_fallback_questions_rotate():
    q1 = _pick_fallback_question()
    assert "?" in q1
    assert _rotation_index(7) in range(7)


def test_record_compose_intel(tmp_path, monkeypatch):
    path = tmp_path / "intel.json"
    monkeypatch.setattr("aria_core.tweet_compose_workflow.INTEL_PATH", path)
    _record_compose_intel(draft="First unique question?")
    _record_compose_intel(draft="Second different angle?")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["recent_drafts"]) == 2
    assert data["recent_drafts"][0].startswith("Second")


def test_sync_published_intel_from_ledger(tmp_path, monkeypatch):
    intel_path = tmp_path / "intel.json"
    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setattr("aria_core.tweet_compose_workflow.INTEL_PATH", intel_path)
    monkeypatch.setattr("aria_core.x_publication_policy.LEDGER_PATH", ledger_path)
    from aria_core.x_publication_policy import _save_ledger

    _save_ledger({
        "posts": [{
            "at": "2026-06-19T08:00:00+00:00",
            "kind": "tweet",
            "text": "Already asked on X — what metric matters most?",
            "tweet_id": "99",
        }],
        "estimated_spend_usd": 0.015,
    })
    synced = _sync_published_intel()
    assert synced[0]["tweet_id"] == "99"
    assert "Already asked" in synced[0]["text"]
    assert synced[0]["follow_up_used"] is False


def test_mark_follow_up_used(tmp_path, monkeypatch):
    intel_path = tmp_path / "intel.json"
    monkeypatch.setattr("aria_core.tweet_compose_workflow.INTEL_PATH", intel_path)
    record_published_intel(text="Parent question on X?", tweet_id="abc", at="2026-06-19T09:00:00+00:00")
    key = _published_parent_key({"tweet_id": "abc", "at": "2026-06-19T09:00:00+00:00"})
    _mark_follow_up_used(key)
    data = json.loads(intel_path.read_text(encoding="utf-8"))
    assert data["published_tweets"][0]["follow_up_used"] is True


@pytest.mark.asyncio
async def test_gather_compose_context_lists_published(tmp_path, monkeypatch):
    intel_path = tmp_path / "intel.json"
    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setattr("aria_core.tweet_compose_workflow.INTEL_PATH", intel_path)
    monkeypatch.setattr("aria_core.x_publication_policy.LEDGER_PATH", ledger_path)
    from aria_core.x_publication_policy import _save_ledger

    _save_ledger({
        "posts": [{
            "at": "2026-06-19T08:00:00+00:00",
            "kind": "tweet",
            "text": "What should I learn from ZHC operators first?",
            "tweet_id": "42",
        }],
        "estimated_spend_usd": 0.015,
    })

    async def fake_count(_since, *, source=None):
        return 0

    monkeypatch.setattr(
        "aria_core.knowledge.cognitive.count_approved_since",
        fake_count,
    )

    context = await _gather_compose_context()
    assert "Déjà publié" in context
    assert "What should I learn" in context
    assert "NE JAMAIS" in context


@pytest.mark.asyncio
async def test_propose_learning_system_prompt_excludes_trading_autonomy(monkeypatch):
    # Root cause d'un vrai incident (10/07) : ARIA a dit en Telegram vouloir "décider
    # seule les allocations de trading" -- traçable a ce system prompt qui disait
    # "décider seule (build, marketing, priorisation)" sans exclure explicitement le
    # trading/capital réel. Verrouille que l'exclusion reste dans le prompt envoyé au LLM.
    import aria_core.tweet_compose_workflow as tcw

    captured = {}

    async def fake_chat(user, system, **kw):
        captured["system"] = system
        return "sujet bidon"

    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)
    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    async def fake_context():
        return ""

    monkeypatch.setattr(tcw, "_gather_compose_context", fake_context)
    monkeypatch.setattr(tcw, "_record_compose_intel", lambda **kw: None)

    async def fake_store(**kw):
        return None

    monkeypatch.setattr(tcw, "_store_compose_learning", fake_store)

    await tcw._propose_learning()

    system = captured["system"]
    assert "JAMAIS sur le trading ou l'allocation" in system
    assert "TOUJOURS humaine" in system


@pytest.mark.asyncio
async def test_polish_english_removes_french_learn_topic():
    mixed = (
        "I'm ARIA – new ZHC agent at Vanguard. Exploring: Approfondir la narrative "
        "Aria Vanguard ZHC et l'autonomie ZHC.. What do you expect from a holding AI like me?"
    )
    polished = await _polish_english_tweet(mixed)
    assert check_tweet_content(polished)[0]
    assert "Approfondir" not in polished


@pytest.mark.asyncio
async def test_start_with_propose_tweet_auto_drafts(monkeypatch):
    async def fake_learn(_ctx: str = "") -> str:
        return "Apprendre agent ZHC"

    async def fake_draft(_state) -> str:
        return "Quelle priorité pour une IA holding en 2026 ?"

    monkeypatch.setattr("aria_core.tweet_compose_workflow._propose_learning", fake_learn)
    monkeypatch.setattr("aria_core.tweet_compose_workflow._draft_tweet", fake_draft)

    msg = (
        "Propose moi un tweet a publié qui t'aidera a être un meilleur agent ZHC "
        "sous forme de question"
    )
    reply = await start_compose_workflow(operator_context=msg)
    assert "Brouillon" in reply
    assert "priorité" in reply.lower() or "?" in reply
    assert "crée un tweet" not in reply.lower()


@pytest.mark.asyncio
async def test_ecrit_moi_le_tweet_in_workflow(monkeypatch):
    async def fake_learn(_ctx: str = "") -> str:
        return "Topic"

    async def fake_draft(_state) -> str:
        return "Question ARIA sur mon rôle ZHC ?"

    monkeypatch.setattr("aria_core.tweet_compose_workflow._propose_learning", fake_learn)
    monkeypatch.setattr("aria_core.tweet_compose_workflow._draft_tweet", fake_draft)

    await start_compose_workflow()
    reply = await handle_workflow_message("Oui mais écrit moi le tweet")
    assert "Brouillon" in reply
    assert "?" in reply or "ARIA" in reply
    assert "Compris — pour le tweet" not in reply


@pytest.mark.asyncio
async def test_propose_tweet_produces_non_empty_draft(monkeypatch):
    async def fake_learn(_ctx: str = "") -> str:
        return "Approfondir narrative ZHC"

    async def fake_draft(_state) -> str:
        return ""

    monkeypatch.setattr("aria_core.tweet_compose_workflow._propose_learning", fake_learn)
    monkeypatch.setattr("aria_core.tweet_compose_workflow._draft_tweet", fake_draft)

    await start_compose_workflow()
    reply = await handle_workflow_message("Mais propose moi un tweet")
    assert "Brouillon" in reply
    assert "?" in reply
    assert "Souhaitez-vous ajouter" in reply


@pytest.mark.asyncio
async def test_expand_thought_before_draft(monkeypatch):
    async def fake_learn(_ctx: str = "") -> str:
        return "Topic court"

    async def fake_expand(state, msg: str) -> str:
        state["learn_topic"] = "Pensée développée sur mon rôle ZHC."
        return "💭 Voici comment je développe ma pensée"

    monkeypatch.setattr("aria_core.tweet_compose_workflow._propose_learning", fake_learn)
    monkeypatch.setattr("aria_core.tweet_compose_workflow._expand_learn_topic", fake_expand)

    await start_compose_workflow()
    reply = await handle_workflow_message("Ok développe ta pensée")
    assert "développe" in reply.lower() or "💭" in reply


@pytest.mark.asyncio
async def test_style_guidance_acknowledged_in_workflow(monkeypatch):
    async def fake_questions(_msg: str) -> str:
        return "Quelles priorités marketing cette semaine ?"

    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: False)
    monkeypatch.setattr(
        "aria_core.tweet_compose_workflow._propose_role_questions",
        fake_questions,
    )

    await start_role_coaching_workflow("As-tu des questions sur ton travail comme PDG ?")
    feedback = (
        "Peut être soit plus personnel et moins direct, les gens ne te connaissent pas"
    )
    reply = await handle_workflow_message(feedback)
    assert "Noté. Dis" not in reply
    assert "personnel" in reply.lower() or "Compris" in reply
    assert "connaiss" in reply.lower() or "direct" in reply.lower()
    assert "crée un tweet" in reply.lower() or "propose un tweet" in reply.lower()


def test_handle_addition_request_detection():
    assert _is_handle_addition_request("+veille") is True
    assert _is_handle_addition_request("@holding") is True
    assert _is_handle_addition_request("ajoute les alias +veille") is True
    assert _is_handle_addition_request("plus personnel") is False


def test_append_handles_plus_veille():
    draft = "Je suis ARIA — nouvelle agente ZHC."
    new_draft, applied = _append_handles_to_draft(draft, "+veille")
    assert applied is True
    assert "@solvrbot" in new_draft
    assert "@grok" in new_draft


@pytest.mark.asyncio
async def test_non_auto_polishes_french_draft(monkeypatch):
    async def fake_learn(_ctx: str = "") -> str:
        return "Approfondir la narrative ZHC"

    async def fake_draft(_state) -> str:
        return (
            "I'm ARIA at Vanguard. Exploring: Approfondir la narrative ZHC. "
            "What do you expect?"
        )

    monkeypatch.setattr("aria_core.tweet_compose_workflow._propose_learning", fake_learn)
    monkeypatch.setattr("aria_core.tweet_compose_workflow._draft_tweet", fake_draft)

    await start_compose_workflow()
    await handle_workflow_message("crée un tweet")
    reply = await handle_workflow_message("non")
    assert "oui" in reply.lower() or "corrigé" in reply.lower()
    assert "Approfondir" not in reply or "corrigé" in reply.lower()


@pytest.mark.asyncio
async def test_add_handles_in_compose_workflow(monkeypatch):
    async def fake_learn(_ctx: str = "") -> str:
        return "Topic ZHC"

    async def fake_draft(_state) -> str:
        return "Je suis ARIA — nouvelle agente ZHC."

    monkeypatch.setattr("aria_core.tweet_compose_workflow._propose_learning", fake_learn)
    monkeypatch.setattr("aria_core.tweet_compose_workflow._draft_tweet", fake_draft)

    await start_compose_workflow()
    await handle_workflow_message("crée un tweet")
    reply = await handle_workflow_message("ajoute les alias +veille")
    assert "@solvrbot" in reply
    assert "Mentions X ajoutées" in reply or "mis à jour" in reply


def test_parse_schedule_hour():
    when = _parse_schedule("18h30")
    assert when is not None
    paris = when.astimezone(ZoneInfo("Europe/Paris"))
    assert paris.hour == 18
    assert paris.minute == 30


# ── 19/07 -- expiration du workflow (incident réel : bloqué depuis minuit, a englouti
# au moins deux messages opérateur sans rapport, dont "Ton portefeuille est composé de
# quoi ?") ──────────────────────────────────────────────────────────────────────────


def test_is_stale_true_when_updated_at_missing():
    from aria_core.tweet_compose_workflow import _is_stale

    assert _is_stale({"phase": "add_more"}) is True


def test_is_stale_false_when_recently_updated():
    from aria_core.tweet_compose_workflow import _is_stale

    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert _is_stale({"phase": "add_more", "updated_at": recent}) is False


def test_is_stale_true_past_threshold():
    from aria_core.tweet_compose_workflow import _WORKFLOW_STALE_MINUTES, _is_stale

    old = (
        datetime.now(timezone.utc) - timedelta(minutes=_WORKFLOW_STALE_MINUTES + 1)
    ).isoformat()
    assert _is_stale({"phase": "add_more", "updated_at": old}) is True


def test_save_stamps_updated_at():
    from aria_core.tweet_compose_workflow import _load, _save

    _save({"phase": TweetComposePhase.IDLE.value, "draft": "", "history": []})
    reloaded = _load()
    assert "updated_at" in reloaded
    # Doit être parseable et récent.
    stamp = datetime.fromisoformat(reloaded["updated_at"])
    assert datetime.now(timezone.utc) - stamp < timedelta(seconds=10)


@pytest.mark.asyncio
async def test_stuck_legacy_workflow_expires_and_releases_unrelated_message():
    """Reproduit l'incident réel 19/07 : un workflow oublié en phase add_more, sans
    updated_at (format antérieur à ce correctif -- exactement l'état trouvé bloqué en
    prod), doit se réinitialiser tout seul et laisser passer une question totalement
    hors sujet vers le routage normal (None), au lieu de l'absorber dans le brouillon."""
    from aria_core.tweet_compose_workflow import WORKFLOW_PATH

    legacy_state = {
        "phase": TweetComposePhase.ADD_MORE.value,
        "mode": "learn",
        "draft": "Testing fixed-weight scoring on BASE launchpads with public feeds.",
        "learn_topic": "scoring pondéré des launchpads",
        "operator_notes": "un message opérateur totalement différent, collé ici",
        "history": [{"at": "2026-07-19T00:08:48+00:00", "note": "draft created"}],
    }
    WORKFLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    WORKFLOW_PATH.write_text(json.dumps(legacy_state), encoding="utf-8")

    reply = await handle_workflow_message("Ton portefeuille est composé de quoi ?")
    assert reply is None

    from aria_core.tweet_compose_workflow import _load

    assert _load()["phase"] == TweetComposePhase.IDLE.value


@pytest.mark.asyncio
async def test_expired_dated_workflow_resets_before_processing():
    """Même scénario mais avec un updated_at explicitement ancien (>seuil) plutôt
    qu'absent -- couvre le chemin de comparaison de dates, pas seulement le fail-safe
    sur absence de champ."""
    from aria_core.tweet_compose_workflow import _WORKFLOW_STALE_MINUTES, _save

    old = (
        datetime.now(timezone.utc) - timedelta(minutes=_WORKFLOW_STALE_MINUTES + 5)
    ).isoformat()
    state = {
        "phase": TweetComposePhase.AWAIT_APPROVAL.value,
        "draft": "Some old draft from a much earlier session.",
        "history": [],
    }
    _save(state)
    # _save() vient d'écraser updated_at avec "maintenant" -- on le force ensuite à
    # une valeur périmée pour isoler précisément le chemin testé.
    from aria_core.tweet_compose_workflow import WORKFLOW_PATH

    state["updated_at"] = old
    WORKFLOW_PATH.write_text(json.dumps(state), encoding="utf-8")

    reply = await handle_workflow_message("est-ce que tu as mangé aujourd'hui ?")
    assert reply is None


@pytest.mark.asyncio
async def test_fresh_workflow_still_absorbs_next_message():
    """Non-régression : un workflow actif récemment (dans les _WORKFLOW_STALE_MINUTES)
    continue de fonctionner normalement -- l'expiration ne casse pas l'usage courant."""
    await start_compose_workflow()
    reply = await handle_workflow_message("crée un tweet")
    assert reply is not None
    reply2 = await handle_workflow_message("quelque chose de totalement différent")
    assert reply2 is not None