"""Resolve people and things named in the turn. TODO: implement."""

from recall.schemas import Entity, Intent, Utterance


def resolve(utterance: Utterance, intent: Intent) -> list[Entity]:
    """Turn the utterance into entities the rest of the pipeline can look up."""
    _ = (utterance, intent)
    raise NotImplementedError
