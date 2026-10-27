"""NVIDIA Magpie TTS: Recall's speaking voice (same NVIDIA key and Riva gRPC endpoint as Parakeet ASR).

    wav = synthesize_wav("Coffee with Alex on Wednesday at 10.")

Returns a 16-bit mono WAV. Long text is split into sentences and stitched, so one
slow sentence never blocks the rest. A small cache makes repeated lines instant.
"""

from __future__ import annotations

import io
import re
import threading
import wave
from collections import OrderedDict

from recall.config import Settings, get_settings

_RIVA_URI = "grpc.nvcf.nvidia.com:443"
SAMPLE_RATE = 22050
MAX_CHARS = 1200
_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_lock = threading.Lock()


class TTSUnavailable(RuntimeError):
    """Magpie couldn't speak (no key, network, quota) — callers fall back to the browser voice."""


def _service(settings: Settings):  # noqa: ANN202
    import riva.client

    key = settings.nvidia_api_key.get_secret_value().strip()
    if not key:
        raise TTSUnavailable("NVIDIA_API_KEY is empty")
    auth = riva.client.Auth(
        uri=_RIVA_URI,
        use_ssl=True,
        metadata_args=[["function-id", settings.tts_function_id.strip()], ["authorization", f"Bearer {key}"]],
    )
    return riva.client.SpeechSynthesisService(auth)


def _clean(text: str) -> str:
    text = re.sub(r"[*_`#>\[\]]", "", text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = text.replace("—", ", ").replace("–", " to ").replace("&", " and ")
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    for p in parts:
        if out and len(out[-1]) + len(p) < 180:
            out[-1] = f"{out[-1]} {p}"
        elif p:
            out.append(p)
    return out


def _language(text: str) -> tuple[str, str]:
    """French voice for French text (bilingual users mix both)."""
    low = text.lower()
    fr = len(re.findall(r"\b(le|la|les|des|est|pour|avec|vous|tu|je|une|du|au)\b", low))
    en = len(re.findall(r"\b(the|and|is|for|with|you|to|of|a|on|at)\b", low))
    return ("fr-FR", "Magpie-Multilingual.FR-FR.Louise") if fr > en + 2 else ("en-US", "")


def synthesize_wav(text: str, settings: Settings | None = None) -> bytes:
    settings = settings or get_settings()
    cleaned = _clean(text)
    if not cleaned:
        raise TTSUnavailable("nothing to say")
    lang, fr_voice = _language(cleaned)
    voice = fr_voice or settings.tts_voice
    key = (voice, cleaned)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]

    import riva.client

    def run(v: str, lang_code: str) -> bytes:
        service = _service(settings)
        return b"".join(
            service.synthesize(
                part, voice_name=v, language_code=lang_code,
                encoding=riva.client.AudioEncoding.LINEAR_PCM, sample_rate_hz=SAMPLE_RATE,
            ).audio
            for part in _sentences(cleaned)
        )

    try:
        try:
            pcm = run(voice, lang)
        except TTSUnavailable:
            raise
        except Exception:  # noqa: BLE001 — e.g. the French voice isn't on this deployment: use the English one
            if voice == settings.tts_voice:
                raise
            pcm = run(settings.tts_voice, "en-US")
    except TTSUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TTSUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    wav = buf.getvalue()
    with _lock:
        _cache[key] = wav
        while len(_cache) > 48:
            _cache.popitem(last=False)
    return wav
