"""Retrieve hierarchical memories. TODO: implement."""

from recall.schemas import Entity, Evidence


def retrieve(entities: list[Entity]) -> list[Evidence]:
    """Return memories that match these entities."""
    _ = entities
    raise NotImplementedError
