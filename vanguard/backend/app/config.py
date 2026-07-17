import json
import os
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STATIC = _PROJECT_ROOT / "frontend" / "dist"


def _parse_id_list(value: str) -> list[int]:
    if not value:
        return []
    return [int(x.strip()) for x in value.split(",") if x.strip().lstrip("-").isdigit()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Aria Vanguard ZHC"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = "sqlite+aiosqlite:///./data/dexpulse.db"  # legacy filename (pre Aria Market rename); data is current product
    dexscreener_base_url: str = "https://api.dexscreener.com"
    geckoterminal_base_url: str = "https://api.geckoterminal.com/api/v2"
    scan_interval_seconds: int = 30
    alert_cooldown_hours: int = 4
    auth_rate_limit_attempts: int = 5
    auth_rate_limit_window_minutes: int = 15
    # #22 — filet applicatif anti-scraping pour les endpoints /api/ PUBLICS (visiteurs
    # anonymes, sans session Privy). /api/aria/chat et /api/aria/community-feedback ont
    # déjà leur propre limiteur plus fin (par visiteur + par IP) et sont exemptés ici.
    # Complète, ne remplace pas, un pare-feu edge (Cloudflare) — cf.
    # docs/edge-firewall-cloudflare.md pour le volet DNS/WAF.
    public_rate_limit_enabled: bool = True
    public_rate_limit_attempts: int = 90
    public_rate_limit_window_seconds: int = 60
    max_candles: int = 300
    # str | list — pydantic-settings JSON-decodes list[...] from env; Render uses comma-separated CORS_ORIGINS
    cors_origins: str | list[str] = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:5174,http://127.0.0.1:5174"
    )
    serve_frontend: bool = False
    static_dir: Path = _DEFAULT_STATIC

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str):
            raw = v.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed if str(x).strip()]
                except json.JSONDecodeError:
                    pass
            return [x.strip() for x in raw.split(",") if x.strip()]
        return []

    @field_validator("static_dir", mode="before")
    @classmethod
    def parse_static_dir(cls, v: Any) -> Path:
        return Path(v) if v else _DEFAULT_STATIC

    # Telegram — token via @BotFather, jamais committer
    telegram_bot_token: str = ""
    telegram_bot_username: str = "Aria_ZHC_Bot"  # @username BotFather (sans @)
    telegram_admin_ids: str = ""  # IDs séparés par virgule
    telegram_admin_username: str = "golderfarfr"  # @username admin (affichage site/Telegram)
    telegram_group_id: int | None = None
    # Kill-switch /stop /start — réservé au propriétaire (chat ID unique), indépendant des admin_ids.
    aria_owner_chat_id: str = ""  # ARIA_OWNER_CHAT_ID ; vide → fallback admin_ids[0]
    # #197 (15/07) — sujet ("topic") Telegram dédié au suivi paper-trading (salon privé
    # opérateur, en plus du DM admin habituel, jamais à la place). Les deux doivent être
    # renseignées pour activer l'envoi vers le topic ; l'une des deux absente = comportement
    # actuel inchangé (DM admin seul), aucune régression.
    aria_trading_topic_chat_id: int | None = None
    aria_trading_topic_thread_id: int | None = None

    @field_validator("telegram_group_id", "aria_trading_topic_chat_id", "aria_trading_topic_thread_id", mode="before")
    @classmethod
    def empty_group_id(cls, v: Any) -> int | None:
        if v is None or v == "":
            return None
        return int(v)

    # Member auth gate — Privy X login via Vanguard holding portal
    access_code_enabled: bool = False
    session_ttl_hours: int = 24
    admin_api_secret: str = ""
    site_base_url: str = ""  # API publique (ex. https://api.ariavanguardzhc.com) — webhooks, Telegram
    holding_domain: str = "ariavanguardzhc.com"  # Site holding statique (Vanguard)

    # Privy — X login on Vanguard (dashboard.privy.io)
    privy_app_id: str = ""
    privy_jwt_verification_key: str = ""

    # LLM — clé API uniquement (jamais de clé privée wallet)
    llm_provider: str = "none"
    llm_api_key: str = ""
    virtuals_api_key: str = ""  # Virtuals Compute Spark — LLM_PROVIDER=virtuals
    deepseek_api_key: str = ""  # DeepSeek direct (api.deepseek.com) — LLM_PROVIDER=deepseek
    # 17/07 -- champ absent alors que aria_core.llm._auth_key_for_provider le référence
    # déjà (_setting_str("grok_api_key")) : GROK_API_KEY (85 car., déjà dans .env) restait
    # donc totalement inutilisé, le provider "grok" retombait silencieusement sur
    # llm_api_key (souvent une clé Groq, pas x.ai -- 401 réel constaté sur le VPS).
    grok_api_key: str = ""  # x.ai direct (api.x.ai) — LLM_PROVIDER=grok/xai
    # 17/07 -- Gemini direct (point d'accès compatible OpenAI officiel Google), candidat
    # pour le départage rapide de la zone grise (momentum_entry._llm_confirm) --
    # LLM_PROVIDER=gemini. Palier gratuit Flash/Flash-Lite vérifié à la source
    # (ai.google.dev/gemini-api/docs/pricing) avant tout câblage.
    gemini_api_key: str = ""
    # 17/07 -- Mistral Small 4 direct (api.mistral.ai, vérifié compatible OpenAI à la
    # source docs.mistral.ai/api) -- LLM_PROVIDER=mistral. reasoning_effort forcé "none"
    # dans llm.py (évite le piège Gemini du soir même : budget de tokens englouti par un
    # raisonnement invisible).
    mistral_api_key: str = ""
    llm_fallback_provider: str = "groq"
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = "llama-3.3-70b-versatile"
    llm_model: str = ""
    aria_spark_aggressive: bool = False
    # Shell opérateur — juste milieu (ouverte côté opérateur, publique reste grounded)
    aria_operator_founder_mode: bool = False
    # Nom d'affichage de l'opérateur (labels relais/logs internes ; sert aussi de préfixe
    # reconnu par le pont Cursor/KART, cf. brain._routing_message). Le vrai nom vit
    # uniquement dans le .env réel du VPS, jamais commité (#114).
    aria_operator_display_name: str = "Operator"
    aria_llm_depth_default: str = "brief"
    aria_llm_context_max_brief: int = 3500
    aria_llm_context_max_standard: int = 5000
    aria_llm_context_max_develop: int = 8000
    aria_llm_max_tokens_brief: int = 180
    aria_llm_max_tokens_standard: int = 400
    aria_llm_max_tokens_develop: int = 900
    aria_llm_cost_footer: bool = True
    aria_llm_model_develop: str = "anthropic-claude-opus-4-8"
    aria_llm_model_standard: str = "x-ai-grok-4-3"
    aria_llm_model_brief: str = "deepseek-deepseek-v4-flash"
    image_api_key: str = ""  # xAI Imagine — scènes portrait (/avatar scene)
    image_api_model: str = "grok-imagine-image"  # 0.02$/img — quality = 0.05$/img
    aria_operator_tz: str = "Europe/Paris"  # GMT+2 — planification tweets (/x compose)
    aria_avatar_style_enabled: bool = True  # rafraîchissement style Grok Imagine 7/14j
    aria_avatar_style_interval_days: int = 14  # minimum 14 jours
    aria_visual_auto_apply: bool = True  # Imagine → avatar sans validation manuelle
    aria_banner_auto_refresh: bool = True  # bannière X 3:1 après changement avatar
    aria_image_style_use_llm: bool = False  # presets locaux = 0 token Groq
    aria_visual_autonomy_interval_minutes: int = 1440  # vérif quotidienne, style 14j
    ollama_base_url: str = "http://127.0.0.1:11434"
    aria_ollama_num_ctx: int = 8192

    # Telegram production (Render) — webhook évite conflit avec instance locale
    telegram_webhook_secret: str = ""

    # ARIA ZHC identity
    aria_email: str = ""
    aria_holding_name: str = "Aria Vanguard ZHC"
    aria_x_handle: str = "Aria_ZHC"

    @field_validator("aria_x_handle", mode="before")
    @classmethod
    def normalize_aria_x_handle(cls, v: Any) -> str:
        if not v:
            return "Aria_ZHC"
        s = str(v).strip().lstrip("@")
        if s.lower() in ("ariazhc", "aria zhc"):
            return "Aria_ZHC"
        return s

    # X (Twitter) API — create @Aria_ZHC with dedicated email first
    x_bearer_token: str = ""
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_token_secret: str = ""

    # X publication policy (pay-per-use cost control)
    x_post_enabled: bool = True
    x_max_posts_per_day: int = 24  # règle d'or anti-ban (0 → défaut 24, plafond dur 50)
    x_max_posts_per_15min: int = 5  # marge sous API X (100/15 min)
    x_min_hours_between_posts: float = 1.0  # min 1h entre tweets auto (non contournable)
    x_monthly_budget_usd: float = 5.0  # abonnement / crédits console X
    x_monthly_spend_cap_usd: float = 1.0  # plafond dépense Aria (enforce)
    x_block_urls_in_posts: bool = True
    x_allow_likes: bool = False
    x_allow_replies: bool = False
    x_allow_dms: bool = False
    x_curiosity_enabled: bool = False
    x_mentions_learn_enabled: bool = False

    # ARIA entrepreneur goal — real revenue (not training sim)
    aria_revenue_goal_monthly_usd: float = 50.0

    # ARIA language on Telegram (en recommended for Telegram translate)
    aria_telegram_lang: str = "en"

    # ZHC — ARIA décide seule (pas de boutons Oui/Non sur Telegram)
    aria_autonomous: bool = True

    # Outreach JUNO — désactivé par défaut (inspiration design seulement)
    aria_juno_outreach: bool = False

    # Public intelligence — ARIA open to everyone (no invite gate)
    aria_public_mode: bool = True
    aria_chat_rate_limit_per_hour: int = 40

    # Anti-hallucination — grounded facts first, low LLM temperature
    aria_grounded_mode: bool = True
    # Master switch — false = FAQ + truth ledger + skills only (safest public mode)
    aria_llm_enabled: bool = False
    aria_llm_temperature: float = 0.2
    aria_llm_enhance_skills: bool = False
    aria_proactive_ideas: bool = True
    aria_curriculum_notify_operator: bool = False
    # ACP v2 — provider poll via acp-cli (local PC / sidecar with keychain)
    aria_acp_provider_enabled: bool = False
    aria_acp_events_file: str = ""
    aria_acp_workflow_used_tweet: bool = True
    aria_qi_shadow_judge_enabled: bool = True
    aria_qi_judge_force_aria: bool = False
    aria_qi_judge_force_ouvrier: bool = False
    aria_vector_memory: bool = False
    aria_ddg_search_cache: bool = False
    # Fournisseur de recherche web : "ddg" (gratuit, défaut) ou "tavily" (opt-in, clé env
    # TAVILY_API_KEY). DDG reste le fallback si Tavily est indisponible.
    aria_web_search_provider: str = "ddg"
    # Épistémique Phase B — vérif web si incertain + gate anti-hallucination
    aria_epistemic_web_verify: bool = True
    aria_epistemic_critic: bool = True

    # GitHub — operator PAT; * = all repos under GITHUB_OWNER (see github_skill exclusions)
    github_token: str = ""
    github_owner: str = "GoldenFarFR"
    github_sandbox_repo: str = "aria-sandbox"
    github_token_repo: str = ""  # optional dedicated token R&D repo (was aria-token-base)
    github_read_repos: str = ""  # comma-separated owner/repo, or * for unlimited read
    github_write_repos: str = ""  # comma-separated owner/repo, or * for unlimited write
    github_excluded_repos: str = ""  # repo names Aria must not touch via GitHub API
    github_protected_repos: str = (
        "aria-vanguard,aria-sandbox,"
        "template-grok-cursor,aria-skills"
    )  # never delete via Telegram / ARIA API
    # Truth ledger GitHub mirror — batch commits (not 1 commit per exchange)
    truth_ledger_github_batch_size: int = 100
    truth_ledger_github_batch_interval_sec: int = 300

    @property
    def admin_ids(self) -> list[int]:
        return _parse_id_list(self.telegram_admin_ids)

    @property
    def owner_chat_id(self) -> int | None:
        """Propriétaire unique autorisé pour /stop /start (kill-switch). Fallback admin_ids[0]."""
        raw = (self.aria_owner_chat_id or "").strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
        admins = self.admin_ids
        return admins[0] if admins else None

    @property
    def telegram_webhook_url(self) -> str | None:
        """Webhook Telegram — même hôte que le site public (domaine custom prioritaire)."""
        base = self.public_site_url.strip().rstrip("/")
        if not base or base.startswith("http://localhost"):
            fallback = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("TELEGRAM_WEBHOOK_URL") or ""
            base = fallback.strip().rstrip("/")
        if not base:
            return None
        return f"{base}/api/telegram/webhook"

    @property
    def use_telegram_webhook(self) -> bool:
        return bool(self.telegram_webhook_url) and not self.debug

    @property
    def public_site_url(self) -> str:
        """Canonical public URL — custom domain (SITE_BASE_URL) beats Render default."""
        explicit = self.site_base_url.strip().rstrip("/")
        if explicit:
            return explicit
        domain = self.holding_domain.strip().lstrip(".")
        if domain and not self.debug:
            return f"https://{domain}"
        render = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
        if render:
            return render
        return "http://localhost:5173"

    @property
    def public_holding_url(self) -> str:
        """Vitrine holding — liens publics."""
        domain = self.holding_domain.strip().lstrip(".")
        if domain and not self.debug:
            return f"https://{domain}"
        return self.public_site_url


_settings = Settings()
if _settings.site_base_url:
    origin = _settings.site_base_url.rstrip("/")
    if origin not in _settings.cors_origins:
        _settings.cors_origins = [*list(_settings.cors_origins), origin]
elif _settings.holding_domain and not _settings.debug:
    origin = f"https://{_settings.holding_domain.strip()}"
    if origin not in _settings.cors_origins:
        _settings.cors_origins = [*list(_settings.cors_origins), origin]

if not _settings.serve_frontend and _settings.static_dir.is_dir() and not _settings.debug:
    _settings.serve_frontend = True
if _settings.aria_public_mode:
    _settings.access_code_enabled = False
elif _settings.serve_frontend and not _settings.debug:
    env_gate = os.getenv("ACCESS_CODE_ENABLED", "").lower()
    if env_gate not in ("false", "0", "no"):
        _settings.access_code_enabled = True
settings = _settings