"""HTTPS upload to POST /api/ingest/mac (used in step c; not called by dry-run)."""

from __future__ import annotations

import gzip
import json
import time
from typing import Any

import httpx

from recall.mac_agent.config import AgentConfig, ConfigError


class UploadError(RuntimeError):
    """Server rejected or network failed after retries."""


def upload_batches(
    cfg: AgentConfig,
    payload: dict[str, Any],
    *,
    batch_size: int = 2000,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Split large message lists into ≤batch_size batches; return last server JSON."""
    try:
        url = cfg.require_server_url()
    except ConfigError as exc:
        raise UploadError(str(exc)) from exc
    if not url.startswith("https://"):
        raise UploadError(f"HTTPS only; got {url!r}")
    token = cfg.device_upload_token.strip()
    if not token:
        raise UploadError("device_upload_token missing in config.json")

    endpoint = f"{url}/api/ingest/mac"
    im = list(payload.get("imessage_messages") or [])
    wa = list(payload.get("whatsapp_messages") or [])
    contacts = list(payload.get("contacts") or [])
    handles = list(payload.get("handles") or [])

    # Contacts/handles go with the first batch; messages chunked.
    combined_msgs = [("imessage", m) for m in im] + [("whatsapp", m) for m in wa]
    if not combined_msgs and not contacts:
        return {"ok": True, "counts": {}, "note": "nothing to upload"}

    last: dict[str, Any] = {}
    offset = 0
    first = True
    while offset < len(combined_msgs) or first:
        chunk = combined_msgs[offset : offset + batch_size]
        offset += batch_size
        body = {
            "device_id": payload.get("device_id"),
            "agent_version": payload.get("agent_version"),
            "cursors": payload.get("cursors") if offset >= len(combined_msgs) else {},
            "contacts": contacts if first else [],
            "handles": handles if first else [],
            "imessage_messages": [m for kind, m in chunk if kind == "imessage"],
            "whatsapp_messages": [m for kind, m in chunk if kind == "whatsapp"],
            "final": offset >= len(combined_msgs),
        }
        first = False
        last = _post_gzip(endpoint, token, body, timeout=timeout)
        if not combined_msgs:
            break
        if offset >= len(combined_msgs):
            break
    return last


def _post_gzip(
    endpoint: str,
    token: str,
    body: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Content-Encoding": "gzip",
        "Accept": "application/json",
    }
    delays = [1.0, 2.0, 4.0]
    last_exc: Exception | None = None
    for attempt, delay in enumerate([0.0, *delays]):
        if delay:
            time.sleep(delay)
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(endpoint, content=compressed, headers=headers)
            if response.status_code >= 500:
                last_exc = UploadError(f"HTTP {response.status_code}: {response.text[:200]}")
                continue
            if response.status_code >= 400:
                raise UploadError(f"HTTP {response.status_code}: {response.text[:300]}")
            data = response.json()
            if not isinstance(data, dict):
                return {"raw": data}
            return data
        except (httpx.HTTPError, UploadError) as exc:
            last_exc = exc
            if isinstance(exc, UploadError) and "HTTP 4" in str(exc):
                raise
            continue
    raise UploadError(str(last_exc or "upload failed"))
