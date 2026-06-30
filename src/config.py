"""
Centralized configuration management.

Reads from .env / environment variables via pydantic-settings.
All paths and secrets are accessed through this module.
"""

from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Project root ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # E:\DOSH\OA


class Settings(BaseSettings):
    """Application settings loaded from .env / environment."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── DeepSeek API (primary) ──────────────────────────
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # ── Claude API (备用) ────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # ── Feishu ─────────────────────────────────────────────
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""

    # ── Web Search ─────────────────────────────────────────
    tavily_api_key: str = ""
    bing_search_api_key: str = ""
    serpapi_key: str = ""
    bocha_api_key: str = ""
    bocha_base_url: str = ""

    # ── Database ───────────────────────────────────────────
    sqlite_path: str = str(PROJECT_ROOT / "data" / "intel.db")
    chroma_path: str = str(PROJECT_ROOT / "data" / "chroma")

    # ── Server ─────────────────────────────────────────────
    host: str = "127.0.0.1"
    port: int = 8000

    # ── Ngrok ──────────────────────────────────────────────
    ngrok_auth_token: str = ""

    # ── Derived paths ──────────────────────────────────────
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
