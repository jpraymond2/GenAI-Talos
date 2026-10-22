"""Read path for source data. Agents import this package, not files on disk."""

from recall.data.query_layer import QueryLayer, get_query_layer

__all__ = ["QueryLayer", "get_query_layer"]
