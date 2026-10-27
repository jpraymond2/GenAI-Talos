"""Magpie voice endpoint: falls back cleanly (503) when NVIDIA can't speak, and returns WAV when it can."""

from __future__ import annotations

import io
import wave

from fastapi.testclient import TestClient


def test_tts_endpoint(monkeypatch, api_headers) -> None:
    from recall.server.app import app
    from recall.voice import magpie

    client = TestClient(app)
    assert client.post("/api/tts", json={"text": "hi"}).status_code == 401

    def boom(text, settings=None):
        raise magpie.TTSUnavailable("NVIDIA_API_KEY is empty")

    monkeypatch.setattr(magpie, "synthesize_wav", boom)
    assert client.post("/api/tts", headers=api_headers, json={"text": "Hello"}).status_code == 503

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(22050), w.writeframes(b"\x00\x00" * 100)
    monkeypatch.setattr(magpie, "synthesize_wav", lambda text, settings=None: buf.getvalue())
    ok = client.post("/api/tts", headers=api_headers, json={"text": "Coffee with Omar on Wednesday."})
    assert ok.status_code == 200 and ok.headers["content-type"] == "audio/wav" and ok.content[:4] == b"RIFF"


def test_text_cleanup_and_language() -> None:
    from recall.voice.magpie import _clean, _language, _sentences

    assert "http" not in _clean("See **this** https://x.y now — ok")
    assert _language("Bonjour, je vous envoie la présentation pour le rendez-vous avec les gens")[0] == "fr-FR"
    assert _language("You owe Mei the model and the data room for the pitch")[0] == "en-US"
    assert all(len(s) < 400 for s in _sentences("One. Two. " * 50))
