"""Pull evidence for resolved entities. TODO: implement. Reads go through the query layer."""

from recall.schemas import Entity, Evidence


def retrieve(entities: list[Entity]) -> list[Evidence]:
    """Collect evidence for these entities."""
    _ = entities
    raise NotImplementedError
