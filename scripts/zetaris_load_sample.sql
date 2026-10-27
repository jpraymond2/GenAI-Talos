-- Load Recall sample CSVs into Zetaris as Lightning FILESTORE tables.
--
-- How to run (Zetaris UI):
--   1. Open the local UI (default http://localhost:3000) and sign in.
--   2. Upload the four CSVs from SAMPLE_DATA_DIR (default: data/synthetic/tables/)
--      into the NDP File System under folder `recall` so paths resolve as
--      dffs://recall/handles.csv (and interactions, events, contacts).
--      Unit-test fixture: data/sample/ — same filenames.
--   3. Open SQL Editor. Run ONE statement at a time (no trailing semicolon).
--      Multi-statement batches are rejected by the REST/UI SQL runner.
--   4. After CREATE LIGHTNING DATABASE, create each FILESTORE table separately.
--
-- Queries from Recall use recall.<table>; set ZETARIS_SCHEMA=recall (default)
-- so the client qualifies to the same Lightning database name.
-- Expected row counts: scripts/zetaris_smoke.py reads settings.sample_data_dir.

-- 1) Create the Lightning database (admin). Skip if it already exists.
CREATE LIGHTNING DATABASE recall DESCRIBE BY "Recall CSVs for hackathon demo"

-- 2) Handles
CREATE LIGHTNING FILESTORE TABLE handles FROM recall FORMAT CSV OPTIONS (
  path "dffs://recall/handles.csv",
  header "true",
  inferSchema "true"
)

-- 3) Interactions
CREATE LIGHTNING FILESTORE TABLE interactions FROM recall FORMAT CSV OPTIONS (
  path "dffs://recall/interactions.csv",
  header "true",
  inferSchema "true"
)

-- 4) Events
CREATE LIGHTNING FILESTORE TABLE events FROM recall FORMAT CSV OPTIONS (
  path "dffs://recall/events.csv",
  header "true",
  inferSchema "true"
)

-- 5) Contacts
CREATE LIGHTNING FILESTORE TABLE contacts FROM recall FORMAT CSV OPTIONS (
  path "dffs://recall/contacts.csv",
  header "true",
  inferSchema "true"
)

-- Smoke check (optional, still one statement):
-- SELECT handle_id, kind, value FROM recall.handles LIMIT 5
