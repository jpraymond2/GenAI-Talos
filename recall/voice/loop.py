"""Push-to-talk voice loop: record → STT → respond → TTS."""

from __future__ import annotations

import asyncio
import logging
import platform
import threading
import time
from typing import Any

import numpy as np

from recall.config import get_settings
from recall.tracing import Tracer
from recall.voice import stt, tts

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_CHANNELS = 1


def respond(text: str) -> dict[str, Any]:
    """Run one meeting-prep turn if orchestrator exists; else a spoken placeholder.

    Returns a dict with at least ``speech`` and usually ``status``. Never raises
    for a missing orchestrator (JP's branch may not be merged yet).
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return {"speech": "I didn't catch that.", "status": "failed"}

    try:
        from recall import orchestrator as orch
    except Exception:  # noqa: BLE001
        name = _guess_name(cleaned)
        return {
            "speech": f"Pulling up {name}…",
            "status": "placeholder",
            "transcript": cleaned,
        }

    try:
        result = asyncio.run(orch.run(cleaned))
    except Exception as exc:  # noqa: BLE001
        logger.warning("orchestrator.run failed: %s", type(exc).__name__)
        name = _guess_name(cleaned)
        return {
            "speech": f"Pulling up {name}…",
            "status": "placeholder",
            "transcript": cleaned,
            "error": type(exc).__name__,
        }

    return _speech_from_run(result, cleaned)


def _speech_from_run(state: Any, transcript: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"transcript": transcript}
    status = getattr(state, "status", None)
    payload["status"] = status
    if hasattr(state, "model_dump"):
        payload["run"] = state.model_dump()
    question = getattr(state, "question", None)
    brief = getattr(state, "final_brief", None)
    if status == "needs_clarification" and question:
        payload["speech"] = str(question)
        payload["question"] = str(question)
        return payload
    if brief is not None:
        headline = getattr(brief, "headline", "") or ""
        points = [
            getattr(p, "text", str(p))
            for p in (getattr(brief, "points", None) or [])
        ]
        payload["speech"] = headline
        payload["headline"] = headline
        payload["points"] = points
        return payload
    name = getattr(state, "entity_name", None) or _guess_name(transcript)
    payload["speech"] = f"Pulling up {name}…"
    return payload


def _guess_name(text: str) -> str:
    lower = text.lower()
    for prefix in ("i'm meeting ", "i am meeting ", "meeting "):
        if lower.startswith(prefix):
            return text[len(prefix) :].strip() or "them"
    return "them"


def run_voice_loop() -> None:
    """Hold right-Ctrl (Windows) / right-Option (macOS) to talk; release to ask."""
    settings = get_settings()
    tracer = Tracer(logs_dir=settings.logs_dir)
    ptt_key = _ptt_key_name()
    print(f"Recall voice — hold {ptt_key} to talk, release to send. Ctrl+C to quit.")
    print(f"SAMPLE_MODE={settings.sample_mode}")

    with tracer.activate():
        try:
            _ptt_session(tracer)
        except KeyboardInterrupt:
            print("\nBye.")
        finally:
            tts.interrupt()


def _ptt_key_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "right Option"
    if system == "windows":
        return "right Ctrl"
    return "right Ctrl"


def _ptt_session(tracer: Tracer) -> None:
    from pynput import keyboard

    import sounddevice as sd

    frames: list[np.ndarray] = []
    recording = {"on": False}
    stream_holder: dict[str, sd.InputStream | None] = {"stream": None}
    busy = threading.Event()

    def on_audio(indata: np.ndarray, _frames: int, _time: Any, status: Any) -> None:
        if status:
            logger.debug("sounddevice status: %s", status)
        if recording["on"]:
            frames.append(indata.copy())

    def start_rec() -> None:
        if recording["on"] or busy.is_set():
            return
        frames.clear()
        tts.interrupt()
        recording["on"] = True
        stream = sd.InputStream(
            samplerate=_SAMPLE_RATE,
            channels=_CHANNELS,
            dtype="int16",
            callback=on_audio,
        )
        stream_holder["stream"] = stream
        stream.start()
        print("\n[recording…]")

    def stop_and_handle() -> None:
        if not recording["on"]:
            return
        recording["on"] = False
        stream = stream_holder.get("stream")
        stream_holder["stream"] = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        if not frames:
            print("[no audio]")
            return
        busy.set()
        try:
            audio = np.concatenate(frames, axis=0)
            wav_bytes = _pcm_to_wav(audio.tobytes())
            transcript = stt.transcribe(wav_bytes, tracer=tracer)
            print(f"You: {transcript}")
            _handle_turn(transcript, tracer)
        except Exception as exc:  # noqa: BLE001
            print(f"Voice turn failed: {exc}")
        finally:
            busy.clear()

    def is_ptt(key: keyboard.Key | keyboard.KeyCode) -> bool:
        system = platform.system().lower()
        if system == "darwin":
            return key in (keyboard.Key.alt_r, getattr(keyboard.Key, "alt_gr", keyboard.Key.alt_r))
        return key == keyboard.Key.ctrl_r

    def on_press(key: keyboard.Key | keyboard.KeyCode) -> None:
        try:
            if is_ptt(key):
                start_rec()
        except Exception:  # noqa: BLE001
            pass

    def on_release(key: keyboard.Key | keyboard.KeyCode) -> None:
        try:
            if is_ptt(key):
                stop_and_handle()
        except Exception:  # noqa: BLE001
            pass

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def _handle_turn(transcript: str, tracer: Tracer) -> None:
    result = respond(transcript)
    speech = str(result.get("speech") or "")
    status = result.get("status")
    tracer.event(
        "voice",
        "respond",
        {"status": status, "speech_chars": len(speech)},
    )
    if status == "needs_clarification":
        print(f"Recall: {speech}")
        tts.speak(speech)
        tts.await_idle(timeout=60)
        answer = _record_once(prompt="Answer (hold PTT again)…")
        if not answer:
            return
        print(f"You: {answer}")
        follow = respond(answer)
        _speak_result(follow)
        return
    _speak_result(result)


def _speak_result(result: dict[str, Any]) -> None:
    headline = str(result.get("headline") or result.get("speech") or "")
    points = result.get("points") or []
    if points:
        print(f"Recall: {headline}")
        for point in points:
            print(f"  - {point}")
        tts.speak_stream(headline, [str(p) for p in points])
    else:
        print(f"Recall: {headline}")
        tts.speak(headline)
    tts.await_idle(timeout=120)


def _record_once(*, prompt: str) -> str:
    """Blocking short capture after a clarification question."""
    import sounddevice as sd

    print(prompt)
    print("(recording 4s…)")
    time.sleep(0.2)
    audio = sd.rec(
        int(4 * _SAMPLE_RATE),
        samplerate=_SAMPLE_RATE,
        channels=_CHANNELS,
        dtype="int16",
    )
    sd.wait()
    wav_bytes = _pcm_to_wav(audio.tobytes())
    return stt.transcribe(wav_bytes)


def _pcm_to_wav(pcm: bytes, *, rate: int = _SAMPLE_RATE) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buf.getvalue()
