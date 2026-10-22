"""Settings loaded from the environment and .env."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DataBackend(StrEnum):
    """Where source reads are executed."""

    ZETARIS = "zetaris"
    DUCKDB = "duckdb"


class LLMProvider(StrEnum):
    """Which provider generator.py is allowed to call."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class Settings(BaseSettings):
    """Process configuration. Secrets stay here and out of code and logs."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    sample_mode: bool = Field(
        default=True,
        description="Use data/sample and data/cache, and dry-run every action.",
    )
    data_backend: DataBackend = DataBackend.DUCKDB
    llm_provider: LLMProvider = LLMProvider.ANTHROPIC

    anthropic_model: str = "your-anthropic-model"
    openai_model: str = "your-openai-model"

    anthropic_api_key: SecretStr = Field(default=SecretStr(""))
    openai_api_key: SecretStr = Field(default=SecretStr(""))
    tavily_api_key: SecretStr = Field(default=SecretStr(""))
    zetaris_url: str = ""
    zetaris_api_key: SecretStr = Field(default=SecretStr(""))
    zetaris_user: str = ""
    zetaris_password: SecretStr = Field(default=SecretStr(""))
    google_client_id: str = ""
    google_client_secret: SecretStr = Field(default=SecretStr(""))
    riva_server: str = ""
    riva_api_key: SecretStr = Field(default=SecretStr(""))

    http_timeout_seconds: float = Field(default=15.0, gt=0, le=15)
    max_critic_rounds: int = Field(default=2, ge=0, le=2)
    max_retries: int = Field(default=3, ge=0, le=3)

    sample_data_dir: Path = Path("data/sample")
    live_data_dir: Path = Path("data/live")
    cache_dir: Path = Path("data/cache")
    logs_dir: Path = Path("logs")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once from the environment and .env."""
    return Settings()
