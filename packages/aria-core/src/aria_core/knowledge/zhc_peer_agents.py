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
    """Aucun produit payant à livrer -- les phases suivent le pacte argent réel
    (docs/protocole-argent-reel.md), pas un calendrier de lancement produit."""
    if lang == "en":
        return [
            "Phase 0 (ongoing): Broad culture (geo, macro, regulation, ecosystem) — study → one takeaway.",
            "Phase 1 (ongoing): Grow the VC/trading track record (vc_predictions) — walk-forward pronostics.",
            "Phase 2 (gate): Clear the §2 proof bar (docs/protocole-argent-reel.md) before any real capital.",
        ]
    return [
        "Phase 0 (continu) : Culture large (géo, macro, régulation, écosystème) — étudier → une synthèse.",
        "Phase 1 (continu) : Faire grandir le track-record VC/trading (vc_predictions) — pronostics walk-forward.",
        "Phase 2 (barrière) : Franchir le barème de preuve (docs/protocole-argent-reel.md §2) avant tout capital réel.",
    ]


def revenue_hypotheses(lang: str = "fr") -> list[tuple[str, str, float]]:
    """Aucune hypothèse de monétisation en test aujourd'hui (ACP abandonné, Stripe retiré,
    aucun produit payant) -- retourne une liste vide plutôt qu'inventer une donnée. Le seul
    chemin réel vers l'argent réel est le barème du pacte (docs/protocole-argent-reel.md)."""
    return []