# Recall web dashboard

Vite + React + TypeScript + Tailwind. One-page meeting-prep UI (input · brief · trace).

## Setup

```bash
cd web
npm install
```

Copy `.env.example` to `.env` if needed. Default:

```
VITE_MOCK=true
```

With mock on (or when `/health` is unreachable), the UI replays local scenarios and streams fake trace events. Set `VITE_MOCK=false` to call `POST /api/ask`, `POST /api/confirm`, `POST /api/voice`, and `WS /ws/trace` on the FastAPI backend.

## Dev

```bash
npm run dev
```

Opens Vite on http://127.0.0.1:5173 with a proxy to the API on `:8000`.

## Build

```bash
npm run build
```

Outputs to `web/dist/`. When that folder exists, `recall.server.app` serves it at `/`.

## Try the mock

1. Type `I'm meeting Karim` → clarification (Haddad vs Saleh).
2. Pick **Karim Haddad** (or type `I'm meeting Karim Haddad`) → brief + trace with Zetaris USL retriever, critic revise, then pass.
