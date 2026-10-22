# Recall

Recall is a voice-first meeting-prep agent for the Open Agent Hackathon 2026 (Track 01, Fragmented Intelligence). You tell it who you are about to meet, and it assembles a brief from personal memory, calendar, messages, and the web, with every step written to a trace. This repository is the Python service: a Typer CLI and FastAPI app that can run on sample data with no credentials.

## Quickstart

```bash
uv sync
cp .env.example .env
uv run python -m recall ask "I'm meeting Karim" --sample
```
