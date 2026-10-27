# Output examples

Each folder has `brief.md`, `run.json`, `trace.jsonl` (this run only), and [`how_it_worked.md`](./01_three_omars/how_it_worked.md) (timeline + critic).

| id | input | shows | rounds | provider | ms | how |
|---|---|---|---|---|---|---|
| `01_three_omars` | I'm meeting Omar | Disambiguation across three Omars; calendar may rank Omar Haddad first. | 1 | offline_rules | 83 | [how](./01_three_omars/how_it_worked.md) |
| `02_brief_omar_haddad` | I'm meeting Omar Haddad on Wednesday | Sourced brief with the unanswered Rachel Goldberg intro ask. | 1 | offline_rules | 23 | [how](./02_brief_omar_haddad/how_it_worked.md) |
| `03_nickname_identity` | I'm seeing Sasha soon, catch me up | Identity resolution: Sasha → Alexandra Petrova across nickname, Messenger, email. | 1 | offline_rules | 37 | [how](./03_nickname_identity/how_it_worked.md) |
| `04_conflicting_sources` | Where does Lucas live? | Contact card Montreal vs messages Paris; World Model conflict with sources. | 1 | offline_rules | 28 | [how](./04_conflicting_sources/how_it_worked.md) |
| `05_memory_carryover` | Note that Omar Haddad wants an intro to Rachel by end of month | H-MEM carries the note into a later Rachel brief (answer-key q23). | 1 | offline_rules | 33 | [how](./05_memory_carryover/how_it_worked.md) |
| `06_revision_loop` | Sync with Mei tomorrow | First draft is meeting-prep logistics; critic catches the higher-priority open financial-model promise and round 2 passes. | 2 | offline_rules | 34 | [how](./06_revision_loop/how_it_worked.md) |
