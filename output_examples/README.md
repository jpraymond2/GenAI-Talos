# Output examples

| id | input | shows | rounds | provider | ms |
|---|---|---|---|---|---|
| `01_three_omars` | I'm meeting Omar | Disambiguation across three Omars; calendar may rank Omar Haddad first. | 1 | offline_rules | 62 |
| `02_brief_omar_haddad` | I'm meeting Omar Haddad on Wednesday | Sourced brief with the unanswered Rachel Goldberg intro ask. | 1 | offline_rules | 7 |
| `03_nickname_identity` | I'm seeing Sasha soon, catch me up | Identity resolution: Sasha → Alexandra Petrova across nickname, Messenger, email. | 1 | offline_rules | 8 |
| `04_conflicting_sources` | Where does Lucas live? | Contact card Montreal vs messages Paris; World Model conflict with sources. | 1 | offline_rules | 8 |
| `05_memory_carryover` | Note that Omar Haddad wants an intro to Rachel by end of month | H-MEM carries the note into a later Rachel brief (answer-key q23). | 1 | offline_rules | 17 |
| `06_revision_loop` | Sync with Mei tomorrow, what's open between us? | Writer↔Critic loop on the promised financial model (q21). | 1 | offline_rules | 21 |
