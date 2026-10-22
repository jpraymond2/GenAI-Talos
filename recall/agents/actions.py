"""Propose side effects. Sample mode must dry-run them. TODO: implement."""

from recall.schemas import ActionPlan, Brief


def plan(brief: Brief) -> ActionPlan:
    """Turn a brief into actions the user can confirm."""
    _ = brief
    raise NotImplementedError
