"""Dashboard workspace API: login, contacts (list / edit / merge), and Mac pulls.

Mac pulls work like a mailbox, because the server can't reach into a Mac:
  1. the dashboard posts a pull request (time range, sources, contacts)   POST /api/mac/pulls
  2. the Mac agent checks in every few seconds and picks it up             GET  /api/mac/agent/next
  3. it extracts with those filters, uploads to /api/ingest/mac, reports   POST /api/mac/agent/jobs/{id}
  4. the dashboard polls the pull until it's done, then shows the cards   GET  /api/mac/pulls/latest
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from recall.config import get_settings
from recall.server.auth import expected_token, require_bearer

router = APIRouter()

AGENT_ONLINE_SECONDS = 90
_JOBS_DB = Path("data/mac_jobs.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- login
class LoginBody(BaseModel):
    email: str = ""
    password: str = Field(min_length=1)


@router.post("/api/login")
def api_login(body: LoginBody) -> dict[str, Any]:
    """Exchange demo credentials (RECALL_LOGIN_EMAIL / RECALL_LOGIN_PASSWORD) for the API token.

    The API token itself is also accepted as a password, so the team can log in
    without extra config.
    """
    settings = get_settings()
    token = expected_token()
    if not token:
        raise HTTPException(status_code=503, detail="RECALL_API_TOKEN is not configured on the server")
    want_email = settings.recall_login_email.strip().lower()
    want_pw = settings.recall_login_password.get_secret_value()
    ok_demo = bool(want_pw) and secrets.compare_digest(body.password, want_pw) and (
        not want_email or body.email.strip().lower() == want_email)
    ok_token = secrets.compare_digest(body.password.strip(), token)
    if not (ok_demo or ok_token):
        raise HTTPException(status_code=401, detail="Wrong email or password")
    # Sample mode + API-token password → sign in as the dataset owner.
    if ok_token and settings.sample_mode and (settings.dataset_owner or "").strip():
        owner = settings.dataset_owner.strip()
        slug = "".join(ch.lower() if ch.isalnum() else "." for ch in owner).strip(".")
        while ".." in slug:
            slug = slug.replace("..", ".")
        email = body.email.strip() or f"{slug or 'owner'}@local.recall"
        return {"token": token, "name": owner, "email": email}
    name = body.email.split("@")[0].replace(".", " ").title() if body.email else "Recall user"
    return {"token": token, "name": name, "email": body.email}


# --------------------------------------------------------------------------- contacts
def _world_conn() -> sqlite3.Connection:
    from recall.engines.world.store import WorldStore, world_db_path

    path = world_db_path(get_settings())
    if not path.exists():
        raise HTTPException(status_code=404, detail="No contacts yet — pull messages or run ingest first")
    store = WorldStore(path=path)  # runs migrations / creates tables
    return store._conn  # noqa: SLF001


@router.get("/api/people", dependencies=[Depends(require_bearer)])
def api_people(q: str = "", limit: int = 500) -> dict[str, Any]:
    """Contact cards for everyone in the world model, most-talked-to first."""
    from recall.engines.world.user_edits import person_card

    conn = _world_conn()
    ids = [r[0] for r in conn.execute("SELECT id FROM entities WHERE type = 'person' ORDER BY mention_count DESC")]
    cards = [c for c in (person_card(conn, i) for i in ids) if c]
    if q.strip():
        needle = q.strip().lower()
        cards = [c for c in cards if needle in json.dumps(c, ensure_ascii=False).lower()]
    return {"people": cards[: max(1, min(limit, 2000))], "total": len(cards)}


@router.get("/api/people/{entity_id}", dependencies=[Depends(require_bearer)])
def api_person_card(entity_id: str) -> dict[str, Any]:
    from recall.engines.world.user_edits import person_card

    card = person_card(_world_conn(), entity_id)
    if not card:
        raise HTTPException(status_code=404, detail="Unknown contact")
    return card


class PersonEdit(BaseModel):
    name: str | None = None
    org: str | None = None
    title: str | None = None
    city: str | None = None
    notes: str | None = None
    add_aliases: list[str] = Field(default_factory=list)
    remove_aliases: list[str] = Field(default_factory=list)


@router.patch("/api/people/{entity_id}", dependencies=[Depends(require_bearer)])
def api_edit_person(entity_id: str, body: PersonEdit) -> dict[str, Any]:
    """Edit a contact card. Edits are remembered and re-applied after every ingest."""
    from recall.engines.world import user_edits as ue

    conn = _world_conn()
    if ue.person_card(conn, entity_id) is None:
        raise HTTPException(status_code=404, detail="Unknown contact")
    changes = {"canonical_name": body.name, "org": body.org, "title": body.title, "city": body.city, "notes": body.notes}
    for field, value in changes.items():
        if value is None:
            continue
        ue.record_edit(conn, kind="field", entity_id=entity_id, field=field, value=value)
        ue.apply_field(conn, entity_id, field, value)
    for alias in body.add_aliases:
        if alias.strip():
            ue.apply_alias(conn, entity_id, alias.strip(), add=True)
            ue.record_edit(conn, kind="alias_add", entity_id=entity_id, value=alias.strip())
    for alias in body.remove_aliases:
        ue.record_edit(conn, kind="alias_remove", entity_id=entity_id, value=alias)
        ue.apply_alias(conn, entity_id, alias, add=False)
    conn.commit()
    return ue.person_card(conn, entity_id) or {}


class MergeBody(BaseModel):
    other_id: str = Field(min_length=1)
    same_person: bool = True


@router.post("/api/people/{entity_id}/merge", dependencies=[Depends(require_bearer)])
def api_merge_people(entity_id: str, body: MergeBody) -> dict[str, Any]:
    """'Same person' merges two cards now (and on every future ingest); 'different' keeps them apart."""
    from recall.engines.world import user_edits as ue
    from recall.engines.world.name_merge import _merge_entities
    from recall.engines.world.store import WorldStore, world_db_path

    conn = _world_conn()
    if ue.person_card(conn, entity_id) is None or ue.person_card(conn, body.other_id) is None:
        raise HTTPException(status_code=404, detail="Unknown contact")
    ue.record_edit(conn, kind="merge" if body.same_person else "split", entity_id=entity_id, other_entity_id=body.other_id)
    if body.same_person:
        store = WorldStore(path=world_db_path(get_settings()))
        _merge_entities(store, entity_id, body.other_id)
        store.close()
    return {"ok": True, "card": ue.person_card(_world_conn(), entity_id)}


# --------------------------------------------------------------------------- Mac pulls (mailbox)
def _jobs() -> sqlite3.Connection:
    _JOBS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_JOBS_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pulls (
            id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, status TEXT,
            params_json TEXT, progress_json TEXT DEFAULT '{}', error TEXT DEFAULT '', device_id TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY, last_seen TEXT, agent_version TEXT, hostname TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS mac_contacts (
            device_id TEXT PRIMARY KEY, contacts_json TEXT, updated_at TEXT
        );
        """
    )
    return conn


def _pull_out(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"id": row["id"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "params": json.loads(row["params_json"] or "{}"), "progress": json.loads(row["progress_json"] or "{}"),
            "error": row["error"], "device_id": row["device_id"]}


def _device_status(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC LIMIT 1").fetchone()
    if row is None:
        return {"connected": False, "online": False, "last_seen": None, "device_id": None, "agent_version": None}
    seen = datetime.strptime(row["last_seen"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return {"connected": True, "online": _now() - seen < timedelta(seconds=AGENT_ONLINE_SECONDS),
            "last_seen": row["last_seen"], "device_id": row["device_id"], "agent_version": row["agent_version"],
            "hostname": row["hostname"]}


class PullBody(BaseModel):
    days: int = Field(default=30, ge=1, le=3650)
    sources: list[Literal["imessage", "whatsapp", "contacts"]] = Field(default_factory=lambda: ["imessage", "contacts"])
    contacts: Literal["all", "selected"] = "all"
    contact_ids: list[str] = Field(default_factory=list)
    contact_handles: list[str] = Field(default_factory=list)
    include_groups: bool = True
    max_group_size: int = Field(default=15, ge=2, le=200)


@router.get("/api/mac/device", dependencies=[Depends(require_bearer)])
def api_mac_device() -> dict[str, Any]:
    conn = _jobs()
    return _device_status(conn)


@router.get("/api/mac/contacts", dependencies=[Depends(require_bearer)])
def api_mac_contacts() -> dict[str, Any]:
    """Address-book list the Mac agent last shared (names + handles), for choosing who to pull."""
    conn = _jobs()
    row = conn.execute("SELECT contacts_json, updated_at FROM mac_contacts ORDER BY updated_at DESC LIMIT 1").fetchone()
    if row is None:
        return {"contacts": [], "updated_at": None}
    return {"contacts": json.loads(row["contacts_json"]), "updated_at": row["updated_at"]}


@router.post("/api/mac/pulls", dependencies=[Depends(require_bearer)])
def api_mac_pull(body: PullBody) -> dict[str, Any]:
    """Queue a pull for the Mac agent to pick up."""
    if body.contacts == "selected" and not (body.contact_ids or body.contact_handles):
        raise HTTPException(status_code=400, detail="Choose at least one contact, or pull everyone")
    conn = _jobs()
    pull_id = f"pull_{uuid.uuid4().hex[:10]}"
    now = _iso(_now())
    # Only one pull at a time: cancel anything still queued.
    conn.execute("UPDATE pulls SET status = 'cancelled', updated_at = ? WHERE status = 'queued'", (now,))
    conn.execute("INSERT INTO pulls(id, created_at, updated_at, status, params_json) VALUES (?,?,?,?,?)",
                 (pull_id, now, now, "queued", body.model_dump_json()))
    conn.commit()
    return {"pull": _pull_out(conn.execute("SELECT * FROM pulls WHERE id = ?", (pull_id,)).fetchone()),
            "device": _device_status(conn)}


@router.get("/api/mac/pulls/latest", dependencies=[Depends(require_bearer)])
def api_mac_pull_latest() -> dict[str, Any]:
    conn = _jobs()
    row = conn.execute("SELECT * FROM pulls ORDER BY created_at DESC LIMIT 1").fetchone()
    return {"pull": _pull_out(row), "device": _device_status(conn)}


# ---- agent side (DEVICE_UPLOAD_TOKEN, same as /api/ingest/mac)
def _require_device(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().device_upload_token.get_secret_value().strip()
    if not expected:
        raise HTTPException(status_code=503, detail="DEVICE_UPLOAD_TOKEN not configured")
    got = (authorization or "").removeprefix("Bearer ").strip()
    if not got or not secrets.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="Invalid device token")


@router.get("/api/mac/agent/next", dependencies=[Depends(_require_device)])
def api_agent_next(device_id: str, agent_version: str = "", hostname: str = "") -> Response:
    """Heartbeat + hand the oldest queued pull to this Mac (204 when there's nothing to do)."""
    conn = _jobs()
    now = _iso(_now())
    conn.execute(
        "INSERT INTO devices(device_id, last_seen, agent_version, hostname) VALUES (?,?,?,?) "
        "ON CONFLICT(device_id) DO UPDATE SET last_seen = excluded.last_seen, agent_version = excluded.agent_version, "
        "hostname = excluded.hostname", (device_id, now, agent_version, hostname))
    row = conn.execute("SELECT * FROM pulls WHERE status = 'queued' ORDER BY created_at LIMIT 1").fetchone()
    if row is None:
        conn.commit()
        return Response(status_code=204)
    conn.execute("UPDATE pulls SET status = 'running', device_id = ?, updated_at = ?, progress_json = ? WHERE id = ?",
                 (device_id, now, json.dumps({"stage": "starting"}), row["id"]))
    conn.commit()
    pull = _pull_out(conn.execute("SELECT * FROM pulls WHERE id = ?", (row["id"],)).fetchone())
    return Response(content=json.dumps(pull), media_type="application/json")


class JobUpdate(BaseModel):
    status: Literal["running", "uploading", "done", "failed"]
    progress: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


@router.post("/api/mac/agent/jobs/{pull_id}", dependencies=[Depends(_require_device)])
def api_agent_update(pull_id: str, body: JobUpdate) -> dict[str, Any]:
    conn = _jobs()
    if conn.execute("SELECT 1 FROM pulls WHERE id = ?", (pull_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Unknown pull")
    status = "building" if body.status == "done" else body.status
    conn.execute("UPDATE pulls SET status = ?, progress_json = ?, error = ?, updated_at = ? WHERE id = ?",
                 (status, json.dumps(body.progress), body.error[:500], _iso(_now()), pull_id))
    conn.commit()
    if body.status == "done":
        _rebuild_people(pull_id)
    return {"ok": True}


class AgentContacts(BaseModel):
    device_id: str
    contacts: list[dict[str, Any]]


@router.post("/api/mac/agent/contacts", dependencies=[Depends(_require_device)])
def api_agent_contacts(body: AgentContacts) -> dict[str, Any]:
    """The agent shares a slim address book (id, name, org, phones, emails) so the dashboard can offer a picker."""
    slim = [{"contact_id": str(c.get("contact_id", "")), "name": str(c.get("name", "")),
             "organization": str(c.get("organization", "")),
             "phones": [str(p) for p in (c.get("phones") or [])][:5],
             "emails": [str(e) for e in (c.get("emails") or [])][:5]} for c in body.contacts if c.get("name")]
    conn = _jobs()
    conn.execute("INSERT INTO mac_contacts(device_id, contacts_json, updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(device_id) DO UPDATE SET contacts_json = excluded.contacts_json, updated_at = excluded.updated_at",
                 (body.device_id, json.dumps(slim, ensure_ascii=False), _iso(_now())))
    conn.commit()
    return {"ok": True, "contacts": len(slim)}


def _rebuild_people(pull_id: str) -> None:
    """After a finished pull, rebuild the contact cards now (not debounced), then mark the pull ready."""
    import threading

    settings = get_settings()

    def _work() -> None:
        note = ""
        try:
            from recall.engines.world import run_world_ingest, world

            world.reset()
            run_world_ingest(sample=settings.sample_mode, settings=settings)
        except Exception as exc:  # noqa: BLE001 — cards may still show the previous build
            note = f"contacts rebuild failed: {type(exc).__name__}"
        conn = _jobs()
        conn.execute("UPDATE pulls SET status = 'ready', error = ?, updated_at = ? WHERE id = ?", (note, _iso(_now()), pull_id))
        conn.commit()

    threading.Thread(target=_work, name="recall-pull-rebuild", daemon=True).start()
