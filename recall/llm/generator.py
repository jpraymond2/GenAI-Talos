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
from pathlib import Path
from typing import Any

from openai import APIStatusError, APITimeoutError, AsyncOpenAI, BadRequestError, RateLimitError
from pydantic import BaseModel, ValidationError

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

_client: AsyncOpenAI | None = None
_client_key: str | None = None


class LLMUnavailable(Exception):
    """Raised when NVIDIA fails after retries; callers may fall back."""


class LLMOutputError(Exception):
    """Raised when the model response cannot be parsed or validated."""

    def __init__(self, message: str, *, reasoning_chars: int = 0) -> None:
        super().__init__(message)
        self.reasoning_chars = reasoning_chars


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
        )
        return text
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
) -> BaseModel:
    """Generate JSON, extract robustly, and validate against schema.

    Appends the schema to the system prompt. Tries response_format json_object,
    then retries without it if the API rejects that parameter. On validation
    failure, retries once with the error appended; then raises LLMOutputError.
    """
    schema_block = (
        "Return only valid JSON matching this schema:\n"
        f"{json.dumps(schema.model_json_schema(), indent=2)}"
    )
    instructions = f"{system}\n\n{schema_block}".strip() if system else schema_block
    try:
        return await _generate_json_once(
            prompt, instructions, schema, role=role, max_tokens=max_tokens
        )
    except (ValidationError, LLMOutputError) as first_error:
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
    """Strip think/fences and return the first top-level JSON object or array."""
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
            return value
        except json.JSONDecodeError:
            continue
    raise LLMOutputError(f"No JSON object or array found in model output: {cleaned[:200]!r}")


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
        if use_sample_provider(settings):
            raw = _sample_text(prompt, role=role, settings=settings)
            prompt_tokens = _estimate_tokens(prompt)
            completion_tokens = _estimate_tokens(raw)
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
                    _record_llm_cache(role, prompt, extract_json_value(raw), settings)
                except LLMOutputError:
                    _record_llm_cache(role, prompt, raw, settings)

        value = extract_json_value(raw)
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
        )
        return model_obj
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
    """Load a canned response from data/cache/llm/<role>.json."""
    path = Path(settings.cache_dir) / "llm" / f"{role}.json"
    cache = _read_json_file(path)
    if not isinstance(cache, dict):
        raise LLMUnavailable(f"Sample LLM cache must be a JSON object: {path}")

    key = _prompt_key(prompt)
    if key in cache:
        return _cache_entry_to_text(cache[key])
    if "default" in cache:
        return _cache_entry_to_text(cache["default"])
    raise LLMUnavailable(f"Sample LLM cache missing key {key!r} and default in {path}")


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
    if "default" not in cache:
        cache["default"] = value
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
) -> None:
    """Write one LLM event. Never logs the API key or the full prompt."""
    payload: dict[str, Any] = {
        "role": role,
        "model": model,
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "success": success,
        "prompt_preview": prompt[:_PROMPT_PREVIEW_CHARS],
        "reasoning_chars": reasoning_chars,
    }
    if error is not None:
        payload["error"] = error
    tracer = Tracer.current()
    if tracer is not None:
        tracer.event("llm", "generate" if role != "embed" else "embed", payload)
