"""Mac agent pull filters (pure; no macOS needed)."""

from __future__ import annotations

from recall.mac_agent.jobs import filter_result
from recall.mac_agent.pipeline import ExtractResult


def _result() -> ExtractResult:
    return ExtractResult(
        imessage=[
            {"handle": "+15145550142", "is_group": False, "group_participants": ["+15145550142"]},
            {"handle": "+15145550100", "is_group": False, "group_participants": ["+15145550100"]},
            {"handle": "+15145550111", "is_group": True, "group_participants": ["+15145550111", "+15145550142"]},
        ],
        whatsapp=[{"handle": "+15145550142", "is_group": False}],
        contacts=[{"contact_id": "ab-1", "name": "Karim", "phones": ["+15145550142"], "emails": []},
                  {"contact_id": "ab-2", "name": "Sophie", "phones": ["+15145550100"], "emails": []}],
        handles=[{"value": "+15145550142"}, {"value": "+15145550100"}, {"value": "+15145550111"}],
    )


def test_sources_and_everyone() -> None:
    out = filter_result(_result(), {"sources": ["imessage"], "contacts": "all"})
    assert len(out.imessage) == 3 and out.whatsapp == [] and out.contacts == []


def test_selected_contact_keeps_their_chats_and_groups() -> None:
    out = filter_result(_result(), {"sources": ["imessage", "contacts"], "contacts": "selected", "contact_ids": ["ab-1"]})
    assert {m["handle"] for m in out.imessage} == {"+15145550142", "+15145550111"}
    assert [c["contact_id"] for c in out.contacts] == ["ab-1"]


def test_without_groups() -> None:
    out = filter_result(_result(), {"sources": ["imessage"], "contacts": "all", "include_groups": False})
    assert all(not m["is_group"] for m in out.imessage)
