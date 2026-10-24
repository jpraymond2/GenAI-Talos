"""Normalize browser uploads to 16 kHz mono 16-bit WAV."""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path


class UnsupportedAudio(ValueError):
    """Raised when the payload cannot be converted to WAV."""


def ensure_wav_16k_mono(data: bytes, *, content_type: str = "", filename: str = "") -> bytes:
    """Return 16 kHz mono int16 WAV bytes.

    Accepts WAV passthrough (re-encoded if needed). WebM/MP4 require ffmpeg;
    without ffmpeg those formats raise UnsupportedAudio (map to HTTP 415).
    """
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    is_wav = (
        name.endswith(".wav")
        or "wav" in ctype
        or data[:4] == b"RIFF"
    )
    if is_wav:
        return _resample_wav_if_needed(data)

    needs_ffmpeg = (
        name.endswith((".webm", ".mp4", ".m4a", ".ogg", ".mpeg", ".mp3"))
        or any(token in ctype for token in ("webm", "mp4", "mpeg", "ogg", "m4a"))
    )
    if not needs_ffmpeg:
        # Unknown: try as WAV first, else require ffmpeg.
        try:
            return _resample_wav_if_needed(data)
        except Exception as exc:  # noqa: BLE001
            raise UnsupportedAudio(
                "Unsupported audio format. Send WAV, or install ffmpeg for webm/mp4."
            ) from exc

    if not shutil.which("ffmpeg"):
        raise UnsupportedAudio(
            "audio/mp4 and webm require ffmpeg on the server PATH. "
            "Install ffmpeg (e.g. winget install ffmpeg), restart the shell, "
            "or upload WAV instead."
        )
    return _ffmpeg_to_wav(
        data,
        suffix=_suffix(name, ctype),
        content_type=content_type or ctype,
    )


def _suffix(name: str, ctype: str) -> str:
    for ext in (".webm", ".mp4", ".m4a", ".ogg", ".mp3"):
        if name.endswith(ext):
            return ext
    if "webm" in ctype:
        return ".webm"
    if "mp4" in ctype or "m4a" in ctype:
        return ".mp4"
    return ".bin"


def _is_mp4_like(suffix: str, content_type: str) -> bool:
    ctype = (content_type or "").lower()
    return suffix in (".mp4", ".m4a") or "mp4" in ctype or "m4a" in ctype


def _ffmpeg_input_attempts(suffix: str, content_type: str) -> list[list[str]]:
    """Default demux, then mp4-specific fallbacks for fragmented/browser blobs."""
    attempts: list[list[str]] = [[]]
    if _is_mp4_like(suffix, content_type):
        attempts.append(["-f", "mov"])
        attempts.append(["-analyzeduration", "0", "-probesize", "32k"])
    return attempts


def _stderr_tail(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    text = text.strip()
    return text[-2000:] if len(text) > 2000 else text


def _ffmpeg_to_wav(
    data: bytes,
    *,
    suffix: str,
    content_type: str = "",
) -> bytes:
    size = len(data)
    ctype = content_type or ""
    last_detail = ""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in{suffix}"
        dst = Path(tmp) / "out.wav"
        src.write_bytes(data)
        for extra_in in _ffmpeg_input_attempts(suffix, ctype):
            if dst.exists():
                dst.unlink()
            cmd = [
                "ffmpeg",
                "-y",
                *extra_in,
                "-i",
                str(src),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(dst),
            ]
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                return dst.read_bytes()
            except subprocess.CalledProcessError as exc:
                last_detail = _stderr_tail(exc.stderr) or str(exc)
                print(
                    f"[voice] ffmpeg attempt failed extra_in={extra_in!r}: {last_detail}",
                    flush=True,
                )
            except subprocess.TimeoutExpired as exc:
                err_tail = _stderr_tail(exc.stderr)
                last_detail = f"timed out after 30s{(': ' + err_tail) if err_tail else ''}"
                print(
                    f"[voice] ffmpeg attempt timed out extra_in={extra_in!r}",
                    flush=True,
                )
                break

    msg = (
        f"ffmpeg conversion failed (content_type={ctype!r} size={size}): "
        f"{last_detail or 'unknown error'}"
    )
    print(f"[voice] {msg}", flush=True)
    raise UnsupportedAudio(msg)


def _resample_wav_if_needed(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if channels == 1 and width == 2 and rate == 16000:
        return data
    # Prefer ffmpeg for resample when available.
    if shutil.which("ffmpeg"):
        return _ffmpeg_to_wav(data, suffix=".wav", content_type="audio/wav")
    if channels == 1 and width == 2:
        # Naive nearest-neighbor downsample for tests without ffmpeg.
        import array

        samples = array.array("h")
        samples.frombytes(frames)
        if rate == 16000:
            return data
        step = max(rate / 16000.0, 0.001)
        out = array.array("h")
        i = 0.0
        while int(i) < len(samples):
            out.append(samples[int(i)])
            i += step
        return _pack_wav(out.tobytes(), 16000)
    raise UnsupportedAudio(
        f"WAV must be 16-bit mono (got ch={channels} width={width} rate={rate}); "
        "install ffmpeg to convert."
    )


def _pack_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buf.getvalue()


def make_silence_wav(*, seconds: float = 0.1, rate: int = 16000) -> bytes:
    """Tiny silent WAV for tests."""
    import array

    n = max(int(seconds * rate), 1)
    pcm = array.array("h", [0] * n).tobytes()
    return _pack_wav(pcm, rate)
