"""Settings loaded from the environment and .env."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DataBackend(StrEnum):
    """Where source reads are executed."""

    ZETARIS = "zetaris"
    DUCKDB = "duckdb"


class ModelTier(StrEnum):
    """Logical model tiers; ROLE_MODELS maps roles onto these."""

    MAIN = "main"
    FAST = "fast"


_DEFAULT_ROLE_MODELS: dict[str, str] = {
    "router": "main",
    "writer": "main",
    "actions": "main",
    "web_extract": "main",
    "default": "main",
    "critic": "fast",
    "memory_extract": "fast",
}


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

    nvidia_api_key: SecretStr = Field(default=SecretStr(""))
    nvidia_main_model: str = Field(
        default="nvidia/nemotron-3-ultra-550b-a55b",
        description="Nemotron 3 Ultra slug for main-tier roles.",
    )
    nvidia_fast_model: str = Field(
        default="nvidia/nemotron-3-super-120b-a12b",
        description="Nemotron 3 Super slug for fast-tier roles.",
    )
    nvidia_embed_model: str = Field(
        default="nvidia/nv-embedqa-e5-v5",
        description="NVIDIA embedding model slug.",
    )
    role_models: dict[str, str] = Field(
        default_factory=lambda: dict(_DEFAULT_ROLE_MODELS),
        description='Map of agent role to model tier: "main" or "fast".',
    )
    record_llm_cache: bool = Field(
        default=False,
        description="When calling NVIDIA for real, also write responses into data/cache.",
    )

    tavily_api_key: SecretStr = Field(default=SecretStr(""))
    zetaris_url: str = ""
    zetaris_api_key: SecretStr = Field(default=SecretStr(""))
    google_client_id: str = ""
    google_client_secret: SecretStr = Field(default=SecretStr(""))
    riva_server: str = ""
    riva_api_key: SecretStr = Field(default=SecretStr(""))

    http_timeout_seconds: float = Field(default=15.0, gt=0, le=15)
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=30)
    max_critic_rounds: int = Field(default=2, ge=0, le=2)
    max_retries: int = Field(default=3, ge=0, le=3)

    sample_data_dir: Path = Path("data/sample")
    cache_dir: Path = Path("data/cache")
    logs_dir: Path = Path("logs")

    @field_validator("role_models", mode="before")
    @classmethod
    def _parse_role_models(cls, value: Any) -> Any:
        """Accept a JSON object from the environment."""
        if value is None or value == "":
            return dict(_DEFAULT_ROLE_MODELS)
        if isinstance(value, str):
            import json

            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError("ROLE_MODELS must be a JSON object")
            return {str(key): str(item) for key, item in parsed.items()}
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once from the environment and .env."""
    return Settings()
