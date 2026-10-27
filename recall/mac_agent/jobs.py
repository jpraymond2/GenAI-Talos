"""Watch mode: the Mac agent checks the server for pull requests from the dashboard.

    python -m recall.mac_agent watch            # keep running; picks up "Pull messages" clicks

Every few seconds it calls GET /api/mac/agent/next (which doubles as a heartbeat,
so the dashboard can show "Mac connected"). When a pull is waiting it runs the
normal extract with the pull's filters (time range, sources, contacts, groups),
uploads through the usual /api/ingest/mac path, and reports progress back.
"""

from __future__ import annotations

import dataclasses
import platform
import time
from typing import Any

import httpx

from recall.mac_agent import __version__
from recall.mac_agent.config import AgentConfig
from recall.mac_agent.pipeline import ExtractResult, payload_dict, run_extract
from recall.mac_agent.state import AgentState

CONTACTS_SHARE_EVERY_S = 30 * 60


class _Server:
    def __init__(self, cfg: AgentConfig) -> None:
        self.base = cfg.normalized_server_url()
        self.headers = {"Authorization": f"Bearer {cfg.device_upload_token.strip()}"}
        self.device_id = cfg.device_id

    def next_pull(self) -> dict[str, Any] | None:
        params = {"device_id": self.device_id, "agent_version": __version__, "hostname": platform.node()}
        with httpx.Client(timeout=20) as client:
            r = client.get(f"{self.base}/api/mac/agent/next", params=params, headers=self.headers)
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    def report(self, pull_id: str, status: str, progress: dict[str, Any] | None = None, error: str = "") -> None:
        body = {"status": status, "progress": progress or {}, "error": error}
        try:
            with httpx.Client(timeout=20) as client:
                client.post(f"{self.base}/api/mac/agent/jobs/{pull_id}", json=body, headers=self.headers)
        except httpx.HTTPError:
            pass  # progress is best-effort; the next report or the upload result will land

    def share_contacts(self, contacts: list[dict[str, Any]]) -> None:
        with httpx.Client(timeout=30) as client:
            client.post(f"{self.base}/api/mac/agent/contacts",
                        json={"device_id": self.device_id, "contacts": contacts}, headers=self.headers)


def filter_result(result: ExtractResult, params: dict[str, Any]) -> ExtractResult:
    """Apply the dashboard's choices: sources, chosen contacts, and group chats."""
    sources = set(params.get("sources") or ["imessage", "contacts"])
    im = result.imessage if "imessage" in sources else []
    wa = result.whatsapp if "whatsapp" in sources else []
    contacts = result.contacts

    if params.get("contacts") == "selected":
        wanted_ids = set(params.get("contact_ids") or [])
        wanted = {h.lower() for h in (params.get("contact_handles") or [])}
        chosen = [c for c in contacts if c.get("contact_id") in wanted_ids]
        for c in chosen:
            wanted |= {str(p).lower() for p in c.get("phones") or []}
            wanted |= {str(e).lower() for e in c.get("emails") or []}
        contacts = chosen

        def keep(m: dict[str, Any]) -> bool:
            parts = {str(p).lower() for p in m.get("group_participants") or []}
            return str(m.get("handle") or "").lower() in wanted or bool(parts & wanted)

        im = [m for m in im if keep(m)]
        wa = [m for m in wa if keep(m)]

    if not params.get("include_groups", True):
        im = [m for m in im if not m.get("is_group")]
        wa = [m for m in wa if not m.get("is_group")]

    used = {str(m.get("handle") or "") for m in im + wa}
    handles = [h for h in result.handles if h.get("value") in used]
    return dataclasses.replace(result, imessage=im, whatsapp=wa,
                               contacts=contacts if "contacts" in sources else [], handles=handles)


def run_pull(cfg: AgentConfig, state: AgentState, server: _Server, pull: dict[str, Any]) -> None:
    from recall.mac_agent.upload import upload_batches

    pull_id = pull["id"]
    params = pull.get("params") or {}
    try:
        server.report(pull_id, "running", {"stage": "reading messages on this Mac"})
        window_cfg = dataclasses.replace(
            cfg,
            backfill_days=int(params.get("days") or cfg.backfill_days),
            max_group_size=int(params.get("max_group_size") or cfg.max_group_size),
        )
        result = filter_result(run_extract(window_cfg, state, ignore_cursors=True), params)
        counts = result.counts()
        server.report(pull_id, "uploading", {"stage": "uploading", **counts})
        upload_batches(cfg, payload_dict(result, device_id=cfg.device_id, agent_version=__version__))
        server.report(pull_id, "done", {"stage": "uploaded", **counts})
        print(f"pull {pull_id} done: {counts}")
    except Exception as exc:  # noqa: BLE001 — report every failure to the dashboard
        server.report(pull_id, "failed", {"stage": "failed"}, error=f"{type(exc).__name__}: {exc}")
        print(f"pull {pull_id} failed: {exc}")


def watch(cfg: AgentConfig, state: AgentState, *, interval: float = 5.0) -> None:
    """Loop forever: heartbeat, share the address book now and then, run queued pulls."""
    from recall.mac_agent.extract_contacts import extract_contacts

    server = _Server(cfg)
    last_share = 0.0
    print(f"watching {server.base} for pulls (device {cfg.device_id}) — Ctrl+C to stop")
    while True:
        try:
            if time.time() - last_share > CONTACTS_SHARE_EVERY_S:
                contacts, _ = extract_contacts(excluded_contacts=cfg.excluded_contacts, default_region=cfg.default_region)
                server.share_contacts(contacts)
                last_share = time.time()
            pull = server.next_pull()
            if pull:
                run_pull(cfg, state, server, pull)
                continue
        except httpx.HTTPError as exc:
            print(f"server unreachable ({type(exc).__name__}); retrying")
        time.sleep(interval)
