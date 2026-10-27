"""Resolve message handles to contact display names."""

from __future__ import annotations

from typing import Any

from recall.mac_agent.phones import normalize_e164


def build_contact_index(
    contacts: list[dict[str, Any]],
    *,
    default_region: str = "CA",
) -> dict[str, str]:
    """Map normalized phone/email → contact name."""
    index: dict[str, str] = {}
    for contact in contacts:
        name = str(contact.get("name") or "").strip()
        if not name:
            continue
        for phone in contact.get("phones") or []:
            key = normalize_e164(str(phone), default_region=default_region) or str(phone)
            if key:
                index[key.lower()] = name
        for email in contact.get("emails") or []:
            key = str(email).strip().lower()
            if key:
                index[key] = name
    return index


def resolve_contact_name(
    handle: str,
    index: dict[str, str],
    *,
    default_region: str = "CA",
) -> str | None:
    if not handle:
        return None
    raw = handle.strip()
    if raw.lower() in index:
        return index[raw.lower()]
    e164 = normalize_e164(raw, default_region=default_region)
    if e164 and e164.lower() in index:
        return index[e164.lower()]
    return None


def attach_contact_names(
    messages: list[dict[str, Any]],
    contacts: list[dict[str, Any]],
    *,
    default_region: str = "CA",
) -> list[dict[str, Any]]:
    index = build_contact_index(contacts, default_region=default_region)
    enriched: list[dict[str, Any]] = []
    for msg in messages:
        copy = dict(msg)
        name = resolve_contact_name(
            str(msg.get("handle") or ""),
            index,
            default_region=default_region,
        )
        if name is None:
            hint = msg.get("contact_name_hint") or msg.get("chat_display_name")
            if hint:
                name = str(hint)
        copy["contact_name"] = name
        copy.pop("contact_name_hint", None)
        enriched.append(copy)
    return enriched
