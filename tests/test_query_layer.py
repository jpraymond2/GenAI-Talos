"""Query layer checks against DuckDB sample CSVs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from recall.config import DataBackend, get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.queries import (
    SAMPLE_ANCHOR,
    contact_cards,
    find_handles_by_name,
    interactions_for_handles,
    upcoming_and_past_events,
)
from recall.data.query_layer import clear_default_query_layer, get_query_layer
from recall.tracing import Tracer


@pytest.fixture(autouse=True)
def _reset_settings_and_duckdb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setenv("LOGS_DIR", str(logs))
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    monkeypatch.delenv("ZETARIS_URL", raising=False)
    monkeypatch.delenv("ZETARIS_REST_URL", raising=False)
    get_settings.cache_clear()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    yield
    DuckDBBackend.clear_connection_cache()
    clear_default_query_layer()
    get_settings.cache_clear()


@pytest.fixture
def tracer(tmp_path: Path) -> Tracer:
    return Tracer(logs_dir=tmp_path / "logs")


def _drain(queue: asyncio.Queue) -> list[dict]:
    events: list[dict] = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return events


def test_named_queries_return_rows(tracer: Tracer) -> None:
    layer = get_query_layer(tracer=tracer)

    handles = find_handles_by_name("Karim", source_tag="test.handles", layer=layer)
    assert handles

    handle_values = [h.value for h in handles]
    interactions = interactions_for_handles(
        handle_values,
        source_tag="test.interactions",
        layer=layer,
    )
    assert interactions

    emails = ["karim@ledgerly.io", "alex.martin@mail.mcgill.ca"]
    events = upcoming_and_past_events(emails, source_tag="test.events", layer=layer)
    assert events

    cards = contact_cards("Karim Haddad", source_tag="test.contact", layer=layer)
    assert len(cards) == 1
    assert cards[0].full_name == "Karim Haddad"


def test_karim_finds_both_people(tracer: Tracer) -> None:
    layer = get_query_layer(tracer=tracer)
    handles = find_handles_by_name("Karim", source_tag="test.both_karims", layer=layer)
    values = {h.value for h in handles}
    assert "karim@ledgerly.io" in values
    assert "karim.saleh@mcgill.ca" in values

    cards = contact_cards("Karim", source_tag="test.cards", layer=layer)
    names = {c.full_name for c in cards}
    assert names == {"Karim Haddad", "Karim Saleh"}


def test_contact_cards_karim_name_and_email(tracer: Tracer) -> None:
    layer = get_query_layer(tracer=tracer)

    by_name = contact_cards("Karim", source_tag="test.cards.name", layer=layer)
    assert {c.full_name for c in by_name} == {"Karim Haddad", "Karim Saleh"}

    by_email = contact_cards("karim@ledgerly.io", source_tag="test.cards.email", layer=layer)
    assert len(by_email) == 1
    assert by_email[0].full_name == "Karim Haddad"


def test_karim_haddad_covers_gmail_whatsapp_imessage(tracer: Tracer) -> None:
    layer = get_query_layer(tracer=tracer)
    handles = find_handles_by_name("Karim Haddad", source_tag="test.kh_channels", layer=layer)
    by_kind = {
        h.kind: h
        for h in handles
        if h.value
        in {
            "karim@ledgerly.io",
            "+15145550142",
            "15145550142@s.whatsapp.net",
        }
        or (h.kind == "imessage" and h.value == "+15145550142")
    }
    assert "email" in by_kind
    assert "wa_jid" in by_kind
    assert "imessage" in by_kind


def test_interactions_include_toronto_and_lina_intro(tracer: Tracer) -> None:
    layer = get_query_layer(tracer=tracer)
    handles = find_handles_by_name("Karim Haddad", source_tag="test.kh_handles", layer=layer)
    values = list({h.value for h in handles} | {"+15145550142", "karim@ledgerly.io"})

    rows = interactions_for_handles(
        values,
        source_tag="test.kh_interactions",
        since_days=120,
        limit=60,
        now=SAMPLE_ANCHOR,
        layer=layer,
    )
    texts = [r.text for r in rows]
    assert any("Toronto" in text and "January" in text for text in texts)
    assert any(
        r.thread_id == "th_kh_lina_intro" and "Lina Chen" in r.text and r.direction == "in"
        for r in rows
    )


def test_june_coffee_with_karim_event(tracer: Tracer) -> None:
    layer = get_query_layer(tracer=tracer)
    events = upcoming_and_past_events(
        ["karim@ledgerly.io", "alex.martin@mail.mcgill.ca"],
        source_tag="test.coffee",
        days_back=180,
        days_forward=30,
        now=SAMPLE_ANCHOR,
        layer=layer,
    )
    coffee = [e for e in events if e.title == "Coffee w/ Karim"]
    assert len(coffee) == 1
    assert coffee[0].start_utc.startswith("2026-06-")
    assert "karim@ledgerly.io" in coffee[0].attendees


def test_zetaris_without_url_falls_back_and_traces(
    tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SAMPLE_MODE + zetaris + empty REST URL → skip network, DuckDB fallback."""
    monkeypatch.setenv("DATA_BACKEND", "zetaris")
    monkeypatch.setenv("ZETARIS_REST_URL", "")
    monkeypatch.setenv("ZETARIS_URL", "")
    monkeypatch.delenv("ZETARIS_USER", raising=False)
    monkeypatch.delenv("ZETARIS_PASSWORD", raising=False)
    get_settings.cache_clear()
    clear_default_query_layer()
    settings = get_settings()
    assert settings.data_backend is DataBackend.ZETARIS
    assert settings.sample_mode is True
    assert not (settings.zetaris_rest_url or "").strip()

    queue = Tracer.subscribe()
    try:
        duck_settings = settings.model_copy(update={"data_backend": DataBackend.DUCKDB})
        duck = get_query_layer(settings=duck_settings, tracer=tracer)
        layer = get_query_layer(settings=settings, tracer=tracer)

        sql = "SELECT handle_id FROM recall.handles ORDER BY handle_id"
        duck_rows = duck.query(sql, source_tag="test.fallback.duck")
        zetaris_rows = layer.query(sql, source_tag="test.fallback.zetaris")

        assert zetaris_rows == duck_rows
        assert zetaris_rows

        events = _drain(queue)
        fallback_events = [e for e in events if e["kind"] == "fallback"]
        assert fallback_events, "expected a fallback trace event"
        assert fallback_events[0]["payload"]["from_backend"] == "zetaris"
        assert fallback_events[0]["payload"]["to_backend"] == "duckdb"
        reason = fallback_events[0]["payload"]["reason"]
        assert "SAMPLE_MODE" in reason or "ZETARIS_REST_URL" in reason
    finally:
        Tracer.unsubscribe(queue)


def test_every_query_writes_trace_with_backend_and_row_count(tracer: Tracer) -> None:
    queue = Tracer.subscribe()
    try:
        layer = get_query_layer(tracer=tracer)
        rows = layer.query(
            "SELECT * FROM recall.contacts LIMIT 5",
            source_tag="test.trace",
        )
        events = _drain(queue)
        query_events = [e for e in events if e["kind"] == "query" and e["agent"] == "query_layer"]
        assert query_events
        payload = query_events[-1]["payload"]
        assert payload["backend"] == "duckdb"
        assert payload["source_tag"] == "test.trace"
        assert payload["row_count"] == len(rows)
        assert "sql" in payload
        assert "latency_ms" in payload
    finally:
        Tracer.unsubscribe(queue)


def test_two_named_queries_one_trace_file(tmp_path: Path, tracer: Tracer) -> None:
    layer = get_query_layer(tracer=tracer)
    contact_cards("Karim Haddad", source_tag="test.one_trace.a", layer=layer)
    interactions_for_handles(
        ["karim@ledgerly.io"],
        source_tag="test.one_trace.b",
        now=SAMPLE_ANCHOR,
        layer=layer,
    )

    traces = list((tmp_path / "logs").glob("*/trace.jsonl"))
    assert len(traces) == 1
    records = [
        json.loads(line)
        for line in traces[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    query_events = [r for r in records if r["kind"] == "query"]
    assert len(query_events) == 2
    assert {r["payload"]["source_tag"] for r in query_events} == {
        "test.one_trace.a",
        "test.one_trace.b",
    }
