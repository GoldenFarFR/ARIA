"""Declarative registry of ALL external APIs ARIA touches — for `/api`
(Telegram, admin-only). Serves two distinct needs, never conflated:

1. Base URL + "configured" (presence of the key/token in the environment,
   mechanical check, always exact) — true for EVERY entry, without exception.
2. LIVE quota — only for the small subset of APIs that actually expose
   a queryable billing/quota endpoint (GitHub, CoinMarketCap,
   x.ai Management, internal x402). For all the others (the majority), no
   number is invented: either a static DOCUMENTED limit (never presented
   as "live"), or an honest "no quota endpoint" note.

Some APIs require NO key by design (DefiLlama, public Clanker,
Frankfurter, DexScreener, GeckoTerminal) — `configured=True` with `note="sans clé"`
(the literal French value used throughout this file, cf. note below), never
confused with a missing key.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


def _env_present(*names: str) -> bool:
    return all(bool(os.environ.get(n, "").strip()) for n in names)


@dataclass
class ApiEntry:
    name: str
    category: str
    base_url: str
    configured: bool
    note: str = ""          # static documented limit, or "sans clé" [no key], etc.
    live_quota: str | None = None   # only filled when a real live check exists


# ── live checkers (deliberately restricted subset, 18/07) ────────

async def _github_quota() -> str | None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.github.com/rate_limit",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
        if r.status_code != 200:
            return f"erreur HTTP {r.status_code}"
        core = r.json().get("resources", {}).get("core", {})
        return f"{core.get('remaining', '?')}/{core.get('limit', '?')} requêtes (fenêtre horaire)"
    except Exception as exc:  # noqa: BLE001 — a broken quota check must never break /api
        logger.info("api_registry: github quota check failed (%s)", exc)
        return "indisponible"


async def _coinmarketcap_quota() -> str | None:
    """Schema verified at the source (docs.coinmarketcap.com, 18/07) before wiring:
    data.usage.current_month.{credits_used,credits_left}, same for current_day."""
    key = os.environ.get("COINMARKETCAP_API_KEY", "").strip()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://pro-api.coinmarketcap.com/v1/key/info",
                headers={"X-CMC_PRO_API_KEY": key},
            )
        if r.status_code != 200:
            return f"erreur HTTP {r.status_code}"
        usage = r.json().get("data", {}).get("usage", {})
        month = usage.get("current_month", {})
        day = usage.get("current_day", {})
        used_m, left_m = month.get("credits_used"), month.get("credits_left")
        used_d, left_d = day.get("credits_used"), day.get("credits_left")
        if used_m is None or left_m is None:
            return "format de réponse inattendu"
        return f"{used_m}/{used_m + left_m} crédits (mois) — {used_d}/{used_d + left_d if used_d is not None and left_d is not None else '?'} (jour)"
    except Exception as exc:  # noqa: BLE001
        logger.info("api_registry: coinmarketcap quota check failed (%s)", exc)
        return "indisponible"


async def _xai_billing_quota() -> str | None:
    from aria_core.services.xai_billing import get_prepaid_balance, xai_billing_configured

    if not xai_billing_configured():
        return None
    result = await get_prepaid_balance()
    if not result.available:
        return f"indisponible ({result.error})"
    return f"{result.balance_usd:.2f}$ prépayé restant"


async def _x402_budget() -> str | None:
    try:
        from aria_core.x402_budget import weekly_status

        status = await weekly_status()
        return f"{status.get('spent_usd', 0):.2f}$/{status.get('cap_usd', 0):.2f}$ dépensés cette semaine"
    except Exception as exc:  # noqa: BLE001
        logger.info("api_registry: x402 budget check failed (%s)", exc)
        return "indisponible"


def _static_entries() -> list[ApiEntry]:
    """Entries with no key required (`configured=True` by design) or whose
    quota is only known via a DOCUMENTED limit (never a live number)."""
    return [
        # ── Données marché / on-chain ──────────────────────────────────────
        ApiEntry("DexScreener", "Données marché", "https://api.dexscreener.com", True, note="sans clé"),
        ApiEntry(
            "GeckoTerminal", "Données marché", "https://api.geckoterminal.com",
            _env_present("COINGECKO_DEMO_API_KEY"),
            note="authentifié (#211, 18/07, clé Demo CoinGecko partagée) — throttle 2.1s/appel "
            "(corrigé 19/07 : le vrai plafond Demo est ~30 req/min, pas 100 -- la clé reste "
            "envoyée pour le quota mensuel, jamais revérifié pour accélérer le débit)"
            if _env_present("COINGECKO_DEMO_API_KEY")
            else "sans clé — ~30 req/min documenté (throttlé à 2.1s/appel côté ARIA)",
        ),
        ApiEntry("DefiLlama", "Données marché", "https://api.llama.fi", True, note="sans clé"),
        ApiEntry(
            "GoPlus Security", "Sécurité", "https://api.gopluslabs.io", _env_present("GOPLUS_APP_KEY", "GOPLUS_APP_SECRET"),
            note="authentifié (#207, 18/07) — 150K CU/mois, 30K CU/jour, 150 CU/min documenté" if _env_present("GOPLUS_APP_KEY", "GOPLUS_APP_SECRET") else "app_key/app_secret absentes — chemin public sans clé (~30 req/min)",
        ),
        ApiEntry("Clanker", "Données marché", "https://www.clanker.world/api", True, note="sans clé"),
        ApiEntry("RugCheck.xyz", "Données on-chain", "https://api.rugcheck.xyz", True, note="sans clé — repli honeypot Solana (#207)"),
        ApiEntry("GitHub API (diligence dépôt, sans clé)", "Données on-chain", "https://api.github.com", True, note="sans clé, 60 req/h/IP — services/project_activity.py, client canonique consommé par vc_analysis.py ET conviction_research.py (19/07)"),
        ApiEntry("Warpcast (Farcaster)", "Données on-chain", "https://api.warpcast.com", True, note="sans clé — vérifie le contenu des profils Farcaster déclarés (conviction_research.py, 19/07)"),
        ApiEntry("Telegram (aperçu public t.me/s/)", "Données on-chain", "https://t.me", True, note="sans clé, scraping HTML fragile par nature — vérifie le contenu des canaux déclarés (conviction_research.py, 19/07)"),
        ApiEntry("Frankfurter (forex)", "Données marché", "https://api.frankfurter.dev", True, note="sans clé"),
        ApiEntry("Polymarket", "Données marché", "https://clob.polymarket.com", True, note="sans clé (lecture)"),
        ApiEntry("blockchain.info", "Données marché", "https://blockchain.info", True, note="sans clé"),
        ApiEntry(
            "Blockscout (Base)", "Données on-chain", "https://base.blockscout.com",
            _env_present("BLOCKSCOUT_PRO_API_KEY"),
            note="Pro configuré" if _env_present("BLOCKSCOUT_PRO_API_KEY") else "chemin public sans clé",
        ),
        ApiEntry("CoinGecko", "Données marché", "https://api.coingecko.com", _env_present("COINGECKO_DEMO_API_KEY"), note="Demo key" if _env_present("COINGECKO_DEMO_API_KEY") else "chemin public sans clé"),
        ApiEntry("Dune Analytics", "Données on-chain", "https://api.dune.com", _env_present("DUNE_API_KEY"), note="DUNE_API_KEY absente du backend prod — utilisée jusqu'ici uniquement via MCP en session Claude Code, jamais par ARIA elle-même" if not _env_present("DUNE_API_KEY") else ""),
        ApiEntry(
            "Mobula", "Données on-chain", "https://api.mobula.io", _env_present("MOBULA_API_KEY"),
            note="authentifié (#212, 18/07) — 3e étage cascade OHLCV momentum (vraies bougies, Base+Solana), 10K crédits/mois, 1 req/s (Free)"
            if _env_present("MOBULA_API_KEY")
            else "MOBULA_API_KEY absente — aucun chemin public chez Mobula (429 dès le 1er appel sans clé), étage cascade neutralisé",
        ),
        ApiEntry(
            "Webacy (Contract Risk)", "Sécurité", "https://api.webacy.com", _env_present("WEBACY_API_KEY"),
            note="21/07 -- 2e avis sécurité contrat, complément à GoPlus (Base full support, Demo gratuit 2 req/s / 2000 req/mois) — client construit et testé (mock), PAS ENCORE branché dans momentum_entry.py, schéma de réponse pas encore confirmé contre un vrai appel"
            if _env_present("WEBACY_API_KEY")
            else "WEBACY_API_KEY absente — client prêt (services/webacy.py), en attente d'une clé Demo pour test réel avant tout branchement",
        ),

        # ── Web / recherche ─────────────────────────────────────────────────
        ApiEntry("Tavily (recherche web)", "Web", "https://api.tavily.com", _env_present("TAVILY_API_KEY")),

        # ── Réseaux sociaux ──────────────────────────────────────────────────
        ApiEntry("X (Twitter) API", "Social", "https://api.x.com", _env_present("X_API_KEY", "X_API_SECRET", "X_BEARER_TOKEN", "X_ACCESS_TOKEN")),
        ApiEntry("TikTok Content API", "Social", "https://open.tiktokapis.com", False, note="aucun compte créé — seam dormant, décision opérateur 12/07"),
        ApiEntry("Google Translate", "Utilitaire", "https://translate.googleapis.com", True, note="sans clé (endpoint public)"),

        # ── Infra / paiements ────────────────────────────────────────────────
        ApiEntry("Telegram Bot API", "Infra", "https://api.telegram.org", _env_present("TELEGRAM_BOT_TOKEN")),
        ApiEntry("Coinbase CDP (agent wallet)", "Paiements", "https://api.cdp.coinbase.com", _env_present("COINBASE_CDP_API_KEY_PRIVATE_KEY")),
        ApiEntry("Cybercentry", "Paiements", "https://api.cybercentry.co.uk", True, note="pay-per-call via x402 — voir le budget hebdo x402 ci-dessous"),
        ApiEntry("Otto AI (digest Twitter)", "Paiements", "https://x402.ottoai.services", True, note="pay-per-call via x402 (0,001$/appel) — voir le budget hebdo x402 ci-dessous, gate ARIA_MARKET_ALERTS_ENABLED (19/07)"),
        ApiEntry("twit.sh (recherche/timeline X)", "Paiements", "https://x402.twit.sh", True, note="pay-per-call via x402 (0,006-0,01$/appel) — repli conviction_research.py quand l'X officiel échoue/est épuisé, #111/#112, voir le budget hebdo x402 ci-dessous"),
        ApiEntry("x402 Bazaar (découverte CDP)", "Paiements", "https://api.cdp.coinbase.com", True, note="lecture seule, sans clé — découverte de services x402, jamais un paiement"),
        ApiEntry("Etherscan V2", "Données on-chain", "https://api.etherscan.io", _env_present("ETHERSCAN_API_KEY"), note="clé stockée, inerte — aucun code ne la consomme encore"),
        ApiEntry("Stripe", "Paiements", "https://api.stripe.com", _env_present("STRIPE_SECRET_KEY")),

        # ── LLM (chat/vision/image) ─────────────────────────────────────────
        ApiEntry("x.ai / Grok (inférence)", "LLM", "https://api.x.ai", _env_present("GROK_API_KEY")),
        ApiEntry("x.ai Imagine (images)", "LLM", "https://api.x.ai", _env_present("IMAGE_API_KEY"), note="même compte/solde que Grok inférence" if os.environ.get("IMAGE_API_KEY") == os.environ.get("GROK_API_KEY") else ""),
        ApiEntry("OpenRouter", "LLM", "https://openrouter.ai", _env_present("OPENROUTER_API_KEY")),
        ApiEntry("Groq (secours)", "LLM", "https://api.groq.com", _env_present("LLM_FALLBACK_API_KEY")),
        ApiEntry("DeepSeek", "LLM", "https://api.deepseek.com", _env_present("DEEPSEEK_API_KEY")),
        ApiEntry("OpenAI", "LLM", "https://api.openai.com", _env_present("OPENAI_API_KEY")),
        ApiEntry("Gemini (Google)", "LLM", "https://generativelanguage.googleapis.com", _env_present("GEMINI_API_KEY")),
        ApiEntry("Mistral", "LLM", "https://api.mistral.ai", _env_present("MISTRAL_API_KEY")),
        ApiEntry("Anthropic (natif)", "LLM", "https://api.anthropic.com", _env_present("ANTHROPIC_API_KEY"), note="0 crédit au 17/07 — prêt, pas encore financé"),
        ApiEntry("Virtuals / Spark", "LLM", "https://compute.virtuals.io", _env_present("VIRTUALS_API_KEY"), note="clé vidée le 17/07 (bascule Grok), infra conservée"),
    ]


async def build_api_inventory() -> list[ApiEntry]:
    """Builds the complete inventory, with live checks launched in
    parallel (the subset that has one). Never globally blocking: each
    checker has its own short timeout and catches its own exceptions."""
    entries = _static_entries()

    live_checks = await asyncio.gather(
        _github_quota(), _coinmarketcap_quota(), _xai_billing_quota(), _x402_budget(),
    )
    github_q, cmc_q, xai_q, x402_q = live_checks

    entries.append(ApiEntry("GitHub API", "Infra", "https://api.github.com", _env_present("GITHUB_TOKEN"), live_quota=github_q))
    entries.append(ApiEntry("CoinMarketCap", "Données marché", "https://pro-api.coinmarketcap.com", _env_present("COINMARKETCAP_API_KEY"), live_quota=cmc_q))
    entries.append(ApiEntry(
        "x.ai Management (solde)", "LLM", "https://management-api.x.ai",
        _env_present("XAI_MANAGEMENT_KEY", "XAI_TEAM_ID"),
        note="clé Management distincte de GROK_API_KEY, absente au 18/07" if not _env_present("XAI_MANAGEMENT_KEY", "XAI_TEAM_ID") else "",
        live_quota=xai_q,
    ))
    entries.append(ApiEntry("Budget x402 (hebdomadaire, interne)", "Paiements", "n/a — suivi local", True, live_quota=x402_q))

    return entries


_CATEGORY_ORDER = (
    "Données marché", "Données on-chain", "Sécurité", "Web", "Social",
    "Utilitaire", "Infra", "Paiements", "LLM",
)


_MAX_MESSAGE_CHARS = 3800  # margin under plain_telegram/Telegram's ~4000 limit


def _entry_line(e: ApiEntry) -> str:
    mark = "✅" if e.configured else "⬜"
    line = f"{mark} {e.name} — {e.base_url}"
    if e.live_quota:
        line += f" — {e.live_quota}"
    elif e.note:
        line += f" — {e.note}"
    return line


def format_api_inventory(entries: list[ApiEntry]) -> list[str]:
    """Formats into compact Telegram messages, grouped by category. Returns a
    LIST of messages (never one giant block) -- Telegram/plain_telegram truncates
    around 4000 characters, an inventory of 30+ APIs would silently exceed
    this limit in a single message. Splits LINE BY LINE (not just between
    categories): a single overloaded category must never produce a message
    that exceeds the limit on its own."""
    by_category: dict[str, list[ApiEntry]] = {}
    for e in entries:
        by_category.setdefault(e.category, []).append(e)

    configured_count = sum(1 for e in entries if e.configured)
    header = f"📡 API — {len(entries)} intégrations ({configured_count} configurées)"

    messages: list[str] = []
    current = header

    def _push_line(line: str) -> None:
        nonlocal current
        if len(current) + 1 + len(line) > _MAX_MESSAGE_CHARS:
            messages.append(current)
            current = line
        else:
            current += f"\n{line}"

    ordered_categories = list(_CATEGORY_ORDER) + [c for c in by_category if c not in _CATEGORY_ORDER]
    for category in ordered_categories:
        items = by_category.pop(category, [])
        if not items:
            continue
        _push_line("")  # blank line before each category, never counted alone against the limit
        _push_line(f"{category} ({len(items)})")
        for e in sorted(items, key=lambda x: x.name):
            _push_line(_entry_line(e))

    if current.strip():
        messages.append(current)
    return messages
