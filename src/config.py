"""
Centralized configuration management.

Reads from .env / environment variables via pydantic-settings.
All paths and secrets are accessed through this module.
"""

from pathlib import Path
from functools import lru_cache
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml


# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # E:\DOSH\OA


class Settings(BaseSettings):
    """Application settings loaded from .env / environment."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DeepSeek API (primary)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # Claude API (backup)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Feishu
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""

    # Web search
    tavily_api_key: str = ""
    bing_search_api_key: str = ""
    serpapi_key: str = ""

    # Database
    sqlite_path: str = str(PROJECT_ROOT / "data" / "intel.db")
    chroma_path: str = str(PROJECT_ROOT / "data" / "chroma")

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    # Ngrok
    ngrok_auth_token: str = ""

    # Derived paths
    @property
    def data_raw_dir(self) -> Path:
        return PROJECT_ROOT / "data" / "raw"

    @property
    def data_processed_dir(self) -> Path:
        return PROJECT_ROOT / "data" / "processed"

    @property
    def competitor_list_path(self) -> Path:
        return PROJECT_ROOT / "data" / "competitor_list.yaml"

    @property
    def prompts_dir(self) -> Path:
        return PROJECT_ROOT / "prompts"


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings singleton."""
    return Settings()


# Convenience accessor
settings = get_settings()


@lru_cache
def _load_competitor_config() -> dict[str, Any]:
    """Load competitor_list.yaml once for feature flags and scoring config."""
    path = settings.competitor_list_path
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_hot_tracker_config() -> dict[str, Any]:
    config = _load_competitor_config().get("hot_tracker", {})
    return config if isinstance(config, dict) else {}


def is_hot_tracker_enabled() -> bool:
    """Whether hot-tracker collection/search should run."""
    return bool(_get_hot_tracker_config().get("enabled", False))


def is_hot_tracker_required() -> bool:
    """Whether hot-tracker failures should fail the pipeline."""
    if not is_hot_tracker_enabled():
        return False
    return bool(_get_hot_tracker_config().get("required", False))
