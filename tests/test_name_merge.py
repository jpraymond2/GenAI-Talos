"""Unit tests for the same/similar-name merge decisions (no DB, no LLM)."""

from __future__ import annotations

from recall.engines.world.name_merge import MERGE_AT, _View, clean_name, judge


def view(eid: str, name: str, *, emails=(), phones=(), channels=(), card_org="", text=()) -> _View:
    return _View(id=eid, name=name, tokens=clean_name(name).split(), emails=set(emails), phones=set(phones),
                 channels=set(channels), card_org=card_org, inbound_text=list(text))


def test_clean_name_drops_labels_and_accents() -> None:
    assert clean_name("Omar B (MBA)") == "omar b"
    assert clean_name("Élodie  Gagnon") == "elodie gagnon"


def test_same_name_split_across_channels_merges() -> None:
    a = view("a", "Lucas Moreau", phones={"+15145550156"}, channels={"contacts", "imessage"})
    b = view("b", "Lucas Moreau", channels={"messenger"})
    j = judge(a, b, {"lucas": 2})
    assert j is not None and j.score >= MERGE_AT


def test_nickname_with_same_surname_merges() -> None:
    a = view("a", "Alexandra Petrova", phones={"+14165550129"}, channels={"contacts", "imessage"})
    b = view("b", "Sasha Petrova", channels={"messenger"})
    j = judge(a, b, {"alexandra": 1, "sasha": 1})
    assert j is not None and j.score >= MERGE_AT


def test_same_name_different_employers_kept_apart() -> None:
    a = view("a", "Daniel Kim", emails={"daniel@mapleridge.vc"}, channels={"gmail"}, card_org="Maple Ridge Ventures")
    b = view("b", "Daniel Kim", emails={"dkim@shopwise.com"}, phones={"+16475550161"}, channels={"imessage"}, card_org="Shopwise")
    j = judge(a, b, {"daniel": 2})
    assert j is not None and j.score < MERGE_AT


def test_two_people_each_with_their_own_phone_not_merged() -> None:
    a = view("a", "Sam Lee", phones={"+15145550111"}, channels={"imessage"})
    b = view("b", "Sam Lee", phones={"+15145550112"}, channels={"imessage"})
    j = judge(a, b, {"sam": 2})
    assert j is not None and j.score < MERGE_AT


def test_new_number_that_introduces_itself_merges() -> None:
    old = view("a", "Yusuf Demir", phones={"+14385550150"}, channels={"contacts", "imessage"})
    new = view("b", "+15145550171", phones={"+15145550171"}, channels={"imessage"},
               text=["hey it's Yusuf, new number! save this one"])
    j = judge(new, old, {"yusuf": 1})
    assert j is not None and j.score >= MERGE_AT


def test_unrelated_names_not_compared() -> None:
    assert judge(view("a", "Omar Haddad"), view("b", "Omar Kassem"), {"omar": 3}) is None


def test_shared_specific_topics_raise_the_score() -> None:
    from recall.engines.world.name_merge import TopicModel

    a = view("a", "Noor Rahimi", phones={"+15145550190"}, channels={"imessage"},
             text=["the regatta crew needs one more rower", "regatta practice at dawn", "rowing machine booked"])
    b = view("b", "Noor Rahimi", channels={"messenger"},
             text=["regatta photos are up", "my rowing club party saturday", "crew dinner after the regatta"])
    others = {f"o{i}": view(f"o{i}", f"Person {i}", text=["lunch tomorrow?", "running late", f"topic{i} update"]) for i in range(8)}
    for v in (a, b):
        v.texts = list(v.inbound_text)
    for v in others.values():
        v.texts = list(v.inbound_text)
    topics = TopicModel({"a": a, "b": b, **others})
    j = judge(a, b, {"noor": 2, "full:noor rahimi": 2}, topics)
    assert j is not None and any("same specific things" in r for r in j.reasons_for)


def test_lookalike_names_need_more_than_a_name() -> None:
    work = view("a", "Lee Park", emails={"lee@fundco.vc"}, channels={"gmail"})
    social = view("b", "Lee Park", channels={"messenger"})
    unique = judge(work, social, {"lee": 2, "full:lee park": 2})
    crowded = judge(work, social, {"lee": 3, "full:lee park": 3})
    assert unique is not None and crowded is not None and unique.score > crowded.score
