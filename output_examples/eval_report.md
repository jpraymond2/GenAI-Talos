# Synthetic eval report

**Overall: 9/23 (39.1%)**

- Person accuracy: 72.2% (13/18)
- Clarify accuracy: 100.0% (1/1)

## By category

| category | pass | total | pct |
|---|---:|---:|---:|
| brief | 1 | 9 | 11% |
| calendar | 0 | 2 | 0% |
| conflict | 2 | 2 | 100% |
| disambiguation | 2 | 2 | 100% |
| identity | 1 | 2 | 50% |
| memory | 0 | 1 | 0% |
| open_loops | 1 | 3 | 33% |
| sparse | 1 | 1 | 100% |
| unknown | 1 | 1 | 100% |

## Per question

| id | category | result | notes |
|---|---|---|---|
| q01 | disambiguation | pass | ok |
| q02 | brief | pass | ok |
| q03 | brief | fail | person want=omar_b got=Amira Khalil; missing omar_b.casecomp |
| q04 | open_loops | fail | missing omar_k.lease |
| q05 | disambiguation | pass | ok |
| q06 | brief | fail | missing daniel_cl.wedding |
| q07 | identity | fail | missing sasha.visit; missing sasha.engaged |
| q08 | conflict | pass | ok |
| q09 | brief | fail | person want=priya got=Ben Okafor; missing priya.pilot; missing priya.job |
| q10 | conflict | pass | ok |
| q11 | identity | pass | ok |
| q12 | brief | fail | person want=rachel got=None; missing rachel.meeting; missing rachel.met |
| q13 | brief | fail | person want=elodie got=Ben Okafor; missing elodie.boreale; missing elodie.deck_fr |
| q14 | open_loops | fail | missing mei.model_ask; missing daniel_vc.dataroom; missing hannah.transcript; missing ruth.draft; missing elodie.deck_fr |
| q15 | open_loops | pass | ok |
| q16 | sparse | pass | ok |
| q17 | unknown | pass | ok |
| q18 | calendar | fail | missing omar_k.plumber; missing hannah.final |
| q19 | calendar | fail | missing priya.cancelled_lunch |
| q20 | brief | fail | person want=mom got=None; missing mom.christmas |
| q21 | brief | fail | missing mei.preseed_close; missing mei.japan |
| q22 | brief | fail | missing isabelle.bday |
| q23 | memory | fail | missing rachel.meeting |
