# Synthetic eval report

**Overall: 19/23 (82.6%)**

- Person accuracy: 100.0% (18/18)
- Clarify accuracy: 100.0% (1/1)

## Method

The 23 questions were visible during development (not a held-out set). The code contains no dataset-specific literals (enforced by `tests/test_no_hardcoding.py`). Failures below are left honest — we do not chase 100%.

## By category

| category | pass | total | pct |
|---|---:|---:|---:|
| brief | 8 | 9 | 89% |
| calendar | 2 | 2 | 100% |
| conflict | 2 | 2 | 100% |
| disambiguation | 2 | 2 | 100% |
| identity | 1 | 2 | 50% |
| memory | 1 | 1 | 100% |
| open_loops | 1 | 3 | 33% |
| sparse | 1 | 1 | 100% |
| unknown | 1 | 1 | 100% |

## Per question

| id | category | result | notes |
|---|---|---|---|
| q01 | disambiguation | pass | ok |
| q02 | brief | pass | ok |
| q03 | brief | pass | ok |
| q04 | open_loops | fail | missing omar_k.lease |
| q05 | disambiguation | pass | ok |
| q06 | brief | pass | ok |
| q07 | identity | fail | missing sasha.visit; missing sasha.engaged; missing sasha.promo |
| q08 | conflict | pass | ok |
| q09 | brief | pass | ok |
| q10 | conflict | pass | ok |
| q11 | identity | pass | ok |
| q12 | brief | pass | ok |
| q13 | brief | pass | ok |
| q14 | open_loops | fail | missing mei.model_ask; missing hannah.transcript; missing ruth.draft; missing elodie.deck_fr |
| q15 | open_loops | pass | ok |
| q16 | sparse | pass | ok |
| q17 | unknown | pass | ok |
| q18 | calendar | pass | ok |
| q19 | calendar | pass | ok |
| q20 | brief | pass | ok |
| q21 | brief | fail | missing mei.preseed_close; missing mei.japan |
| q22 | brief | pass | ok |
| q23 | memory | pass | ok |
