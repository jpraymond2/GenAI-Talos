"""Speech-to-text: NVIDIA Riva Parakeet primary, optional faster-whisper fallback."""

from __future__ import annotations

import io
import time
import wave
from typing import Any

from pydantic import SecretStr

from recall.config import Settings, get_settings
from recall.tracing import Tracer

SAMPLE_TRANSCRIPT = "I'm meeting Karim Haddad"
_RIVA_URI = "grpc.nvcf.nvidia.com:443"


class TranscriptionError(RuntimeError):
    """Raised when no STT engine can produce a transcript."""


def _secret(value: SecretStr | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value)


def transcribe(
    wav_bytes: bytes,
    *,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
) -> str:
    """Transcribe 16 kHz mono WAV bytes to text.

    SAMPLE_MODE with no NVIDIA key returns a fixed demo transcript.
    Never logs audio bytes or API keys.
    """
    text, _engine = transcribe_detailed(wav_bytes, settings=settings, tracer=tracer)
    return text


def transcribe_detailed(
    wav_bytes: bytes,
    *,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
) -> tuple[str, str]:
    """Like ``transcribe`` but also returns the engine name (``sample``/``riva``/…)."""
    active = settings if settings is not None else get_settings()
    active_tracer = tracer if tracer is not None else Tracer.current()
    started = time.perf_counter()

    api_key = _secret(active.nvidia_api_key).strip()
    if active.sample_mode and not api_key:
        engine = "sample"
        _trace(active_tracer, engine=engine, latency_ms=_ms(started), ok=True)
        return SAMPLE_TRANSCRIPT, engine

    errors: list[str] = []

    try:
        text = _transcribe_riva(wav_bytes, active)
        engine = "riva"
        _trace(active_tracer, engine=engine, latency_ms=_ms(started), ok=True)
        return text, engine
    except Exception as exc:  # noqa: BLE001 - try fallback
        errors.append(f"riva: {type(exc).__name__}: {exc}")

    try:
        text = _transcribe_whisper(wav_bytes)
        engine = "faster-whisper"
        _trace(active_tracer, engine=engine, latency_ms=_ms(started), ok=True)
        return text, engine
    except ImportError:
        errors.append("faster-whisper: not installed (optional extra: uv sync --extra whisper)")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"faster-whisper: {type(exc).__name__}: {exc}")

    _trace(
        active_tracer,
        engine="failed",
        latency_ms=_ms(started),
        ok=False,
        detail="; ".join(errors)[:240],
    )
    raise TranscriptionError(
        "Speech-to-text unavailable. Set NVIDIA_API_KEY + RIVA_ASR_FUNCTION_ID for Riva, "
        "or install optional faster-whisper (`uv sync --extra whisper`). "
        f"Attempts: {'; '.join(errors)}"
    )


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _trace(
    tracer: Tracer | None,
    *,
    engine: str,
    latency_ms: int,
    ok: bool,
    detail: str | None = None,
) -> None:
    if tracer is None:
        return
    payload: dict[str, Any] = {
        "engine": engine,
        "latency_ms": latency_ms,
        "ok": ok,
    }
    if detail:
        payload["detail"] = detail
    tracer.event("voice", "stt", payload)


def _pcm_and_rate(wav_bytes: bytes) -> tuple[bytes, int]:
    """Extract LINEAR_PCM frames and sample rate from a WAV container."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if channels != 1 or sample_width != 2:
        raise TranscriptionError(
            f"Expected 16-bit mono WAV, got channels={channels} width={sample_width}"
        )
    return frames, rate


def _transcribe_riva(wav_bytes: bytes, settings: Settings) -> str:
    api_key = _secret(settings.nvidia_api_key).strip()
    function_id = (settings.riva_asr_function_id or "").strip()
    if not api_key:
        raise TranscriptionError("NVIDIA_API_KEY is required for Riva ASR")
    if not function_id:
        raise TranscriptionError("RIVA_ASR_FUNCTION_ID is required for Riva ASR")

    import riva.client

    pcm, rate = _pcm_and_rate(wav_bytes)
    auth = riva.client.Auth(
        uri=_RIVA_URI,
        use_ssl=True,
        metadata_args=[
            ["function-id", function_id],
            ["authorization", f"Bearer {api_key}"],
        ],
    )
    asr = riva.client.ASRService(auth)
    config = riva.client.RecognitionConfig(
        language_code="en-US",
        max_alternatives=1,
        enable_automatic_punctuation=True,
        sample_rate_hertz=rate,
        audio_channel_count=1,
        encoding=riva.client.AudioEncoding.LINEAR_PCM,
    )
    response = asr.offline_recognize(pcm, config)
    parts: list[str] = []
    for result in getattr(response, "results", []) or []:
        alts = getattr(result, "alternatives", None) or []
        if alts:
            transcript = getattr(alts[0], "transcript", "") or ""
            if transcript.strip():
                parts.append(transcript.strip())
    text = " ".join(parts).strip()
    if not text:
        raise TranscriptionError("Riva returned an empty transcript")
    return text


def _transcribe_whisper(wav_bytes: bytes) -> str:
    """Optional local fallback; ImportError if faster-whisper is not installed."""
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    pcm, rate = _pcm_and_rate(wav_bytes)
    import numpy as np

    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio, language="en", beam_size=1)
    text = " ".join(seg.text.strip() for seg in segments if seg.text).strip()
    if not text:
        raise TranscriptionError("faster-whisper returned an empty transcript")
    return text
