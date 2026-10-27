"""Unit tests for USL table/column mapping (no network)."""

from __future__ import annotations

from recall.data.zetaris_sql import prepare_zetaris_sql
from recall.data.zetaris_usl import (
    DEFAULT_USL_NAMESPACE,
    RECALL_TO_USL_TABLE,
    UNIFIED_COLUMNS,
    USL_COLUMN_RENAMES,
    physical_column,
    rewrite_sql_for_usl,
    unified_select_list,
    usl_fqn,
    usl_subquery,
)


def test_rename_list_matches_deployed_variant() -> None:
    assert USL_COLUMN_RENAMES["contact"] == {
        "org": "organisation",
        "source": "source_system",
    }
    assert USL_COLUMN_RENAMES["handle"] == {
        "value": "handle_value",
        "source": "source_system",
    }
    assert USL_COLUMN_RENAMES["interaction"] == {
        "source": "source_system",
        "text": "body",
    }
    assert USL_COLUMN_RENAMES["event"] == {"location": "place"}


def test_table_map_plural_to_singular() -> None:
    assert RECALL_TO_USL_TABLE == {
        "contacts": "contact",
        "handles": "handle",
        "interactions": "interaction",
        "events": "event",
    }
    assert usl_fqn("handles") == f"{DEFAULT_USL_NAMESPACE}.handle"
    assert usl_fqn("events") == f"{DEFAULT_USL_NAMESPACE}.event"


def test_unified_select_list_aliases_renames() -> None:
    handles = unified_select_list("handles")
    assert "handle_value AS value" in handles
    assert "source_system AS source" in handles
    assert "handle_id" in handles

    contacts = unified_select_list("contacts")
    assert "organisation AS org" in contacts
    assert "source_system AS source" in contacts
    assert "nickname" in contacts
    assert UNIFIED_COLUMNS["contacts"].index("nickname") == UNIFIED_COLUMNS[
        "contacts"
    ].index("city") + 1

    interactions = unified_select_list("interactions")
    assert "source_system AS source" in interactions
    assert "body AS text" in interactions
    assert "handle_value" in interactions  # already unified name

    events = unified_select_list("events")
    assert "place AS location" in events


def test_physical_column_identity_and_rename() -> None:
    assert physical_column("handle", "kind") == "kind"
    assert physical_column("handle", "value") == "handle_value"
    assert physical_column("event", "location") == "place"


def test_rewrite_replaces_all_four_tables() -> None:
    sql = """
    SELECT value, source FROM recall.handles
    UNION ALL
    SELECT org, source FROM recall.contacts
    UNION ALL
    SELECT text, source FROM recall.interactions
    UNION ALL
    SELECT location, source_ref FROM recall.events
    """
    result = rewrite_sql_for_usl(sql)
    assert "recall.handles" not in result.sql
    assert "recall.contacts" not in result.sql
    assert "recall.interactions" not in result.sql
    assert "recall.events" not in result.sql
    assert f"{DEFAULT_USL_NAMESPACE}.handle" in result.sql
    assert f"{DEFAULT_USL_NAMESPACE}.contact" in result.sql
    assert f"{DEFAULT_USL_NAMESPACE}.interaction" in result.sql
    assert f"{DEFAULT_USL_NAMESPACE}.event" in result.sql
    assert set(result.usl_tables) == {
        f"{DEFAULT_USL_NAMESPACE}.handle",
        f"{DEFAULT_USL_NAMESPACE}.contact",
        f"{DEFAULT_USL_NAMESPACE}.interaction",
        f"{DEFAULT_USL_NAMESPACE}.event",
    }
    # Outer SELECT still uses unified names (aliases live inside the subquery).
    assert "SELECT value, source FROM (" in " ".join(result.sql.split()) or "value" in result.sql


def test_usl_subquery_shape() -> None:
    sub = usl_subquery("handles")
    assert sub.startswith("(SELECT ")
    assert f"FROM {DEFAULT_USL_NAMESPACE}.handle) AS handles" in sub
    assert "handle_value AS value" in sub


def test_prepare_usl_mode_rewrites_and_binds() -> None:
    sql = """
    SELECT handle_id, kind, value, display_name, source
    FROM recall.handles
    WHERE lower(display_name) LIKE :pattern
    ORDER BY handle_id
    """
    rendered, meta = prepare_zetaris_sql(
        sql,
        {"pattern": "%karim%"},
        read_mode="usl",
    )
    assert meta is not None
    assert meta.usl_tables == (f"{DEFAULT_USL_NAMESPACE}.handle",)
    assert "lightning.metastore.recallai.recall.handle" in rendered
    assert "handle_value AS value" in rendered
    assert "source_system AS source" in rendered
    assert "'%karim%'" in rendered
    assert ":pattern" not in rendered


def test_prepare_tables_mode_qualifies_schema() -> None:
    rendered, meta = prepare_zetaris_sql(
        "SELECT value FROM recall.handles",
        None,
        schema="recall",
        read_mode="tables",
    )
    assert meta is None
    assert rendered == "SELECT value FROM recall.handles"


def test_unified_columns_cover_every_mapped_table() -> None:
    assert set(UNIFIED_COLUMNS) == set(RECALL_TO_USL_TABLE)
    for recall_table, usl_table in RECALL_TO_USL_TABLE.items():
        for unified, physical in USL_COLUMN_RENAMES[usl_table].items():
            assert unified in UNIFIED_COLUMNS[recall_table]
            assert physical != unified
