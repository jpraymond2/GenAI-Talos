# Synthetic ingest report

Tables: handles=121, interactions=2652, events=176, contacts=42

Facts with resolved evidence: 41/41

## Where the connectors disagreed with the answer key

Each item is corrected in the tables above; the count is how many rows were affected.

- **186×** gmail: skipped by live query (promotions/social)
- **102×** whatsapp: group message credited to the group, not the person who wrote it (fixed from ZGROUPMEMBER)
  - e.g. `yes approved`
  - e.g. `hiring tip: post on the McGill job board, got 3 good applicants`
  - e.g. `great idea thanks`
- **79×** gmail: snippet is HTML-escaped (connector stores &#39; etc.)
  - e.g. `Hey Jordan, We&#39;re hosting a small event on climate data next week. Would you like to join? Mei`
  - e.g. `Thanks for the invite Mei, I&#39;ll try to make it.`
  - e.g. `Les nouvelles de l&#39;automne: projets pilotes, événements et plus.`
- **61×** messenger: mojibake fixed (export encodes UTF-8 as latin-1)
  - e.g. `happy friday ð»`
  - e.g. `happy friday ð»`
  - e.g. `ok official: wedding is June 12 2027 in Toronto. and I'd love for you to be a groomsman ð¤µ`
- **31×** imessage: tapback reaction ingested as a message (associated_message_type≠0)
  - e.g. `Loved “oui je suis là après 17h”`
  - e.g. `Loved “parfait”`
  - e.g. `Loved “yes approved”`
- **9×** gmail: fact-carrying email longer than the 200-char snippet (metadata-only fetch loses the end)
  - e.g. `Jordan, Tom — Connecting you two. Tom runs Lumen Grid in Berlin (grid flexibilit`
  - e.g. `Hey Jordan, Quick ask: could you intro me to Rachel Goldberg at Northwind? We&#3`
  - e.g. `Hi Jordan, Great meeting you at the Maple Ridge founders breakfast. We&#39;d lov`
- **9×** messenger: photo-only message skipped
- **1×** whatsapp: 66 photo/deleted messages correctly skipped (no text)
- **1×** calendar: cancelled event would be ingested as a real meeting (connector ignores status)
  - e.g. `Lunch w/ Priya`
