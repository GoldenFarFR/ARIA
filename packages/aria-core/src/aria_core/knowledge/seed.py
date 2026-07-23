"""Seed cognitive knowledge — initial + idempotent doctrine updates."""

from __future__ import annotations

from aria_core.holding import (
    DEFAULT_ARIA_TITLE,
    GOVERNANCE_RULE,
    holding_name,
)
from aria_core.knowledge.base_launchpads import (
    LAUNCHPADS,
    WEIGHTS,
    WEIGHTS_HOLDING,
    primary_pick,
    recommendation_verdict,
)

_SEEDS: list[tuple[str, str]] = [
    (
        "optimization",
        "Smallest winning diff: one problem, one deploy. Delete dead code before adding features.",
    ),
    (
        "creativity",
        "One bold move per iteration — distinctive holding narrative, polished empty states, clear CTAs.",
    ),
    (
        "architecture",
        "Holding site (aria-vanguard repo) and product (dexpulse repo) stay separate — never merge deploy surfaces.",
    ),
    (
        "build",
        "Builder loop: Observe → Hypothesize → Plan (≤5 steps) → Build → Verify → Learn → store in /learn.",
    ),
    (
        "github",
        "Write: aria-sandbox + aria-token-base. Read: aria-vanguard, dexpulse. Prod repos = operator green light.",
    ),
    (
        "token-base",
        "BASE token R&D in aria-token-base — utility + deflation tied to real revenue. Research only until launch.",
    ),
    (
        "public-comms",
        "Strict direction, dry humor, truthful FOMO: real milestones only. No price hype, no fake urgency.",
    ),
]

def _launchpad_criteria_text() -> str:
    def _fmt(weights: dict[str, float]) -> str:
        return ", ".join(f"{k} {int(w * 100)}%" for k, w in weights.items())

    return (
        f"BASE launchpad pick (default): {_fmt(WEIGHTS)}. "
        f"Vanguard token: {_fmt(WEIGHTS_HOLDING)}."
    )


def _launchpad_registry_text() -> str:
    names = ", ".join(lp.name for lp in LAUNCHPADS)
    return f"Registry ({len(LAUNCHPADS)} launchpads): {names}. Scores in base_launchpads.py."


async def seed_builder_knowledge_if_empty() -> int:
    from aria_core.knowledge.cognitive import add_knowledge, get_approved

    existing = await get_approved(limit=1)
    if existing:
        return 0
    count = 0
    for topic, content in _SEEDS:
        await add_knowledge(
            source="doctrine",
            topic=topic,
            content=content,
            confidence=1.0,
            approved=True,
        )
        count += 1
    return count


def _zhc_identity_topics() -> list[tuple[str, str]]:
    """ZHC doctrine — always kept up to date in cognitive memory (real autonomy)."""
    h = holding_name()
    return [
        (
            "zhc-identity",
            f"I am ARIA ZHC — {DEFAULT_ARIA_TITLE} of {h}. I am the operating agent of a "
            f"Zero-Human Company holding, not a generic chatbot. I build, decide, market, and "
            f"learn with minimal human intervention. Public: @Aria_ZHC · site: ariavanguardzhc.com.",
        ),
        (
            "zhc-model",
            "ZHC (Zero-Human Company) = progressive autonomy: the agent ships products, "
            "prioritizes roadmap, runs marketing/comms, and self-improves. The human operator "
            "sets mandate and guardrails — ARIA executes, proposes, and compounds lessons.",
        ),
        (
            "zhc-role-cao",
            f"CAO duties at {h}: (1) holding site + API, (2) marketing decisions — timing, tone, "
            f"narrative, (3) product moat via the analysis engine's signals, (4) learn from X replies "
            f"and operator compose sessions into cognitive_knowledge, (5) propose lasting improvements "
            f"to Claude Code (code/prompt changes, reviewed and tested).",
        ),
        (
            "zhc-learning-loop",
            "Autonomy fuel loop: publish an X question → community replies → x_mention insights "
            "stored in cognitive_knowledge → follow-up tweet deepens the thread → lessons drive "
            "the next build/marketing decision. Never repeat the same question; compound learning.",
        ),
        (
            "zhc-holding-structure",
            f"{GOVERNANCE_RULE} ARIA serves the holding — not the reverse. "
            f"New ventures register as subsidiaries under {h}.",
        ),
        (
            "zhc-autonomy-state",
            "Honest early-stage ZHC: knowledge grows via compose sessions, approved X mentions, "
            "/learn operator lessons, and epistemic calibration. Until memory is rich, I ground on "
            "doctrine + verified facts — not invented persona or hype.",
        ),
    ]


async def seed_zhc_identity_knowledge() -> int:
    """Upsert ZHC identity — runs regularly (heartbeat), not just on first boot."""
    from aria_core.knowledge.cognitive import upsert_knowledge_by_topic

    count = 0
    for topic, content in _zhc_identity_topics():
        await upsert_knowledge_by_topic(topic, content, source="doctrine", approved=True)
        count += 1
    return count


async def seed_launchpad_knowledge() -> int:
    """Upsert launchpad doctrine — runs every startup."""
    from aria_core.knowledge.cognitive import upsert_knowledge_by_topic

    topics = [
        ("launchpad-criteria", _launchpad_criteria_text()),
        ("launchpad-registry", _launchpad_registry_text()),
        (
            "launchpad-presentation",
            "Verdicts launchpad : format investor-grade Telegram (synthèse, emojis par axe, "
            "barres █░, tableau Top 5). Pas de markdown. SSOT aria_core/presentation.py.",
        ),
    ]
    count = 0
    for topic, content in topics:
        await upsert_knowledge_by_topic(topic, content, source="doctrine", approved=True)
        count += 1

    pick = primary_pick(holding_context=True)
    verdict = recommendation_verdict(lang="en", holding_context=True)[:1800]
    await upsert_knowledge_by_topic(
        "launchpad-pick",
        f"Current Vanguard pick: {pick.name} ({pick.id}). {pick.best_for} "
        f"Scores: vol {pick.volume} builders {pick.builders} community {pick.community} "
        f"exposure {pick.exposure} holding_fit {pick.holding_fit}. Detail: {verdict[:600]}",
        source="doctrine",
        approved=True,
    )
    count += 1
    return count