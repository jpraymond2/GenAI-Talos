"""Turn data/synthetic/raw/* into Recall's four tables, using the real connector code.

    uv run python scripts/synthetic/raw_to_tables.py [--root data/synthetic]

Writes data/synthetic/tables/{handles,interactions,events,contacts}.csv (same columns
as data/sample), ground_truth/fact_evidence.json (facts -> real source_refs), and
ingest_report.md listing every place the connectors' raw output disagreed with the
answer key. Those disagreements are corrected in the tables and counted in the report,
so the tables are what a *fixed* connector would produce.

Point Recall at it:  SAMPLE_DATA_DIR=data/synthetic/tables
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recall.connectors.gcal import map_gcal_event  # noqa: E402
from recall.connectors.gmail import map_gmail_message  # noqa: E402
from recall.connectors.imessage import _apple_ts_to_utc, _normalize_handle, decode_attributed_body  # noqa: E402
from recall.connectors.imessage import _stable_id as im_id  # noqa: E402
from recall.connectors.imessage import _stable_id as sid  # noqa: E402
from recall.connectors.schema import TABLE_COLUMNS  # noqa: E402

SKIP_GMAIL_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"}  # mirrors the live Gmail query
SNIPPET_LEN = 200


class Report:
    def __init__(self) -> None:
        self.c: Counter[str] = Counter()
        self.examples: dict[str, list[str]] = defaultdict(list)

    def hit(self, key: str, example: str = "") -> None:
        self.c[key] += 1
        if example and len(self.examples[key]) < 3:
            self.examples[key].append(example[:160])


def e164(raw: str) -> str:
    d = re.sub(r"\D", "", raw)
    if len(d) == 10:
        d = "1" + d
    return "+" + d if d else raw


def load_gt(root: Path) -> list[dict]:
    with (root / "ground_truth" / "messages.jsonl").open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


# --------------------------------------------------------------------------- contacts (.vcf)
def parse_vcf(path: Path) -> list[dict]:
    cards, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "BEGIN:VCARD":
            cur = {"emails": [], "phones": []}
        elif line == "END:VCARD" and cur is not None:
            cards.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, value = line.split(":", 1)
            name = key.split(";")[0].upper()
            if name == "FN":
                cur["fn"] = value
            elif name == "NICKNAME":
                cur["nickname"] = value
            elif name == "ORG":
                cur["org"] = value.split(";")[0]
            elif name == "TITLE":
                cur["title"] = value
            elif name == "EMAIL":
                cur["emails"].append(value.lower())
            elif name == "TEL":
                cur["phones"].append(e164(value))
            elif name == "ADR":
                parts = value.split(";")
                cur["city"] = parts[3] if len(parts) > 3 else ""
            elif name == "BDAY":
                cur["bday"] = value
    return cards


# --------------------------------------------------------------------------- main
def convert(root: Path) -> None:
    raw = root / "raw"
    out = root / "tables"
    out.mkdir(parents=True, exist_ok=True)
    rep = Report()
    gt = load_gt(root)
    handles: dict[str, dict] = {}
    interactions: list[dict] = []
    key_to_ref: dict[str, dict] = {}

    def add_handle(kind: str, value: str, display: str, source: str) -> None:
        hid = sid("h", kind, value)
        if hid not in handles:
            handles[hid] = {"handle_id": hid, "kind": kind, "value": value, "display_name": display or value, "source": source}

    profile = json.loads((raw / "gmail" / "profile.json").read_text(encoding="utf-8"))
    me_email = profile["emailAddress"]
    # The owner's own handles (the sample data lists them first too)
    add_handle("email", me_email, "Jordan Lefebvre", "gmail")
    add_handle("phone", "+15145550199", "Jordan Lefebvre", "contacts")
    add_handle("imessage", "+15145550199", "Jordan Lefebvre", "imessage")

    # ---------------- contacts
    cards = parse_vcf(raw / "contacts" / "contacts.vcf")
    contacts = []
    for i, c in enumerate(cards):
        cid = sid("c", c.get("fn", ""), str(i))
        contacts.append({
            "contact_id": cid,
            "full_name": c.get("fn", ""),
            "org": c.get("org", ""),
            "title": c.get("title", ""),
            "city": c.get("city", ""),
            "nickname": c.get("nickname", ""),
            "emails": json.dumps(c["emails"]),
            "phones": json.dumps(c["phones"]),
            "source": "contacts",
        })
        for e in c["emails"]:
            add_handle("email", e, c.get("fn", ""), "contacts")
        for ph in c["phones"]:
            add_handle("phone", ph, c.get("fn", ""), "contacts")

    # ---------------- Gmail
    gt_by_gmail = {g["raw_ref"].get("gmail_id"): g for g in gt if g["source"] == "gmail"}
    for line in (raw / "gmail" / "messages.jsonl").read_text(encoding="utf-8").splitlines():
        msg = json.loads(line)
        if SKIP_GMAIL_LABELS & set(msg.get("labelIds", [])):
            rep.hit("gmail: skipped by live query (promotions/social)")
            continue
        hs, row = map_gmail_message(msg, user_email=me_email)
        if row is None:
            continue
        for h in hs:
            add_handle(h["kind"], h["value"], h["display_name"], "gmail")
        if "&#39;" in row["text"] or "&quot;" in row["text"] or "&amp;" in row["text"]:
            rep.hit("gmail: snippet is HTML-escaped (connector stores &#39; etc.)", row["text"])
        # Use the decoded text/plain body (minus quoted reply) instead of the 200-char snippet
        body = ""
        for part in msg["payload"].get("parts", []):
            if part["mimeType"] == "text/plain":
                data = part["body"]["data"]
                body = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")
        body = re.split(r"\n\nOn .{10,80} wrote:\n", body)[0].strip()
        g = gt_by_gmail.get(msg["id"])
        if g and g["facts"] and len(" ".join(body.split())) > SNIPPET_LEN:
            rep.hit("gmail: fact-carrying email longer than the 200-char snippet (metadata-only fetch loses the end)", msg["snippet"][:80])
        row["text"] = body or html.unescape(row["text"])
        interactions.append(row)
        if g:
            key_to_ref[g["key"]] = {"interaction_id": row["interaction_id"], "source_ref": row["source_ref"]}

    # ---------------- iMessage chat.db (same SQL as the connector, without the 120-day cutoff)
    con = sqlite3.connect(f"file:{raw / 'imessage' / 'chat.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT m.ROWID AS message_id, m.text, m.attributedBody AS attributed_body, m.is_from_me, m.date AS msg_date,
                  m.associated_message_type AS assoc, h.id AS handle_id, c.chat_identifier, c.ROWID AS chat_rowid
           FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
           JOIN chat c ON c.ROWID = cmj.chat_id LEFT JOIN handle h ON h.ROWID = m.handle_id ORDER BY m.date"""
    ).fetchall()
    con.close()
    gt_im = {g["raw_ref"]["rowid"]: g for g in gt if g["source"] == "imessage"}
    for r in rows:
        text = str(r["text"] or "").strip()
        if not text:
            text = decode_attributed_body(r["attributed_body"]).strip()
            g = gt_im.get(r["message_id"])
            if g and text != g["text"]:
                rep.hit("imessage: attributedBody mis-decoded (length byte 1–5/43/99 mistaken for a tag byte)", f"{text[:60]!r} vs {g['text'][:60]!r}")
                text = g["text"]
        if r["assoc"]:
            rep.hit("imessage: tapback reaction ingested as a message (associated_message_type≠0)", text)
            continue
        kind, value = _normalize_handle(str(r["handle_id"] or r["chat_identifier"]))
        add_handle(kind, value, value, "imessage")
        row = {"interaction_id": im_id("i", "imessage", str(r["message_id"])), "source": "imessage",
               "thread_id": im_id("th", "imessage", str(r["chat_identifier"])), "handle_value": value,
               "direction": "out" if r["is_from_me"] else "in", "ts_utc": _apple_ts_to_utc(r["msg_date"]),
               "subject": "", "text": text, "source_ref": f"imessage:{r['message_id']}"}
        interactions.append(row)
        g = gt_im.get(r["message_id"])
        if g:
            key_to_ref[g["key"]] = {"interaction_id": row["interaction_id"], "source_ref": row["source_ref"]}

    # ---------------- Messenger (no connector yet: minimal mapper)
    gt_ms = {(g["raw_ref"]["thread_path"], g["raw_ref"]["timestamp_ms"]): g for g in gt if g["source"] == "messenger"}

    def fix(s: str) -> str:
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s

    for path in sorted((raw / "messenger" / "inbox").glob("*/message_1.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        title = fix(doc["title"])
        tp = doc["thread_path"]
        for m in doc["messages"]:
            if "content" not in m:
                rep.hit("messenger: photo-only message skipped")
                continue
            sender = fix(m["sender_name"])
            text = fix(m["content"])
            if text != m["content"]:
                rep.hit("messenger: mojibake fixed (export encodes UTF-8 as latin-1)", m["content"])
            direction = "out" if sender == "Jordan Lefebvre" else "in"
            hv = title if direction == "out" and len(doc["participants"]) == 2 else sender
            if direction == "out" and len(doc["participants"]) > 2:
                hv = "Jordan Lefebvre"
            add_handle("messenger", hv, hv, "messenger")
            ts = datetime.fromtimestamp(m["timestamp_ms"] / 1000, tz=timezone.utc)
            iid = sid("i", "messenger", tp, str(m["timestamp_ms"]), sender)
            row = {"interaction_id": iid, "source": "messenger", "thread_id": sid("th", "messenger", tp), "handle_value": hv,
                   "direction": direction, "ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "subject": "", "text": text,
                   "source_ref": f"messenger:{tp}:{m['timestamp_ms']}"}
            interactions.append(row)
            g = gt_ms.get((tp, m["timestamp_ms"]))
            if g:
                key_to_ref[g["key"]] = {"interaction_id": iid, "source_ref": row["source_ref"]}

    # ---------------- Calendar
    doc = json.loads((raw / "calendar" / "events.json").read_text(encoding="utf-8"))
    events = []
    event_ref = {}
    for item in doc["items"]:
        if item.get("status") == "cancelled":
            rep.hit("calendar: cancelled event would be ingested as a real meeting (connector ignores status)", item["summary"])
            row = map_gcal_event(item)
            event_ref[item["id"]] = {"event_id": row["event_id"], "source_ref": row["source_ref"], "status": "cancelled"}
            continue
        row = map_gcal_event(item)
        if row:
            events.append(row)
            event_ref[item["id"]] = {"event_id": row["event_id"], "source_ref": row["source_ref"]}

    # ---------------- write tables
    interactions.sort(key=lambda r: r["ts_utc"])
    tables = {"handles": list(handles.values()), "interactions": interactions, "events": events, "contacts": contacts}
    for name, rows_ in tables.items():
        with (out / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=TABLE_COLUMNS[name], lineterminator="\n")
            w.writeheader()
            for r in rows_:
                w.writerow({k: r.get(k, "") for k in TABLE_COLUMNS[name]})

    # ---------------- facts -> real source refs
    facts = json.loads((root / "ground_truth" / "facts.json").read_text(encoding="utf-8"))
    unresolved = []
    for fid, f in facts.items():
        refs = []
        for ev in f["evidence"]:
            if ev["source"] == "calendar":
                r = event_ref.get(ev["raw_ref"]["event_id"])
            else:
                r = key_to_ref.get(ev["key"])
            if r:
                refs.append(r)
        f["source_refs"] = refs
        if not refs:
            unresolved.append(fid)
    (root / "ground_truth" / "fact_evidence.json").write_text(json.dumps(facts, indent=2, ensure_ascii=False), encoding="utf-8", newline="")

    (out / "voice_sample.txt").write_text("I'm meeting Omar Haddad on Wednesday\n", encoding="utf-8", newline="")

    # ---------------- report
    lines = ["# Synthetic ingest report", "",
             f"Tables: handles={len(handles)}, interactions={len(interactions)}, events={len(events)}, contacts={len(contacts)}", "",
             f"Facts with resolved evidence: {len(facts) - len(unresolved)}/{len(facts)}" + (f" (unresolved: {', '.join(unresolved)})" if unresolved else ""), "",
             "## Where the connectors disagreed with the answer key", "",
             "Each item is corrected in the tables above; the count is how many rows were affected.", ""]
    for key, n in sorted(rep.c.items(), key=lambda kv: -kv[1]):
        lines.append(f"- **{n}×** {key}")
        for ex in rep.examples[key]:
            lines.append(f"  - e.g. `{ex}`")
    (root / "ingest_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    print("\n".join(lines[:6]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default="data/synthetic")
    convert(Path(ap.parse_args().root))


if __name__ == "__main__":
    main()
