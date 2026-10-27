"""Extract macOS Contacts from AddressBook DBs (safe copy)."""

from __future__ import annotations

import sqlite3
from typing import Any

from recall.mac_agent.copy_db import copied_sqlite
from recall.mac_agent.paths import addressbook_sources
from recall.mac_agent.phones import normalize_e164

_AB_NAME = "AddressBook-v22.abcddb"


def extract_contacts(
    *,
    after_zpk: int = 0,
    excluded_contacts: list[str] | None = None,
    default_region: str = "CA",
) -> tuple[list[dict[str, Any]], int]:
    """Return (contacts, max_zpk_seen). contact_id = ab-{source}-{z_pk}."""
    excluded = {n.lower() for n in (excluded_contacts or [])}
    sources = addressbook_sources()
    if not sources.is_dir():
        return [], after_zpk

    contacts: dict[str, dict[str, Any]] = {}
    max_zpk = after_zpk

    for db_path in sorted(sources.glob(f"*/{_AB_NAME}")):
        source_id = db_path.parent.name[:8]
        try:
            batch, local_max = _read_one(
                db_path,
                source_id=source_id,
                after_zpk=after_zpk,
                excluded=excluded,
                default_region=default_region,
            )
        except OSError:
            continue
        max_zpk = max(max_zpk, local_max)
        for row in batch:
            contacts[row["contact_id"]] = row

    return list(contacts.values()), max_zpk


def _read_one(
    db_path,
    *,
    source_id: str,
    after_zpk: int,
    excluded: set[str],
    default_region: str,
) -> tuple[list[dict[str, Any]], int]:
    with copied_sqlite(db_path, prefix="recall-ab-") as copy:
        conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            records = conn.execute(
                """
                SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION, ZJOBTITLE
                FROM ZABCDRECORD
                WHERE Z_PK > ?
                ORDER BY Z_PK ASC
                """,
                (after_zpk,),
            ).fetchall()
            emails = conn.execute(
                "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"
            ).fetchall()
            phones = conn.execute(
                "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"
            ).fetchall()
        except sqlite3.Error:
            conn.close()
            return [], after_zpk
        conn.close()

    email_map: dict[int, list[str]] = {}
    for row in emails:
        owner = int(row["ZOWNER"] or 0)
        addr = str(row["ZADDRESS"] or "").strip().lower()
        if addr:
            email_map.setdefault(owner, []).append(addr)

    phone_map: dict[int, list[str]] = {}
    for row in phones:
        owner = int(row["ZOWNER"] or 0)
        e164 = normalize_e164(str(row["ZFULLNUMBER"] or ""), default_region=default_region)
        if e164:
            phone_map.setdefault(owner, []).append(e164)

    out: list[dict[str, Any]] = []
    max_zpk = after_zpk
    for row in records:
        pk = int(row["Z_PK"])
        max_zpk = max(max_zpk, pk)
        first = (row["ZFIRSTNAME"] or "").strip()
        last = (row["ZLASTNAME"] or "").strip()
        name = f"{first} {last}".strip()
        org = (row["ZORGANIZATION"] or "").strip()
        title = (row["ZJOBTITLE"] or "").strip()
        if not name and org:
            name = org
        if not name:
            continue
        if name.lower() in excluded:
            continue
        phones_list = sorted(set(phone_map.get(pk, [])))
        emails_list = sorted(set(email_map.get(pk, [])))
        if not phones_list and not emails_list:
            continue
        out.append(
            {
                "contact_id": f"ab-{source_id}-{pk}",
                "z_pk": pk,
                "name": name,
                "organization": org,
                "title": title,
                "phones": phones_list,
                "emails": emails_list,
                "source": "macos_contacts",
            }
        )
    return out, max_zpk
