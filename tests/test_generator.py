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
    LLMUnavailable,
    embed,
    extract_json_value,
    generate,
    generate_json,
    reset_client,
    resolve_model,
    resolve_tier,
    strip_think,
    use_sample_provider,
)
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
    get_settings.cache_clear()
    assert use_sample_provider() is True


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
    (cache / "writer.json").write_text(
        json.dumps(
            {
                "default": "<think>secret reasoning</think>\nHello there friend now."
            }
        ),
        encoding="utf-8",
    )
    text = asyncio.run(generate("Say hello in five words.", role="writer"))
    assert text == "Hello there friend now."
    assert "<think>" not in text


def test_generate_json_sample_uses_prompt_key_then_default() -> None:
    result = asyncio.run(
        generate_json("structured fact please", None, TinySchema, role="actions")
    )
    assert result.name == "Ada"
    assert result.count == 3

    fallback = asyncio.run(generate_json("other prompt", None, TinySchema, role="actions"))
    assert fallback.name == "fallback"


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
    with pytest.raises(LLMUnavailable):
        asyncio.run(generate("no cache", role="router"))
