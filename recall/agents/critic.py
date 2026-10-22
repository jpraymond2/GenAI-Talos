"""Judge a brief. At most settings.max_critic_rounds revisions. TODO: implement."""

from recall.schemas import Brief, CriticVerdict


def critique(brief: Brief) -> CriticVerdict:
    """Accept the brief or send it back. Stop after the configured round cap."""
    _ = brief
    raise NotImplementedError
