# Zetaris query API notes

Template for wiring the live Zetaris backend. Fill in each TODO before
`DATA_BACKEND=zetaris` can succeed without falling back to DuckDB.

## Endpoint

TODO: confirm the absolute URL path used for SQL reads.

- Base URL comes from `ZETARIS_URL` (no trailing slash required).
- Default request path used by `recall.data.zetaris_backend`: `POST {ZETARIS_URL}/query`.
- Replace `/query` in code once the real path is known.

## Auth

TODO: confirm which credential scheme Zetaris expects.

Supported env vars (never log these):

| Variable | Role |
|----------|------|
| `ZETARIS_API_KEY` | Bearer token when set |
| `ZETARIS_USER` / `ZETARIS_PASSWORD` | Optional HTTP Basic when both are set |

Current client behaviour:

1. If `ZETARIS_API_KEY` is non-empty → `Authorization: Bearer <key>`.
2. Else if user and password are both set → HTTP Basic.
3. Else → request is sent without an Authorization header (may 401).

## Request format

TODO: confirm the JSON body schema.

Named queries emit portable ANSI SQL with `:name` placeholders (see
`recall/data/queries.py`). The Zetaris backend expands list params to
`IN (?, ?, ...)` and converts markers to positional `?` before POST.

Current provisional body:

```json
{
  "sql": "SELECT ... WHERE handle_value IN (?, ?) AND ts_utc >= ?",
  "params": ["a@example.com", "+15551212", "2026-06-01T00:00:00Z"]
}
```

- `sql`: statement text after `?` expansion (same logical SQL as DuckDB).
- `params`: positional array aligned with `?` markers (empty array when unbound).

## Response format

TODO: confirm the success payload shape.

Current client accepts either:

```json
{ "rows": [ { "col": "value" }, ... ] }
```

or a bare JSON array of row objects:

```json
[ { "col": "value" }, ... ]
```

Any other shape, non-2xx status, timeout, or transport error raises
`DataUnavailable` so `FallbackQueryLayer` can switch to DuckDB.

## Operational bounds

- HTTP timeout: `HTTP_TIMEOUT_SECONDS` (capped at 15 s).
- Retries: 2 (three attempts total) with no credential logging on failure.
