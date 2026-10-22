"""Run one meeting-prep pass.

Order, once wired: router, resolver, retriever, web scout, writer, critic, actions.
Each step writes a trace event. Critic rounds stop at settings.max_critic_rounds.
"""

from recall.schemas import RunState


def run(utterance: str) -> RunState:
    """Run the pipeline for one utterance."""
    _ = utterance
    raise NotImplementedError
