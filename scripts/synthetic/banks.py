"""Phrase banks for filler conversations and noise email. Short, plausible, fictional.

A dialogue is a list of (speaker, text) with speaker "a" or "b"; the generator
decides who is "a". ``{first}`` is the other person's first name, ``{me}`` the owner's.
"""

from __future__ import annotations

CASUAL_EN = [
    [("a", "you around this weekend?"), ("b", "saturday yes, sunday is chores day"), ("a", "brunch saturday then?"), ("b", "deal 🙌")],
    [("a", "running 10 min late sorry!!"), ("b", "no worries, grabbed a table at the back")],
    [("a", "did you watch the game last night"), ("b", "only the third period, what a comeback"), ("a", "Suzuki is unreal this year")],
    [("a", "lol"), ("b", "right??")],
    [("a", "happy friday 🍻"), ("b", "finally. what a week")],
    [("a", "can you send me that podcast you mentioned"), ("b", "https://podcasts.example.com/ep/412"), ("a", "thanks!")],
    [("a", "are you still coming tonight?"), ("b", "yes! bringing wine"), ("a", "perfect, door code is 2041")],
    [("a", "how was the trip?"), ("b", "amazing, too short as always. photos soon"), ("a", "can't wait")],
    [("a", "omg it's snowing already"), ("b", "absolutely not. it's October"), ("a", "Montreal said yes")],
    [("a", "free for a quick call?"), ("b", "in 20 min?"), ("a", "perfect")],
    [("a", "thanks again for yesterday, really needed that"), ("b", "anytime ❤️")],
    [("a", "new ramen place on Saint-Laurent, have you tried it?"), ("b", "not yet, is it good?"), ("a", "broth is 10/10, lines are long"), ("b", "let's go thursday")],
    [("a", "📍 here"), ("b", "coming down")],
    [("a", "did I leave my scarf at your place?"), ("b", "the grey one? yes, it's on the chair"), ("a", "phew, I'll grab it next time")],
    [("a", "ok I finally booked the dentist"), ("b", "proud of you"), ("a", "lol")],
    [("a", "movie tonight?"), ("b", "can't, deadline. tomorrow?"), ("a", "tomorrow works")],
    [("a", "this weather 😩"), ("b", "I refuse to go outside"), ("a", "same")],
]

CASUAL_FR = [
    [("a", "t'es dispo samedi?"), ("b", "oui en après-midi"), ("a", "café au Olimpico vers 14h?"), ("b", "parfait")],
    [("a", "désolé je suis en retard de 10 min"), ("b", "pas grave, je t'attends en avant")],
    [("a", "as-tu vu le match hier?"), ("b", "juste la troisième période, quelle remontée"), ("a", "Suzuki est malade cette année")],
    [("a", "haha"), ("b", "exactement 😂")],
    [("a", "bonne fin de semaine!"), ("b", "toi aussi, repose-toi")],
    [("a", "tu viens toujours ce soir?"), ("b", "oui! j'apporte du vin"), ("a", "parfait, le code de porte c'est 2041")],
    [("a", "il neige déjà 😩"), ("b", "non. on est en octobre"), ("a", "Montréal a dit oui")],
    [("a", "merci encore pour hier"), ("b", "avec plaisir ❤️")],
    [("a", "on se fait un souper la semaine prochaine?"), ("b", "jeudi?"), ("a", "jeudi ça marche")],
    [("a", "t'as des nouvelles de Marc?"), ("b", "il est en voyage au Portugal je pense"), ("a", "chanceux")],
    [("a", "je passe chercher mes affaires demain?"), ("b", "oui je suis là après 17h")],
    [("a", "📍 je suis arrivé"), ("b", "je descends")],
    [("a", "film ce soir?"), ("b", "pas ce soir, demain?"), ("a", "go pour demain")],
]

SCHOOL_EN = [
    [("a", "did anyone get what the prof meant by question 3?"), ("b", "I think it's asking for the NPV with the second scenario"), ("a", "ahh ok that makes sense")],
    [("a", "group meeting tomorrow 6pm Bronfman 2nd floor?"), ("b", "works for me"), ("a", "booked room 245")],
    [("a", "uploaded my part to the shared drive"), ("b", "thanks, I'll merge tonight")],
    [("a", "is the reading for Thursday chapter 6 or 7?"), ("b", "6, and the HBR case")],
    [("a", "can someone share the slides from today?"), ("b", "just posted them on myCourses")],
    [("a", "who's taking notes today?"), ("b", "me, I'll share after class")],
    [("a", "the case is so long 😩"), ("b", "skim the exhibits first, trust me")],
    [("a", "exam room changed to Leacock 132 btw"), ("b", "thanks for the heads up")],
    [("a", "coffee before class?"), ("b", "yes, Second Cup at 8:10")],
]

STARTUP_EN = [
    [("a", "investor update draft is in the doc, can you review?"), ("b", "on it, comments in 30 min")],
    [("a", "the API was down for 4 min, fixed now"), ("b", "root cause?"), ("a", "expired cert on the staging proxy, renewing all of them")],
    [("a", "got a warm intro to a VC in Toronto"), ("b", "nice, which fund?"), ("a", "early stage climate, I'll send details")],
    [("a", "can you jump on the customer call at 2?"), ("b", "yes, sending notes after")],
    [("a", "new signup from a utility in Ontario 👀"), ("b", "let's book a demo this week")],
    [("a", "payroll goes out tomorrow, all good on your side?"), ("b", "yes approved")],
    [("a", "do we have the SOC 2 questionnaire answers somewhere?"), ("b", "in the security folder, v3 is the latest")],
    [("a", "demo day slides v4 uploaded"), ("b", "slide 7 still has the old revenue number"), ("a", "fixing")],
    [("a", "anyone going to the founders dinner thursday?"), ("b", "I'll be there"), ("a", "same, see you")],
    [("a", "hiring tip: post on the McGill job board, got 3 good applicants"), ("b", "great idea thanks")],
    [("a", "board deck due friday"), ("b", "I'll do the metrics section")],
]

FAMILY_FR = [
    [("a", "Tu manges bien au moins?"), ("b", "Oui maman 😅"), ("a", "Je t'envoie la recette de la soupe")],
    [("a", "Bonne semaine mon chéri ❤️"), ("b", "Merci, toi aussi!")],
    [("a", "Ton père demande si tu viens dimanche"), ("b", "Pas cette semaine, la prochaine c'est sûr")],
    [("a", "As-tu reçu le colis?"), ("b", "Oui merci! Les biscuits sont déjà finis 😂")],
    [("a", "Appelle ta grand-mère, elle s'ennuie"), ("b", "Je l'appelle ce soir promis")],
    [("a", "Il fait -2 ici ce matin"), ("b", "Ici aussi, l'hiver arrive")],
    [("a", "Bravo pour ta présentation!"), ("b", "Merci! Ça s'est bien passé")],
    [("a", "On a mis les pneus d'hiver"), ("b", "Bonne idée, je dois le faire aussi")],
]

GYM_EN = [
    [("a", "gym at 7 tomorrow?"), ("b", "6:30, I have class at 9"), ("a", "ok 6:30")],
    [("a", "legs today 😵"), ("b", "pray for us")],
    [("a", "skipping today, sick"), ("b", "rest up man")],
    [("a", "new PR on deadlift 140kg"), ("b", "beast 💪")],
    [("a", "run sunday morning?"), ("b", "yes, mountain loop at 8"), ("a", "see you at the cross")],
]

BANKS = {
    "casual_en": CASUAL_EN, "casual_fr": CASUAL_FR, "school": SCHOOL_EN,
    "startup": STARTUP_EN, "family": FAMILY_FR, "gym": GYM_EN,
}

REL_BANKS = {
    "cofounder": ["startup", "startup", "casual_en"],
    "founder": ["startup", "casual_en"],
    "classmate": ["school", "school", "casual_en"],
    "friend": ["casual_en"],
    "family": ["family"],
    "gym": ["gym", "casual_en"],
    "landlord": ["casual_en"],
    "colleague": ["casual_en", "startup"],
    "advisor": ["startup"],
    "acquaintance": ["casual_en"],
    "prof": ["school"],
}

# Email filler: (subject, body_from_them, reply_from_me)
EMAIL_THREADS = [
    ("Quick question", "Hi {me_first},\n\nDo you have 15 minutes this week for a quick question about your experience with {topic}?\n\nThanks,\n{first}", "Hi {first},\n\nSure — Thursday afternoon works. Send me a time.\n\n{me_first}"),
    ("Notes from today", "Hi {me_first},\n\nAttaching my notes from today. Let me know if I missed anything.\n\nBest,\n{first}", "Thanks {first}, looks complete!"),
    ("Following up", "Hi {me_first},\n\nJust following up on my last email — any thoughts?\n\n{first}", "Hi {first}, sorry for the delay, will get back to you by Friday."),
    ("Event next week", "Hey {me_first},\n\nWe're hosting a small event on {topic} next week. Would you like to join?\n\n{first}", "Thanks for the invite {first}, I'll try to make it."),
    ("Re: slides", "Hi {me_first},\n\nHere are the updated slides. I changed the market sizing per your comments.\n\n{first}", "Perfect, thanks {first}."),
    ("Coffee?", "Hi {me_first},\n\nWould be great to catch up — coffee sometime next week?\n\n{first}", "Would love to. Tuesday morning?"),
]
TOPICS = ["climate data", "fundraising", "the MBA program", "utility partnerships", "hiring engineers", "carbon accounting", "pricing"]

# Noise: (from_name, from_addr, label, subject, body)
NOISE = [
    ("Climate Tech Weekly", "digest@climatetechweekly.news", "CATEGORY_UPDATES", "This week in climate tech: {n} deals",
     "This week: {n} climate deals closed. Grid software leads the pack. Read more inside."),
    ("Montreal Startup Digest", "hello@mtlstartupdigest.ca", "CATEGORY_UPDATES", "Montreal Startup Digest #{n}",
     "Funding roundup, events and jobs from the Montreal ecosystem."),
    ("RideNow", "receipts@ridenow.app", "CATEGORY_UPDATES", "Your trip receipt",
     "Thanks for riding with RideNow. Total: ${amt}. Plateau-Mont-Royal → Downtown."),
    ("Banque Boréale", "alertes@banqueboreale.ca", "CATEGORY_UPDATES", "Alerte de transaction / Transaction alert",
     "Une transaction de {amt} $ a été effectuée sur votre carte se terminant par 4417."),
    ("McGill IT Services", "noreply@mcgill.ca", "CATEGORY_UPDATES", "Your verification code",
     "Your verification code is {code}. It expires in 10 minutes."),
    ("Desautels Student Affairs", "studentaffairs.desautels@mcgill.ca", "CATEGORY_UPDATES", "Weekly MBA bulletin",
     "Career events, club news and reminders for the week."),
    ("Google Calendar", "calendar-notification@google.com", "CATEGORY_UPDATES", "Reminder: event tomorrow",
     "This is a reminder for an event on your calendar tomorrow."),
    ("SoleMate Shoes", "deals@solemate.shop", "CATEGORY_PROMOTIONS", "30% off everything this weekend 👟",
     "Our biggest sale of the season. Use code FALL30."),
    ("Nimbus Air", "offers@nimbusair.travel", "CATEGORY_PROMOTIONS", "Fares to Paris from $589",
     "Fly Montreal → Paris this winter. Limited seats."),
    ("LinkedUp", "notifications@linkedup.example", "CATEGORY_SOCIAL", "You appeared in {n} searches this week",
     "See who's looking at your profile."),
    ("Hydrolab Newsletter", "nouvelles@hydrolab.ca", "CATEGORY_UPDATES", "Nouvelles Hydrolab — automne",
     "Les nouvelles de l'automne: projets pilotes, événements et plus."),
]
