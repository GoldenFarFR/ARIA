"""Registre déclaratif de TOUTES les API externes qu'ARIA touche — pour `/api`
(Telegram, admin-only). Répond à deux besoins distincts, jamais confondus :

1. URL de base + « configurée » (présence de la clé/du token dans l'environnement,
   check mécanique, toujours exact) — vrai pour CHAQUE entrée, sans exception.
2. Quota EN DIRECT — seulement pour le petit sous-ensemble d'API qui exposent
   réellement un endpoint de facturation/quota interrogeable (GitHub, CoinMarketCap,
   x.ai Management, x402 interne). Pour toutes les autres (la majorité), aucun
   chiffre n'est inventé : soit une limite DOCUMENTÉE statique (jamais présentée
   comme « en direct »), soit une mention honnête « pas d'endpoint de quota ».

Certaines API n'exigent AUCUNE clé par conception (DefiLlama, Clanker public,
Frankfurter, DexScreener, GeckoTerminal) — `configured=True` avec `note="sans clé"`,
jamais confondu avec une clé manquante.
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
    note: str = ""          # limite documentée statique, ou "sans clé", etc.
    live_quota: str | None = None   # rempli uniquement si un vrai check en direct existe


# ── checkers en direct (sous-ensemble volontairement restreint, 18/07) ────────

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
    except Exception as exc:  # noqa: BLE001 — un quota en panne ne doit jamais casser /api
        logger.info("api_registry: github quota check échoué (%s)", exc)
        return "indisponible"


async def _coinmarketcap_quota() -> str | None:
    """Schéma vérifié à la source (docs.coinmarketcap.com, 18/07) avant câblage :
    data.usage.current_month.{credits_used,credits_left}, idem current_day."""
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
        logger.info("api_registry: coinmarketcap quota check échoué (%s)", exc)
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
        logger.info("api_registry: x402 budget check échoué (%s)", exc)
        return "indisponible"


def _static_entries() -> list[ApiEntry]:
    """Entrées sans clé requise (`configured=True` par conception) ou dont le
    quota n'est connu que via limite DOCUMENTÉE (jamais un chiffre en direct)."""
    return [
        # ── Données marché / on-chain ──────────────────────────────────────
        ApiEntry("DexScreener", "Données marché", "https://api.dexscreener.com", True, note="sans clé"),
        ApiEntry(
            "GeckoTerminal", "Données marché", "https://api.geckoterminal.com", True,
            note="sans clé — ~30 req/min documenté (throttlé à 2.1s/appel côté ARIA)",
        ),
        ApiEntry("DefiLlama", "Données marché", "https://api.llama.fi", True, note="sans clé"),
        ApiEntry(
            "GoPlus Security", "Sécurité", "https://api.gopluslabs.io", _env_present("GOPLUS_APP_KEY", "GOPLUS_APP_SECRET"),
            note="authentifié (#207, 18/07) — 150K CU/mois, 30K CU/jour, 150 CU/min documenté" if _env_present("GOPLUS_APP_KEY", "GOPLUS_APP_SECRET") else "app_key/app_secret absentes — chemin public sans clé (~30 req/min)",
        ),
        ApiEntry("Clanker", "Données marché", "https://www.clanker.world/api", True, note="sans clé"),
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
    """Construit l'inventaire complet, avec les vérifications en direct lancées en
    parallèle (le sous-ensemble qui en a une). Jamais bloquant globalement : chaque
    checker a son propre timeout court et capte ses propres exceptions."""
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


_MAX_MESSAGE_CHARS = 3800  # marge sous la limite ~4000 de plain_telegram/Telegram


def _entry_line(e: ApiEntry) -> str:
    mark = "✅" if e.configured else "⬜"
    line = f"{mark} {e.name} — {e.base_url}"
    if e.live_quota:
        line += f" — {e.live_quota}"
    elif e.note:
        line += f" — {e.note}"
    return line


def format_api_inventory(entries: list[ApiEntry]) -> list[str]:
    """Formate en messages Telegram compacts, groupés par catégorie. Retourne une
    LISTE de messages (jamais un seul bloc géant) -- Telegram/plain_telegram tronque
    autour de 4000 caractères, un inventaire de 30+ API dépasserait silencieusement
    cette limite en un seul message. Découpe LIGNE PAR LIGNE (pas juste entre
    catégories) : une seule catégorie surchargée ne doit jamais produire un message
    qui dépasse la limite à elle seule."""
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
        _push_line("")  # ligne vide avant chaque catégorie, jamais comptée seule contre la limite
        _push_line(f"{category} ({len(items)})")
        for e in sorted(items, key=lambda x: x.name):
            _push_line(_entry_line(e))

    if current.strip():
        messages.append(current)
    return messages
