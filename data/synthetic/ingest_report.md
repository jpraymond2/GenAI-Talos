# Synthetic ingest report

Tables: handles=152, interactions=2622, events=176, contacts=42

Facts with resolved evidence: 41/41

## Where the connectors disagreed with the answer key

Each item is corrected in the tables above; the count is how many rows were affected.

- **186×** gmail: skipped by live query (promotions/social)
- **119×** imessage: tapback reaction ingested as a message (associated_message_type≠0)
  - e.g. `Loved “thanks for the heads up”`
  - e.g. `Loved “avec plaisir ❤️”`
  - e.g. `Loved “oui je suis là après 17h”`
- **99×** messenger: mojibake fixed (export encodes UTF-8 as latin-1)
  - e.g. `happy friday ð»`
  - e.g. `happy friday ð»`
  - e.g. `ok official: wedding is June 12 2027 in Toronto. and I'd love for you to be a groomsman ð¤µ`
- **79×** gmail: snippet is HTML-escaped (connector stores &#39; etc.)
  - e.g. `Hey Jordan, We&#39;re hosting a small event on climate data next week. Would you like to join? Mei`
  - e.g. `Thanks for the invite Mei, I&#39;ll try to make it.`
  - e.g. `Les nouvelles de l&#39;automne: projets pilotes, événements et plus.`
- **9×** gmail: fact-carrying email longer than the 200-char snippet (metadata-only fetch loses the end)
  - e.g. `Jordan, Tom — Connecting you two. Tom runs Lumen Grid in Berlin (grid flexibilit`
  - e.g. `Hey Jordan, Quick ask: could you intro me to Rachel Goldberg at Northwind? We&#3`
  - e.g. `Hi Jordan, Great meeting you at the Maple Ridge founders breakfast. We&#39;d lov`
- **8×** messenger: photo-only message skipped
- **1×** imessage: attributedBody mis-decoded (length byte 1–5/43/99 mistaken for a tag byte)
  - e.g. `'Bonjour! plumber is coming Friday Oct 30 between 9 and 12 fo' vs 'Bonjour! plumber is coming Friday Oct 30 between 9 and 12 fo'`
- **1×** calendar: cancelled event would be ingested as a real meeting (connector ignores status)
  - e.g. `Lunch w/ Priya`
