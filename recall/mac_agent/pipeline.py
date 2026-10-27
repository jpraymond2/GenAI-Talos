"""Orchestrate Mac extractors into one payload."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from recall.mac_agent.config import AgentConfig
from recall.mac_agent.extract_contacts import extract_contacts
from recall.mac_agent.extract_imessage import extract_imessage
from recall.mac_agent.extract_whatsapp import extract_whatsapp
from recall.mac_agent.resolve import attach_contact_names
from recall.mac_agent.state import AgentState


@dataclass
class ExtractResult:
    """One extract pass (dry-run or upload candidate)."""

    imessage: list[dict[str, Any]] = field(default_factory=list)
    whatsapp: list[dict[str, Any]] = field(default_factory=list)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    handles: list[dict[str, Any]] = field(default_factory=list)
    cursors: dict[str, int] = field(default_factory=dict)
    skips: list[str] = field(default_factory=list)
    skipped_large_groups: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "imessage_messages": len(self.imessage),
            "whatsapp_messages": len(self.whatsapp),
            "contacts": len(self.contacts),
            "handles": len(self.handles),
            "skipped_large_groups_imessage": int(
                self.skipped_large_groups.get("imessage", 0)
            ),
            "skipped_large_groups_whatsapp": int(
                self.skipped_large_groups.get("whatsapp", 0)
            ),
        }


def run_extract(
    cfg: AgentConfig,
    state: AgentState,
    *,
    ignore_cursors: bool = False,
    message_limit: int | None = None,
) -> ExtractResult:
    """Extract incremental (or full backfill) rows. Does not advance state."""
    after_im = 0 if ignore_cursors else state.imessage_last_rowid
    after_wa = 0 if ignore_cursors else state.whatsapp_last_zpk
    after_ab = 0 if ignore_cursors else state.contacts_last_zpk

    contacts, contacts_max = extract_contacts(
        after_zpk=after_ab,
        excluded_contacts=cfg.excluded_contacts,
        default_region=cfg.default_region,
    )

    imessage, im_handles, im_max, im_skip_large = extract_imessage(
        after_rowid=after_im,
        backfill_days=cfg.backfill_days,
        max_group_size=cfg.max_group_size,
        excluded_chats=cfg.excluded_chats,
        default_region=cfg.default_region,
        limit=message_limit,
    )
    imessage = attach_contact_names(
        imessage, contacts, default_region=cfg.default_region
    )

    whatsapp, wa_handles, wa_max, wa_skip_large, wa_skip = extract_whatsapp(
        after_zpk=after_wa,
        backfill_days=cfg.backfill_days,
        max_group_size=cfg.max_group_size,
        default_region=cfg.default_region,
        limit=message_limit,
    )
    whatsapp = attach_contact_names(
        whatsapp, contacts, default_region=cfg.default_region
    )

    handles = list({h["handle_id"]: h for h in [*im_handles, *wa_handles]}.values())
    skips: list[str] = []
    if wa_skip:
        skips.append(wa_skip)

    return ExtractResult(
        imessage=imessage,
        whatsapp=whatsapp,
        contacts=contacts,
        handles=handles,
        cursors={
            "imessage_last_rowid": im_max,
            "whatsapp_last_zpk": wa_max,
            "contacts_last_zpk": contacts_max,
        },
        skips=skips,
        skipped_large_groups={
            "imessage": im_skip_large,
            "whatsapp": wa_skip_large,
        },
    )


def payload_dict(
    result: ExtractResult,
    *,
    device_id: str,
    agent_version: str,
) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "agent_version": agent_version,
        "counts": result.counts(),
        "cursors": result.cursors,
        "imessage_messages": result.imessage,
        "whatsapp_messages": result.whatsapp,
        "contacts": result.contacts,
        "handles": result.handles,
        "skips": result.skips,
        "skipped_large_groups": result.skipped_large_groups,
    }
