"""Append-only ledger of memory writes. TODO: implement."""


def record(kind: str, payload: dict[str, str]) -> None:
    """Append one memory write to the ledger."""
    _ = (kind, payload)
    raise NotImplementedError
