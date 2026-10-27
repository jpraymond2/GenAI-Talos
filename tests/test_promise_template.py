"""Promise talking-point wording: verb-led → you'd; else quote."""

from __future__ import annotations

from recall.agents.offline import (
    _format_promise_point,
    _promise_is_verb_led,
    _split_topic_and_action,
)


def test_verb_led_detection() -> None:
    assert _promise_is_verb_led("I'll send details")
    assert _promise_is_verb_led("send the revised model")
    assert not _promise_is_verb_led("early stage climate, I'll send details")


def test_topic_action_split() -> None:
    topic, action = _split_topic_and_action("early stage climate, I'll send details")
    assert topic == "early stage climate"
    assert action == "I'll send details"


def test_non_verb_promise_is_quoted() -> None:
    text = _format_promise_point(
        "Promise: early stage climate, I'll send details (re: nice, which fund?)",
        first_name="Mei",
        day="Sat Oct 24",
        person="Mei Tanaka",
        settings=None,
    )
    assert text is not None
    assert "you'd early stage" not in text
    assert text == (
        'You said on Sat Oct 24: "I\'ll send details" (re: early stage climate); still open.'
    )


def test_verb_led_promise_uses_youd() -> None:
    text = _format_promise_point(
        "Promise: send the deck by Friday (re: follow-up on the investor pack)",
        first_name="Mei",
        day="Thu Oct 22",
        person="Mei Tanaka",
        settings=None,
    )
    assert text is not None
    assert "you'd send the deck by Friday" in text
    assert "You said on" not in text
