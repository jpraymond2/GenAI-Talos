"""Unit tests for live connectors — no real accounts, network, or macOS DBs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from recall.connectors.gcal import map_gcal_event
from recall.connectors.gmail import map_gmail_message
from recall.connectors.imessage import decode_attributed_body, fetch_imessage
from recall.connectors.whatsapp import fetch_whatsapp, jid_to_phone, parse_whatsapp_export
from recall.tracing import Tracer


def test_jid_to_phone() -> None:
    assert jid_to_phone("15145550142@s.whatsapp.net") == "+15145550142"


def test_parse_whatsapp_export_iphone_android_multiline_media() -> None:
    iphone = "\n".join(
        [
            "[01/10/2026, 14:30:00] Karim Haddad: Hello from iPhone",
            "still going on line two",
            "[01/10/2026, 14:31:00] Karim Haddad: <Media omitted>",
            "[01/10/2026, 14:32:00] Alex Martin: Got it",
        ]
    )
    android = "\n".join(
        [
            "02/10/2026, 09:15 - Lina Chen: Hello from Android",
            "and a second line",
            "02/10/2026, 09:16 - Lina Chen: <Media omitted>",
            "02/10/2026, 09:17 - Alex Martin: Thanks",
        ]
    )

    iphone_rows = parse_whatsapp_export(iphone, chat_name="iphone_chat")
    android_rows = parse_whatsapp_export(android, chat_name="android_chat")

    assert len(iphone_rows) == 2
    first = iphone_rows[0]
    assert first["text"].startswith("Hello from iPhone")
    assert "still going on line two" in first["text"]
    assert first["source"] == "whatsapp"
    assert all("<Media omitted>" not in r["text"] for r in iphone_rows)

    assert len(android_rows) == 2
    assert "Hello from Android" in android_rows[0]["text"]
    assert "second line" in android_rows[0]["text"]
    assert all("<media omitted>" not in r["text"].lower() for r in android_rows)


def test_decode_attributed_body_handmade_blob() -> None:
    text = "Coffee tomorrow?"
    encoded = text.encode("utf-8")
    # Legacy short form (tag + length).
    blob = b"prefixNSString" + bytes([0x01, len(encoded)]) + encoded + b"\x00trailing"
    assert decode_attributed_body(blob) == text
    # Real chat.db streamtyped form: '+' then 1-byte length then UTF-8.
    real = b"xxxxNSString\x01\x94\x84\x01\x2b" + bytes([len(encoded)]) + encoded + b"\x86NSDictionary"
    assert decode_attributed_body(real) == text
    assert decode_attributed_body(None) == ""
    assert decode_attributed_body(b"no marker here") == ""


def test_map_gmail_message_in_and_out() -> None:
    incoming = {
        "id": "msg_in_1",
        "threadId": "th_1",
        "snippet": "Can you intro me to Lina?",
        "payload": {
            "headers": [
                {"name": "From", "value": "Karim Haddad <karim@ledgerly.io>"},
                {"name": "To", "value": "Alex Martin <alex.martin@mail.mcgill.ca>"},
                {"name": "Subject", "value": "Intro ask"},
                {"name": "Date", "value": "Fri, 26 Sep 2026 10:14:00 +0000"},
            ]
        },
    }
    handles, interaction = map_gmail_message(
        incoming, user_email="alex.martin@mail.mcgill.ca"
    )
    assert interaction is not None
    assert interaction["direction"] == "in"
    assert interaction["handle_value"] == "karim@ledgerly.io"
    assert interaction["source_ref"] == "gmail:msg_in_1"
    assert interaction["thread_id"] == "th_1"
    assert any(h["value"] == "karim@ledgerly.io" for h in handles)

    outgoing = {
        "id": "msg_out_1",
        "threadId": "th_2",
        "snippet": "Sure, happy to intro.",
        "payload": {
            "headers": [
                {"name": "From", "value": "alex.martin@mail.mcgill.ca"},
                {"name": "To", "value": "karim@ledgerly.io"},
                {"name": "Subject", "value": "Re: Intro ask"},
                {"name": "Date", "value": "Fri, 26 Sep 2026 11:00:00 +0000"},
            ]
        },
    }
    _, out_interaction = map_gmail_message(
        outgoing, user_email="alex.martin@mail.mcgill.ca"
    )
    assert out_interaction is not None
    assert out_interaction["direction"] == "out"
    assert out_interaction["handle_value"] == "karim@ledgerly.io"


def test_map_gcal_event() -> None:
    event = {
        "id": "evt_123",
        "summary": "Coffee w/ Karim",
        "location": "Mile End",
        "start": {"dateTime": "2026-06-22T15:00:00Z"},
        "end": {"dateTime": "2026-06-22T15:45:00Z"},
        "attendees": [
            {"email": "alex.martin@mail.mcgill.ca"},
            {"email": "karim@ledgerly.io"},
        ],
    }
    row = map_gcal_event(event)
    assert row is not None
    assert row["title"] == "Coffee w/ Karim"
    assert row["source_ref"] == "calendar:evt_123"
    assert row["start_utc"].startswith("2026-06-22T15:00")
    assert "karim@ledgerly.io" in json.loads(row["attendees"])


def test_imessage_skipped_on_non_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "darwin":
        pytest.skip("skip behaviour is for non-macOS hosts")
    tracer = Tracer(logs_dir=tmp_path / "logs")
    with tracer.activate():
        result = fetch_imessage(tracer=tracer)
    assert result.skipped is not None
    assert "macOS" in result.skipped
    assert result.interactions == []
    lines = tracer.path.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    payload = json.loads(lines[-1])
    assert payload["kind"] == "skipped"
    assert payload["payload"]["connector"] == "imessage"
    assert "reason" in payload["payload"]
    # No message bodies or emails in the skip trace.
    dumped = json.dumps(payload)
    assert "@" not in dumped or "imessage" in dumped


def test_whatsapp_skips_chatstorage_on_non_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform == "darwin":
        pytest.skip("skip behaviour is for non-macOS hosts")
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path / "live"))
    from recall.config import get_settings

    get_settings.cache_clear()
    tracer = Tracer(logs_dir=tmp_path / "logs")
    with tracer.activate():
        result = fetch_whatsapp(settings=get_settings(), tracer=tracer)
    # Without export files, non-macOS should skip overall.
    assert result.skipped is not None
    assert "macOS" in result.skipped or "export" in result.skipped.lower()
    get_settings.cache_clear()


def test_whatsapp_exports_only_on_non_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = tmp_path / "live"
    export_dir = live / "whatsapp_exports"
    export_dir.mkdir(parents=True)
    (export_dir / "karim.txt").write_text(
        "[03/10/2026, 19:41:00] Karim: I'm moving to Toronto in January.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LIVE_DATA_DIR", str(live))
    from recall.config import get_settings

    get_settings.cache_clear()
    tracer = Tracer(logs_dir=tmp_path / "logs")
    with tracer.activate():
        result = fetch_whatsapp(settings=get_settings(), tracer=tracer)
    assert result.skipped is None
    assert result.interactions
    assert any("Toronto" in row["text"] for row in result.interactions)
    get_settings.cache_clear()
