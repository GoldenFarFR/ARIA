"""BASE launchpad registry — scored by volume, builders, community, long-term exposure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Operator mandate: pick launchpads with real volume, builder adoption,
# community depth, and durable ecosystem exposure — not hype.

WEIGHTS = {
    "volume": 0.35,
    "builders": 0.25,
    "community": 0.25,
    "exposure": 0.15,
}

WEIGHTS_HOLDING = {
    "volume": 0.25,
    "builders": 0.20,
    "community": 0.20,
    "exposure": 0.15,
    "holding_fit": 0.20,
}


@dataclass(frozen=True)
class Launchpad:
    id: str
    name: str
    url: str
    volume: int  # 0-100 trading/fees dominance on BASE
    builders: int  # SDK, API, launch tooling, deploy velocity
    community: int  # active users, social graph, retention
    exposure: int  # brand, integrations, long-term visibility
    holding_fit: int  # fit for ZHC holding + AI CAO + utility token
    tags: tuple[str, ...]
    summary: str
    best_for: str


LAUNCHPADS: tuple[Launchpad, ...] = (
    Launchpad(
        id="clanker",
        name="Clanker",
        url="https://clanker.world",
        volume=95,
        builders=92,
        community=88,
        exposure=90,
        holding_fit=82,
        tags=("permissionless", "farcaster", "agents", "fees-leader"),
        summary="Dominant BASE launchpad by fees/volume. Farcaster + agent launches at scale.",
        best_for="Maximum volume, builder velocity, and long-term discoverability on Base.",
    ),
    Launchpad(
        id="bankr",
        name="Bankr",
        url="https://bankr.bot",
        volume=72,
        builders=96,
        community=78,
        exposure=84,
        holding_fit=98,
        tags=("ai-agents", "api", "cli", "x-deploy", "uniswap-v4"),
        summary="Agent-native launches on Base. NL/CLI/API + @bankrbot X deploy. Fee claims built-in.",
        best_for="AI operators (ARIA-style): API deploy, social proof, self-funding via trading fees.",
    ),
    Launchpad(
        id="virtuals",
        name="Virtuals Protocol",
        url="https://virtuals.io",
        volume=68,
        builders=86,
        community=96,
        exposure=92,
        holding_fit=94,
        tags=("ai-agents", "genesis", "curated", "capital-formation"),
        summary="AI agent capital-formation layer. Genesis launches, strong community, Base-native.",
        best_for="AI agent narrative with curated genesis exposure and large holder community.",
    ),
    Launchpad(
        id="flaunch",
        name="Flaunch",
        url="https://flaunch.gg",
        volume=62,
        builders=90,
        community=72,
        exposure=78,
        holding_fit=88,
        tags=("fair-launch", "creator-revenue", "buybacks", "sdk"),
        summary="Fair-price launches, ETH creator revenue, auto buybacks, SDK for custom launchpads.",
        best_for="Utility + deflation story: treasury managers, buybacks, builder-grade SDK.",
    ),
    Launchpad(
        id="zora",
        name="ZORA Coins",
        url="https://zora.co",
        volume=58,
        builders=76,
        community=86,
        exposure=91,
        holding_fit=62,
        tags=("creators", "social", "onchain-media"),
        summary="Creator coins on Base. Social distribution via Zora network.",
        best_for="Creator/social distribution — less ideal for corporate holding utility tokens.",
    ),
    Launchpad(
        id="aerodrome-ignition",
        name="Aerodrome Ignition",
        url="https://aerodrome.finance",
        volume=70,
        builders=58,
        community=64,
        exposure=88,
        holding_fit=52,
        tags=("defi", "liquidity", "aerodrome"),
        summary="Liquidity bootstrapping via Aerodrome flywheel. High TVL, DeFi-native.",
        best_for="DeFi liquidity pairs — not a classic meme/agent launchpad.",
    ),
    Launchpad(
        id="liquid-protocol",
        name="Liquid Protocol",
        url="https://liquidprotocol.xyz",
        volume=42,
        builders=48,
        community=44,
        exposure=40,
        holding_fit=35,
        tags=("experimental",),
        summary="Smaller BASE launchpad. Limited fees vs leaders.",
        best_for="Niche experiments only.",
    ),
    Launchpad(
        id="rainbow",
        name="Rainbow Token Launchpad",
        url="https://rainbow.me",
        volume=38,
        builders=58,
        community=52,
        exposure=48,
        holding_fit=42,
        tags=("wallet", "consumer"),
        summary="Wallet-adjacent launches. Moderate traction on Base.",
        best_for="Consumer UX experiments tied to Rainbow wallet users.",
    ),
    Launchpad(
        id="ape-store",
        name="Ape.Store",
        url="https://ape.store",
        volume=32,
        builders=44,
        community=40,
        exposure=36,
        holding_fit=30,
        tags=("meme", "storefront"),
        summary="Meme token storefront. Low fees vs Clanker/Bankr tier.",
        best_for="Quick meme tests — not primary for holding-grade launches.",
    ),
    Launchpad(
        id="mint-club",
        name="Mint Club",
        url="https://mint.club",
        volume=28,
        builders=62,
        community=48,
        exposure=42,
        holding_fit=48,
        tags=("bonding-curve", "multi-chain"),
        summary="Bonding-curve token minting. Multi-chain including Base.",
        best_for="Bonding-curve mechanics — secondary option.",
    ),
    Launchpad(
        id="dxsale",
        name="DxSale",
        url="https://dxsale.app",
        volume=22,
        builders=38,
        community=34,
        exposure=30,
        holding_fit=25,
        tags=("presale", "legacy"),
        summary="Legacy presale launchpad. Generic, low Base-specific momentum.",
        best_for="Presale-style raises — poor fit for ZHC public launch narrative.",
    ),
    Launchpad(
        id="pinksale",
        name="PinkSale",
        url="https://pinksale.finance",
        volume=18,
        builders=34,
        community=30,
        exposure=26,
        holding_fit=22,
        tags=("presale", "legacy"),
        summary="Cross-chain presale platform. Minimal BASE launchpad fees.",
        best_for="Avoid for Vanguard — low exposure and trust on Base.",
    ),
)

_LAST_REFRESH: datetime | None = None
_DEFILLAMA_NOTE = (
    "DeFiLlama BASE launchpads (ref. mid-2026): Clanker leads 7d fees (~$88k); "
    "flaunch ~$1.5m TVL; Aerodrome Ignition ~$24m TVL (liquidity, not launch fees)."
)


def _score(lp: Launchpad, weights: dict[str, float]) -> float:
    total = 0.0
    for key, w in weights.items():
        total += w * getattr(lp, key)
    return round(total, 2)


def rank_launchpads(*, holding_context: bool = False) -> list[tuple[Launchpad, float]]:
    weights = WEIGHTS_HOLDING if holding_context else WEIGHTS
    scored = [(lp, _score(lp, weights)) for lp in LAUNCHPADS]
    return sorted(scored, key=lambda x: x[1], reverse=True)


def primary_pick(holding_context: bool = True) -> Launchpad:
    return rank_launchpads(holding_context=holding_context)[0][0]


def recommendation_verdict(lang: str = "en", holding_context: bool = True) -> str:
    from aria_core.presentation import _fmt_score, format_axis_profile, format_ranking_table

    ranked = rank_launchpads(holding_context=holding_context)
    top, top_score = ranked[0]
    volume_king = rank_launchpads(holding_context=False)[0][0]

    if lang == "fr":
        ctx = "jeton holding · Aria Vanguard ZHC" if holding_context else "analyse générale Base"
        lines = [
            "══════════════════════════════════",
            "🏛 LAUNCHPADS BASE — Synthèse ARIA",
            "══════════════════════════════════",
            f"Contexte : {ctx}",
            "Format : investor-grade · scores SSOT opérateur",
            "",
            f"🥇 VERDICT — {top.name}",
            f"Score composite : {_fmt_score(top_score)} / 100",
            "",
            *format_axis_profile(top, lang="fr", holding_context=holding_context),
            "",
            "💡 Résumé",
            top.summary,
            "",
            "✅ Recommandé pour",
            top.best_for,
        ]
        if volume_king.id != top.id:
            lines.extend([
                "",
                "📌 Note volume",
                f"Si priorité #1 = fees / flux max → {volume_king.name} (leader volume pur).",
            ])
        lines.extend(format_ranking_table(ranked, lang="fr", holding_context=holding_context))
        lines.extend([
            "",
            "📎 Référence marché",
            _DEFILLAMA_NOTE,
            "",
            "🔬 Méthodologie & sources",
            "Demande : « explique tes sources par axe » ou « méthodologie launchpad ».",
        ])
        return "\n".join(lines)

    ctx = "holding token · Aria Vanguard ZHC" if holding_context else "general Base analysis"
    lines = [
        "══════════════════════════════════",
        "🏛 BASE LAUNCHPADS — ARIA Brief",
        "══════════════════════════════════",
        f"Context: {ctx}",
        "Format: investor-grade · operator SSOT scores",
        "",
        f"🥇 VERDICT — {top.name}",
        f"Composite score: {_fmt_score(top_score)} / 100",
        "",
        *format_axis_profile(top, lang="en", holding_context=holding_context),
        "",
        "💡 Summary",
        top.summary,
        "",
        "✅ Best for",
        top.best_for,
    ]
    if volume_king.id != top.id:
        lines.extend([
            "",
            "📌 Volume note",
            f"If goal #1 is max fees/flow → {volume_king.name} (raw volume leader).",
        ])
    lines.extend(format_ranking_table(ranked, lang="en", holding_context=holding_context))
    lines.extend([
        "",
        "📎 Market reference",
        _DEFILLAMA_NOTE,
        "",
        "🔬 Methodology & sources",
        "Ask: « explain your sources per axis » or « launchpad methodology ».",
    ])
    return "\n".join(lines)


def _axis_definitions(lang: str) -> list[tuple[str, str, str]]:
    """(label, definition, calibration_source) per scoring axis."""
    if lang == "fr":
        return [
            (
                "Volume",
                "Frais 7j/30j, flux de trading, activité soutenue sur Base (pas un pic isolé).",
                "DeFiLlama fees/TVL + observation opérateur ; calibré dans ce fichier.",
            ),
            (
                "Builders (développeurs)",
                "SDK, API, CLI, tooling de déploiement, vélocité des intégrations.",
                "Docs officielles, GitHub/SDK publics, déploiements observés (Bankr API, Flaunch SDK…).",
            ),
            (
                "Communauté",
                "Détenteurs actifs, rétention, profondeur du graphe social (Farcaster, X, genesis).",
                "Signaux sociaux + communautés genesis (ex. Virtuals) — score opérateur 0–100.",
            ),
            (
                "Exposition (visibilité LT)",
                "Marque, intégrations écosystème, découvrabilité durable — pas le hype court terme.",
                "Présence écosystème Base, partenariats, rappels média — calibré opérateur.",
            ),
            (
                "Holding fit",
                "Adéquation jeton utility CAO + holding Aria Vanguard (API, narrative AI, buybacks).",
                "Mandat opérateur ZHC — pondéré seulement en contexte holding/token.",
            ),
        ]
    return [
        (
            "Volume",
            "7d/30d fees, trading flow, sustained Base activity (not a one-off spike).",
            "DeFiLlama fees/TVL + operator observation; calibrated in this file.",
        ),
        (
            "Builders",
            "SDK, API, CLI, deploy tooling, integration velocity.",
            "Official docs, public SDKs, observed deploy paths (Bankr API, Flaunch SDK…).",
        ),
        (
            "Community",
            "Active holders, retention, social-graph depth (Farcaster, X, genesis).",
            "Social signals + genesis communities (e.g. Virtuals) — operator score 0–100.",
        ),
        (
            "Exposure (long-term visibility)",
            "Brand, ecosystem integrations, durable discoverability — not short hype.",
            "Base ecosystem presence, partnerships — operator calibration.",
        ),
        (
            "Holding fit",
            "Fit for CAO utility token + Aria Vanguard holding (API, AI narrative, buybacks).",
            "ZHC operator mandate — weighted only in holding/token context.",
        ),
    ]


def methodology_markdown(*, lang: str = "fr", holding_context: bool = True) -> str:
    """Explain scoring axes, weights, formula, and honest source limits."""
    from aria_core.presentation import _divider

    weights = WEIGHTS_HOLDING if holding_context else WEIGHTS
    axes = _axis_definitions(lang)
    w_parts = [f"{k} {int(weights[k] * 100)}%" for k in weights]
    formula = " + ".join(f"{k}×{weights[k]:.2f}" for k in weights)
    axis_emoji = {
        "Volume": "📈",
        "Builders (développeurs)": "🛠",
        "Builders": "🛠",
        "Communauté": "👥",
        "Community": "👥",
        "Exposition (visibilité LT)": "📣",
        "Exposure (long-term visibility)": "📣",
        "Holding fit": "🎯",
    }

    if lang == "fr":
        ctx = "holding / token" if holding_context else "général"
        lines = [
            "══════════════════════════════════",
            "🔬 MÉTHODOLOGIE — Launchpads BASE",
            "══════════════════════════════════",
            f"Contexte : {ctx} · format investor-grade",
            "",
            "📐 Principe",
            "Chaque launchpad = scores 0–100 par axe (pas une métrique live unique).",
            f"Score final = moyenne pondérée : {formula}",
            "",
            f"⚖️ Pondération ({ctx})",
            " · ".join(w_parts),
            "",
            _divider(),
            "📊 Axes, définition & sources",
        ]
        for label, definition, source in axes:
            if label == "Holding fit" and not holding_context:
                continue
            em = axis_emoji.get(label, "•")
            lines.append(f"{em} {label}")
            lines.append(f"   {definition}")
            lines.append(f"   Source : {source}")
            lines.append("")
        lines.extend([
            "📎 Référence marché",
            _DEFILLAMA_NOTE,
            "",
            "📁 Fichier SSOT",
            "base_launchpads.py — révision opérateur quand DeFiLlama / terrain bougent.",
            "",
            "⚠️ Limites honnêtes",
            "· Pas de flux temps réel dans le bot (heartbeat launchpad_watch).",
            "· Communauté / exposition = jugement qualitatif encodé.",
            "· Un score 72 = rang relatif, pas 72 % de parts de marché.",
        ])
        return "\n".join(lines)

    ctx = "holding / token" if holding_context else "general"
    lines = [
        "══════════════════════════════════",
        "🔬 METHODOLOGY — BASE Launchpads",
        "══════════════════════════════════",
        f"Context: {ctx} · investor-grade format",
        "",
        "📐 Principle",
        "Each launchpad = 0–100 axis scores (not one live metric).",
        f"Final score = weighted sum: {formula}",
        "",
        f"⚖️ Weights ({ctx})",
        " · ".join(w_parts),
        "",
        _divider(),
        "📊 Axes, definition & sources",
    ]
    for label, definition, source in axes:
        if label == "Holding fit" and not holding_context:
            continue
        em = axis_emoji.get(label, "•")
        lines.append(f"{em} {label}")
        lines.append(f"   {definition}")
        lines.append(f"   Source: {source}")
        lines.append("")
    lines.extend([
        "📎 Market reference",
        _DEFILLAMA_NOTE,
        "",
        "📁 SSOT file",
        "base_launchpads.py — operator revision when DeFiLlama / field shifts.",
        "",
        "⚠️ Honest limits",
        "· No live stream in-bot (launchpad_watch heartbeat).",
        "· Community / exposure = encoded qualitative judgment.",
        "· Score 72 = relative rank, not 72% market share.",
    ])
    return "\n".join(lines)


def compare_launchpads_markdown(
    launchpads: list[Launchpad],
    *,
    lang: str = "fr",
    holding_context: bool = True,
) -> str:
    """Side-by-side comparison — investor table."""
    from aria_core.presentation import format_compare_table

    if not launchpads:
        return ""

    weights = WEIGHTS_HOLDING if holding_context else WEIGHTS
    scores = [_score(lp, weights) for lp in launchpads]
    return "\n".join(
        format_compare_table(
            launchpads, scores, lang=lang, holding_context=holding_context,
        ),
    )


def registry_markdown() -> str:
    """Full registry for doctrine injection — ARIA learns by heart."""
    lines = [
        "# BASE launchpads — ARIA registry",
        "",
        "Selection criteria (operator mandate):",
        "1. **Volume** — fees, trading flow, sustained activity",
        "2. **Builders** — SDK/API, deploy tooling, launch velocity",
        "3. **Community** — active holders, social graph, retention",
        "4. **Long-term exposure** — brand, integrations, ecosystem durability",
        "",
        "For **Aria Vanguard ZHC** token: also weigh **holding_fit** (AI CAO, utility, deflation).",
        "",
        _DEFILLAMA_NOTE,
        "",
        "## Ranked registry",
        "",
    ]
    for lp, sc in rank_launchpads(holding_context=True):
        lines.append(f"### {lp.name} (`{lp.id}`) — score {sc}")
        lines.append(f"- URL: {lp.url}")
        lines.append(f"- Scores: volume {lp.volume}, builders {lp.builders}, "
                     f"community {lp.community}, exposure {lp.exposure}, holding_fit {lp.holding_fit}")
        lines.append(f"- Tags: {', '.join(lp.tags)}")
        lines.append(f"- {lp.summary}")
        lines.append(f"- Best for: {lp.best_for}")
        lines.append("")
    pick = primary_pick(holding_context=True)
    vol = primary_pick(holding_context=False)
    lines.append("## Current picks")
    lines.append(f"- **Vanguard token (balanced)**: {pick.name}")
    lines.append(f"- **Raw volume leader**: {vol.name}")
    lines.append("- **Runner-ups**: Bankr (AI/API), Virtuals (genesis community), Flaunch (buybacks/SDK)")
    return "\n".join(lines)


def touch_refresh() -> datetime:
    global _LAST_REFRESH
    _LAST_REFRESH = datetime.now(timezone.utc)
    return _LAST_REFRESH


def last_refresh() -> datetime | None:
    return _LAST_REFRESH