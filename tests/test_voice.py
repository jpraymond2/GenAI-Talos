"""Voice STT/TTS and /api/voice tests (no mic, no network)."""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from recall.config import Settings, get_settings
from recall.server.app import app
from recall.tracing import Tracer
from recall.voice.audio_convert import ensure_wav_16k_mono, make_silence_wav
from recall.voice.stt import TranscriptionError, load_sample_transcript, transcribe
from recall.voice.tts import tts_command

_VOICE_SAMPLE = Path("data/sample/voice_sample.txt")


@pytest.fixture(autouse=True)
def _reset_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setenv("LOGS_DIR", str(logs))
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("RIVA_ASR_FUNCTION_ID", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_sample_mode_returns_fixed_transcript(tmp_path: Path) -> None:
    expected = _VOICE_SAMPLE.read_text(encoding="utf-8").strip()
    settings = Settings(
        sample_mode=True,
        nvidia_api_key="",
        logs_dir=tmp_path / "logs",
        sample_data_dir=Path("data/sample"),
    )
    tracer = Tracer(logs_dir=settings.logs_dir)
    wav = make_silence_wav()
    with tracer.activate():
        text = transcribe(wav, settings=settings, tracer=tracer)
    assert text == expected
    assert text == load_sample_transcript(settings=settings)


def test_sample_transcript_fallback_when_file_missing(tmp_path: Path) -> None:
    settings = Settings(
        sample_mode=True,
        nvidia_api_key="",
        logs_dir=tmp_path / "logs",
        sample_data_dir=tmp_path / "empty_sample",
    )
    assert load_sample_transcript(settings=settings) == "Who am I meeting next?"
    wav = make_silence_wav()
    assert transcribe(wav, settings=settings) == "Who am I meeting next?"


def test_riva_offline_recognize_wired(tmp_path: Path) -> None:
    """Exercise _transcribe_riva with a fully mocked riva.client module."""
    settings = Settings(
        sample_mode=False,
        nvidia_api_key="test-key",
        riva_asr_function_id="fn-parakeet",
        logs_dir=tmp_path / "logs",
    )
    wav = make_silence_wav()

    fake_alt = MagicMock(transcript="Meeting Karim")
    fake_result = MagicMock(alternatives=[fake_alt])
    fake_response = MagicMock(results=[fake_result])
    fake_asr = MagicMock()
    fake_asr.offline_recognize.return_value = fake_response

    import types
    import sys

    fake_mod = types.ModuleType("riva.client")
    fake_mod.Auth = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    fake_mod.ASRService = MagicMock(return_value=fake_asr)  # type: ignore[attr-defined]
    fake_mod.RecognitionConfig = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    fake_encoding = MagicMock()
    fake_encoding.LINEAR_PCM = 1
    fake_mod.AudioEncoding = fake_encoding  # type: ignore[attr-defined]

    parent = types.ModuleType("riva")
    parent.client = fake_mod  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"riva": parent, "riva.client": fake_mod}):
        from recall.voice.stt import _transcribe_riva

        assert _transcribe_riva(wav, settings) == "Meeting Karim"
        fake_asr.offline_recognize.assert_called_once()
        auth_call = fake_mod.Auth.call_args
        assert auth_call.kwargs["uri"] == "grpc.nvcf.nvidia.com:443"
        assert auth_call.kwargs["use_ssl"] is True
        meta = dict(auth_call.kwargs["metadata_args"])
        assert meta["function-id"] == "fn-parakeet"
        assert meta["authorization"] == "Bearer test-key"


def test_tts_command_macos() -> None:
    cmd = tts_command("Hello there", system="Darwin")
    assert cmd == ["say", "-v", "Samantha", "-r", "190", "Hello there"]


def test_tts_command_windows() -> None:
    cmd = tts_command("Hello", system="Windows")
    assert cmd is not None
    assert cmd[0].lower() == "powershell"
    joined = " ".join(cmd)
    assert "System.Speech" in joined
    assert "SpeechSynthesizer" in joined
    assert "Hello" in joined


def test_tts_command_linux_without_espeak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("recall.voice.tts._which", lambda _name: False)
    assert tts_command("Hi", system="Linux") is None


def test_api_voice_sample_wav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_headers: dict[str, str],
) -> None:
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    from recall.schemas import Brief, RunState, TalkingPoint, Utterance

    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear()
    (tmp_path / "logs").mkdir(exist_ok=True)

    done = RunState(
        run_id="voice-1",
        utterance=Utterance(
            text=load_sample_transcript(),
            ts=datetime.now(timezone.utc),
            mode="voice",
        ),
        status="done",
        final_brief=Brief(
            person="demo",
            headline="ok",
            points=[
                TalkingPoint(text="point", evidence_ids=[], kind="catch_up", priority=1)
            ],
        ),
    )
    monkeypatch.setattr("recall.orchestrator.run", AsyncMock(return_value=done))

    wav = make_silence_wav(seconds=0.05)
    client = TestClient(app)
    res = client.post(
        "/api/voice",
        headers=api_headers,
        files={"audio": ("utterance.wav", wav, "audio/wav")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["transcript"] == load_sample_transcript()
    assert body["engine"] == "sample"
    assert body["status"] == "done"
    assert "speech" in body
    timing = body.get("voice_timing") or {}
    assert "upload_ms" in timing
    assert "ffmpeg_ms" in timing
    assert "stt_ms" in timing
    assert "ask_ms" in timing
    assert "total_ms" in timing


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_ensure_wav_from_tiny_aac_mp4(tmp_path: Path) -> None:
    """Real AAC mp4 from ffmpeg must convert to 16 kHz mono WAV."""
    wav = tmp_path / "silence.wav"
    mp4 = tmp_path / "silence.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "0.25",
            str(wav),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav), "-c:a", "aac", str(mp4)],
        check=True,
        capture_output=True,
    )
    raw = mp4.read_bytes()
    assert len(raw) > 100
    out = ensure_wav_16k_mono(
        raw,
        content_type="audio/mp4",
        filename="recording.mp4",
    )
    with wave.open(io.BytesIO(out), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert handle.getnframes() > 0


def test_api_voice_rejects_webm_without_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_headers: dict[str, str],
) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear()
    (tmp_path / "logs").mkdir(exist_ok=True)

    monkeypatch.setattr("recall.voice.audio_convert.shutil.which", lambda _name: None)
    client = TestClient(app)
    res = client.post(
        "/api/voice",
        headers=api_headers,
        files={"audio": ("utterance.webm", b"not-really-webm", "audio/webm")},
    )
    assert res.status_code == 415
    assert "ffmpeg" in res.json()["detail"].lower()


def test_transcribe_fails_clearly_without_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        sample_mode=False,
        nvidia_api_key="",
        riva_asr_function_id="",
        logs_dir=tmp_path / "logs",
    )
    wav = make_silence_wav()
    with pytest.raises(TranscriptionError, match="Speech-to-text unavailable"):
        transcribe(wav, settings=settings)
