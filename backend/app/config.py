"""Application configuration.

All configuration comes from environment variables (optionally via a
``.env`` file in the repo root). No secrets are hardcoded anywhere in
this module.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root: <repo>/backend/app/config.py -> parents[2] == <repo>
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Environment-driven settings for the Tender Co-Pilot backend."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    DATABASE_URL: str = "sqlite:///./data/tendercopilot.db"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"
    CHROMA_DIR: str = str(BASE_DIR / "data" / "chroma")
    EMBEDDINGS_MODEL: str = "all-MiniLM-L6-v2"
    INGEST_INTERVAL_HOURS: int = 6
    SCAN_KEYWORDS: str = "works,supply,services"
    SEED_PATH: str = str(BASE_DIR / "seed" / "seed_tenders.json")
    DISABLE_SCHEDULER: str = "0"
    PORT: int = 8000


settings = Settings()
