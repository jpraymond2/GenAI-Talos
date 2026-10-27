# How it worked — `05_memory_carryover`

Run `example-05_memory_carryover-c8ec8021` · status `done` · critic rounds `1`

## Timeline

1. **hmem** `create` — hmem/create
2. **hmem** `mine` — hmem/mine
3. **hmem** `create` — hmem/create
4. **hmem** `conflict_detected` — hmem/conflict_detected
5. **hmem** `conflict_detected` — hmem/conflict_detected
6. **hmem** `supersede` — hmem/supersede
7. **hmem** `conflict_resolved` — hmem/conflict_resolved
8. **hmem** `conflict_resolved` — hmem/conflict_resolved
9. **hmem** `mine` — hmem/mine
10. **hmem** `create` — hmem/create
11. **hmem** `mine` — hmem/mine
12. **hmem** `create` — hmem/create
13. **hmem** `mine` — hmem/mine
14. **hmem** `create` — hmem/create
15. **hmem** `mine` — hmem/mine
16. **hmem** `create` — hmem/create
17. **hmem** `mine` — hmem/mine
18. **hmem** `notes_ingest` — mined 6 memories from notes.jsonl
19. **router** `complete` — kind=note person='Omar B (MBA)' field=None confidence=0.90
20. **world** `resolve` — resolve 'Omar B (MBA)' candidates=1 top=Omar B (MBA) score=1.00 (exact alias match)
21. **orchestrator** `resolve` — name='Omar B (MBA)' candidates=1
22. **world** `resolve` — resolve 'Omar Haddad' candidates=1 top=Omar Haddad score=1.00 (exact alias match)
23. **llm** `embed` · 0 ms — embed count≈16 provider=sample ms=0 success=True
24. **hmem** `create` — hmem/create
25. **hmem** `mine` — hmem/mine
26. **orchestrator** `note` — Noted: Note that Omar Haddad wants an intro to Rachel by end of month
27. **orchestrator** `final` · 21 ms — status=done points=0 caveats=0 latency_ms=21 llm_calls=0
28. **router** `complete` — kind=brief person='Rachel Goldberg' field=None confidence=0.90
29. **world** `resolve` — resolve 'Rachel Goldberg' candidates=1 top=Rachel Goldberg score=1.00 (exact alias match)
30. **orchestrator** `resolve` — name='Rachel Goldberg' candidates=1
31. **query_layer** `fallback` — fallback zetaris→duckdb reason=circuit_open tag=world.relationship.interactions
32. **query_layer** `query` · 0 ms — query world.relationship.interactions rows=1 backend=duckdb ms=0.812
33. **orchestrator** `relationship` — label=professional confidence=0.68
34. **llm** `embed` · 0 ms — embed count≈7 provider=sample ms=0 success=True
35. **query_layer** `fallback` — fallback zetaris→duckdb reason=circuit_open tag=world.context.thread_peers
36. **hmem** `retrieve` — hmem retrieve hits=1 candidates=6 top=[('mem_a13750f2de8c', 0.4624)]
37. **query_layer** `query` · 1 ms — query world.context.thread_peers rows=1 backend=duckdb ms=1.392
38. **web_scout** `complete` — web_scout sample hits=1 org=Northwind Capital
39. **query_layer** `fallback` — fallback zetaris→duckdb reason=circuit_open tag=world.context.events
40. **query_layer** `query` · 0 ms — query world.context.events rows=167 backend=duckdb ms=0.765
41. **world** `person_context` — person_context open_loops=0 conflicts=0 upcoming=1 past_events=0 recent=1
42. **orchestrator** `retrieve` — world_open_loops=0 memories=1 backend=duckdb
43. **orchestrator** `memories` — count=1 top_score=0.462
44. **orchestrator** `evidence_pack` — size=4 ids=['e1', 'e2', 'm1', 'w1']
45. **writer** `complete` — person='Rachel Goldberg' points=4 headline='Rachel Goldberg, Partner at Northwind Capital; last contact 14 weeks ago on gmai'
46. **critic** `complete` — overall=pass keep=4/4 missing=0 refetch=4
47. **orchestrator** `round` — round 1: initial draft + critic
48. **hmem** `feedback` — hmem/feedback
49. **hmem** `feedback` — hmem/feedback
50. **orchestrator** `feedback` — feedback=1
51. **actions** `plan` — steps=0
52. **orchestrator** `final` · 9 ms — status=done points=4 caveats=0 latency_ms=9 llm_calls=0

## Candidate choice

1. **Rachel Goldberg** score=1.00 — exact alias match ← chosen

## Sources & backend

- fallback: zetaris → duckdb (circuit_open)
- `world.relationship.interactions` → duckdb (1 rows)
- fallback: zetaris → duckdb (circuit_open)
- `world.context.thread_peers` → duckdb (1 rows)
- fallback: zetaris → duckdb (circuit_open)
- `world.context.events` → duckdb (167 rows)

## Memories used

- `mem_a13750f2de8c` score=0.4624: Note that Omar Haddad wants an intro to Rachel Goldberg by end of month.

## Critic verdicts

### Round 1 — overall `pass`

- point 0 **keep**: keep: cites H-MEM note (m1)
- point 1 **keep**: keep: cites upcoming/past calendar Fri Nov 6, 11:00
- point 2 **keep**: keep: cites web Fri Jul 31, 20:00
- point 3 **keep**: keep: cites gmail Fri Jul 17, 18:22
- refetch: calendar, gmail, memory, web
