"""The only module allowed to call an LLM provider SDK.

Meterless-style injected generator: every agent and engine calls generate(),
generate_json(), or embed() here. NVIDIA build.nvidia.com is the only provider;
role routing picks main vs fast model from config.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import types
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from openai import APIStatusError, APITimeoutError, AsyncOpenAI, BadRequestError, RateLimitError
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from recall.config import ModelTier, Settings, get_settings
from recall.tracing import Tracer

logger = logging.getLogger(__name__)

_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
_LLM_ATTEMPTS = 3  # initial try + 2 retries
_BACKOFF_SECONDS = (1.0, 3.0)
_EMBED_BATCH_SIZE = 32
_EMBED_DIM = 384
_PROMPT_PREVIEW_CHARS = 200
_REASONING_MIN_TOKENS = 4000
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_SCHEMA_ECHO_REPAIR = (
    "You returned the schema, not an answer. Return the filled-in JSON object only."
)

# Compact filled-in examples keyed by Pydantic model class name.
_SCHEMA_EXAMPLES: dict[str, dict[str, Any]] = {
    "CriticVerdict": {
        "overall": "revise",
        "verdicts": [
            {
                "index": 0,
                "decision": "keep",
                "reason": "Supported by e2.",
                "fix_hint": None,
            },
            {
                "index": 1,
                "decision": "drop",
                "reason": "Apartment search is not in e4.",
                "fix_hint": None,
            },
        ],
        "missing": ["city conflict (e3, e4) not mentioned"],
        "refetch": [],
    },
}

_client: AsyncOpenAI | None = None
_client_key: str | None = None


class LLMUnavailable(Exception):
    """Raised when NVIDIA fails after retries; callers may fall back."""


class SampleCacheMiss(Exception):
    """Sample-mode cache has no entry for this role/prompt (no default fallback)."""

    def __init__(self, role: str, key: str) -> None:
        self.role = role
        self.key = key
        super().__init__(f"Sample cache miss for role={role!r} key={key!r}")


class LLMOutputError(Exception):
    """Raised when the model response cannot be parsed or validated."""

    def __init__(self, message: str, *, reasoning_chars: int = 0) -> None:
        super().__init__(message)
        self.reasoning_chars = reasoning_chars


class LLMSchemaEchoError(LLMOutputError):
    """Raised when the model returns a JSON Schema document instead of an instance."""


# Roles that must not fall back to a global "default" cache entry.
_NO_DEFAULT_ROLES = frozenset({"router", "writer", "critic", "actions", "web_extract"})


def reset_client() -> None:
    """Drop the cached AsyncOpenAI client (for tests and key rotation)."""
    global _client, _client_key
    _client = None
    _client_key = None


def use_sample_provider(settings: Settings | None = None) -> bool:
    """True when offline cache should be used (sample mode or missing key)."""
    active = settings if settings is not None else get_settings()
    return active.sample_mode or not active.nvidia_api_key.get_secret_value()


def resolve_tier(role: str, settings: Settings | None = None) -> ModelTier:
    """Map a role to main or fast. Changing ROLE_MODELS is the only switch."""
    active = settings if settings is not None else get_settings()
    mapped = active.role_models.get(role) or active.role_models.get("default") or "main"
    try:
        return ModelTier(mapped)
    except ValueError as exc:
        raise LLMUnavailable(f"Unknown model tier for role {role!r}: {mapped!r}") from exc


def resolve_model(role: str, settings: Settings | None = None) -> str:
    """Return the configured NVIDIA model slug for this role."""
    active = settings if settings is not None else get_settings()
    if use_sample_provider(active):
        return "sample"
    tier = resolve_tier(role, active)
    if tier is ModelTier.FAST:
        return active.nvidia_fast_model
    return active.nvidia_main_model


async def generate(
    prompt: str,
    system: str | None = None,
    *,
    role: str = "default",
    max_tokens: int = 1200,
    temperature: float = 0.2,
) -> str:
    """Generate text for a role. Strips any <think>...</think> reasoning block."""
    settings = get_settings()
    model = resolve_model(role, settings)
    started = time.perf_counter()
    try:
        if use_sample_provider(settings):
            text = _sample_text(prompt, role=role, settings=settings)
            text = strip_think(text)
            _log_call(
                role=role,
                model=model,
                latency_ms=_elapsed_ms(started),
                prompt_tokens=_estimate_tokens(prompt),
                completion_tokens=_estimate_tokens(text),
                success=True,
                prompt=prompt,
                provider="sample_cache",
            )
            return text

        text, prompt_tokens, completion_tokens, reasoning_chars = await _with_retries(
            lambda: _nvidia_chat(
                prompt=prompt,
                system=system,
                model=model,
                role=role,
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=False,
                settings=settings,
            )
        )
        text = strip_think(text)
        if settings.record_llm_cache:
            _record_llm_cache(role, prompt, text, settings)
        _log_call(
            role=role,
            model=model,
            latency_ms=_elapsed_ms(started),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            success=True,
            prompt=prompt,
            reasoning_chars=reasoning_chars,
            provider="nvidia",
        )
        return text
    except SampleCacheMiss:
        raise
    except Exception as exc:
        _log_call(
            role=role,
            model=model,
            latency_ms=_elapsed_ms(started),
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            prompt=prompt,
            error=str(exc),
            reasoning_chars=getattr(exc, "reasoning_chars", 0),
        )
        raise


async def generate_json(
    prompt: str,
    system: str | None,
    schema: type[BaseModel],
    *,
    role: str = "default",
    max_tokens: int = 1500,
    example: dict[str, Any] | None = None,
) -> BaseModel:
    """Generate JSON, extract robustly, and validate against schema.

    Instructs the model with a compact field list and one filled-in EXAMPLE
    (never the raw JSON Schema). If the model echoes a schema document, retries
    once with a repair message. On other validation failures, retries once with
    the error appended; then raises LLMOutputError.
    """
    schema_block = build_json_instructions(schema, example=example)
    instructions = f"{system}\n\n{schema_block}".strip() if system else schema_block
    try:
        return await _generate_json_once(
            prompt, instructions, schema, role=role, max_tokens=max_tokens
        )
    except (ValidationError, LLMOutputError) as first_error:
        if isinstance(first_error, LLMSchemaEchoError):
            repair_prompt = f"{prompt}\n\n{_SCHEMA_ECHO_REPAIR}"
        else:
            repair_prompt = (
                f"{prompt}\n\nYour previous JSON was invalid:\n{first_error}\n"
                "Return corrected JSON only."
            )
        try:
            return await _generate_json_once(
                repair_prompt, instructions, schema, role=role, max_tokens=max_tokens
            )
        except (ValidationError, LLMOutputError) as second_error:
            raise LLMOutputError(
                f"JSON output failed validation for role={role!r}: {second_error}"
            ) from second_error


async def embed(texts: list[str], *, input_type: str = "passage") -> list[list[float]]:
    """Embed texts. Use input_type 'query' for search, 'passage' for stored text."""
    if not texts:
        return []
    settings = get_settings()
    model = "sample" if use_sample_provider(settings) else settings.nvidia_embed_model
    started = time.perf_counter()
    try:
        if use_sample_provider(settings):
            vectors = [_sample_embedding(text, settings) for text in texts]
            _log_call(
                role="embed",
                model=model,
                latency_ms=_elapsed_ms(started),
                prompt_tokens=sum(_estimate_tokens(t) for t in texts),
                completion_tokens=0,
                success=True,
                prompt=texts[0],
                provider="sample",
            )
            return vectors

        vectors = await _with_retries(
            lambda: _nvidia_embed(texts, input_type=input_type, settings=settings)
        )
        if settings.record_llm_cache:
            _record_embeddings(texts, vectors, settings)
        _log_call(
            role="embed",
            model=model,
            latency_ms=_elapsed_ms(started),
            prompt_tokens=sum(_estimate_tokens(t) for t in texts),
            completion_tokens=0,
            success=True,
            prompt=texts[0],
        )
        return vectors
    except Exception as exc:
        _log_call(
            role="embed",
            model=model,
            latency_ms=_elapsed_ms(started),
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            prompt=texts[0] if texts else "",
            error=str(exc),
        )
        raise


def strip_think(text: str) -> str:
    """Remove Nemotron <think>...</think> reasoning blocks."""
    return _THINK_RE.sub("", text).strip()


def extract_json_value(text: str) -> Any:
    """Strip think/fences and return the first top-level JSON object or array.

    Raises LLMSchemaEchoError when the value looks like an echoed JSON Schema
    (contains $defs, or both properties and type=object).
    """
    cleaned = strip_think(text).strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
            if is_echoed_schema(value):
                raise LLMSchemaEchoError(_SCHEMA_ECHO_REPAIR)
            return value
        except json.JSONDecodeError:
            continue
    raise LLMOutputError(f"No JSON object or array found in model output: {cleaned[:200]!r}")


def is_echoed_schema(value: Any) -> bool:
    """True when value looks like a JSON Schema document, not a data instance."""
    if not isinstance(value, dict):
        return False
    if "$defs" in value or "$schema" in value:
        return True
    return value.get("type") == "object" and "properties" in value


def build_json_instructions(
    schema: type[BaseModel],
    *,
    example: dict[str, Any] | None = None,
) -> str:
    """Compact field guide + one filled-in EXAMPLE. Never the raw JSON Schema."""
    lines = [f"Fill in a JSON object for {schema.__name__} with these fields:"]
    for name, field in schema.model_fields.items():
        lines.append(f"- {name}: {_describe_annotation(field.annotation)}")
    filled = example if example is not None else schema_example(schema)
    lines.append("EXAMPLE:")
    lines.append(json.dumps(filled, indent=2))
    lines.append(
        "Return ONLY a JSON object with these fields filled in for this task. "
        "Do not return the schema."
    )
    return "\n".join(lines)


def schema_example(schema: type[BaseModel]) -> dict[str, Any]:
    """Return a registered or generated minimal valid example for schema."""
    registered = _SCHEMA_EXAMPLES.get(schema.__name__)
    if registered is not None:
        return registered
    return _minimal_example(schema)


def register_schema_example(schema: type[BaseModel], example: dict[str, Any]) -> None:
    """Register an explicit filled-in example used by generate_json instructions."""
    _SCHEMA_EXAMPLES[schema.__name__] = example


def _describe_annotation(annotation: Any) -> str:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if annotation is type(None):
        return "null"

    if origin is Literal:
        return " | ".join(json.dumps(arg) for arg in args)

    if origin is Union or origin is types.UnionType:
        return " | ".join(_describe_annotation(arg) for arg in args)

    if origin is list:
        inner = args[0] if args else Any
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return f"list[{inner.__name__} {{{_inline_fields(inner)}}}]"
        return f"list[{_describe_annotation(inner)}]"

    if origin is dict:
        return "object"

    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return f"{annotation.__name__} {{{_inline_fields(annotation)}}}"
        if annotation is str:
            return "str"
        if annotation is int:
            return "int"
        if annotation is float:
            return "float"
        if annotation is bool:
            return "bool"
        if annotation is datetime:
            return "datetime (ISO-8601)"
        return annotation.__name__

    return str(annotation).replace("typing.", "")


def _inline_fields(schema: type[BaseModel]) -> str:
    parts = [
        f"{name}: {_describe_annotation(field.annotation)}"
        for name, field in schema.model_fields.items()
    ]
    return ", ".join(parts)


def _minimal_example(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        name: _example_for_field(field)
        for name, field in schema.model_fields.items()
    }


def _example_for_field(field: FieldInfo) -> Any:
    return _example_for_annotation(field.annotation)


def _example_for_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if annotation is type(None):
        return None

    if origin is Literal:
        return args[0] if args else None

    if origin is Union or origin is types.UnionType:
        non_none = [arg for arg in args if arg is not type(None)]
        if type(None) in args and non_none:
            # Prefer a concrete value so examples look filled in.
            return _example_for_annotation(non_none[0])
        return _example_for_annotation(args[0]) if args else None

    if origin is list:
        inner = args[0] if args else str
        return [_example_for_annotation(inner)]

    if origin is dict:
        return {}

    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return _minimal_example(annotation)
        if annotation is str:
            return ""
        if annotation is int:
            return 0
        if annotation is float:
            return 0.0
        if annotation is bool:
            return False
        if annotation is datetime:
            return "2026-01-01T00:00:00Z"

    return None


async def _generate_json_once(
    prompt: str,
    system: str,
    schema: type[BaseModel],
    *,
    role: str,
    max_tokens: int,
) -> BaseModel:
    settings = get_settings()
    model = resolve_model(role, settings)
    started = time.perf_counter()
    try:
        reasoning_chars = 0
        provider = "nvidia"
        if use_sample_provider(settings):
            raw = _sample_text(prompt, role=role, settings=settings)
            prompt_tokens = _estimate_tokens(prompt)
            completion_tokens = _estimate_tokens(raw)
            provider = "sample_cache"
        else:
            raw, prompt_tokens, completion_tokens, reasoning_chars = await _with_retries(
                lambda: _nvidia_chat(
                    prompt=prompt,
                    system=system,
                    model=model,
                    role=role,
                    max_tokens=max_tokens,
                    temperature=0.2,
                    json_mode=True,
                    settings=settings,
                )
            )
            if settings.record_llm_cache:
                try:
                    parsed = extract_json_value(raw)
                    _record_llm_cache(role, prompt, parsed, settings)
                except LLMOutputError:
                    _record_llm_cache(role, prompt, raw, settings)

        value = extract_json_value(raw)
        if is_echoed_schema(value):
            raise LLMSchemaEchoError(_SCHEMA_ECHO_REPAIR)
        model_obj = schema.model_validate(value)
        _log_call(
            role=role,
            model=model,
            latency_ms=_elapsed_ms(started),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            success=True,
            prompt=prompt,
            reasoning_chars=reasoning_chars,
            provider=provider,
        )
        return model_obj
    except SampleCacheMiss:
        raise
    except Exception as exc:
        _log_call(
            role=role,
            model=model,
            latency_ms=_elapsed_ms(started),
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            prompt=prompt,
            error=str(exc),
            reasoning_chars=getattr(exc, "reasoning_chars", 0),
        )
        raise


async def _nvidia_chat(
    *,
    prompt: str,
    system: str | None,
    model: str,
    role: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
    settings: Settings,
) -> tuple[str, int, int, int]:
    """Call NVIDIA chat. Never treats reasoning_content as the answer.

    By default thinking is off. Roles in REASONING_ROLES get enable_thinking
    and at least 4000 max_tokens. Empty message.content triggers one retry with
    thinking forced off and doubled max_tokens.
    """
    enable_thinking = role in settings.reasoning_roles
    effective_max = max(max_tokens, _REASONING_MIN_TOKENS) if enable_thinking else max_tokens

    text, prompt_tokens, completion_tokens, reasoning_chars = await _nvidia_chat_once(
        prompt=prompt,
        system=system,
        model=model,
        max_tokens=effective_max,
        temperature=temperature,
        json_mode=json_mode,
        enable_thinking=enable_thinking,
        settings=settings,
    )
    if _has_content(text):
        return text, prompt_tokens, completion_tokens, reasoning_chars

    logger.warning(
        "Empty message.content for role=%s model=%s; retrying with thinking disabled",
        role,
        model,
    )
    text2, prompt_tokens2, completion_tokens2, reasoning_chars2 = await _nvidia_chat_once(
        prompt=prompt,
        system=system,
        model=model,
        max_tokens=effective_max * 2,
        temperature=temperature,
        json_mode=json_mode,
        enable_thinking=False,
        settings=settings,
    )
    total_reasoning = reasoning_chars + reasoning_chars2
    if _has_content(text2):
        return text2, prompt_tokens2, completion_tokens2, total_reasoning

    raise LLMOutputError(
        f"Model returned empty content for role={role!r} model={model!r} "
        f"(reasoning_chars={total_reasoning})",
        reasoning_chars=total_reasoning,
    )


async def _nvidia_chat_once(
    *,
    prompt: str,
    system: str | None,
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
    enable_thinking: bool,
    settings: Settings,
) -> tuple[str, int, int, int]:
    client = _get_client(settings)
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(**kwargs),
            timeout=settings.llm_timeout_seconds,
        )
    except BadRequestError as exc:
        if json_mode and _rejects_response_format(exc):
            kwargs.pop("response_format", None)
            response = await asyncio.wait_for(
                client.chat.completions.create(**kwargs),
                timeout=settings.llm_timeout_seconds,
            )
        else:
            raise

    message = response.choices[0].message
    text = _message_content(message)
    reasoning_chars = _reasoning_chars(message)
    usage = response.usage
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    return text, prompt_tokens, completion_tokens, reasoning_chars


def _has_content(text: str) -> bool:
    return bool(text and text.strip())


def _message_content(message: Any) -> str:
    """Return message.content only. Never fall back to reasoning_content."""
    content = getattr(message, "content", None)
    if content is None:
        return ""
    return str(content)


def _reasoning_chars(message: Any) -> int:
    """Character length of reasoning_content when the SDK exposes it."""
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning is None:
        extra = getattr(message, "model_extra", None) or {}
        if isinstance(extra, dict):
            reasoning = extra.get("reasoning_content")
    if not isinstance(reasoning, str):
        return 0
    return len(reasoning)


async def _nvidia_embed(
    texts: list[str],
    *,
    input_type: str,
    settings: Settings,
) -> list[list[float]]:
    client = _get_client(settings)
    model = settings.nvidia_embed_model
    vectors: list[list[float] | None] = [None] * len(texts)

    for start in range(0, len(texts), _EMBED_BATCH_SIZE):
        batch = texts[start : start + _EMBED_BATCH_SIZE]
        response = await asyncio.wait_for(
            client.embeddings.create(
                model=model,
                input=batch,
                extra_body={"input_type": input_type, "truncate": "END"},
            ),
            timeout=settings.llm_timeout_seconds,
        )
        for item in response.data:
            vectors[start + item.index] = list(item.embedding)

    if any(vector is None for vector in vectors):
        raise LLMUnavailable("Embedding response missing vectors")
    return [vector for vector in vectors if vector is not None]


async def _with_retries(operation: Any) -> Any:
    """Run an async operation with 2 retries on timeout / 429 / 5xx."""
    last_error: Exception | None = None
    for attempt in range(_LLM_ATTEMPTS):
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001 - classify below
            last_error = exc
            if not _is_retryable(exc) or attempt >= _LLM_ATTEMPTS - 1:
                if isinstance(exc, LLMUnavailable):
                    raise
                if _is_retryable(exc) or isinstance(
                    exc, (TimeoutError, asyncio.TimeoutError, APITimeoutError, RateLimitError)
                ):
                    break
                raise
            delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
            logger.warning("LLM retryable failure attempt=%s delay=%ss: %s", attempt, delay, exc)
            await asyncio.sleep(delay)
    raise LLMUnavailable(
        f"LLM unavailable after {_LLM_ATTEMPTS} attempts: {last_error}"
    ) from last_error


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, APITimeoutError, RateLimitError)):
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    if isinstance(status, int) and status >= 500:
        return True
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        return True
    return False


def _rejects_response_format(exc: BadRequestError) -> bool:
    message = str(exc).lower()
    return "response_format" in message or "json_object" in message


def _get_client(settings: Settings) -> AsyncOpenAI:
    """Create the NVIDIA OpenAI-compatible client once and reuse it."""
    global _client, _client_key
    api_key = settings.nvidia_api_key.get_secret_value()
    if not api_key:
        raise LLMUnavailable("NVIDIA_API_KEY is not set")
    if _client is None or _client_key != api_key:
        _client = AsyncOpenAI(
            base_url=_NVIDIA_BASE_URL,
            api_key=api_key,
            timeout=settings.llm_timeout_seconds,
        )
        _client_key = api_key
    return _client


def _sample_text(prompt: str, *, role: str, settings: Settings) -> str:
    """Load a canned response from data/cache/llm/<role>.json.

    Agent roles (router/writer/critic/actions/web_extract) never fall back to
    a global ``"default"`` entry — a miss raises ``SampleCacheMiss``.
    """
    path = Path(settings.cache_dir) / "llm" / f"{role}.json"
    cache = _read_json_file(path)
    if not isinstance(cache, dict):
        raise LLMUnavailable(f"Sample LLM cache must be a JSON object: {path}")

    key = _prompt_key(prompt)
    if key in cache:
        return _cache_entry_to_text(cache[key])
    if role in _NO_DEFAULT_ROLES:
        raise SampleCacheMiss(role, key)
    if "default" in cache:
        return _cache_entry_to_text(cache["default"])
    raise SampleCacheMiss(role, key)


def _sample_embedding(text: str, settings: Settings) -> list[float]:
    """Load a cached embedding or build a deterministic 384-d vector."""
    path = Path(settings.cache_dir) / "embeddings.json"
    cache = _read_json_file(path)
    if isinstance(cache, dict):
        key = _prompt_key(text)
        if key in cache and isinstance(cache[key], list):
            return [float(x) for x in cache[key]]
        if "default" in cache and isinstance(cache["default"], list):
            return [float(x) for x in cache["default"]]
    return _hash_embedding(text)


def _cache_entry_to_text(entry: Any) -> str:
    """Normalize a cache value to model text (string or JSON text)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and set(entry) == {"text"}:
        return str(entry["text"])
    return json.dumps(entry)


def _record_llm_cache(role: str, prompt: str, value: Any, settings: Settings) -> None:
    path = Path(settings.cache_dir) / "llm" / f"{role}.json"
    cache = _read_json_file(path)
    if not isinstance(cache, dict):
        cache = {}
    cache[_prompt_key(prompt)] = value
    # Never write a global "default" for agent roles — keys must match prompts.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, default=str) + "\n", encoding="utf-8")


def _record_embeddings(texts: list[str], vectors: list[list[float]], settings: Settings) -> None:
    path = Path(settings.cache_dir) / "embeddings.json"
    cache = _read_json_file(path)
    if not isinstance(cache, dict):
        cache = {}
    for text, vector in zip(texts, vectors, strict=True):
        cache[_prompt_key(text)] = vector
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache) + "\n", encoding="utf-8")


def _read_json_file(path: Path) -> Any:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"Cache is not valid JSON: {path}") from exc


def _prompt_key(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()


def _hash_embedding(text: str, dim: int = _EMBED_DIM) -> list[float]:
    """Deterministic pseudo-embedding for sample mode."""
    values: list[float] = []
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    while len(values) < dim:
        for byte in seed:
            values.append((byte / 127.5) - 1.0)
            if len(values) >= dim:
                break
        seed = hashlib.sha256(seed).digest()
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _log_call(
    *,
    role: str,
    model: str,
    latency_ms: int,
    prompt_tokens: int,
    completion_tokens: int,
    success: bool,
    prompt: str,
    error: str | None = None,
    reasoning_chars: int = 0,
    provider: str | None = None,
) -> None:
    """Write one LLM event. Never logs the API key or the full prompt."""
    kind = "embed" if role == "embed" else "generate"
    if kind == "embed":
        summary = (
            f"embed count≈{max(1, prompt_tokens)} provider={provider or model} "
            f"ms={latency_ms} success={success}"
        )
    else:
        summary = (
            f"{role} model={model} ms={latency_ms} "
            f"tokens={prompt_tokens}+{completion_tokens} success={success}"
        )
    payload: dict[str, Any] = {
        "summary": summary,
        "role": role,
        "model": model,
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "success": success,
        "prompt_preview": prompt[:_PROMPT_PREVIEW_CHARS],
        "reasoning_chars": reasoning_chars,
    }
    if provider is not None:
        payload["provider"] = provider
    if error is not None:
        payload["error"] = error
    tracer = Tracer.current()
    if tracer is not None:
        tracer.event("llm", kind, payload)
