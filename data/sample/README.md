# Sample data (synthetic)

Fully **fictional** corpus for persona **Alex Martin**, a Montreal business student.
No real personal data. Generated for local SAMPLE_MODE demos and tests.

## Source

Synthetic. Produced by `scripts/make_sample_data.py` with a fixed random seed and
fixed anchor date (`2026-10-15`). Message bodies may be LLM-authored once, then
cached in `_text_cache.json`; with `SAMPLE_MODE=true` (or no key) the script uses
deterministic templates instead.

## Row counts

| File | Rows |
|------|------|
| `contacts.csv` | 10 |
| `handles.csv` | 34 |
| `interactions.csv` | ~384 (+ food/family extras) |
| `events.csv` | 26 |

Total on-disk size of the CSVs is kept under 1 MB.

## How to regenerate

```bash
uv run python scripts/make_sample_data.py
```

Reruns are byte-identical when the text cache is unchanged. Delete
`_text_cache.json` only if you intentionally want new LLM wording (requires a
configured provider and `SAMPLE_MODE=false`).
