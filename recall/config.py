"""Settings loaded from the environment and .env."""

from __future__ import annotations

import logging
import re
import warnings
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class DataBackend(StrEnum):
    """Where source reads are executed."""

    ZETARIS = "zetaris"
    DUCKDB = "duckdb"


class ZetarisReadMode(StrEnum):
    """Zetaris source: activated USL tables, or raw FILESTORE tables."""

    USL = "usl"
    TABLES = "tables"


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
    reasoning_roles: Annotated[frozenset[str], NoDecode] = Field(
        default_factory=frozenset,
        description="Roles that send enable_thinking=true (comma-separated in env).",
    )

    tavily_api_key: SecretStr = Field(default=SecretStr(""))
    zetaris_rest_url: str = Field(
        default="http://localhost:8888/api/v1.0",
        description="Direct Zetaris REST base (POST …/sql-editor/sqls/run).",
    )
    zetaris_base_url: str = Field(
        default="http://localhost:3000",
        description="UI base for POST /api/auth/login (idToken fallback).",
    )
    zetaris_org_id: str = Field(default="1", description="X-Org-ID header value.")
    zetaris_schema: str = Field(
        default="recall",
        description="Lightning/Spark schema prefix for recall.<table> queries.",
    )
    zetaris_read_mode: ZetarisReadMode = Field(
        default=ZetarisReadMode.USL,
        description="usl = lightning.metastore.recallai.recall.*; tables = raw recall.*",
    )
    zetaris_usl_namespace: str = Field(
        default="lightning.metastore.recallai.recall",
        description="Fully-qualified USL model path (without the table leaf).",
    )
    zetaris_url: str = Field(
        default="",
        description="Deprecated alias for ZETARIS_REST_URL.",
    )
    zetaris_api_key: SecretStr = Field(default=SecretStr(""))
    zetaris_user: str = ""
    zetaris_password: SecretStr = Field(default=SecretStr(""))
    google_client_id: str = ""
    google_client_secret: SecretStr = Field(default=SecretStr(""))
    riva_server: str = ""
    riva_api_key: SecretStr = Field(default=SecretStr(""))
    riva_asr_function_id: str = Field(
        default="",
        description="NVIDIA Cloud Functions id for Riva Parakeet ASR (NVCF function-id).",
    )
    recall_api_token: SecretStr = Field(
        default=SecretStr(""),
        description="Bearer token required for /api/* and /ws/trace?token=.",
    )

    http_timeout_seconds: float = Field(default=15.0, gt=0, le=15)
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=30)
    max_critic_rounds: int = Field(default=2, ge=0, le=2)
    max_retries: int = Field(default=3, ge=0, le=3)

    sample_data_dir: Path = Path("data/sample")
    live_data_dir: Path = Path("data/live")
    cache_dir: Path = Path("data/cache")
    logs_dir: Path = Path("logs")
    preference_profiles: bool = Field(
        default=True,
        description="When false, never build or serve food/places preference profiles.",
    )

    @model_validator(mode="after")
    def _alias_zetaris_url(self) -> Self:
        """Map deprecated ZETARIS_URL onto ZETARIS_REST_URL when set."""
        legacy = (self.zetaris_url or "").strip()
        if not legacy:
            return self
        return self.model_copy(update={"zetaris_rest_url": legacy})

    @model_validator(mode="after")
    def _default_zetaris_read_mode(self) -> Self:
        """When DATA_BACKEND=zetaris, prefer USL reads unless tables was set."""
        # Field default is already USL; keep tables when explicitly chosen.
        if self.data_backend is DataBackend.ZETARIS and self.zetaris_read_mode not in (
            ZetarisReadMode.USL,
            ZetarisReadMode.TABLES,
        ):
            return self.model_copy(update={"zetaris_read_mode": ZetarisReadMode.USL})
        return self

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

    @field_validator("reasoning_roles", mode="before")
    @classmethod
    def _parse_reasoning_roles(cls, value: Any) -> Any:
        """Accept a comma-separated list from the environment."""
        if value is None or value == "":
            return frozenset()
        if isinstance(value, str):
            return frozenset(part.strip() for part in value.split(",") if part.strip())
        if isinstance(value, (list, set, tuple, frozenset)):
            return frozenset(str(item).strip() for item in value if str(item).strip())
        return value

    @model_validator(mode="after")
    def _warn_nvidia_credential_shapes(self) -> Self:
        """Warn (do not raise) when NVIDIA/Riva env vars look swapped or malformed."""
        for message in nvidia_credential_warnings(self):
            warnings.warn(message, UserWarning, stacklevel=2)
            logger.warning("%s", message)
        return self


def _secret_text(value: SecretStr | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value)


def is_uuid(value: str) -> bool:
    """True when ``value`` is a canonical UUID string."""
    text = (value or "").strip()
    if not text or not _UUID_RE.fullmatch(text):
        return False
    try:
        UUID(text)
    except ValueError:
        return False
    return True


def nvidia_credential_warnings(settings: Settings) -> list[str]:
    """Return human-readable warnings for mis-pasted NVIDIA / Riva credentials.

    Never includes secret values — only prefixes / shape hints.
    """
    messages: list[str] = []
    api_key = _secret_text(settings.nvidia_api_key).strip()
    function_id = (settings.riva_asr_function_id or "").strip()
    riva_key = _secret_text(settings.riva_api_key).strip()

    if api_key and not api_key.startswith("nvapi-"):
        messages.append(
            "NVIDIA_API_KEY does not start with 'nvapi-'. "
            "Paste the key from build.nvidia.com (Get API Key), not a function id."
        )

    if function_id:
        if function_id.startswith("nvapi-"):
            messages.append(
                "RIVA_ASR_FUNCTION_ID looks like an NVIDIA API key (starts with 'nvapi-'). "
                "Move that value to NVIDIA_API_KEY; RIVA_ASR_FUNCTION_ID must be a UUID "
                "from the model's API tab on build.nvidia.com."
            )
        elif not is_uuid(function_id):
            messages.append(
                "RIVA_ASR_FUNCTION_ID is not a UUID. "
                "Copy the function-id from the Parakeet ASR API tab on build.nvidia.com "
                "(e.g. nvidia/parakeet-ctc-1_1b-asr)."
            )

    if riva_key.startswith("nvapi-") and not api_key:
        messages.append(
            "RIVA_API_KEY looks like an NVIDIA API key, but NVIDIA_API_KEY is empty. "
            "ASR on NVCF uses NVIDIA_API_KEY + RIVA_ASR_FUNCTION_ID."
        )

    if api_key and is_uuid(api_key):
        messages.append(
            "NVIDIA_API_KEY looks like a UUID (function id). "
            "Swap it with RIVA_ASR_FUNCTION_ID."
        )

    return messages


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once from the environment and .env."""
    return Settings()
