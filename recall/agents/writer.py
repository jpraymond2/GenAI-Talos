"""Write the meeting brief. TODO: implement. LLM calls go through generator.py."""

from recall.schemas import Brief, Evidence


def write_brief(evidence: list[Evidence]) -> Brief:
    """Draft a brief from the gathered evidence."""
    _ = evidence
    raise NotImplementedError
