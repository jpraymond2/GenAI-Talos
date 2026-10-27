# Synthetic world (large) — for testing the agent

A bigger, messier, fully **fictional** world than `data/sample`, built to check whether
Recall's answers are *correct*. It comes in each app's **raw export format**, is converted
to Recall's tables by the real connector code, and ships with an **answer key**.

Owner persona: **Jordan Lefebvre** — McGill MBA student and co-founder/CEO of Tidepool
(climate-data startup), Montreal. "Today" is **Mon Oct 26, 2026, 18:00** Montreal time.

| | |
|---|---|
| People | 59 (21 hand-written, 38 filler) |
| Messages | ~2,950 over 18 months (Gmail, WhatsApp, iMessage, Messenger) |
| Calendar events | ~175 (incl. recurring classes/syncs, a cancelled lunch, all-day events) |
| Facts in the answer key | 41 |
| Eval questions | 23 |

## Layout

```
raw/gmail/messages.jsonl        Gmail API users.messages.get (format=full), one per line
raw/gmail/profile.json          Gmail API users.getProfile
raw/calendar/events.json        Calendar API events.list (singleEvents=true, showDeleted=true)
raw/whatsapp/ChatStorage.sqlite WhatsApp Desktop for Mac DB (real schema subset) — read with the Mac agent's extractor
raw/imessage/chat.db            macOS Messages DB (real schema subset; text often only in attributedBody)
raw/messenger/inbox/*/message_1.json   Facebook "Download your information" (latin-1 mojibake, newest first)
raw/contacts/contacts.vcf       vCard 3.0 address book (some cards stale on purpose)
tables/*.csv                    Recall's 4 tables, same columns as data/sample
ground_truth/people.json        who every handle really belongs to
ground_truth/facts.json         41 facts + the generated messages that prove them
ground_truth/fact_evidence.json same, with the real interaction_id / source_ref in tables/
ground_truth/fact_keywords.json keyword groups for cheap automatic grading
ground_truth/questions.json     23 questions with expected behaviour
ground_truth/messages.jsonl     every message with its true UTC time and raw location
ingest_report.md                every place the connectors disagreed with the answer key
```

## Hard cases on purpose

- **Three Omars** (founder, MBA classmate saved as "Omar B (MBA)", landlord saved as "Omar Kassem (proprio)" in the address book).
- **Two different people named Daniel Kim** (an investor, and a UofT friend saved as "Dan Kim (UofT)") — must never merge.
- **Nicknames:** Alexandra Petrova is "Sasha" (WhatsApp, Messenger); family saved as "Maman", "Papa"; some filler saved by first name only; unsaved WhatsApp numbers show only the person's push name ("Léo", "Yusuf ✌️").
- **Changed handles:** Priya's email changes with her job (card still shows the old job); Yusuf's number changes and the new one isn't in contacts (it says so on iMessage, then chats on WhatsApp).
- **Stale facts:** Lucas's card says Montreal, but he moved to Paris (French-language messages).
- **Assistant scheduling:** Tyler Brooks books the meeting, but it's with Rachel Goldberg.
- **Open loops:** asks Jordan never answered (Omar's intro, Kevin's question) and promises Jordan made (financial model, data room, transcript, French deck…).
- **Noise:** newsletters, receipts, OTP codes, bank alerts, promotions (~450 emails), iMessage tapbacks, Messenger photos.
- **Time zones:** events after Nov 1 are EST (−05:00).
- **Group chats:** WhatsApp groups (founders, family) where each message must be credited to its sender, plus Messenger groups (study group, undergrad crew).
- **Sparse and unknown people:** Tom Becker (two emails); "Sarah Whitfield" doesn't exist.

## Use it

```bash
uv run python scripts/synthetic/make_synthetic.py      # raw/ + ground_truth/  (deterministic)
uv run python scripts/synthetic/raw_to_tables.py       # tables/ + ingest_report.md
SAMPLE_DATA_DIR=data/synthetic/tables uv run python -m recall ask "I'm meeting Omar" --sample
```

`--scale` on the generator changes filler volume (default 1.6). Never commit real data here.
