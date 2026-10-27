"""Bearer-token auth for /api/* and the trace WebSocket."""

from __future__ import annotations

import logging
import secrets

from fastapi import Header, HTTPException, Request, WebSocket

from recall.config import get_settings

logger = logging.getLogger(__name__)

# Per-process token when SAMPLE_MODE=true and RECALL_API_TOKEN is empty.
_ephemeral_token: str | None = None
_ephemeral_announced = False


def ensure_sample_token() -> str | None:
    """Create/return an ephemeral token for sample mode with no configured token.

    Returns None when a real token is configured, or when not in sample mode
    (live mode still requires RECALL_API_TOKEN).
    """
    global _ephemeral_token, _ephemeral_announced
    settings = get_settings()
    configured = settings.recall_api_token.get_secret_value().strip()
    if configured:
        return None
    if not settings.sample_mode:
        return None
    if _ephemeral_token is None:
        _ephemeral_token = secrets.token_urlsafe(24)
    if not _ephemeral_announced:
        _ephemeral_announced = True
        print(
            f"[recall] SAMPLE_MODE local API token (localhost only): {_ephemeral_token}",
            flush=True,
        )
        logger.info("Generated per-process sample API token for localhost")
    return _ephemeral_token


def reset_ephemeral_token_for_tests() -> None:
    """Clear ephemeral token state (tests only)."""
    global _ephemeral_token, _ephemeral_announced
    _ephemeral_token = None
    _ephemeral_announced = False


def expected_token() -> str:
    """Return the configured API token, or the sample-mode ephemeral one."""
    configured = get_settings().recall_api_token.get_secret_value().strip()
    if configured:
        return configured
    ephemeral = ensure_sample_token()
    return ephemeral or ""


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: require ``Authorization: Bearer <token>``."""
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


_PROXY_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
    "tailscale-user-login",
    "tailscale-user-name",
    "tailscale-headers-info",
)


def request_has_proxy_headers(request: Request) -> bool:
    """True when the request was clearly forwarded (Tailscale serve, reverse proxy)."""
    for name in _PROXY_HEADERS:
        if request.headers.get(name):
            return True
    return False


def client_is_loopback(request: Request) -> bool:
    """True when the request originates from 127.0.0.1 / ::1 (no proxy hop)."""
    if request_has_proxy_headers(request):
        return False
    client = request.client
    host = ""
    if client is not None:
        host = (client.host or "").strip().lower()
    # Starlette TestClient reports host "testclient"; treat as loopback in tests.
    if host in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return True
    # Fall back to Host header (some TestClient base_url setups).
    hdr = (request.headers.get("host") or "").split(":", 1)[0].strip().lower()
    return hdr in {"127.0.0.1", "localhost"}