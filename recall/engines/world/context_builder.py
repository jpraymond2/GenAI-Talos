"""Build world context for the entities in a turn. TODO: implement."""

from recall.schemas import Entity, Evidence


def build_context(entities: list[Entity]) -> list[Evidence]:
    """Return world evidence for these entities."""
    _ = entities
    raise NotImplementedError
