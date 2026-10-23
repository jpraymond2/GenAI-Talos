"""Zetaris REST backend: mocked HTTP, SQL escaping, DuckDB fallback."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from recall.config import DataBackend, Settings, ZetarisReadMode, get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import (
    DataUnavailable,
    FallbackQueryLayer,
    clear_default_query_layer,
    get_query_layer,
)
from recall.data.zetaris_backend import ZetarisBackend
from recall.data.zetaris_sql import prepare_zetaris_sql, sql_literal
from recall.tracing import Tracer


class _ZetarisMockState:
    """Mutable request log shared with the HTTP handler."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.mode: str = "happy"
        self.sql_hits: int = 0


def _make_handler(state: _ZetarisMockState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode() or "{}")

        def _write(self, code: int, body: dict[str, Any] | list[Any]) -> None:
            payload = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            body = self._read_json()
            auth = self.headers.get("Authorization", "")
            record = {
                "path": path,
                "body": body,
                "authorization": auth,
                "org": self.headers.get("X-Org-ID"),
                "request_id": self.headers.get("X-Request-ID"),
                "content_type": self.headers.get("Content-Type"),
            }
            state.requests.append(record)

            if path.endswith("/api/auth/login"):
                # Never echo password back; accept any non-empty body.
                self._write(200, {"idToken": "login-id-token"})
                return

            if "/sql-editor/sqls/run" in path:
                state.sql_hits += 1
                if state.mode == "not_allowed_then_ok":
                    if auth == "Bearer bad-api-key":
                        self._write(400, {"msg": "Not Allowed"})
                        return
                    if auth == "Bearer login-id-token":
                        self._write(
                            200,
                            {
                                "headers": ["handle_id", "kind"],
                                "data": [["h1", "email"]],
                            },
                        )
                        return
                    self._write(401, {"msg": "unauthorized"})
                    return

                if state.mode == "missing_headers":
                    self._write(200, {"data": [["1"]]})
                    return

                if state.mode == "fail":
                    self._write(503, {"msg": "down"})
                    return

                # happy
                self._write(
                    200,
                    {
                        "headers": [{"name": "n"}, "v"],
                        "data": [["1", "alpha"], ["2", "beta"]],
                    },
                )
                return

            self._write(404, {"msg": "unknown"})

    return Handler


@pytest.fixture
def mock_zetaris() -> Iterator[tuple[str, str, _ZetarisMockState]]:
    """Start a local HTTP server covering REST + login paths."""
    state = _ZetarisMockState()
    server = HTTPServer(("127.0.0.1", 0), _make_handler(state))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    rest = f"http://127.0.0.1:{port}/api/v1.0"
    base = f"http://127.0.0.1:{port}"
    try:
        yield rest, base, state
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _reset_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setenv("LOGS_DIR", str(logs))
    monkeypatch.setenv("SAMPLE_MODE", "false")
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    get_settings.cache_clear()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    yield
    DuckDBBackend.clear_connection_cache()
    clear_default_query_layer()
    get_settings.cache_clear()


def _settings(rest: str, base: str, **kwargs: Any) -> Settings:
    defaults: dict[str, Any] = {
        "sample_mode": False,
        "data_backend": DataBackend.ZETARIS,
        "zetaris_rest_url": rest,
        "zetaris_base_url": base,
        "zetaris_org_id": "1",
        "zetaris_schema": "recall",
        # HTTP mock tests assert raw FILESTORE SQL; USL mapping covered separately.
        "zetaris_read_mode": ZetarisReadMode.TABLES,
        "zetaris_api_key": "test-api-key",
        "zetaris_user": "demo",
        "zetaris_password": "demo-pass",
        "http_timeout_seconds": 5.0,
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def test_happy_path_and_headers(mock_zetaris: tuple[str, str, _ZetarisMockState]) -> None:
    rest, base, state = mock_zetaris
    state.mode = "happy"
    backend = ZetarisBackend(_settings(rest, base))
    rows = backend.query(
        "SELECT n, v FROM recall.handles WHERE kind = :kind",
        {"kind": "email"},
        source_tag="test.happy",
    )
    assert rows == [{"n": "1", "v": "alpha"}, {"n": "2", "v": "beta"}]
    assert state.requests, "expected at least one HTTP call"
    sql_req = next(r for r in state.requests if "/sql-editor/sqls/run" in r["path"])
    assert sql_req["authorization"] == "Bearer test-api-key"
    assert sql_req["org"] == "1"
    assert sql_req["request_id"]
    assert "application/json" in (sql_req["content_type"] or "")
    assert sql_req["body"]["source"] == "SqlEditor"
    assert "queryId" in sql_req["body"]
    assert "WHERE kind = 'email'" in sql_req["body"]["sql"]
    assert "recall.handles" in sql_req["body"]["sql"]
    assert not sql_req["body"]["sql"].endswith(";")


def test_login_fallback_on_not_allowed(
    mock_zetaris: tuple[str, str, _ZetarisMockState],
) -> None:
    rest, base, state = mock_zetaris
    state.mode = "not_allowed_then_ok"
    backend = ZetarisBackend(
        _settings(rest, base, zetaris_api_key="bad-api-key")
    )
    rows = backend.query("SELECT 1 AS n", source_tag="test.login")
    assert rows == [{"handle_id": "h1", "kind": "email"}]
    paths = [r["path"] for r in state.requests]
    assert any(p.endswith("/api/auth/login") for p in paths)
    assert state.sql_hits >= 2
    # Login body must not appear in our logs; request should have used username/password keys only.
    login = next(r for r in state.requests if r["path"].endswith("/api/auth/login"))
    assert set(login["body"].keys()) == {"username", "password"}
    assert login["body"]["username"] == "demo"


def test_missing_headers_raises(mock_zetaris: tuple[str, str, _ZetarisMockState]) -> None:
    rest, base, state = mock_zetaris
    state.mode = "missing_headers"
    backend = ZetarisBackend(_settings(rest, base))
    with pytest.raises(DataUnavailable, match="missing headers"):
        backend.query("SELECT 1", source_tag="test.no_headers")


def test_string_rows_to_dicts(mock_zetaris: tuple[str, str, _ZetarisMockState]) -> None:
    rest, base, state = mock_zetaris
    state.mode = "happy"
    backend = ZetarisBackend(_settings(rest, base))
    rows = backend.query("SELECT n, v FROM recall.contacts", source_tag="test.dicts")
    assert all(isinstance(v, str) for row in rows for v in row.values())
    assert list(rows[0].keys()) == ["n", "v"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("O'Brien", "'O''Brien'"),
        ("'; DROP TABLE t; --", "'''; DROP TABLE t; --'"),
        ("x'; DROP TABLE t; --", "'x''; DROP TABLE t; --'"),
        (42, "42"),
        (True, "TRUE"),
        (None, "NULL"),
        (1.5, "1.5"),
    ],
)
def test_sql_literal_escaping(value: Any, expected: str) -> None:
    assert sql_literal(value) == expected


def test_prepare_rejects_injection_and_bad_types() -> None:
    rendered, _meta = prepare_zetaris_sql(
        "SELECT * FROM recall.handles WHERE value = :v",
        {"v": "x'; DROP TABLE handles; --"},
        schema="recall",
        read_mode="tables",
    )
    # Quote-doubling keeps the attack inside one literal (prepare would raise otherwise).
    assert "WHERE value = 'x''; DROP TABLE handles; --'" in rendered

    with pytest.raises(DataUnavailable, match="one statement"):
        prepare_zetaris_sql("SELECT 1; SELECT 2", None, read_mode="tables")

    with pytest.raises(DataUnavailable, match="not allowed"):
        prepare_zetaris_sql("SELECT :v", {"v": {"nested": 1}}, read_mode="tables")

    with pytest.raises(DataUnavailable, match="not allowed"):
        prepare_zetaris_sql("SELECT :v", {"v": object()}, read_mode="tables")

    in_sql, _ = prepare_zetaris_sql(
        "SELECT * FROM recall.handles WHERE kind IN (:kinds)",
        {"kinds": ["email", "phone"]},
        read_mode="tables",
    )
    assert "IN ('email', 'phone')" in in_sql


def test_schema_qualify() -> None:
    sql, _ = prepare_zetaris_sql(
        "SELECT * FROM recall.handles JOIN recall.contacts c ON 1=1",
        None,
        schema="demo_db",
        read_mode="tables",
    )
    assert "demo_db.handles" in sql
    assert "demo_db.contacts" in sql
    assert "recall." not in sql


def test_sample_mode_calls_zetaris_when_configured(
    mock_zetaris: tuple[str, str, _ZetarisMockState],
) -> None:
    rest, base, state = mock_zetaris
    state.mode = "happy"
    backend = ZetarisBackend(
        _settings(rest, base, sample_mode=True, data_backend=DataBackend.ZETARIS)
    )
    rows = backend.query("SELECT n, v FROM recall.handles", source_tag="test.sample.call")
    assert rows
    assert any("/sql-editor/sqls/run" in r["path"] for r in state.requests)


def test_sample_mode_skips_without_rest_url() -> None:
    backend = ZetarisBackend(
        _settings(
            "",
            "http://127.0.0.1:9",
            sample_mode=True,
            data_backend=DataBackend.ZETARIS,
            zetaris_rest_url="",
            zetaris_url="",
        )
    )
    with pytest.raises(DataUnavailable, match="SAMPLE_MODE"):
        backend.query("SELECT 1", source_tag="test.sample.skip")


def test_fallback_to_duckdb_on_failure(
    mock_zetaris: tuple[str, str, _ZetarisMockState],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rest, base, state = mock_zetaris
    state.mode = "fail"
    monkeypatch.setenv("SAMPLE_MODE", "true")
    get_settings.cache_clear()

    tracer = Tracer(logs_dir=tmp_path / "logs")
    # SAMPLE_MODE + zetaris + URL → call mock; on failure FallbackQueryLayer → DuckDB.
    settings = _settings(rest, base, sample_mode=True, data_backend=DataBackend.ZETARIS)
    layer = get_query_layer(settings=settings, tracer=tracer)
    rows = layer.query(
        "SELECT handle_id FROM recall.handles ORDER BY handle_id LIMIT 3",
        source_tag="test.fallback",
    )
    assert len(rows) == 3
    assert "handle_id" in rows[0]
    assert state.sql_hits >= 1

    live = _settings(rest, base, sample_mode=False)
    primary = ZetarisBackend(live)
    duck = DuckDBBackend(
        Settings(sample_mode=True, data_backend=DataBackend.DUCKDB),
        tracer=tracer,
    )
    wrapped = FallbackQueryLayer(primary, duck, tracer)
    with pytest.raises(DataUnavailable):
        primary.query("SELECT 1", source_tag="test.fail.direct")
    rescued = wrapped.query(
        "SELECT handle_id FROM recall.handles ORDER BY handle_id LIMIT 2",
        source_tag="test.fail.wrap",
    )
    assert len(rescued) == 2


def test_no_api_key_logs_in(mock_zetaris: tuple[str, str, _ZetarisMockState]) -> None:
    rest, base, state = mock_zetaris
    state.mode = "not_allowed_then_ok"
    backend = ZetarisBackend(
        _settings(rest, base, zetaris_api_key="")
    )
    # Without a key, login runs first; server then accepts login-id-token.
    rows = backend.query("SELECT 1", source_tag="test.nokey")
    assert rows == [{"handle_id": "h1", "kind": "email"}]
    assert any(r["path"].endswith("/api/auth/login") for r in state.requests)
