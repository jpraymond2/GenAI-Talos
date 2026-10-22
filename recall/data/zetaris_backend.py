"""Zetaris REST backend. See docs/zetaris_notes.md for the live API contract."""

from __future__ import annotations

import time
from typing import Any

import httpx

from recall.config import Settings
from recall.data.query_layer import DataUnavailable, timed_query_event
from recall.data.sql_bind import to_qmark
from recall.tracing import Tracer

# Provisional path until docs/zetaris_notes.md is filled in.
_QUERY_PATH = "/query"
_ZETARIS_RETRIES = 2


class ZetarisBackend:
    """Remote SQL reads against the Zetaris query API."""

    def __init__(self, settings: Settings, tracer: Tracer | None = None) -> None:
        """Bind settings and an optional tracer for successful query events."""
        self._settings = settings
        self._tracer = tracer
        self._backend_name = "zetaris"

    def query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        source_tag: str,
    ) -> list[dict[str, Any]]:
        """POST a parameterized query. Raises DataUnavailable on any failure."""
        started = time.perf_counter()
        base = (self._settings.zetaris_url or "").strip().rstrip("/")
        if not base:
            raise DataUnavailable("ZETARIS_URL is not configured")
        if self._settings.sample_mode:
            # SAMPLE_MODE must not contact live systems; FallbackQueryLayer uses DuckDB.
            raise DataUnavailable("Zetaris is not called in SAMPLE_MODE")

        bound_sql, bound_params = to_qmark(sql, params)
        url = f"{base}{_QUERY_PATH}"
        body: dict[str, Any] = {"sql": bound_sql, "params": bound_params}

        rows = self._request_with_retries(url, body)
        timed_query_event(
            self._tracer,
            backend=self._backend_name,
            source_tag=source_tag,
            sql=sql,
            row_count=len(rows),
            started=started,
        )
        return rows

    def _request_with_retries(self, url: str, body: dict[str, Any]) -> list[dict[str, Any]]:
        timeout = self._settings.http_timeout_seconds
        attempts = _ZETARIS_RETRIES + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._once(url, body, timeout=timeout)
            except DataUnavailable as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
        raise DataUnavailable(str(last_error) if last_error else "Zetaris request failed")

    def _once(self, url: str, body: dict[str, Any], *, timeout: float) -> list[dict[str, Any]]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        auth: httpx.Auth | None = None
        api_key = self._settings.zetaris_api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif self._settings.zetaris_user and self._settings.zetaris_password.get_secret_value():
            auth = httpx.BasicAuth(
                self._settings.zetaris_user,
                self._settings.zetaris_password.get_secret_value(),
            )

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, json=body, headers=headers, auth=auth)
        except httpx.HTTPError as exc:
            # Never include request headers/body credentials in the message.
            raise DataUnavailable(f"Zetaris transport error: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise DataUnavailable(f"Zetaris HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise DataUnavailable("Zetaris returned non-JSON") from exc

        return self._extract_rows(payload)

    @staticmethod
    def _extract_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            if all(isinstance(row, dict) for row in payload):
                return payload
            raise DataUnavailable("Zetaris array response must contain objects")
        if isinstance(payload, dict) and "rows" in payload:
            rows = payload["rows"]
            if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
                return rows
            raise DataUnavailable("Zetaris rows field must be a list of objects")
        raise DataUnavailable("Zetaris response missing rows")
