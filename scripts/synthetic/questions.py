"""Eval questions for the synthetic world. "Today" is Mon Oct 26, 2026, 18:00 Montreal time.

expect keys:
  person            pid the brief must be about
  clarify           list of pids Recall should ask between (instead of guessing)
  must_cover        fact ids the answer must include
  must_not          fact ids (from other people) that must NOT appear
  notes             what a good answer looks like, for a human or LLM judge
"""

QUESTIONS = [
    # --- identity: same first name
    {"id": "q01", "category": "disambiguation", "ask": "I'm meeting Omar",
     "expect": {"clarify": ["omar_h", "omar_b", "omar_k"],
                "notes": "Three Omars. Ideal: ask which one, or use the calendar (coffee with Omar Haddad on Wed Oct 28) to propose Omar Haddad first."}},
    {"id": "q02", "category": "brief", "ask": "I'm meeting Omar Haddad on Wednesday",
     "expect": {"person": "omar_h", "must_cover": ["omar_h.coffee", "omar_h.series_a", "omar_h.intro_ask"],
                "must_not": ["omar_b.casecomp", "omar_k.lease"],
                "notes": "Must surface the unanswered intro request to Rachel Goldberg — that's the awkward thing to fix before coffee."}},
    {"id": "q03", "category": "brief", "ask": "Brief me on Omar from my MBA",
     "expect": {"person": "omar_b", "must_cover": ["omar_b.casecomp"], "must_not": ["omar_h.series_a", "omar_k.lease"]}},
    {"id": "q04", "category": "open_loops", "ask": "What do I owe my landlord?",
     "expect": {"person": "omar_k", "must_cover": ["omar_k.lease", "omar_k.plumber"],
                "notes": "Lease answer due Nov 15; rent $1,450 → $1,525 from July 1, 2027."}},
    # --- identity: same full name
    {"id": "q05", "category": "disambiguation", "ask": "I'm meeting Daniel Kim on Thursday",
     "expect": {"person": "daniel_vc", "must_cover": ["daniel_vc.pitch", "daniel_vc.dataroom"], "must_not": ["daniel_cl.wedding"],
                "notes": "Two different people named Daniel Kim. Thursday's calendar event resolves it to the investor; never merge the two."}},
    {"id": "q06", "category": "brief", "ask": "What's new with Dan from UofT?",
     "expect": {"person": "daniel_cl", "must_cover": ["daniel_cl.wedding"], "must_not": ["daniel_vc.pitch"]}},
    # --- identity: nickname
    {"id": "q07", "category": "identity", "ask": "I'm seeing Sasha soon, catch me up",
     "expect": {"person": "sasha", "must_cover": ["sasha.visit", "sasha.engaged", "sasha.promo"],
                "notes": "Sasha = Alexandra Petrova (contact nickname 'Sasha', Messenger 'Sasha Petrova', email alexandra.petrova@…). Dinner Sat Nov 14 is NOT booked yet."}},
    # --- identity: email change / stale card
    {"id": "q08", "category": "conflict", "ask": "Where does Priya work now?",
     "expect": {"person": "priya", "must_cover": ["priya.job"],
                "notes": "Stellarfin (since June 2026). Contact card still says Brightloop — newest source wins, mention the change."}},
    {"id": "q09", "category": "brief", "ask": "Prep me for the Stellarfin pilot call",
     "expect": {"person": "priya", "must_cover": ["priya.pilot", "priya.job"], "notes": "Wed Nov 4, 13:00. Mei attends; Mei leaves for Japan Nov 5."}},
    # --- conflict: city move
    {"id": "q10", "category": "conflict", "ask": "Where does Lucas live?",
     "expect": {"person": "lucas", "must_cover": ["lucas.paris", "lucas.xmas"],
                "notes": "Paris (since Aug 2026), address book says Montreal (stale). Back in Montreal Dec 18 – Jan 4. Source messages are in French."}},
    # --- phone number change
    {"id": "q11", "category": "identity", "ask": "What's Yusuf's number?",
     "expect": {"person": "yusuf", "must_cover": ["yusuf.newnumber"],
                "notes": "+1 514-555-0171 since Jul 12, 2026. The old 438 number in contacts is dead. The new number isn't in the address book, so it only appears as a bare number in iMessage."}},
    # --- assistant scheduling
    {"id": "q12", "category": "brief", "ask": "Who am I meeting on November 6 and what should I know?",
     "expect": {"person": "rachel", "must_cover": ["rachel.meeting", "rachel.met"],
                "notes": "Rachel Goldberg (Northwind). Scheduled by Tyler Brooks, her EA — the meeting is with Rachel, not Tyler. Bonus: Omar Haddad asked for an intro to her."}},
    # --- French-language source
    {"id": "q13", "category": "brief", "ask": "Prep me for the Énergie Boréale call",
     "expect": {"person": "elodie", "must_cover": ["elodie.boreale", "elodie.deck_fr"],
                "notes": "Nov 3, 14:00 (EST — after the Nov 1 time change). Élodie asked for the deck in French and reminded on Oct 23; still owed."}},
    # --- open loops across people
    {"id": "q14", "category": "open_loops", "ask": "What do I owe people this week?",
     "expect": {"must_cover": ["mei.model_ask", "daniel_vc.dataroom", "hannah.transcript", "ruth.draft", "elodie.deck_fr"],
                "notes": "Also acceptable: lease answer (Nov 15) and Maman's Christmas dates. Unanswered asks: Omar's intro, Kevin's hiring question."}},
    {"id": "q15", "category": "open_loops", "ask": "Did I ever reply to Kevin?",
     "expect": {"person": "kevin", "must_cover": ["kevin.hiring_q"], "notes": "No — his Oct 10 Messenger question about data-engineering roles is unanswered."}},
    # --- sparse person (anti-hallucination)
    {"id": "q16", "category": "sparse", "ask": "I'm meeting Tom Becker",
     "expect": {"person": "tom", "must_cover": ["tom.intro"],
                "notes": "Only an intro email (Dec 2025) and one coffee (Feb 2026). A good brief says little and invents nothing."}},
    # --- unknown person
    {"id": "q17", "category": "unknown", "ask": "I'm meeting Sarah Whitfield",
     "expect": {"notes": "Nobody by that name exists. Recall must say it has nothing on her, not guess."}},
    # --- calendar
    {"id": "q18", "category": "calendar", "ask": "What's on my calendar Friday?",
     "expect": {"must_cover": ["omar_k.plumber", "hannah.final"], "notes": "Fri Oct 30: plumber 9–12, Aldridge final round 13:00."}},
    {"id": "q19", "category": "calendar", "ask": "Am I having lunch with Priya on the 21st?",
     "expect": {"person": "priya", "must_cover": ["priya.cancelled_lunch"], "notes": "No, it was cancelled (she messaged Oct 20; calendar event status=cancelled)."}},
    # --- family (French)
    {"id": "q20", "category": "brief", "ask": "Anything I need to answer my mom about?",
     "expect": {"person": "mom", "must_cover": ["mom.christmas"], "notes": "Maman (Nadia Lefebvre) asked to confirm Christmas dates in Sherbrooke."}},
    # --- cofounder, high volume
    {"id": "q21", "category": "brief", "ask": "Sync with Mei tomorrow, what's open between us?",
     "expect": {"person": "mei", "must_cover": ["mei.model_ask", "mei.preseed_close", "mei.japan"],
                "notes": "Jordan promised the revised financial model by Tuesday (= tomorrow). High-volume chat: must pick the few things that matter."}},
    # --- birthday
    {"id": "q22", "category": "brief", "ask": "Whose birthday is coming up?",
     "expect": {"must_cover": ["isabelle.bday"], "notes": "Isabelle, Nov 3 — dinner at Le Petit Bistro 19:30 (Jordan booked for 6)."}},
    # --- memory / note (H-MEM)
    {"id": "q23", "category": "memory", "ask": "Note that Omar Haddad wants an intro to Rachel by end of month. Then: I'm meeting Rachel Goldberg",
     "expect": {"person": "rachel", "must_cover": ["rachel.meeting"], "notes": "Second brief should surface the new note (intro for Omar)."}},
]

# Cheap automatic grading: an answer "covers" a fact when every group has at least
# one alternative present (case-insensitive substring). Use an LLM judge for nuance.
FACT_KEYWORDS: dict[str, list[list[str]]] = {
    "mei.preseed_close": [["dec 15", "december 15", "15 dec"], ["pre-seed", "preseed", "round", "close"]],
    "mei.japan": [["japan"], ["nov 5", "november 5", "5 nov", "nov 15", "november 15"]],
    "mei.model_ask": [["financial model", "model"], ["tuesday", "oct 27", "october 27", "tomorrow"]],
    "mei.pilot_hydrolab": [["hydrolab"], ["pipeline", "demo"]],
    "omar_h.series_a": [["series a"], ["8m", "8 m", "$8", "8 million"]],
    "omar_h.intro_ask": [["rachel"], ["intro"]],
    "omar_h.coffee": [["névé", "neve", "coffee"], ["wednesday", "oct 28", "october 28", "10"]],
    "omar_h.hiring": [["hiring", "backend"]],
    "omar_b.casecomp": [["case comp", "case competition"], ["nov 7", "november 7", "saturday"]],
    "omar_b.notes": [["notes"], ["midterm", "strategy"]],
    "omar_k.lease": [["lease", "renew", "bail"], ["1,525", "1525", "nov 15", "november 15"]],
    "omar_k.plumber": [["plumber", "plombier"], ["oct 30", "october 30", "friday"]],
    "sasha.engaged": [["engaged", "proposed", "fiancé", "fiance"]],
    "sasha.visit": [["nov 13", "november 13", "nov 14", "november 14", "13–16", "13-16"]],
    "sasha.promo": [["senior"], ["promot", "ux"]],
    "priya.job": [["stellarfin"], ["brightloop", "left", "joined", "moved", "new job"]],
    "priya.pilot": [["pilot"], ["nov 4", "november 4", "wednesday"]],
    "priya.cancelled_lunch": [["cancel"]],
    "lucas.paris": [["paris"]],
    "lucas.xmas": [["dec 18", "december 18", "18 déc", "18 dec", "christmas", "noël", "holidays"]],
    "elodie.boreale": [["boréale", "boreale"], ["nov 3", "november 3", "3 novembre", "tuesday", "14:00", "14 h", "2 pm", "2pm"]],
    "elodie.deck_fr": [["french", "français", "francais"], ["deck", "présentation", "presentation"]],
    "daniel_vc.pitch": [["pitch", "maple ridge"], ["thursday", "oct 29", "october 29", "3 pm", "3pm", "15:00"]],
    "daniel_vc.dataroom": [["data room", "data-room", "dataroom"]],
    "daniel_vc.payloop": [["payloop"]],
    "daniel_cl.wedding": [["wedding", "married", "groomsman"]],
    "rachel.met": [["startupfest"]],
    "rachel.meeting": [["nov 6", "november 6", "friday"], ["11"]],
    "ruth.extension": [["nov 2", "november 2", "extension"]],
    "ruth.draft": [["outline", "draft"], ["oct 30", "october 30"]],
    "hannah.final": [["final"], ["oct 30", "october 30", "friday", "1 pm", "1pm", "13:00"]],
    "hannah.transcript": [["transcript"]],
    "kevin.vancouver": [["vancouver"]],
    "kevin.hiring_q": [["hiring", "data engineer"]],
    "isabelle.bday": [["birthday", "anniversaire", "fête"], ["nov 3", "november 3", "petit bistro"]],
    "isabelle.nights": [["night", "nuit"]],
    "yusuf.newnumber": [["555-0171", "5550171", "555 0171"]],
    "yusuf.marathon": [["half", "marathon", "long run"]],
    "mom.christmas": [["christmas", "noël", "noel"], ["dates", "confirm", "sherbrooke"]],
    "chloe.exam": [["anatomy", "anatomie", "exam", "examen"]],
    "tom.intro": [["lumen grid", "berlin", "intro"]],
}
