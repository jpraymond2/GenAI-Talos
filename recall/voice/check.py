"""Non-interactive NVIDIA + Riva credential checks (no microphone)."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from recall.config import (
    Settings,
    get_settings,
    is_uuid,
    nvidia_credential_warnings,
)
from recall.tracing import Tracer
from recall.voice.audio_convert import make_silence_wav
from recall.voice.stt import TranscriptionError, _transcribe_riva


@dataclass(frozen=True)
class CheckResult:
    """One PASS/FAIL line for ``recall voice --check``."""

    name: str
    ok: bool
    detail: str

    def format(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        return f"[{status}] {self.name}: {self.detail}"


def _preview(secret: str) -> str:
    text = (secret or "").strip()
    if not text:
        return "(empty)"
    return f"{text[:6]}…"


async def _check_chat(settings: Settings, tracer: Tracer) -> CheckResult:
    """Tiny LLM call with SAMPLE_MODE forced off for this check only."""
    from recall.llm.generator import generate

    if not settings.nvidia_api_key.get_secret_value().strip():
        return CheckResult("nvidia_chat", False, "NVIDIA_API_KEY is empty")
    if not settings.nvidia_api_key.get_secret_value().strip().startswith("nvapi-"):
        return CheckResult(
            "nvidia_chat",
            False,
            f"NVIDIA_API_KEY shape looks wrong (prefix {_preview(settings.nvidia_api_key.get_secret_value())})",
        )

    prev = os.environ.get("SAMPLE_MODE")
    os.environ["SAMPLE_MODE"] = "false"
    get_settings.cache_clear()
    try:
        # generate() reads get_settings(); SAMPLE_MODE=false for this probe only.
        with tracer.activate():
            text = await generate(
                "Reply with exactly the word OK.",
                system="You are a connectivity probe. Reply with one word only.",
                role="critic",
            )
        preview = (text or "").replace("\n", " ").strip()[:40]
        if not preview:
            return CheckResult("nvidia_chat", False, "empty model response")
        return CheckResult("nvidia_chat", True, f"model responded ({preview!r})")
    except Exception as exc:  # noqa: BLE001 - surface reason, never the key
        return CheckResult("nvidia_chat", False, f"{type(exc).__name__}: {exc}")
    finally:
        if prev is None:
            os.environ.pop("SAMPLE_MODE", None)
        else:
            os.environ["SAMPLE_MODE"] = prev
        get_settings.cache_clear()


def _check_riva(settings: Settings) -> CheckResult:
    function_id = (settings.riva_asr_function_id or "").strip()
    api_key = settings.nvidia_api_key.get_secret_value().strip()
    if not api_key:
        return CheckResult("riva_asr", False, "NVIDIA_API_KEY is empty")
    if not function_id:
        return CheckResult(
            "riva_asr",
            False,
            "RIVA_ASR_FUNCTION_ID is empty — copy the UUID from the Parakeet API tab",
        )
    if function_id.startswith("nvapi-"):
        return CheckResult(
            "riva_asr",
            False,
            "RIVA_ASR_FUNCTION_ID looks like an API key; expected a UUID function id",
        )
    if not is_uuid(function_id):
        return CheckResult("riva_asr", False, "RIVA_ASR_FUNCTION_ID is not a UUID")

    live = settings.model_copy(update={"sample_mode": False})
    wav = make_silence_wav(seconds=1.0)
    try:
        text = _transcribe_riva(wav, live)
        # Silence often yields empty; acceptance means the RPC succeeded.
        detail = "accepted silent wav"
        if text.strip():
            detail += f" (transcript chars={len(text.strip())})"
        return CheckResult("riva_asr", True, detail)
    except TranscriptionError as exc:
        # Empty transcript after a successful RPC is still a soft pass for connectivity.
        msg = str(exc)
        if "empty transcript" in msg.lower():
            return CheckResult("riva_asr", True, "RPC ok (empty transcript on silence)")
        return CheckResult("riva_asr", False, f"{type(exc).__name__}: {msg}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("riva_asr", False, f"{type(exc).__name__}: {exc}")


def run_voice_checks(*, settings: Settings | None = None) -> list[CheckResult]:
    """Run chat + Riva checks; print nothing (caller prints)."""
    get_settings.cache_clear()
    active = settings if settings is not None else get_settings()
    tracer = Tracer(logs_dir=active.logs_dir)

    results: list[CheckResult] = []
    warns = nvidia_credential_warnings(active)
    if warns:
        results.append(
            CheckResult(
                "env_shape",
                False,
                "; ".join(warns),
            )
        )
    else:
        results.append(CheckResult("env_shape", True, "NVIDIA_API_KEY / RIVA_ASR_FUNCTION_ID shapes look ok"))

    results.append(asyncio.run(_check_chat(active, tracer)))
    results.append(_check_riva(active))
    return results


def print_voice_checks(results: list[CheckResult] | None = None) -> int:
    """Print PASS/FAIL lines; return process exit code (0 if all pass)."""
    rows = results if results is not None else run_voice_checks()
    print("Recall voice --check")
    print(f"  NVIDIA_API_KEY prefix: {_preview(get_settings().nvidia_api_key.get_secret_value())}")
    fid = (get_settings().riva_asr_function_id or "").strip()
    print(f"  RIVA_ASR_FUNCTION_ID: {fid or '(empty)'}")
    print()
    for row in rows:
        print(row.format())
    failed = sum(1 for row in rows if not row.ok)
    print()
    print(f"SUMMARY: {len(rows) - failed}/{len(rows)} PASS")
    return 0 if failed == 0 else 1
