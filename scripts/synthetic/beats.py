"""Hand-written story beats. Each one carries facts from world.FACTS.

Beat fields: chat (person pid or group id), source, local time, sender ("me" or pid),
text, facts, and for email: subject, thread key, cc.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Beat:
    chat: str
    source: str  # gmail | imessage | messenger
    when: str  # local America/Toronto "YYYY-MM-DD HH:MM"
    sender: str  # "me" or a pid
    text: str
    facts: tuple[str, ...] = ()
    subject: str = ""
    thread: str = ""
    cc: tuple[str, ...] = ()
    to: tuple[str, ...] = ()  # email recipients override (pids); default = chat person / me


B = Beat

BEATS: list[Beat] = [
    # ------------------------------------------------------------------ Mei (cofounder)
    B("mei", "imessage", "2026-09-14 21:12", "mei", "ok so after today's board-ish chat: let's aim to close the pre-seed by Dec 15. hard deadline, otherwise we bridge with the SR&ED refund", ("mei.preseed_close",)),
    B("mei", "imessage", "2026-09-14 21:15", "me", "agreed. Dec 15. I'll update the tracker tonight", ("mei.preseed_close",)),
    B("mei", "gmail", "2026-09-15 09:02", "me", "Hi Mei,\n\nConfirming what we said last night: target close for the pre-seed is December 15. I updated the investor tracker and moved Maple Ridge and Northwind to 'active'.\n\nJ", ("mei.preseed_close",), subject="Pre-seed timeline", thread="mei_preseed"),
    B("mei", "gmail", "2026-09-15 10:41", "mei", "Perfect. I'll have the Hydrolab pipeline demo ready before the Boréale call so we have something live to show.\n\nMei", ("mei.pilot_hydrolab",), subject="Re: Pre-seed timeline", thread="mei_preseed"),
    B("mei", "imessage", "2026-10-07 18:30", "mei", "heads up I booked Japan!! Nov 5 to Nov 15. I'll have my laptop but assume I'm mostly offline 🇯🇵", ("mei.japan",)),
    B("mei", "imessage", "2026-10-07 18:33", "me", "amazing, you deserve it. let's get the Boréale call and Priya's pilot call done before you leave then", ("mei.japan",)),
    B("mei", "imessage", "2026-10-22 16:48", "mei", "can you send me the revised financial model? the one with the new hiring plan. need it to finish the data room section", ("mei.model_ask",)),
    B("mei", "imessage", "2026-10-22 17:05", "me", "yes, will send it by Tuesday", ("mei.model_ask",)),
    B("mei", "imessage", "2026-10-25 11:20", "mei", "pipeline demo works end to end now. Hydrolab sample data loads in under a minute", ("mei.pilot_hydrolab",)),

    # ------------------------------------------------------------------ Omar Haddad
    B("omar_h", "gmail", "2025-12-03 14:20", "omar_h", "Jordan, Tom —\n\nConnecting you two. Tom runs Lumen Grid in Berlin (grid flexibility data), Jordan is building Tidepool here in Montreal. I think there's overlap on utility data partnerships.\n\nI'll let you take it from here.\nOmar", ("tom.intro",), subject="Intro: Jordan (Tidepool) <> Tom (Lumen Grid)", thread="tom_intro", to=("me", "tom")),
    B("omar_h", "gmail", "2025-12-03 16:02", "tom", "Thanks Omar (moving you to bcc). Jordan, happy to chat — I'm in Montreal in February for a conference, maybe coffee then?\n\nTom", ("tom.intro",), subject="Re: Intro: Jordan (Tidepool) <> Tom (Lumen Grid)", thread="tom_intro", to=("me",)),
    B("omar_h", "gmail", "2026-09-30 08:55", "omar_h", "Hey Jordan,\n\nQuick ask: could you intro me to Rachel Goldberg at Northwind? We're starting conversations for our next round and I know you met her at Startupfest. Happy to send a forwardable blurb.\n\nThanks!\nOmar", ("omar_h.intro_ask",), subject="Intro to Rachel Goldberg?", thread="omar_rachel"),
    B("omar_h", "imessage", "2026-10-14 07:31", "omar_h", "it's out!! Series A closed, $8M led by Maple Ridge 🎉 announcement went live this morning", ("omar_h.series_a", "daniel_vc.payloop")),
    B("omar_h", "imessage", "2026-10-14 07:40", "me", "HUGE congrats Omar!! so deserved", ("omar_h.series_a",)),
    B("omar_h", "imessage", "2026-10-14 07:44", "omar_h", "thanks man. now the fun part, hiring two backend engineers asap. send anyone good my way", ("omar_h.hiring",)),
    B("omar_h", "imessage", "2026-10-21 12:10", "omar_h", "coffee next week? would love to hear how the raise is going for you", ("omar_h.coffee",)),
    B("omar_h", "imessage", "2026-10-21 12:26", "me", "yes! Wednesday 10am at Café Névé? sending an invite", ("omar_h.coffee",)),
    B("omar_h", "messenger", "2026-08-02 20:14", "omar_h", "saw your Startupfest panel clip, you killed it", ()),

    # ------------------------------------------------------------------ Omar Benali (classmate)
    B("omar_b", "imessage", "2026-10-19 22:03", "omar_b", "sending you my Strategy midterm notes, chapters 4 to 7. the Porter section is the important one", ("omar_b.notes",)),
    B("omar_b", "imessage", "2026-10-20 13:15", "omar_b", "case comp is confirmed for Saturday Nov 7. I'll own the slides, you take the financials + Q&A?", ("omar_b.casecomp",)),
    B("omar_b", "imessage", "2026-10-20 13:40", "me", "deal. slides are yours, I'll do financials and Q&A", ("omar_b.casecomp",)),

    # ------------------------------------------------------------------ Omar Kassem (landlord)
    B("omar_k", "gmail", "2026-10-15 10:12", "omar_k", "Bonjour Jordan,\n\nYour lease at 4520 rue Saint-Denis ends June 30, 2027. For the renewal starting July 1, 2027 the rent will be $1,525/month (currently $1,450). Please confirm by November 15 if you want to renew.\n\nMerci,\nOmar Kassem", ("omar_k.lease",), subject="Renouvellement de bail / Lease renewal 2027", thread="lease"),
    B("omar_k", "imessage", "2026-10-24 09:30", "omar_k", "Bonjour! plumber is coming Friday Oct 30 between 9 and 12 for the bathroom sink. Someone needs to be home or leave the key with the concierge", ("omar_k.plumber",)),
    B("omar_k", "imessage", "2026-10-24 10:02", "me", "ok I'll leave the key with the concierge, thanks", ("omar_k.plumber",)),

    # ------------------------------------------------------------------ Sasha
    B("sasha", "messenger", "2026-08-16 23:47", "sasha", "JORDAN. Ben proposed tonight 💍😭 I said yes obviously", ("sasha.engaged",)),
    B("sasha", "messenger", "2026-08-17 00:03", "me", "WHAT!!! congrats to you both, I'm so happy for you", ("sasha.engaged",)),
    B("sasha", "imessage", "2026-09-22 19:10", "sasha", "small news: they made me senior UX designer at Fieldnote 🙈", ("sasha.promo",)),
    B("sasha", "imessage", "2026-10-18 20:44", "sasha", "I'll be in Montreal Nov 13 to 16 for a design conference! dinner on Saturday the 14th?", ("sasha.visit",)),
    B("sasha", "imessage", "2026-10-18 21:02", "me", "yes!! Saturday 14th it is, I'll book somewhere in Mile End", ("sasha.visit",)),

    # ------------------------------------------------------------------ Priya
    B("priya", "gmail", "2026-05-27 17:05", "priya", "Hi all,\n\nAfter four great years, Friday is my last day at Brightloop. You can reach me on my personal number in the meantime. More news soon!\n\nPriya", ("priya.job",), subject="Moving on from Brightloop", thread="priya_leaving"),
    B("priya", "imessage", "2026-06-03 12:20", "priya", "started at Stellarfin this week! Senior PM on the sustainability reporting product. new email is priya@stellarfin.com", ("priya.job",)),
    B("priya", "gmail", "2026-10-09 15:31", "priya", "Hi Jordan,\n\nOur sustainability team would love to try Tidepool on our supplier emissions data. Could we do a pilot kickoff call? Wednesday Nov 4 at 1pm works on our side.\n\nBest,\nPriya Nair\nSenior Product Manager, Stellarfin", ("priya.pilot",), subject="Tidepool pilot with Stellarfin", thread="priya_pilot"),
    B("priya", "gmail", "2026-10-09 16:02", "me", "Hi Priya,\n\nThat's great news. Nov 4 at 1pm works, I'll send an invite and bring Mei.\n\nJordan", ("priya.pilot",), subject="Re: Tidepool pilot with Stellarfin", thread="priya_pilot"),
    B("priya", "imessage", "2026-10-20 08:12", "priya", "so sorry, have to cancel lunch tomorrow, last-minute exec review 😩 let's do it after the pilot call", ("priya.cancelled_lunch",)),

    # ------------------------------------------------------------------ Lucas
    B("lucas", "imessage", "2026-07-28 19:20", "lucas", "c'est officiel, je déménage à Paris le 10 août! nouveau job chez Atelier Nord, data science", ("lucas.paris",)),
    B("lucas", "imessage", "2026-07-28 19:31", "me", "nooon tu vas me manquer!! mais félicitations 🎉", ("lucas.paris",)),
    B("lucas", "messenger", "2026-08-24 14:05", "lucas", "premier dimanche à Paris, l'appart est minuscule mais la vue est folle", ("lucas.paris",)),
    B("lucas", "imessage", "2026-10-16 15:44", "lucas", "billets achetés: je suis à Montréal du 18 décembre au 4 janvier 🎄 on se fait une soirée?", ("lucas.xmas",)),

    # ------------------------------------------------------------------ Élodie
    B("elodie", "gmail", "2026-10-13 11:20", "elodie", "Bonjour Jordan,\n\nComme promis, je vous mets en contact avec l'équipe innovation d'Énergie Boréale. Ils sont disponibles le mardi 3 novembre à 14 h pour un premier appel.\n\nPourriez-vous m'envoyer la présentation en français avant l'appel? Ce serait apprécié par leur équipe.\n\nÉlodie Gagnon\nDirectrice innovation, Hydrolab", ("elodie.boreale", "elodie.deck_fr"), subject="Présentation à Énergie Boréale", thread="elodie_boreale"),
    B("elodie", "gmail", "2026-10-13 14:02", "me", "Bonjour Élodie,\n\nMerci beaucoup! Le 3 novembre à 14 h nous convient. Je vous envoie la présentation en français la semaine prochaine.\n\nJordan", ("elodie.boreale", "elodie.deck_fr"), subject="Re: Présentation à Énergie Boréale", thread="elodie_boreale"),
    B("elodie", "imessage", "2026-10-23 17:15", "elodie", "Petit rappel pour la présentation en français 🙂 l'équipe de Boréale l'attend", ("elodie.deck_fr",)),

    # ------------------------------------------------------------------ Daniel Kim (investor)
    B("daniel_vc", "gmail", "2026-10-08 10:05", "daniel_vc", "Hi Jordan,\n\nGreat meeting you at the Maple Ridge founders breakfast. We'd love to hear the full Tidepool pitch. Would Thursday Oct 29 at 3pm at our office (1250 René-Lévesque, 18th floor) work?\n\nDaniel Kim\nPrincipal, Maple Ridge Ventures", ("daniel_vc.pitch",), subject="Tidepool pitch at Maple Ridge", thread="dvc_pitch"),
    B("daniel_vc", "gmail", "2026-10-08 11:30", "me", "Hi Daniel,\n\nThursday Oct 29 at 3pm works. Mei (our CTO) will join me.\n\nThanks,\nJordan", ("daniel_vc.pitch",), subject="Re: Tidepool pitch at Maple Ridge", thread="dvc_pitch"),
    B("daniel_vc", "gmail", "2026-10-23 16:40", "daniel_vc", "Hi Jordan,\n\nAhead of next Thursday, could you give us access to the data room? Mostly interested in the cohort data and the pilot pipeline.\n\nAlso, small world: we just led Payloop's Series A — I think you know Omar.\n\nDaniel", ("daniel_vc.dataroom", "daniel_vc.payloop"), subject="Re: Tidepool pitch at Maple Ridge", thread="dvc_pitch"),

    # ------------------------------------------------------------------ Daniel Kim (UofT friend)
    B("daniel_cl", "messenger", "2026-09-05 21:40", "daniel_cl", "ok official: wedding is June 12 2027 in Toronto. and I'd love for you to be a groomsman 🤵", ("daniel_cl.wedding",)),
    B("daniel_cl", "messenger", "2026-09-05 21:52", "me", "Dan!! of course, honoured. count me in", ("daniel_cl.wedding",)),
    B("daniel_cl", "imessage", "2026-10-11 18:02", "daniel_cl", "groomsmen suit fittings will be in May, I'll send details", ("daniel_cl.wedding",)),

    # ------------------------------------------------------------------ Rachel / Tyler
    B("rachel", "gmail", "2026-07-17 18:22", "rachel", "Jordan,\n\nGreat to meet you at Startupfest. Climate data infrastructure is a big theme for us this year. Let's stay in touch as you raise.\n\nRachel Goldberg\nPartner, Northwind Capital", ("rachel.met",), subject="Great meeting at Startupfest", thread="rachel_met"),
    B("rachel", "gmail", "2026-10-19 09:15", "tyler", "Hi Jordan,\n\nRachel would like to follow up on your Startupfest conversation. Would Friday, November 6 at 11:00 ET work for a 45-minute video call? I'll send a calendar invite once confirmed.\n\nBest,\nTyler Brooks\nExecutive Assistant to Rachel Goldberg, Northwind Capital", ("rachel.meeting",), subject="Northwind <> Tidepool follow-up", thread="rachel_followup", cc=("rachel",), to=("me",)),
    B("rachel", "gmail", "2026-10-19 10:01", "me", "Hi Tyler,\n\nNov 6 at 11:00 works perfectly. Looking forward to it, Rachel.\n\nJordan", ("rachel.meeting",), subject="Re: Northwind <> Tidepool follow-up", thread="rachel_followup", cc=("rachel",), to=("tyler",)),

    # ------------------------------------------------------------------ Prof Ruth Abernathy
    B("ruth", "gmail", "2026-10-21 13:44", "me", "Dear Professor Abernathy,\n\nWould it be possible to get a one-week extension on the term paper (due Oct 26)? Our startup is in the middle of fundraising meetings.\n\nBest regards,\nJordan Lefebvre", ("ruth.extension",), subject="Term paper extension request", thread="ruth_ext"),
    B("ruth", "gmail", "2026-10-22 08:30", "ruth", "Jordan,\n\nYou may submit by Monday, November 2. Please send me a draft outline by October 30 so I can check the direction.\n\nR. Abernathy", ("ruth.extension", "ruth.draft"), subject="Re: Term paper extension request", thread="ruth_ext"),

    # ------------------------------------------------------------------ Hannah (recruiter)
    B("hannah", "gmail", "2026-10-16 12:00", "hannah", "Hi Jordan,\n\nCongratulations — you've been selected for the final round with Aldridge & Co. It will be on Friday, October 30 at 1:00 pm ET over video. Before then, please send me an unofficial transcript.\n\nBest,\nHannah Weiss\nCampus Recruiting, Aldridge & Co.", ("hannah.final", "hannah.transcript"), subject="Final round — Aldridge & Co.", thread="hannah_final"),

    # ------------------------------------------------------------------ Kevin
    B("kevin", "messenger", "2026-09-12 19:25", "kevin", "update from the west coast: moved to Vancouver last week, new job at the same company just remote. the café near my place is great", ("kevin.vancouver",)),
    B("kevin", "messenger", "2026-10-10 20:02", "kevin", "random q, is Tidepool hiring data engineers? asking for me 😅", ("kevin.hiring_q",)),

    # ------------------------------------------------------------------ Isabelle
    B("isabelle", "messenger", "2026-10-05 22:30", "isabelle", "nouvelle vie: je suis de nuit au CHUM depuis lundi. si je réponds à 4h du matin c'est normal 😴", ("isabelle.nights",)),
    B("isabelle", "imessage", "2026-10-20 12:15", "me", "pour ton anniversaire le 3 novembre: Le Petit Bistro à 19h30? j'ai réservé pour 6", ("isabelle.bday",)),
    B("isabelle", "imessage", "2026-10-20 12:40", "isabelle", "parfait!! je suis en congé ce soir-là 🥳", ("isabelle.bday",)),

    # ------------------------------------------------------------------ Yusuf (number change)
    B("yusuf", "imessage", "2026-07-12 09:05", "yusuf", "hey it's Yusuf, new number! save this one, the old 438 one is dead", ("yusuf.newnumber",)),
    B("yusuf", "imessage", "2026-09-20 07:15", "yusuf", "half-marathon plan week 6: long run Sunday 18k. you in?", ("yusuf.marathon",)),

    # ------------------------------------------------------------------ family
    B("mom", "imessage", "2026-10-18 10:30", "mom", "Mon chéri, peux-tu me confirmer tes dates pour Noël à Sherbrooke? Ta tante veut savoir pour le souper du 24", ("mom.christmas",)),
    B("msgr_family", "messenger", "2026-10-25 19:02", "chloe", "examen d'anatomie le 9 novembre, priez pour moi 🙏", ("chloe.exam",)),
]
