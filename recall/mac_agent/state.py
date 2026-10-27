"""Per-source incremental cursors in state.json."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from recall.mac_agent.paths import ensure_support_dirs, state_path


@dataclass
class AgentState:
    """Cursors advanced only after the server confirms an upload."""

    imessage_last_rowid: int = 0
    whatsapp_last_zpk: int = 0
    contacts_last_zpk: int = 0
    last_upload_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def load_state() -> AgentState:
    ensure_support_dirs()
    path = state_path()
    if not path.is_file():
        return AgentState()
    data = json.loads(path.read_text(encoding="utf-8"))
    return AgentState(
        imessage_last_rowid=int(data.get("imessage_last_rowid") or 0),
        whatsapp_last_zpk=int(data.get("whatsapp_last_zpk") or 0),
        contacts_last_zpk=int(data.get("contacts_last_zpk") or 0),
        last_upload_at=str(data.get("last_upload_at") or ""),
        extra=dict(data.get("extra") or {}),
    )


def save_state(state: AgentState) -> None:
    ensure_support_dirs()
    path = state_path()
    payload = asdict(state)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
