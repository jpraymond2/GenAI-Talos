"""Cross-platform text-to-speech: non-blocking and interruptible."""

from __future__ import annotations

import platform
import subprocess
import threading
from collections.abc import Sequence

_lock = threading.Lock()
_current: subprocess.Popen[str] | None = None


def interrupt() -> None:
    """Stop any in-flight speak() process."""
    global _current
    with _lock:
        proc = _current
        _current = None
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=1.5)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def speak(text: str) -> None:
    """Speak ``text`` asynchronously; a new call interrupts the previous one."""
    cleaned = (text or "").strip()
    if not cleaned:
        return
    interrupt()
    cmd = tts_command(cleaned)
    if cmd is None:
        print(f"[tts] {cleaned}")
        return
    with _lock:
        global _current
        _current = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )


def speak_stream(headline: str, points: Sequence[str] | None = None) -> None:
    """Speak the headline, then each talking point in order (interruptible)."""
    parts = [headline.strip()] if headline and headline.strip() else []
    for point in points or []:
        item = point.strip() if isinstance(point, str) else str(point).strip()
        if item:
            parts.append(item)
    if not parts:
        return
    # One process speaking joined lines keeps interrupt simple across platforms.
    speak(". ".join(parts))


def tts_command(text: str, *, system: str | None = None) -> list[str] | None:
    """Return the OS-specific TTS argv (used by speak and unit tests)."""
    os_name = (system or platform.system()).lower()
    if os_name == "darwin":
        return ["say", "-v", "Samantha", "-r", "190", text]
    if os_name == "windows":
        return _windows_speech_command(text)
    if os_name == "linux":
        if _which("espeak"):
            return ["espeak", text]
        if _which("espeak-ng"):
            return ["espeak-ng", text]
        return None
    return None


def _windows_speech_command(text: str) -> list[str]:
    # Escape for single-quoted PowerShell string (double any single quotes).
    safe = text.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 1; "
        f"$s.Speak('{safe}')"
    )
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script,
    ]


def _which(name: str) -> bool:
    from shutil import which

    return which(name) is not None


def await_idle(timeout: float | None = None) -> None:
    """Block until the current utterance finishes (tests / CLI helpers)."""
    with _lock:
        proc = _current
    if proc is None:
        return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        interrupt()
