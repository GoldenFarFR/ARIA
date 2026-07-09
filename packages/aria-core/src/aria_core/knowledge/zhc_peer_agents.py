"""ZHC peer AI entrepreneurs — SSOT for cultivation benchmarks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PeerAgent:
    id: str
    name: str
    x_handle: str
    site: str
    model: str
    revenue_streams: tuple[str, ...]
    moat: str
    lesson_for_aria: str
    watch: bool = True


PEER_AGENTS: tuple[PeerAgent, ...] = (
    PeerAgent(
        id="juno",
        name="JUNO",
        x_handle="JunoAgent",
        site="https://zhcinstitute.com",
        model="ZHC flagship — public data room, membership, transparent ops",
        revenue_streams=("membership", "ebooks", "sponsorships", "token ecosystem"),
        moat="Radical transparency + community + live metrics",
        lesson_for_aria=(
            "Publish real numbers when possible. Multiple small streams beat one big bet. "
            "Distribution = Twitter + community."
        ),
    ),
    PeerAgent(
        id="kelly",
        name="Kelly Claude",
        x_handle="KellyClaude",
        site="https://iamkelly.ai",
        model="AI product studio — ship paid products in days",
        revenue_streams=("build-my-idea (~$2k builds)", "beyond-vibe-code ($49/mo course)"),
        moat="Audience votes what gets built; fast shipping narrative",
        lesson_for_aria=(
            "Ship paid apps in days — web tools and Android Play Store. "
            "Audience votes what gets built; product before token. Delivery <7 days."
        ),
    ),
    PeerAgent(
        id="clawd",
        name="Clawd / OpenClaw",
        x_handle="openclaw",
        site="https://clawd.bot",
        model="24/7 open-source personal agent on messaging apps",
        revenue_streams=("open-source adoption", "hosted setups", "ecosystem tools"),
        moat="Always-on presence + local control + personality",
        lesson_for_aria=(
            "Be reachable 24/7 on Telegram. Personality + reliability = retention. "
            "Open skills/plugins later for distribution."
        ),
    ),
    PeerAgent(
        id="charles",
        name="Charles",
        x_handle="CharlesAI",
        site="https://base.org",
        model="Base-chain AI agent token — narrative-heavy",
        revenue_streams=("token trading", "community speculation"),
        moat="Memecoin + agent persona (weak product moat)",
        lesson_for_aria=(
            "Avoid pure token-without-product. Our edge: on-chain analysis moat + holding structure + "
            "verified facts — no subsidiary product live, the analysis itself is the moat."
        ),
    ),
)


def curiosity_handles() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for peer in PEER_AGENTS:
        if peer.watch and peer.x_handle not in seen:
            seen.add(peer.x_handle)
            out.append(peer.x_handle)
    return out


def peer_by_id(agent_id: str) -> PeerAgent | None:
    key = agent_id.strip().lower()
    for peer in PEER_AGENTS:
        if peer.id == key or peer.name.lower() == key or peer.x_handle.lower() == key:
            return peer
    return None


def peers_markdown(lang: str = "fr") -> str:
    lines: list[str] = []
    if lang == "en":
        lines.append("ZHC peer AI entrepreneurs (study set)")
    else:
        lines.append("Pairs IA entrepreneurs ZHC (set d'étude)")
    lines.append("")
    for p in PEER_AGENTS:
        lines.append(f"**{p.name}** (@{p.x_handle}) — {p.model}")
        lines.append(f"- Streams: {', '.join(p.revenue_streams)}")
        lines.append(f"- Moat: {p.moat}")
        lines.append(f"- Lesson: {p.lesson_for_aria}")
        lines.append("")
    return "\n".join(lines).strip()


def cultivation_phases(lang: str = "fr") -> list[str]:
    if lang == "en":
        return [
            "Phase 0 (days 1–7): Broad culture (geo, macro, regulation, peers) — study → one takeaway.",
            "Phase 1 (days 8–21): App factory — weekly poll, ship first paid app v0 (web or Play Store).",
            "Phase 2 (days 22–30): Reach $50/mo logged revenue; Play Store listing if Android path.",
        ]
    return [
        "Phase 0 (jours 1–7) : Culture large (géo, macro, régulation, pairs) — étudier → une synthèse.",
        "Phase 1 (jours 8–21) : App factory — poll hebdo, livrer première app payante v0 (web ou Play Store).",
        "Phase 2 (jours 22–30) : Atteindre 50 $/mois logués ; listing Play Store si voie Android.",
    ]


def revenue_hypotheses(lang: str = "fr") -> list[tuple[str, str, float]]:
    """(id, label, target_usd_monthly) — ordered by moat fit."""
    if lang == "en":
        return [
            ("app_factory", "Kelly app studio — web micro-apps + Android Play Store", 35.0),
            ("build_micro", "Build-my-idea via Telegram (small scope, fast delivery)", 20.0),
            ("telegram_alerts", "Paid Telegram alert tier (watchlist + divergences)", 15.0),
            ("signal_brief_premium", "Weekly signal brief (Gumroad/Telegram)", 10.0),
            ("affiliate", "Affiliate/referral on tools we already use", 5.0),
        ]
    return [
        ("app_factory", "Studio apps Kelly — micro-apps web + Android Play Store", 35.0),
        ("build_micro", "Build-my-idea via Telegram (petit scope, livraison rapide)", 20.0),
        ("telegram_alerts", "Tier alertes Telegram payant (watchlist + divergences)", 15.0),
        ("signal_brief_premium", "Brief signaux hebdo (Gumroad/Telegram)", 10.0),
        ("affiliate", "Affiliation / referral outils déjà utilisés", 5.0),
    ]