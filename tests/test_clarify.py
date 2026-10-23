"""Clarification reply matching for interactive ask."""

from recall.__main__ import match_clarification_reply
from recall.schemas import Candidate

CANDIDATES = [
    Candidate(
        entity_id="karim-haddad",
        name="Karim Haddad",
        score=0.55,
        reason="founder",
    ),
    Candidate(
        entity_id="karim-saleh",
        name="Karim Saleh",
        score=0.45,
        reason="classmate",
    ),
]


def test_match_by_number() -> None:
    assert match_clarification_reply("1", CANDIDATES).entity_id == "karim-haddad"
    assert match_clarification_reply("2", CANDIDATES).entity_id == "karim-saleh"
    assert match_clarification_reply("3", CANDIDATES) is None


def test_match_by_full_name() -> None:
    assert match_clarification_reply("Karim Haddad", CANDIDATES).entity_id == "karim-haddad"
    assert match_clarification_reply("karim saleh", CANDIDATES).entity_id == "karim-saleh"


def test_match_by_last_name() -> None:
    assert match_clarification_reply("Haddad", CANDIDATES).entity_id == "karim-haddad"
    assert match_clarification_reply("saleh", CANDIDATES).entity_id == "karim-saleh"


def test_match_ambiguous_first_name_fails() -> None:
    assert match_clarification_reply("Karim", CANDIDATES) is None
