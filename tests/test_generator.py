"""Tests for the NVIDIA injected generator (sample provider + parsing)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from recall.config import ModelTier, get_settings
from recall.llm.generator import (
    LLMOutputError,
    LLMSchemaEchoError,
    LLMUnavailable,
    SampleCacheMiss,
    build_json_instructions,
    embed,
    extract_json_value,
    generate,
    generate_json,
    is_echoed_schema,
    reset_client,
    resolve_model,
    resolve_tier,
    strip_think,
    use_sample_provider,
)
from recall.schemas import CriticVerdict
from recall.tracing import Tracer


@pytest.fixture(autouse=True)
def _sample_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    cache = tmp_path / "cache"
    llm_dir = cache / "llm"
    llm_dir.mkdir(parents=True)
    (llm_dir / "default.json").write_text(
        json.dumps({"default": "Recall sample reply"}),
        encoding="utf-8",
    )
    (llm_dir / "writer.json").write_text(
        json.dumps({"default": "Hello from writer sample."}),
        encoding="utf-8",
    )
    (llm_dir / "critic.json").write_text(
        json.dumps({"default": "Critique sample."}),
        encoding="utf-8",
    )
    prompt = "structured fact please"
    key = hashlib.sha1(prompt.encode("utf-8")).hexdigest()
    (llm_dir / "actions.json").write_text(
        json.dumps(
            {
                "default": {"name": "fallback", "count": 0},
                key: {"name": "Ada", "count": 3},
            }
        ),
        encoding="utf-8",
    )
    (cache / "embeddings.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("CACHE_DIR", str(cache))
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv(
        "ROLE_MODELS",
        json.dumps(
            {
                "router": "main",
                "writer": "main",
                "actions": "main",
                "web_extract": "main",
                "default": "main",
                "critic": "fast",
                "memory_extract": "fast",
            }
        ),
    )
    get_settings.cache_clear()
    reset_client()
    yield
    get_settings.cache_clear()
    reset_client()


class TinySchema(BaseModel):
    name: str
    count: int


def test_use_sample_when_sample_mode_or_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert use_sample_provider() is True
    monkeypatch.setenv("SAMPLE_MODE", "false")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("LLM_LIVE", "false")
    get_settings.cache_clear()
    assert use_sample_provider() is True


def test_llm_live_disables_sample_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("LLM_LIVE", "true")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    get_settings.cache_clear()
    assert use_sample_provider() is False


def test_role_routing_main_vs_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "false")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
    monkeypatch.setenv("NVIDIA_MAIN_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    monkeypatch.setenv("NVIDIA_FAST_MODEL", "nvidia/nemotron-3-super-120b-a12b")
    get_settings.cache_clear()

    assert resolve_tier("writer") is ModelTier.MAIN
    assert resolve_tier("critic") is ModelTier.FAST
    assert resolve_tier("memory_extract") is ModelTier.FAST
    assert resolve_tier("unknown_role") is ModelTier.MAIN
    assert resolve_model("writer") == "nvidia/nemotron-3-ultra-550b-a55b"
    assert resolve_model("critic") == "nvidia/nemotron-3-super-120b-a12b"


def test_generate_sample_returns_canned_text() -> None:
    text = asyncio.run(generate("I'm meeting Karim", role="default"))
    assert text == "Recall sample reply"


def test_generate_strips_think_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = Path(get_settings().cache_dir) / "llm"
    prompt = "Say hello in five words."
    key = hashlib.sha1(prompt.encode("utf-8")).hexdigest()
    (cache / "writer.json").write_text(
        json.dumps(
            {
                key: "<think>secret reasoning</think>\nHello there friend now."
            }
        ),
        encoding="utf-8",
    )
    text = asyncio.run(generate(prompt, role="writer"))
    assert text == "Hello there friend now."
    assert "<think>" not in text


def test_generate_json_sample_uses_prompt_key_no_default_fallback() -> None:
    result = asyncio.run(
        generate_json("structured fact please", None, TinySchema, role="actions")
    )
    assert result.name == "Ada"
    assert result.count == 3

    with pytest.raises(SampleCacheMiss):
        asyncio.run(generate_json("other prompt", None, TinySchema, role="actions"))


def test_strip_think_and_extract_json_from_fenced_think_output() -> None:
    raw = """<think>
I should answer carefully.
</think>
```json
{"name": "Ada", "count": 3}
```
"""
    assert strip_think(raw).startswith("```json")
    value = extract_json_value(raw)
    assert value == {"name": "Ada", "count": 3}
    model = TinySchema.model_validate(value)
    assert model.name == "Ada"


def test_extract_json_from_prefixed_prose() -> None:
    raw = 'Here you go:\n{"name": "Karim", "count": 2}\nThanks!'
    assert extract_json_value(raw) == {"name": "Karim", "count": 2}


def test_generate_json_raises_llm_output_error_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def always_invalid(
        prompt: str,
        system: str,
        schema: type[BaseModel],
        *,
        role: str = "default",
        max_tokens: int = 1500,
    ) -> BaseModel:
        _ = (system, schema, role, max_tokens)
        calls["n"] += 1
        raise LLMOutputError(f"bad for {prompt[:12]}")

    monkeypatch.setattr("recall.llm.generator._generate_json_once", always_invalid)
    with pytest.raises(LLMOutputError):
        asyncio.run(generate_json("x", None, TinySchema, role="default"))
    assert calls["n"] == 2


def test_embed_sample_deterministic_dim() -> None:
    vectors = asyncio.run(embed(["alpha", "beta"], input_type="query"))
    assert len(vectors) == 2
    assert len(vectors[0]) == 384
    assert vectors[0] != vectors[1]
    again = asyncio.run(embed(["alpha"], input_type="query"))
    assert again[0] == vectors[0]


def test_generate_logs_preview_never_api_key(tmp_path: Path) -> None:
    tracer = Tracer(logs_dir=tmp_path / "logs")
    with tracer.activate():
        asyncio.run(generate("hello from preview test", role="default"))

    record = json.loads(tracer.path.read_text(encoding="utf-8").strip())
    payload = record["payload"]
    assert payload["role"] == "default"
    assert payload["model"] == "sample"
    assert payload["success"] is True
    assert payload["prompt_preview"] == "hello from preview test"
    assert "prompt_tokens" in payload
    assert "completion_tokens" in payload
    dumped = json.dumps(record).lower()
    assert "api_key" not in dumped
    assert "nvapi-" not in dumped


def test_missing_sample_cache_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "empty-cache"))
    get_settings.cache_clear()
    with pytest.raises(SampleCacheMiss):
        asyncio.run(generate("no cache", role="router"))


def test_empty_content_with_reasoning_retries_then_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "false")
    monkeypatch.setenv("LLM_LIVE", "false")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
    monkeypatch.setenv("REASONING_ROLES", "")
    get_settings.cache_clear()
    reset_client()

    calls: list[dict[str, object]] = []
    reasoning = "x" * 42

    class _Message:
        def __init__(self) -> None:
            self.content = None
            self.reasoning_content = reasoning

    class _Choice:
        def __init__(self) -> None:
            self.message = _Message()

    class _Response:
        def __init__(self) -> None:
            self.choices = [_Choice()]
            self.usage = None

    class _Completions:
        async def create(self, **kwargs: object) -> _Response:
            calls.append(kwargs)
            return _Response()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _Client:
        def __init__(self) -> None:
            self.chat = _Chat()

    monkeypatch.setattr("recall.llm.generator._get_client", lambda settings: _Client())

    tracer = Tracer(logs_dir=tmp_path / "logs")
    with tracer.activate():
        with pytest.raises(LLMOutputError, match="empty content") as exc_info:
            asyncio.run(generate("Say hello in five words.", role="writer", max_tokens=1200))

    assert exc_info.value.reasoning_chars == 84  # 42 + 42 from both attempts
    assert len(calls) == 2
    first_extra = calls[0]["extra_body"]
    second_extra = calls[1]["extra_body"]
    assert isinstance(first_extra, dict)
    assert isinstance(second_extra, dict)
    assert first_extra["chat_template_kwargs"]["enable_thinking"] is False
    assert second_extra["chat_template_kwargs"]["enable_thinking"] is False
    assert calls[1]["max_tokens"] == 2400

    record = json.loads(tracer.path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["payload"]["success"] is False
    assert record["payload"]["reasoning_chars"] == 84


def test_build_json_instructions_uses_compact_fields_and_critic_example() -> None:
    text = build_json_instructions(CriticVerdict)
    assert "$defs" not in text
    assert '"properties"' not in text
    assert "overall:" in text
    assert '"pass"' in text or '"revise"' in text
    assert "EXAMPLE:" in text
    assert "Apartment search is not in e4." in text
    assert "Do not return the schema." in text


def test_is_echoed_schema_detects_json_schema_shape() -> None:
    assert is_echoed_schema(CriticVerdict.model_json_schema()) is True
    assert is_echoed_schema({"type": "object", "properties": {"overall": {"type": "string"}}})
    assert is_echoed_schema({"overall": "pass", "verdicts": []}) is False


def test_extract_json_value_rejects_echoed_schema() -> None:
    raw = json.dumps(CriticVerdict.model_json_schema())
    with pytest.raises(LLMSchemaEchoError, match="schema, not an answer"):
        extract_json_value(raw)


def test_generate_json_retries_when_model_echoes_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "false")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
    get_settings.cache_clear()
    reset_client()

    valid = {
        "overall": "pass",
        "verdicts": [
            {
                "index": 0,
                "decision": "keep",
                "reason": "Supported by e2.",
                "fix_hint": None,
            }
        ],
        "missing": [],
        "refetch": [],
    }
    calls: list[str] = []

    async def fake_nvidia_chat(**kwargs: object) -> tuple[str, int, int, int]:
        prompt = str(kwargs.get("prompt", ""))
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(CriticVerdict.model_json_schema()), 10, 20, 0
        return json.dumps(valid), 10, 20, 0

    monkeypatch.setattr("recall.llm.generator._nvidia_chat", fake_nvidia_chat)

    result = asyncio.run(
        generate_json("Review this draft brief.", "You are the critic.", CriticVerdict, role="critic")
    )
    assert isinstance(result, CriticVerdict)
    assert result.overall == "pass"
    assert result.verdicts[0].decision == "keep"
    assert len(calls) == 2
    assert "You returned the schema, not an answer" in calls[1]


def test_reasoning_role_enables_thinking_and_min_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "false")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
    monkeypatch.setenv("REASONING_ROLES", "critic,memory_extract")
    get_settings.cache_clear()
    reset_client()

    calls: list[dict[str, object]] = []

    class _Message:
        def __init__(self) -> None:
            self.content = "ok"
            self.reasoning_content = "short"

    class _Choice:
        def __init__(self) -> None:
            self.message = _Message()

    class _Response:
        def __init__(self) -> None:
            self.choices = [_Choice()]
            self.usage = None

    class _Completions:
        async def create(self, **kwargs: object) -> _Response:
            calls.append(kwargs)
            return _Response()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _Client:
        def __init__(self) -> None:
            self.chat = _Chat()

    monkeypatch.setattr("recall.llm.generator._get_client", lambda settings: _Client())
    text = asyncio.run(generate("review", role="critic", max_tokens=500))
    assert text == "ok"
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 4000
    extra = calls[0]["extra_body"]
    assert isinstance(extra, dict)
    assert extra["chat_template_kwargs"]["enable_thinking"] is True
