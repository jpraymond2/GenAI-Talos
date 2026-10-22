"""The only module allowed to call an LLM provider SDK."""

from typing import Any

from pydantic import BaseModel

from recall.config import get_settings


def generate(prompt: str, *, system: str | None = None) -> str:
    """Generate text. Sample mode returns an empty string and calls no provider.

    HTTP calls made here must use settings.http_timeout_seconds (15s).
    """
    settings = get_settings()
    if settings.sample_mode:
        return ""
    _ = (prompt, system)
    raise NotImplementedError("LLM generation is not wired yet")


def generate_json(
    prompt: str,
    *,
    system: str | None = None,
    schema: type[BaseModel] | None = None,
) -> dict[str, Any]:
    """Generate a JSON object. Sample mode calls no provider.

    When schema is set in sample mode, the result is that model's defaults.
    """
    settings = get_settings()
    if settings.sample_mode:
        if schema is not None:
            return schema().model_dump()
        return {}
    _ = (prompt, system)
    raise NotImplementedError("LLM JSON generation is not wired yet")
