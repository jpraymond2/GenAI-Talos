# Recall

Recall is a voice-first meeting-prep agent for the Open Agent Hackathon 2026 (Track 01, Fragmented Intelligence). You tell it who you are about to meet, and it assembles a brief from personal memory, calendar, messages, and the web, with every step written to a trace. This repository is the Python service: a Typer CLI and FastAPI app that can run on sample data with no credentials.

## Quickstart

```bash
uv sync
cp .env.example .env
uv run python -m recall ask "I'm meeting Karim" --sample
```

## Voice

Push-to-talk STT uses **NVIDIA Riva Parakeet** on NVCF (`grpc.nvcf.nvidia.com:443`). Set in `.env`:

```bash
NVIDIA_API_KEY=nvapi-…
RIVA_ASR_FUNCTION_ID=<your Parakeet ASR function id>
SAMPLE_MODE=false
```

With `SAMPLE_MODE=true` and no API key, STT returns the fixed demo line `I'm meeting Karim Haddad` (no mic/network needed for the happy path in tests).

Optional local fallback (not required for `uv sync`):

```bash
uv sync --extra whisper   # installs faster-whisper when your Python supports it
```

Validate keys without the mic (chat + silent-wav ASR; never prints secrets):

```bash
uv run python -m recall voice --check
```

CLI loop — hold **right Ctrl** (Windows) or **right Option** (macOS), release to transcribe and speak:

```bash
uv run python -m recall voice
```

Dashboard: hold the big mic button (`web/` → `npm run dev`). Browser audio posts to `POST /api/voice` (WAV, or webm/mp4 if `ffmpeg` is on PATH). Mock mode (`VITE_MOCK=true`) uses the fixed transcript and `window.speechSynthesis` for the brief.
