"""Shared live-ingest row shapes — columns match data/sample CSVs exactly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HANDLE_COLUMNS = ["handle_id", "kind", "value", "display_name", "source"]
INTERACTION_COLUMNS = [
    "interaction_id",
    "source",
    "thread_id",
    "handle_value",
    "direction",
    "ts_utc",
    "subject",
    "text",
    "source_ref",
]
EVENT_COLUMNS = [
    "event_id",
    "title",
    "start_utc",
    "end_utc",
    "location",
    "attendees",
    "source_ref",
]
CONTACT_COLUMNS = [
    "contact_id",
    "full_name",
    "org",
    "title",
    "city",
    "nickname",
    "emails",
    "phones",
    "source",
]

TABLE_COLUMNS: dict[str, list[str]] = {
    "handles": HANDLE_COLUMNS,
    "interactions": INTERACTION_COLUMNS,
    "events": EVENT_COLUMNS,
    "contacts": CONTACT_COLUMNS,
}


@dataclass
class ConnectorResult:
    """Rows produced by one connector (may be empty when skipped)."""

    name: str
    handles: list[dict[str, Any]] = field(default_factory=list)
    interactions: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    skipped: str | None = None

    def counts(self) -> dict[str, int]:
        """Row counts only — safe for traces (no content)."""
        return {
            "handles": len(self.handles),
            "interactions": len(self.interactions),
            "events": len(self.events),
            "contacts": len(self.contacts),
        }
