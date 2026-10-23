"""Trusted contact-card anchors (confidence 1.0) for identity resolution."""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from recall.config import Settings, get_settings
from recall.data.queries import ContactRow, HandleRow, all_contacts, all_handles
from recall.data.query_layer import QueryLayer, get_query_layer
from recall.tracing import Tracer

_FREE_PATH_MARKER = "AddressBook-v22.abcddb"


@dataclass(frozen=True)
class AnchorLink:
    """A phone or email listed on a person's own contact card."""

    entity_name: str
    handle_kind: str  # email | phone
    handle_value: str
    contact_id: str
    confidence: float = 1.0


def normalize_phone(value: str) -> str:
    """Normalize phone / WhatsApp JID local-part to +E.164 digits when possible."""
    raw = (value or "").strip()
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    return f"+{digits}"


def normalize_email(value: str) -> str:
    """Lowercase trimmed email."""
    return (value or "").strip().lower()


def load_anchors(
    *,
    settings: Settings | None = None,
    layer: QueryLayer | None = None,
    tracer: Tracer | None = None,
) -> list[AnchorLink]:
    """Load anchor links from sample CSVs or live macOS Contacts + iMessage phones."""
    active = settings if settings is not None else get_settings()
    if active.sample_mode:
        return _anchors_from_sample(active, layer=layer, tracer=tracer)
    live = _anchors_from_macos_contacts()
    if live:
        return live
    # Fall back to CSV contacts if present (tests / hybrid).
    return _anchors_from_sample(active, layer=layer, tracer=tracer)


def anchor_handle_values(anchors: Iterable[AnchorLink]) -> set[str]:
    """Set of normalized handle values that are anchors."""
    out: set[str] = set()
    for link in anchors:
        if link.handle_kind == "email":
            out.add(normalize_email(link.handle_value))
        else:
            out.add(normalize_phone(link.handle_value))
    return out


def _anchors_from_sample(
    settings: Settings,
    *,
    layer: QueryLayer | None,
    tracer: Tracer | None,
) -> list[AnchorLink]:
    ql = layer if layer is not None else get_query_layer(settings, tracer=tracer)
    contacts = all_contacts(source_tag="world.anchors.contacts", layer=ql)
    handles = all_handles(source_tag="world.anchors.handles", layer=ql)
    return _anchors_from_contacts_and_handles(contacts, handles)


def _anchors_from_contacts_and_handles(
    contacts: list[ContactRow],
    handles: list[HandleRow],
) -> list[AnchorLink]:
    """Any email/phone on a contact card is an anchor for that person."""
    links: list[AnchorLink] = []
    seen: set[tuple[str, str]] = set()
    for contact in contacts:
        for email in contact.emails:
            key = ("email", normalize_email(email))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            links.append(
                AnchorLink(
                    entity_name=contact.full_name,
                    handle_kind="email",
                    handle_value=key[1],
                    contact_id=contact.contact_id,
                )
            )
        for phone in contact.phones:
            norm = normalize_phone(phone)
            key = ("phone", norm)
            if not norm or key in seen:
                continue
            seen.add(key)
            links.append(
                AnchorLink(
                    entity_name=contact.full_name,
                    handle_kind="phone",
                    handle_value=norm,
                    contact_id=contact.contact_id,
                )
            )
    # Join iMessage / wa handles whose normalized phone is already an anchor.
    phone_anchors = {link.handle_value for link in links if link.handle_kind == "phone"}
    for handle in handles:
        if handle.kind not in {"imessage", "phone", "wa_jid"}:
            continue
        norm = normalize_phone(handle.value)
        if norm and norm in phone_anchors:
            key = ("phone", norm)
            if key not in seen:
                seen.add(key)
                links.append(
                    AnchorLink(
                        entity_name=handle.display_name or norm,
                        handle_kind="phone",
                        handle_value=norm,
                        contact_id="imessage-join",
                    )
                )
    return links


def _anchors_from_macos_contacts() -> list[AnchorLink]:
    """Read macOS AddressBook SQLite (copy then open read-only). Empty if unavailable."""
    sources = Path.home() / "Library" / "Application Support" / "AddressBook" / "Sources"
    if not sources.is_dir():
        return []
    links: list[AnchorLink] = []
    for db_path in sources.glob(f"*/{_FREE_PATH_MARKER}"):
        links.extend(_read_one_addressbook(db_path))
    return links


def _read_one_addressbook(db_path: Path) -> list[AnchorLink]:
    """Copy AddressBook DB to a temp file and extract emails/phones."""
    links: list[AnchorLink] = []
    try:
        with tempfile.TemporaryDirectory(prefix="recall-ab-") as tmp:
            copy_path = Path(tmp) / "AddressBook-v22.abcddb"
            shutil.copy2(db_path, copy_path)
            # Related wal/shm may be absent; ignore.
            uri = f"file:{copy_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row
            try:
                records = conn.execute(
                    "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME FROM ZABCDRECORD"
                ).fetchall()
                emails = conn.execute(
                    "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"
                ).fetchall()
                phones = conn.execute(
                    "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"
                ).fetchall()
            except sqlite3.Error:
                conn.close()
                return []
            names: dict[int, str] = {}
            for row in records:
                pk = int(row["Z_PK"])
                first = (row["ZFIRSTNAME"] or "").strip()
                last = (row["ZLASTNAME"] or "").strip()
                full = f"{first} {last}".strip() or f"contact-{pk}"
                names[pk] = full
            for row in emails:
                owner = int(row["ZOWNER"] or 0)
                email = normalize_email(str(row["ZADDRESS"] or ""))
                if not email or owner not in names:
                    continue
                links.append(
                    AnchorLink(
                        entity_name=names[owner],
                        handle_kind="email",
                        handle_value=email,
                        contact_id=f"ab-{owner}",
                    )
                )
            for row in phones:
                owner = int(row["ZOWNER"] or 0)
                phone = normalize_phone(str(row["ZFULLNUMBER"] or ""))
                if not phone or owner not in names:
                    continue
                links.append(
                    AnchorLink(
                        entity_name=names[owner],
                        handle_kind="phone",
                        handle_value=phone,
                        contact_id=f"ab-{owner}",
                    )
                )
            conn.close()
    except OSError:
        return []
    return links
