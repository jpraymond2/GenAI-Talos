"""Bearer-token auth for /api/* and the trace WebSocket."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, WebSocket

from recall.config import get_settings


def expected_token() -> str:
    """Return the configured API token (empty if unset)."""
    return get_settings().recall_api_token.get_secret_value().strip()


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: require ``Authorization: Bearer <RECALL_API_TOKEN>``."""
    expected = expected_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="RECALL_API_TOKEN is not configured on the server",
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization Bearer token")
    got = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="Invalid API token")


async def require_ws_token(websocket: WebSocket) -> bool:
    """Validate ``?token=`` on the WebSocket; close with 1008 when invalid."""
    expected = expected_token()
    if not expected:
        await websocket.close(code=1013, reason="RECALL_API_TOKEN not configured")
        return False
    got = (websocket.query_params.get("token") or "").strip()
    if not got or not secrets.compare_digest(got, expected):
        await websocket.close(code=1008, reason="Invalid or missing token")
        return False
    return True
