# Zetaris notes (Open Agent Hackathon 2026)

_Researched 2026-10-23 (read-only). Times from HackOS are as displayed in my browser (ET). "Repo" = github.com/zetaris/getting-started @ `6a2fbaa` (2026-10-22); the HackOS "Getting Started Prompt" pins an older commit `44668b2`._

## Access

- **There is no Zetaris-hosted / shared instance.** Each team runs its own: local Docker install **or** a managed cluster in your own AWS Free Tier account. Either is fine for eligibility.
  - Michael Hay (Zetaris), Help Zetaris, Oct 19, pinned: "there are two ways to get a Zetaris instance running: 1.)within the AWS free tier AND 2.)on a laptop or desktop with docker."
  - Michael Hay, Help Zetaris, Oct 23 12:44 PM: "We don't have a shared Zetaris instance for general consumption."
  - The "shared instance" wording in the repo means *the instance your team shares*, not a hosted one (Michael Hay, pinned reply to Bens Lucca, Oct 19).
- **Sign-up:** https://cloud.enterprise.zetaris.com/register → choose local or AWS.
  - Local: Run locally page (https://cloud.enterprise.zetaris.com/dashboard/download) gives `zetaris-platform.zip` and a `docker login zetarishackathon.azurecr.io` command with registry credentials. New credentials can take up to ~24 h to activate (participant reports, Oct 21). Needs ~8 GB RAM / 4 CPUs for Docker, 15 GB disk, amd64 images (repo: docs/install/choose-install.md). A 16 GB GitHub Codespace was OK'd (Joleen Liu, Oct 23).
  - AWS: Zetaris Cloud → Compute → Clusters → Create Cluster (Mini). Repo: docs/install/managed-aws-cluster-guide.md.
- **Where the logins are (don't paste them anywhere):**
  - Local: the admin email/password you set as `ZETARIS_ADMIN_EMAIL` / `ZETARIS_ADMIN_PASSWORD` in the platform bundle's `.env` before bootstrap. UI at http://localhost:3000. (Not your Zetaris Cloud login.)
  - AWS: cluster **Overview → Organisations** shows the admin email + "Copy password"; **Overview → Access** has the Sign-in URL, hostname and JDBC endpoint.
  - API key: UI → **Management → API Tokens** → `+`. Shown once.
  - Org ID: UI → **Management → Organisation** → numeric **ID** field. Bevan Lewis (Zetaris), Oct 19, pinned: "For your instances the org id is 1" (later caveat from Himanshu Varia: check your own).
  - JDBC driver JAR: shipped in the platform bundle (participant path: `zetaris-platform/jdbc/ndp-jdbc-driver-2.4.3.1-…-driver.jar`).

## Endpoint

Three ways to query from Python (all confirmed). No Python SDK or MCP server is provided; coding assistants use JDBC/REST via the repo scripts.

| Route | URL (local default) | Method / path |
|---|---|---|
| **Direct REST** (recommended for apps) | `ZETARIS_REST_URL=http://localhost:8888/api/v1.0` | `POST {REST}/sql-editor/sqls/run` |
| UI proxy | `ZETARIS_BASE_URL=http://localhost:3000` | `POST {BASE}/api/proxy/sql-editor/sqls/run-query` |
| JDBC | `jdbc:zetaris:lightning@localhost:10000` | driver class `com.zetaris.lightning.jdbc.LightningDriver` (Python: `jaydebeapi` + JDK 11+) |

Other useful REST paths: `GET {REST}/datasource/datasources`; `POST {BASE}/api/auth/login`; OpenAPI spec at `{host}:8888/redoc/docs.yaml` (Basic auth only); docs UI at `http://localhost:8888/redoc/index.html`. For AWS, use your cluster's own hostname/endpoints. (Sources: repo `.env.example`, `docs/connections/client-setup.md`, `scripts/zetaris_sql.py`, `scripts/query_zetaris.py`.)

## Auth

- **Bearer token** in `Authorization: Bearer <token>` **plus** headers `X-Org-ID: <numeric org id>` and `X-Request-ID: <fresh UUID per request>`. No `x-api-key`-style header.
- Token = API key from Management → API Tokens (direct REST), **or** a login token: `POST {BASE}/api/auth/login` with JSON `{"username": ..., "password": ...}` → use `idToken` (repo helper also accepts `token`/`accessToken`) as the Bearer.
- OpenAPI spec route uses **HTTP Basic** (username/password), not the bearer key.
- JDBC uses username + password.
- **Known bug (local bundle 2.4.3.1):** fresh API keys return `HTTP 400 {"msg":"Not Allowed"}`; api log says "Api token is not allowed". Workaround confirmed by Zetaris (Himanshu Varia, Hélène AUERBACH thread, Oct 22): use JDBC, or log in via `/api/auth/login` and send the `idToken` (with `X-Org-ID`) to `/api/proxy/sql-editor/sqls/run-query`. Sean Rugge (Oct 21 thread) reports the idToken also works on the direct `:8888` route. Zetaris says it has "raised it with the team".
- Tokens can't be permission-scoped; for a read-only agent create a non-admin user with a "General" role (view only) and generate its key (Bevan Lewis, Oct 22; repo docs/guides/permissions.md).

## Request format

Direct REST (`POST {REST}/sql-editor/sqls/run`), from repo `scripts/zetaris_sql.py`:

```json
{"queryId": "<uuid4>", "sql": "SELECT 1", "source": "SqlEditor", "limit": 100}
```

UI proxy (`POST {BASE}/api/proxy/sql-editor/sqls/run-query`), from `scripts/query_zetaris.py`:

```json
{"sql": "SELECT 1", "limit": 1000, "engineId": "<optional>"}
```

- `queryId` and `X-Request-ID` must be UUIDs, otherwise HTTP 400.
- **One statement per request**, no trailing semicolon. Two statements → error, neither runs (Bevan Lewis, Oct 22). Exception: a multi-table `COMPILE USL …` must be sent whole.
- **Parameters / placeholders (`?`, `:name`): UNKNOWN** — not mentioned anywhere; assume no binding over REST and escape/validate values client-side. (JDBC via jaydebeapi may support `?`; untested.)

## Response format

- Direct REST: JSON object; rows are under `data` as **arrays (positional), values as strings**. `SELECT 1` → `data == [["1"]]` (repo `preflight.py`).
- "REST returns values as strings, and decimals may use exponent format; convert them before comparing." (repo client-setup.md)
- Column names: repo `examples/pudl-chart/render.py` reads an optional `headers` list (strings or `{"name": ...}` objects). **Exact column-metadata shape: UNKNOWN** — inspect a real response.
- Pagination: **UNKNOWN** — only a `limit` field is documented (UI proxy default 1000).
- Errors: non-2xx with JSON body, e.g. `{"msg":"Not Allowed"}`.

## SQL dialect

- **Spark SQL** ("Registered tables are queried with standard Spark SQL", repo SQL command reference §3) plus Lightning commands (`SHOW LIGHTNING DATABASES`, `CREATE LIGHTNING …`, `CACHE TABLE`, USL/DQ commands). Tested on Zetaris 2.4.3.1.
- Always qualify tables: `<source>.<table>`. Backticks for mixed-case/reserved/punctuated identifiers.
- Known gotchas (repo docs/guides/zetaris-lightning-sql-companion.md):
  - Referencing the same `UNION ALL`/`explode()` view twice (self-join, `IN`/`HAVING` subquery) → `MISSING_ATTRIBUTES.RESOLVED_ATTRIBUTE_APPEAR_IN_OPERATION`; use window functions.
  - No window functions in `REGISTER DQ` rules; no row alias before `AS`.
  - `CREATE SCHEMASTORE CONTAINER` has no `IF NOT EXISTS`.
  - `DESCRIBE BY` text rejects `()`, `/`, `:`.
  - Quoted JSON numbers stay strings → `CAST(... AS DOUBLE)`.
  - Typographic quotes `“…”` aren't parsed; use straight quotes.
  - Replacing a USL model clears activation; re-run `ACTIVATE USL TABLE`. Rebuilt USLs get a new ID, so re-grant access.

## Data sources

- Supported (repo SQL command reference §4–6): **JDBC databases (Postgres, MySQL, etc.), Cassandra, MongoDB, DynamoDB** via `CREATE DATASOURCE`; **files** (CSV, JSON, Parquet, ORC, Delta, XML, Avro, PDF, …) on S3 / Azure Blob / GCS / HDFS / platform storage via `CREATE LIGHTNING FILESTORE TABLE`; **REST/JSON APIs** via `CREATE LIGHTNING REST TABLE`; Kafka, Iceberg/data lakes, Hive/Glue. Michael Hay: "You can also attach RESTful sources, CSV, Postgres, etc." (Oct 19). Google Sheets: not mentioned (UNKNOWN).
- Pre-loaded samples: `NY_TAXI`, `TPCDS_DB` (some installs also show `POKEAPI_REST`).
- Register a database:
  ```sql
  CREATE DATASOURCE crm_pg DESCRIBE BY "PostgreSQL" OPTIONS (
    jdbcdriver "org.postgresql.Driver",
    jdbcurl "jdbc:postgresql://host:5432/db",
    username "...", password "...", fetchsize "1000")
  REGISTER DATASOURCE TABLES FROM crm_pg
  ```
- Files: `CREATE LIGHTNING DATABASE <db> DESCRIBE BY "..."` first, then `CREATE LIGHTNING FILESTORE TABLE <t> FROM <db> FORMAT CSV OPTIONS (path "...", header "true", inferSchema "true")`. Local upload: put file in UI's NDP File System under `file-store`, then `path "dffs://<folder>/<file>.csv"` (Bevan Lewis, Oct 22) — or create it in the UI (Saketh Reddy, Oct 22). Public S3: `isS3BucketPublic "true"`, no keys needed (Michael Hay, Oct 22).
- Views: `CREATE SCHEMASTORE CONTAINER <c>` then `CREATE [OR REPLACE] SCHEMASTORE VIEW <v> WITH CONTAINER <c> AS SELECT ...`; or `CREATE DATASOURCE VIEW <v> FROM <ds> AS <native SQL>`.
- Most `CREATE`/`REGISTER` commands are **administrators only**.
- **Judging:** building a **Unified Semantic Layer (USL)** is "the key thing we will be judging" (Michael Hay, Oct 21): onboard a few sources, build + query a USL, add a basic data-quality rule, then reflect on it.

## Limits

- **Localhost / laptop DB:** inside Zetaris SQL, `localhost` is the Zetaris container. On a local install, host services are reachable at `host.docker.internal:<port>` (participant Osman Can Çetlenbik, Oct 23 — not official). An AWS cluster can only reach sources reachable from AWS. Files on your laptop aren't reachable unless copied into Zetaris storage (repo troubleshooting).
- **Timeouts:** repo clients use 30 s (UI proxy helper) and 600 s (direct REST runner). No server-side query timeout is documented (UNKNOWN).
- **Rate limits on Zetaris itself:** none documented (UNKNOWN). Upstream REST sources have their own (e.g. company_dns, SEC EDGAR need a `User-Agent: app_name you@domain`).
- **Performance:** REST tables re-fetch on every query (40–50 s seen) → `CACHE TABLE` after creating; cache is lost on restart, so re-cache on startup (Joleen Liu, Oct 23).
- **Other known issues:** local API-key 400 bug (see Auth); JSON Lines files only return their first row (use one JSON array per file); some open-data recipes are marked `known_to_fail` in the repo; AWS clusters fail in eu-north-1 / when new accounts block load balancers (try us-west-1).
- **Non-admin USL access:** Management → Permissions → tick "View" on Unified Semantic Layer for the role, then USL → namespace/model → Access Control → assign user/role → tick SELECT (Bevan Lewis + Rahul Mahadik, Oct 23).

## Example code

Verbatim from Zetaris staff (Bevan Lewis, Help Zetaris thread "local install rejects API tokens", Oct 21):

```sh
curl -i http://localhost:8888/api/v1.0/datasource/datasources \ -H "Authorization: Bearer YOUR_API_KEY" \ -H "X-Org-ID: 1"
```

Minimal Python (stdlib), condensed from repo `scripts/zetaris_sql.py` `RestChannel` (Apache-licensed Zetaris repo):

```python
import json, os, uuid
from urllib.request import Request, urlopen

BASE = os.environ.get("ZETARIS_REST_URL", "http://localhost:8888/api/v1.0").rstrip("/")

def run_sql(sql: str, limit: int = 100):
    req = Request(
        BASE + "/sql-editor/sqls/run",
        data=json.dumps({"queryId": str(uuid.uuid4()), "sql": sql,
                         "source": "SqlEditor", "limit": limit}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['ZETARIS_API_KEY']}",  # or login idToken
            "X-Org-ID": os.environ.get("ZETARIS_ORG_ID", "1"),
            "X-Request-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=600) as resp:
        out = json.loads(resp.read().decode() or "null")
    return out.get("data") if isinstance(out, dict) else None  # list of string arrays

print(run_sql("SELECT 1"))  # [["1"]]
```

Login-token workaround (from repo `scripts/zetaris_api.py`): `POST {BASE}/api/auth/login` with `{"username": ..., "password": ...}` → take `idToken` → use as Bearer with `X-Org-ID` and `X-Request-ID` on `POST {BASE}/api/proxy/sql-editor/sqls/run-query` body `{"sql": ..., "limit": 1000}`.

JDBC from Python (repo `zetaris_sql.py` `JdbcChannel`): `jaydebeapi.connect("com.zetaris.lightning.jdbc.LightningDriver", "jdbc:zetaris:lightning@localhost:10000", [user, password], "/path/to/driver.jar")`.

## Recall CSVs to upload (`dffs://recall/`)

Upload these four files from **`SAMPLE_DATA_DIR`** (default **`data/synthetic/tables/`**; unit-test fixture is `data/sample/`):

| Local file | Zetaris path |
|---|---|
| `contacts.csv` | `dffs://recall/contacts.csv` |
| `handles.csv` | `dffs://recall/handles.csv` |
| `interactions.csv` | `dffs://recall/interactions.csv` |
| `events.csv` | `dffs://recall/events.csv` |

Then run `scripts/zetaris_load_sample.sql` (one statement at a time), `scripts/zetaris_setup_usl.py`, and `scripts/zetaris_smoke.py`. Smoke/setup take expected row counts from `settings.sample_data_dir`.

## Live setup on our GCP VM

How the current team instance is wired (no credentials in this doc — use the VM’s `.env` / secrets):

1. **CSVs in NDP file-store.** Upload the four tables from `SAMPLE_DATA_DIR` (default `data/synthetic/tables/`) into the platform file browser under **`file-store/recall/`** so they resolve as `dffs://recall/{contacts,handles,interactions,events}.csv`.

2. **FILESTORE tables with explicit schema.** Create the Lightning FILESTORE tables with `inferSchema "false"` (and `header "true"`). For **`interactions`**, also set `multiLine "true"` so quoted newlines in message bodies do not split rows. Prefer the statements in `scripts/zetaris_load_sample.sql` (one REST statement at a time) over UI inference.

3. **Events from Postgres (`calendar_pg`).** The `events` USL activation reads `calendar_pg.calendar_events`, not the CSV FILESTORE:
   - In the **`pg`** container, create a `calendar_events` table whose columns match the CSV (`event_id`, `title`, `start_utc`, `end_utc`, `location`, `attendees`, `source_ref`) and load the CSV rows into it.
   - Create a **read-only** Postgres role that can `SELECT` that table only (store the password in secrets / env — never commit it).
   - In Zetaris SQL:
     ```sql
     CREATE DATASOURCE calendar_pg DESCRIBE BY "PostgreSQL" OPTIONS (
       jdbcdriver "org.postgresql.Driver",
       jdbcurl "jdbc:postgresql://pg:5432/<database>",
       username "<readonly_user>", password "<from secrets>", fetchsize "1000")
     REGISTER DATASOURCE TABLES FROM calendar_pg
     ```
   - Confirm `SHOW TABLES IN calendar_pg` lists `calendar_events`.

4. **USL + smoke.** From the repo on the VM (with `DATA_BACKEND=zetaris` and REST/login env set):
   ```sh
   uv run python scripts/zetaris_setup_usl.py --reset
   uv run python scripts/zetaris_smoke.py
   ```
   Expect table + USL counts to match the CSVs, and named-query parity against DuckDB. The REST SQL editor caps each response at **1000** rows; `ZetarisBackend` pages with `LIMIT`/`OFFSET` so `all_interactions` still returns the full query limit.

## Sources

- HackOS Resources → Zetaris folder (24 items): "Zetaris: Getting Started Prompt" (read in full); repo/website, install, connect, SQL reference/companion, troubleshooting links all point to github.com/zetaris/getting-started / zetaris.github.io/getting-started.
- Repo files: `START-HERE.md`, `.env.example`, `docs/connections/{README,client-setup,claude-code-connection}.md`, `docs/install/{choose-install,updated_zetaris_installation_guide,managed-aws-cluster-guide}.md`, `docs/guides/{zetaris-lightning-sql-commands,zetaris-lightning-sql-companion,troubleshooting}.md`, `scripts/{HOWTO.md,zetaris_sql.py,zetaris_api.py,query_zetaris.py,preflight.py}`, `examples/pudl-chart/render.py`.
- HackOS → Messages → **Help Zetaris** (full history Oct 13–23, 4 pinned messages, plus threads: Sean Rugge API-token 400; Hélène AUERBACH JDBC/REST; Adrien Oke S3/permissions; Manoj Rajan file path; Rahul Mahadik USL access; Nikolay Miroshnichenko judging; HASEBUL HASSAN CHOWDHURY questions). Staff: Michael Hay, Bevan Lewis, Himanshu Varia, Joleen Liu, Saketh Reddy.
- HackOS → Announcement channel: only workshop scheduling, nothing technical. No separate Zetaris announcements channel exists.
- "Data Day with Zetaris" recording: video only, no notes/slides attached — **not reviewed**.