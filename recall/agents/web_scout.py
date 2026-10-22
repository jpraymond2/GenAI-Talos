"""Look outside personal data for public context. TODO: implement."""

from recall.schemas import Entity, Evidence


def scout(entities: list[Entity]) -> list[Evidence]:
    """Search the web for these entities. Sample mode must not call the network."""
    _ = entities
    raise NotImplementedError
