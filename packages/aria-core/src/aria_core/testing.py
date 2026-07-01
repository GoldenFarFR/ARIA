"""Test runtime helpers — minimal settings without dexpulse host."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aria_core import bootstrap
from aria_core import runtime


def _parse_id_list(value: str) -> list[int]:
    if not value:
        return []
    return [int(x.strip()) for x in value.split(",") if x.strip().lstrip("-").isdigit()]


class AriaRuntimeSettings(BaseSettings):
    """Subset of dexpulse Settings used by aria-core modules."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    debug: bool = True
    telegram_bot_token: str = ""
    telegram_bot_username: str = "Aria_ZHC_Bot"
    telegram_admin_ids: str = ""
    telegram_admin_username: str = "golderfarfr"
    telegram_group_id: int | None = None

    access_code_enabled: bool = False
    site_base_url: str = ""
    holding_domain: str = "ariavanguardzhc.com"

    llm_provider: str = "none"
    llm_api_key: str = ""
    llm_model: str = ""
    image_api_key: str = ""
    image_api_model: str = "grok-imagine-image"
    ollama_base_url: str = "http://127.0.0.1:11434"

    aria_email: str = ""
    aria_holding_name: str = "Aria Vanguard ZHC"
    aria_x_handle: str = "Aria_ZHC"

    x_bearer_token: str = ""
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_token_secret: str = ""
    x_post_enabled: bool = True
    x_max_posts_per_day: int = 3
    x_min_hours_between_posts: float = 4.0
    x_monthly_budget_usd: float = 5.0
    x_monthly_spend_cap_usd: float = 1.0
    x_block_urls_in_posts: bool = True
    x_allow_likes: bool = False
    x_allow_replies: bool = False
    x_allow_dms: bool = False
    x_curiosity_enabled: bool = False
    x_mentions_learn_enabled: bool = False

    aria_revenue_goal_monthly_usd: float = 50.0
    aria_telegram_lang: str = "en"
    aria_autonomous: bool = True
    aria_juno_outreach: bool = False
    aria_public_mode: bool = True
    aria_chat_rate_limit_per_hour: int = 40
    aria_grounded_mode: bool = True
    aria_llm_enabled: bool = False
    aria_llm_temperature: float = 0.2
    aria_llm_enhance_skills: bool = False
    aria_proactive_ideas: bool = True
    aria_epistemic_web_verify: bool = True
    aria_epistemic_critic: bool = True
    aria_operator_tz: str = "Europe/Paris"
    aria_avatar_style_enabled: bool = True
    aria_avatar_style_interval_days: int = 7
    aria_visual_auto_apply: bool = True
    aria_banner_auto_refresh: bool = True
    aria_image_style_use_llm: bool = False
    aria_visual_autonomy_interval_minutes: int = 1440

    # Curriculum épistémique / culture large — mémoire ARIA seulement (pas spam Telegram opérateur)
    aria_curriculum_notify_operator: bool = False
    aria_qi_shadow_judge_enabled: bool = True
    aria_qi_judge_force_aria: bool = False
    aria_qi_judge_force_ouvrier: bool = False
    # Mémoire vectorielle Chroma — opt-in, désactivé par défaut (Phase B stub)
    aria_vector_memory: bool = False
    # Cache DDG — opt-in, évite requêtes répétées (gratuit, fichier local)
    aria_ddg_search_cache: bool = False

    github_token: str = ""
    github_owner: str = "GoldenFarFR"
    github_sandbox_repo: str = "aria-sandbox"
    github_token_repo: str = ""
    github_read_repos: str = ""
    github_write_repos: str = ""
    github_excluded_repos: str = ""
    github_protected_repos: str = (
        "dexpulse,aria-vanguard,aria-sandbox,"
        "template-grok-cursor,aria-skills"
    )
    truth_ledger_github_batch_size: int = 100
    truth_ledger_github_batch_interval_sec: int = 300

    @field_validator("telegram_group_id", mode="before")
    @classmethod
    def empty_group_id(cls, v: Any) -> int | None:
        if v is None or v == "":
            return None
        return int(v)

    @field_validator("aria_x_handle", mode="before")
    @classmethod
    def normalize_aria_x_handle(cls, v: Any) -> str:
        if not v:
            return "Aria_ZHC"
        s = str(v).strip().lstrip("@")
        if s.lower() in ("ariazhc", "aria zhc"):
            return "Aria_ZHC"
        return s

    @property
    def admin_ids(self) -> list[int]:
        return _parse_id_list(self.telegram_admin_ids)

    @property
    def public_site_url(self) -> str:
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


_DATA_DIR: Path | None = None


def configure_test_runtime(
    *,
    data_dir: Path | None = None,
    settings: AriaRuntimeSettings | None = None,
    auth_db_path: Path | None = None,
) -> tuple[Path, AriaRuntimeSettings]:
    global _DATA_DIR
    path = Path(data_dir) if data_dir else _DATA_DIR or Path.cwd() / ".aria-test-data"
    path.mkdir(parents=True, exist_ok=True)
    _DATA_DIR = path
    cfg = settings or AriaRuntimeSettings()
    bootstrap.configure(data_dir=path, settings=cfg)
    if auth_db_path is not None:
        bootstrap.register_host_integrations(auth_db_path=auth_db_path)
    return path, cfg


def reload_test_settings(monkeypatch: Any, **env: str) -> AriaRuntimeSettings:
    """Re-read env into AriaRuntimeSettings and re-bind aria_core.runtime."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    cfg = AriaRuntimeSettings()
    runtime.configure(cfg)
    return cfg