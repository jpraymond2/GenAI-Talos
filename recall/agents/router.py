"""Classify an utterance into an intent. TODO: implement."""

from recall.schemas import Intent, Utterance


def route(utterance: Utterance) -> Intent:
    """Choose the intent for this turn."""
    _ = utterance
    raise NotImplementedError
