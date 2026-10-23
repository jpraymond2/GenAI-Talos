# World Model Agent Engine (Meterless)

Recall's World Model stores people, orgs, relationships, and identity assumptions
in SQLite (`data/world_sample.db` in SAMPLE_MODE, else `data/world.db`).

## Provenance and append-only rules

- **Observations** are append-only: never update or delete a prior observation row.
- **Conflicts** record competing attribute values; they are recomputed per ingest,
  not silently overwritten without a new conflict record.
- **Identity assumptions** are versioned: a rescore writes a new score file under
  `data/identity_scores/<handle_slug>/<run_id>.json`, inserts a new assumption
  row, and marks the previous assumption `superseded`. Never overwrite a score file.
- Every identity decision is also appended to:
  - `logs/<run_id>/identity_ledger.jsonl`
  - `logs/<run_id>/world_provenance.jsonl`
- User `confirm` / `reject` sets status `confirmed_by_user` / `rejected_by_user`
  with probability 1.0 / 0.0 and must not be overridden by later automatic rescoring.

## Anchors

Phone/email listed on a person's own contact card (address book + iMessage join,
or sample `contacts.csv` / `handles.csv`) are **anchors** at confidence 1.0.
Unlinked handles go through identity scoring (`recall/engines/world/identity_scoring.py`).
