"""The synthetic world: one owner, ~60 people, scripted story beats, and the answer key.

Everything here is fictional. Phone numbers use the 555-01xx range reserved for
fiction; company names and domains are invented.

A *beat* is a hand-written message that carries a fact the agent should find.
Each beat may tag fact ids; the facts themselves live in ``FACTS`` and are the
ground truth used by the eval questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

OWNER_TZ = "America/Toronto"
NOW = "2026-10-26 18:00"  # local time the dataset is "as of"
START = "2025-05-01 00:00"


@dataclass
class Person:
    pid: str
    name: str  # canonical full name
    rel: str  # cofounder | founder | investor | classmate | friend | family | landlord | prof | recruiter | gym | colleague | assistant | acquaintance
    org: str = ""
    title: str = ""
    city: str = "Montreal"
    # (address, valid_from, valid_to) — dates inclusive, "" = open
    emails: list[tuple[str, str, str]] = field(default_factory=list)
    phones: list[tuple[str, str, str]] = field(default_factory=list)
    phone_label: str | None = None  # label saved in the owner's phone (address-book name); None = unsaved number
    msgr_name: str | None = None  # Facebook display name; None = not on Messenger
    imessage: bool = False
    # Contact card in the owner's address book (may be stale on purpose); None = no card
    card: dict | None = None
    # Relative weights of filler chatter per channel
    channels: dict[str, float] = field(default_factory=dict)
    filler_sessions: int = 0
    lang: str = "en"  # en | fr


OWNER = Person(
    pid="owner",
    name="Jordan Lefebvre",
    rel="self",
    org="Tidepool",
    title="Co-founder & CEO; MBA candidate, McGill Desautels",
    emails=[("jordan.lefebvre@mail.mcgill.ca", "", "")],
    phones=[("+15145550199", "", "")],
    phone_label="Jordan Lefebvre",
    msgr_name="Jordan Lefebvre",
)

# --------------------------------------------------------------------------- core cast
CORE: list[Person] = [
    Person(
        "mei", "Mei Tanaka", "cofounder", "Tidepool", "Co-founder & CTO",
        emails=[("mei@tidepool.earth", "", "")],
        phones=[("+14385550117", "", "")],
        phone_label="Mei Tanaka", imessage=True,
        card={"org": "Tidepool", "title": "CTO", "city": "Montreal"},
        channels={"imessage": 7, "gmail": 2}, filler_sessions=70,
    ),
    Person(
        "omar_h", "Omar Haddad", "founder", "Payloop", "Founder & CEO",
        emails=[("omar@payloop.co", "", "")],
        phones=[("+15145550142", "", "")],
        phone_label="Omar Haddad", msgr_name="Omar Haddad",
        card={"org": "Payloop", "title": "CEO", "city": "Montreal"},
        channels={"gmail": 2, "messenger": 1, "imessage": 3}, filler_sessions=14,
    ),
    Person(
        "omar_b", "Omar Benali", "classmate", "McGill Desautels", "MBA candidate",
        emails=[("omar.benali@mail.mcgill.ca", "", "")],
        phones=[("+14385550163", "", "")],
        phone_label="Omar B (MBA)",
        card={"fn": "Omar B (MBA)", "org": "McGill MBA", "city": "Montreal"},
        channels={"gmail": 1, "imessage": 4}, filler_sessions=16,
    ),
    Person(
        "omar_k", "Omar Kassem", "landlord", "", "Landlord",
        emails=[("omar.kassem@outlook.com", "", "")],
        phones=[("+15145550188", "", "")],
        phone_label="Omar Kassem (proprio)",
        card={"fn": "Omar Kassem (proprio)", "title": "Landlord — 4520 rue Saint-Denis", "city": "Montreal"},
        channels={"gmail": 1, "imessage": 3}, filler_sessions=6,
    ),
    Person(
        "sasha", "Alexandra Petrova", "friend", "Fieldnote", "Senior UX Designer", city="Toronto",
        emails=[("alexandra.petrova@fieldnote.design", "", "")],
        phones=[("+14165550129", "", "")],
        phone_label="Sasha", msgr_name="Sasha Petrova",
        card={"org": "Fieldnote", "title": "UX Designer", "city": "Toronto", "nickname": "Sasha", "bday": "1998-04-17"},
        channels={"messenger": 3, "imessage": 3}, filler_sessions=22,
    ),
    Person(
        "priya", "Priya Nair", "colleague", "Stellarfin", "Senior Product Manager", city="Toronto",
        emails=[("priya.nair@brightloop.com", "2025-05-01", "2026-05-29"), ("priya@stellarfin.com", "2026-06-01", "")],
        phones=[("+16475550135", "", "")],
        phone_label="Priya Nair",
        # Stale on purpose: the card still shows her old job.
        card={"org": "Brightloop", "title": "Product Manager", "city": "Toronto"},
        channels={"gmail": 2, "imessage": 2}, filler_sessions=10,
    ),
    Person(
        "lucas", "Lucas Moreau", "friend", "Atelier Nord", "Data Scientist", city="Paris",
        emails=[("lucas.moreau.qc@gmail.com", "", "")],
        phones=[("+15145550156", "", "")],
        phone_label="Lucas Moreau", msgr_name="Lucas Moreau",
        # Stale on purpose: card says Montreal; he moved to Paris in Aug 2026.
        card={"city": "Montreal"},
        channels={"messenger": 2, "imessage": 3}, filler_sessions=14, lang="fr",
    ),
    Person(
        "elodie", "Élodie Gagnon", "advisor", "Hydrolab", "Directrice innovation", city="Quebec City",
        emails=[("elodie.gagnon@hydrolab.ca", "", "")],
        phones=[("+14185550144", "", "")],
        phone_label="Élodie Gagnon",
        card={"org": "Hydrolab", "title": "Directrice innovation", "city": "Québec"},
        channels={"gmail": 3, "imessage": 2}, filler_sessions=8, lang="fr",
    ),
    Person(
        "daniel_vc", "Daniel Kim", "investor", "Maple Ridge Ventures", "Principal",
        emails=[("daniel@mapleridge.vc", "", "")],
        card={"org": "Maple Ridge Ventures", "title": "Principal", "city": "Montreal"},
        channels={"gmail": 1}, filler_sessions=0,
    ),
    Person(
        "daniel_cl", "Daniel Kim", "friend", "Shopwise", "Software Engineer", city="Toronto",
        emails=[("dkim.toronto@gmail.com", "", "")],
        phones=[("+16475550161", "", "")],
        phone_label="Dan Kim (UofT)", msgr_name="Daniel Kim",
        card={"fn": "Dan Kim (UofT)", "org": "Shopwise", "city": "Toronto"},
        channels={"messenger": 2, "imessage": 3}, filler_sessions=14,
    ),
    Person(
        "rachel", "Rachel Goldberg", "investor", "Northwind Capital", "Partner", city="Toronto",
        emails=[("rachel@northwindcap.com", "", "")],
        card={"org": "Northwind Capital", "title": "Partner", "city": "Toronto"},
        channels={"gmail": 1}, filler_sessions=0,
    ),
    Person(
        "tyler", "Tyler Brooks", "assistant", "Northwind Capital", "Executive Assistant to Rachel Goldberg", city="Toronto",
        emails=[("tyler@northwindcap.com", "", "")],
        channels={"gmail": 1}, filler_sessions=0,
    ),
    Person(
        "ruth", "Ruth Abernathy", "prof", "McGill Desautels", "Professor of Strategy",
        emails=[("ruth.abernathy@mcgill.ca", "", "")],
        channels={"gmail": 1}, filler_sessions=2,
    ),
    Person(
        "hannah", "Hannah Weiss", "recruiter", "Aldridge & Co.", "Campus Recruiter",
        emails=[("hannah.weiss@aldridgeco.com", "", "")],
        channels={"gmail": 1}, filler_sessions=0,
    ),
    Person(
        "kevin", "Kevin O'Brien", "colleague", "Northbeam Analytics", "Data Engineer", city="Vancouver",
        msgr_name="Kevin O'Brien",
        channels={"messenger": 1}, filler_sessions=8,
    ),
    Person(
        "isabelle", "Isabelle Roy", "friend", "CHUM", "Nurse",
        phones=[("+15145550172", "", "")],
        msgr_name="Isabelle Roy", imessage=True,
        card={"org": "CHUM", "city": "Montreal", "bday": "1997-11-03"},
        channels={"messenger": 3, "imessage": 3}, filler_sessions=20, lang="fr",
    ),
    Person(
        "yusuf", "Yusuf Demir", "gym", "", "",
        phones=[("+14385550150", "2025-05-01", "2026-07-11"), ("+15145550171", "2026-07-12", "")],
        phone_label="Yusuf Demir",  # saved under the OLD number only
        imessage=True,
        card={"phones_only_old": True},
        channels={"imessage": 4}, filler_sessions=18,
    ),
    Person(
        "mom", "Nadia Lefebvre", "family", "", "", city="Sherbrooke",
        emails=[("nadia.lefebvre@videotron.ca", "", "")],
        phones=[("+18195550102", "", "")],
        phone_label="Maman", imessage=True,
        card={"nickname": "Maman", "city": "Sherbrooke"},
        channels={"imessage": 4}, filler_sessions=26, lang="fr",
    ),
    Person(
        "dad", "Pierre Lefebvre", "family", "", "", city="Sherbrooke",
        emails=[("pierre.lefebvre49@gmail.com", "", "")],
        phones=[("+18195550103", "", "")],
        phone_label="Papa",
        card={"nickname": "Papa", "city": "Sherbrooke"},
        channels={"gmail": 1, "imessage": 1}, filler_sessions=6, lang="fr",
    ),
    Person(
        "chloe", "Chloé Lefebvre", "family", "Université de Sherbrooke", "Medical student", city="Sherbrooke",
        phones=[("+18195550104", "", "")],
        phone_label="Chloé", imessage=True,
        card={"fn": "Chloé", "city": "Sherbrooke"},
        channels={"imessage": 3}, filler_sessions=12, lang="fr",
    ),
    Person(
        "tom", "Tom Becker", "acquaintance", "Lumen Grid", "Co-founder", city="Berlin",
        emails=[("tom@lumengrid.de", "", "")],
        channels={"gmail": 1}, filler_sessions=0,
    ),
]

# --------------------------------------------------------------------------- filler cast
# (pid, name, rel, org, city, channels, sessions, lang)
_FILLER_SPECS = [
    ("f_amira", "Amira Khalil", "classmate", "McGill MBA", "Montreal", {"imessage": 3, "gmail": 1}, 8, "en"),
    ("f_ben", "Ben Okafor", "classmate", "McGill MBA", "Montreal", {"imessage": 2, "gmail": 1}, 6, "en"),
    ("f_clara", "Clara Fontaine", "classmate", "McGill MBA", "Montreal", {"imessage": 2, "messenger": 1}, 6, "fr"),
    ("f_diego", "Diego Ramírez", "classmate", "McGill MBA", "Montreal", {"imessage": 2}, 5, "en"),
    ("f_emma", "Emma Lindqvist", "classmate", "McGill MBA", "Montreal", {"gmail": 2, "imessage": 1}, 5, "en"),
    ("f_felix", "Félix Bouchard", "founder", "Gridwise", "Montreal", {"imessage": 2, "gmail": 1}, 5, "fr"),
    ("f_grace", "Grace Liu", "founder", "Orbitly", "Montreal", {"imessage": 2, "gmail": 1}, 5, "en"),
    ("f_hugo", "Hugo Lambert", "founder", "Sève Analytics", "Montreal", {"imessage": 2}, 4, "fr"),
    ("f_ines", "Inès Haddad", "friend", "", "Montreal", {"imessage": 3}, 8, "fr"),
    ("f_jake", "Jake Thompson", "gym", "", "Montreal", {"imessage": 2}, 6, "en"),
    ("f_kayla", "Kayla Morgan", "friend", "", "Montreal", {"messenger": 2}, 6, "en"),
    ("f_leo", "Léo Tremblay", "friend", "", "Montreal", {"imessage": 2, "messenger": 1}, 7, "fr"),
    ("f_maya", "Maya Singh", "colleague", "Northbeam Analytics", "Toronto", {"gmail": 1, "messenger": 1}, 4, "en"),
    ("f_noah", "Noah Cohen", "friend", "", "New York", {"imessage": 2}, 5, "en"),
    ("f_olivia", "Olivia Brown", "classmate", "McGill MBA", "Montreal", {"gmail": 1, "imessage": 1}, 4, "en"),
    ("f_pablo", "Pablo Ortiz", "acquaintance", "Startupfest", "Montreal", {"gmail": 1}, 3, "en"),
    ("f_quentin", "Quentin Roux", "friend", "", "Lyon", {"messenger": 1, "imessage": 1}, 4, "fr"),
    ("f_rosa", "Rosa Delgado", "acquaintance", "District 3", "Montreal", {"gmail": 2}, 4, "en"),
    ("f_sam", "Samuel Girard", "friend", "", "Montreal", {"imessage": 2}, 6, "fr"),
    ("f_tara", "Tara Walsh", "founder", "Kelpworks", "Halifax", {"gmail": 1, "imessage": 1}, 3, "en"),
    ("f_uma", "Uma Patel", "classmate", "McGill MBA", "Montreal", {"imessage": 2}, 5, "en"),
    ("f_victor", "Victor Nguyen", "friend", "", "Montreal", {"messenger": 2, "imessage": 1}, 6, "en"),
    ("f_wen", "Wen Zhao", "founder", "Loomtrace", "Montreal", {"gmail": 1, "imessage": 1}, 3, "en"),
    ("f_xavier", "Xavier Pelletier", "gym", "", "Montreal", {"imessage": 2}, 4, "fr"),
    ("f_yara", "Yara Haddad", "friend", "", "Beirut", {"imessage": 2}, 4, "en"),
    ("f_zoe", "Zoé Martin", "classmate", "McGill MBA", "Montreal", {"imessage": 1, "gmail": 1}, 4, "fr"),
    ("f_aiden", "Aiden Murphy", "acquaintance", "Montreal NewTech", "Montreal", {"gmail": 1}, 2, "en"),
    ("f_bianca", "Bianca Rossi", "friend", "", "Montreal", {"messenger": 2}, 5, "en"),
    ("f_cyrus", "Cyrus Farahani", "founder", "Voltane", "Toronto", {"gmail": 1, "imessage": 1}, 3, "en"),
    ("f_dina", "Dina Saleh", "classmate", "McGill MBA", "Montreal", {"imessage": 2}, 4, "en"),
    ("f_eric", "Eric Boisvert", "colleague", "Northbeam Analytics", "Montreal", {"gmail": 1, "messenger": 1}, 3, "fr"),
    ("f_fatima", "Fatima Zahra Idrissi", "classmate", "McGill MBA", "Montreal", {"imessage": 2}, 4, "fr"),
    ("f_gabe", "Gabriel Côté", "gym", "", "Montreal", {"imessage": 2}, 3, "fr"),
    ("f_hana", "Hana Sato", "friend", "", "Vancouver", {"messenger": 1}, 3, "en"),
    ("f_ivan", "Ivan Petrov", "acquaintance", "", "Montreal", {"imessage": 1}, 2, "en"),
    ("f_julia", "Julia Becker", "acquaintance", "Lumen Grid", "Berlin", {"gmail": 1}, 1, "en"),
    ("f_karl", "Karl Andersen", "founder", "Fjordbyte", "Montreal", {"gmail": 1}, 2, "en"),
    ("f_lea", "Léa Gauthier", "friend", "", "Montreal", {"imessage": 2, "messenger": 1}, 5, "fr"),
]

_DOMAINS = {
    "McGill MBA": "mail.mcgill.ca", "Gridwise": "gridwise.io", "Orbitly": "orbitly.app",
    "Sève Analytics": "seve-analytics.ca", "Northbeam Analytics": "northbeam.ai", "Startupfest": "startupfest.example",
    "District 3": "d3center.ca", "Kelpworks": "kelpworks.co", "Loomtrace": "loomtrace.io",
    "Montreal NewTech": "montrealnewtech.org", "Voltane": "voltane.com", "Lumen Grid": "lumengrid.de",
    "Fjordbyte": "fjordbyte.dev",
}


def _slug(name: str) -> str:
    import unicodedata

    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return "".join(ch if ch.isalnum() else "." for ch in plain).strip(".")


def build_filler() -> list[Person]:
    people: list[Person] = []
    phone_n = 200  # fiction block continues past the core cast (area codes vary below)
    area_codes = ["514", "438", "263", "647", "416", "902", "604", "212"]
    for i, (pid, name, rel, org, city, channels, sessions, lang) in enumerate(_FILLER_SPECS):
        first, last = name.split(" ", 1)
        emails = []
        if "gmail" in channels:
            dom = _DOMAINS.get(org)
            local = _slug(name)
            emails.append((f"{local}@{dom}" if dom else f"{local.replace('.', '')}{(i * 7) % 90 + 10}@gmail.com", "", ""))
        phones = []
        if "imessage" in channels:
            ac = area_codes[i % len(area_codes)]
            phones.append((f"+1{ac}555{(100 + (phone_n + i) % 100):04d}"[:12], "", ""))
        has_card = i % 3 != 2
        phone_label = None
        if "imessage" in channels and has_card:
            # saved contacts show the address-book name (sometimes just a first name)
            phone_label = first if i % 4 == 0 else name
        people.append(
            Person(
                pid, name, rel, org, "", city, emails=emails, phones=phones,
                phone_label=phone_label,
                msgr_name=name if "messenger" in channels else None,
                imessage="imessage" in channels,
                card=({"fn": phone_label or name, "org": org, "city": city} if has_card else None),
                channels=channels, filler_sessions=sessions, lang=lang,
            )
        )
    return people


# --------------------------------------------------------------------------- groups
GROUPS = {
    "msgr_founders": {"source": "messenger", "name": "Startup founders MTL 🚀",
                    "members": ["omar_h", "mei", "f_felix", "f_grace", "f_hugo", "f_wen", "f_cyrus"], "sessions": 14},
    "msgr_family": {"source": "messenger", "name": "Famille Lefebvre ❤️",
                  "members": ["mom", "dad", "chloe"], "sessions": 18},
    "msgr_studygroup": {"source": "messenger", "name": "MBA Study Group B",
                      "members": ["omar_b", "f_amira", "f_uma", "f_dina"], "sessions": 16},
    "msgr_undergrad": {"source": "messenger", "name": "Undergrad crew",
                       "members": ["daniel_cl", "lucas", "isabelle", "f_victor"], "sessions": 10},
}

# --------------------------------------------------------------------------- facts (answer key)
# fact_id -> (person, statement)
FACTS: dict[str, tuple[str, str]] = {
    # Mei
    "mei.preseed_close": ("mei", "Tidepool plans to close its pre-seed round by Dec 15, 2026."),
    "mei.japan": ("mei", "Mei is on vacation in Japan Nov 5–15, 2026 (limited availability)."),
    "mei.model_ask": ("mei", "OPEN LOOP (Jordan owes): send Mei the revised financial model; Jordan promised it by Tue Oct 27."),
    "mei.pilot_hydrolab": ("mei", "Mei is preparing the Hydrolab pilot data pipeline demo."),
    # Omar Haddad
    "omar_h.series_a": ("omar_h", "Payloop raised an $8M Series A led by Maple Ridge Ventures (announced Oct 14, 2026)."),
    "omar_h.intro_ask": ("omar_h", "OPEN LOOP (unanswered): on Sep 30 Omar asked Jordan for an intro to Rachel Goldberg (Northwind); Jordan never replied."),
    "omar_h.coffee": ("omar_h", "Coffee with Omar Haddad on Wed Oct 28, 10:00 at Café Névé (calendar)."),
    "omar_h.hiring": ("omar_h", "Payloop is hiring two backend engineers after the round."),
    # Omar Benali
    "omar_b.casecomp": ("omar_b", "Omar Benali is on Jordan's team for the Desautels case competition on Sat Nov 7; Omar owns the slides."),
    "omar_b.notes": ("omar_b", "Omar Benali shared his Strategy midterm notes (Oct 19)."),
    # Omar Kassem
    "omar_k.lease": ("omar_k", "OPEN LOOP (Jordan owes): lease renewal — rent goes from $1,450 to $1,525 from July 1, 2027; Omar Kassem needs an answer by Nov 15."),
    "omar_k.plumber": ("omar_k", "Plumber visit at the apartment Fri Oct 30 between 9:00 and 12:00."),
    # Sasha
    "sasha.engaged": ("sasha", "Sasha (Alexandra Petrova) got engaged to Ben in August 2026."),
    "sasha.visit": ("sasha", "Sasha is visiting Montreal Nov 13–16, 2026 and wants dinner on Sat Nov 14."),
    "sasha.promo": ("sasha", "Sasha was promoted to Senior UX Designer at Fieldnote (Sept 2026)."),
    # Priya
    "priya.job": ("priya", "Priya Nair left Brightloop in May 2026 and joined Stellarfin as Senior Product Manager in June 2026 (contact card is stale)."),
    "priya.pilot": ("priya", "Priya offered a Stellarfin sustainability-team pilot of Tidepool; pilot call Wed Nov 4, 13:00."),
    "priya.cancelled_lunch": ("priya", "Lunch with Priya on Oct 21 was cancelled (calendar)."),
    # Lucas
    "lucas.paris": ("lucas", "Lucas Moreau moved to Paris in Aug 2026 for a data-science job at Atelier Nord (address book still says Montreal)."),
    "lucas.xmas": ("lucas", "Lucas is back in Montreal Dec 18, 2026 – Jan 4, 2027."),
    # Élodie
    "elodie.boreale": ("elodie", "Élodie introduced Tidepool to the innovation team at Énergie Boréale; call Tue Nov 3, 14:00."),
    "elodie.deck_fr": ("elodie", "OPEN LOOP (Jordan owes): Élodie asked for the pitch deck in French before the Énergie Boréale call."),
    # Daniel Kim (investor)
    "daniel_vc.pitch": ("daniel_vc", "Tidepool pitch to Daniel Kim (Maple Ridge Ventures) on Thu Oct 29, 15:00 at their office."),
    "daniel_vc.dataroom": ("daniel_vc", "OPEN LOOP (Jordan owes): Daniel Kim asked for data-room access (Oct 23)."),
    "daniel_vc.payloop": ("daniel_vc", "Maple Ridge (Daniel's fund) led Payloop's Series A."),
    # Daniel Kim (friend)
    "daniel_cl.wedding": ("daniel_cl", "Dan Kim (UofT friend) is getting married June 12, 2027 in Toronto; Jordan agreed to be a groomsman."),
    # Rachel / Tyler
    "rachel.met": ("rachel", "Jordan met Rachel Goldberg at Startupfest in July 2026; she is interested in climate data."),
    "rachel.meeting": ("rachel", "Meeting with Rachel Goldberg Fri Nov 6, 11:00 (video), scheduled by her assistant Tyler Brooks."),
    # Ruth
    "ruth.extension": ("ruth", "Prof. Abernathy extended Jordan's term paper deadline from Oct 26 to Mon Nov 2."),
    "ruth.draft": ("ruth", "OPEN LOOP (Jordan owes): send Prof. Abernathy a draft outline by Oct 30."),
    # Hannah
    "hannah.final": ("hannah", "Final-round interview with Aldridge & Co. on Fri Oct 30, 13:00 (video)."),
    "hannah.transcript": ("hannah", "OPEN LOOP (Jordan owes): Hannah asked for an unofficial transcript before the final round."),
    # Kevin
    "kevin.vancouver": ("kevin", "Kevin O'Brien moved to Vancouver in Sept 2026."),
    "kevin.hiring_q": ("kevin", "OPEN LOOP (unanswered): Kevin asked whether Tidepool is hiring data engineers."),
    # Isabelle
    "isabelle.bday": ("isabelle", "Isabelle's birthday is Nov 3; birthday dinner Tue Nov 3, 19:30 at Le Petit Bistro."),
    "isabelle.nights": ("isabelle", "Isabelle switched to night shifts at CHUM in October 2026."),
    # Yusuf
    "yusuf.newnumber": ("yusuf", "Yusuf changed his number on Jul 12, 2026 (+1 514-555-0171); the old number +1 438-555-0150 is no longer used."),
    "yusuf.marathon": ("yusuf", "Yusuf is running the Montreal half-marathon training plan; long runs on Sundays."),
    # Family
    "mom.christmas": ("mom", "OPEN LOOP (Jordan owes): Maman asked Jordan to confirm Christmas dates in Sherbrooke."),
    "chloe.exam": ("chloe", "Chloé has a big anatomy exam on Nov 9."),
    # Tom (sparse)
    "tom.intro": ("tom", "Tom Becker (Lumen Grid, Berlin) was introduced by Omar Haddad in Dec 2025; only two emails ever exchanged."),
}
