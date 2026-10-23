"""Zetaris REST backend. See docs/zetaris_notes.md for the live API contract."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx

from pydantic import SecretStr

from recall.config import DataBackend, Settings
from recall.data.query_layer import DataUnavailable, timed_query_event
from recall.data.zetaris_sql import prepare_zetaris_sql
from recall.tracing import Tracer

logger = logging.getLogger(__name__)


def _secret_text(value: SecretStr | str | None) -> str:
    """Read a SecretStr or plain string without assuming the wrapper type."""
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value)

_ZETARIS_RETRIES = 2
_DEFAULT_LIMIT = 1000
_SQL_RUN_PATH = "/sql-editor/sqls/run"
_LOGIN_PATH = "/api/auth/login"


class ZetarisBackend:
    """Remote Spark SQL reads against Zetaris ``sql-editor/sqls/run``."""

    def __init__(self, settings: Settings, tracer: Tracer | None = None) -> None:
        """Bind settings and an optional tracer for successful query events."""
        self._settings = settings
        self._tracer = tracer
        self._backend_name = "zetaris"
        self._cached_token: str | None = None
        self._auth_method: str | None = None  # "api_key" | "login" (for smoke/diagnostics)

    def query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        source_tag: str,
    ) -> list[dict[str, Any]]:
        """POST one rendered SQL statement. Raises DataUnavailable on any failure."""
        started = time.perf_counter()
        rest = self._rest_url()
        if self._settings.sample_mode:
            # Sample data can live in Zetaris too: call when backend+URL are configured.
            if self._settings.data_backend is not DataBackend.ZETARIS or not rest:
                raise DataUnavailable(
                    "Zetaris skipped in SAMPLE_MODE (need DATA_BACKEND=zetaris and ZETARIS_REST_URL)"
                )

        if not rest:
            raise DataUnavailable("ZETARIS_REST_URL is not configured")

        try:
            rendered = prepare_zetaris_sql(
                sql,
                params,
                schema=self._settings.zetaris_schema or "recall",
            )
        except DataUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DataUnavailable(f"Zetaris SQL prepare failed: {type(exc).__name__}") from exc

        rows = self._request_with_retries(rest, rendered)
        timed_query_event(
            self._tracer,
            backend=self._backend_name,
            source_tag=source_tag,
            sql=sql,
            row_count=len(rows),
            started=started,
        )
        return rows

    def _rest_url(self) -> str:
        return (
            self._settings.zetaris_rest_url
            or self._settings.zetaris_url
            or ""
        ).strip().rstrip("/")

    def _base_url(self) -> str:
        return (self._settings.zetaris_base_url or "").strip().rstrip("/")

    def _request_with_retries(self, rest: str, sql: str) -> list[dict[str, Any]]:
        timeout = self._settings.http_timeout_seconds
        attempts = _ZETARIS_RETRIES + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._run_sql(rest, sql, timeout=timeout)
            except DataUnavailable as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
        raise DataUnavailable(str(last_error) if last_error else "Zetaris request failed")

    def _run_sql(self, rest: str, sql: str, *, timeout: float) -> list[dict[str, Any]]:
        token = self._resolve_token(timeout=timeout)
        url = f"{rest}{_SQL_RUN_PATH}"
        body = {
            "queryId": str(uuid.uuid4()),
            "sql": sql,
            "source": "SqlEditor",
            "limit": _DEFAULT_LIMIT,
        }
        response = self._post_json(url, body, token=token, timeout=timeout)

        # Known local bug: API key returns 400 {"msg":"Not Allowed"} — login and retry once.
        if response.status_code == 400 and self._is_not_allowed(response):
            logger.info("Zetaris API key rejected (Not Allowed); falling back to login token")
            self._cached_token = None
            token = self._login(timeout=timeout)
            self._cached_token = token
            self._auth_method = "login"
            response = self._post_json(url, body, token=token, timeout=timeout)

        if response.status_code >= 400:
            detail = self._error_detail(response)
            raise DataUnavailable(f"Zetaris HTTP {response.status_code}{detail}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise DataUnavailable("Zetaris returned non-JSON") from exc

        return self._extract_rows(payload)

    def _resolve_token(self, *, timeout: float) -> str:
        if self._cached_token:
            return self._cached_token
        api_key = _secret_text(self._settings.zetaris_api_key).strip()
        if api_key:
            self._auth_method = "api_key"
            return api_key
        token = self._login(timeout=timeout)
        self._cached_token = token
        self._auth_method = "login"
        return token

    def _login(self, *, timeout: float) -> str:
        base = self._base_url()
        if not base:
            raise DataUnavailable("ZETARIS_BASE_URL is not configured for login")
        user = (self._settings.zetaris_user or "").strip()
        password = _secret_text(self._settings.zetaris_password)
        if not user or not password:
            raise DataUnavailable("Zetaris login requires ZETARIS_USER and ZETARIS_PASSWORD")
        url = f"{base}{_LOGIN_PATH}"
        # Never log username/password/token.
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    url,
                    json={"username": user, "password": password},
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise DataUnavailable(f"Zetaris login transport error: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise DataUnavailable(f"Zetaris login HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DataUnavailable("Zetaris login returned non-JSON") from exc
        if not isinstance(payload, dict):
            raise DataUnavailable("Zetaris login response is not an object")
        token = (
            payload.get("idToken")
            or payload.get("token")
            or payload.get("accessToken")
            or ""
        )
        if not isinstance(token, str) or not token.strip():
            raise DataUnavailable("Zetaris login response missing idToken")
        return token.strip()

    def _post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        token: str,
        timeout: float,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Org-ID": str(self._settings.zetaris_org_id or "1"),
            "X-Request-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                return client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise DataUnavailable(f"Zetaris transport error: {type(exc).__name__}") from exc

    @staticmethod
    def _is_not_allowed(response: httpx.Response) -> bool:
        try:
            payload = response.json()
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        msg = str(payload.get("msg") or payload.get("message") or "")
        return msg.strip().lower() == "not allowed"

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Safe short error snippet (never includes Authorization / tokens)."""
        try:
            payload = response.json()
        except ValueError:
            text = (response.text or "")[:160].replace("\n", " ")
            return f": {text}" if text else ""
        if not isinstance(payload, dict):
            return ""
        msg = payload.get("msg") or payload.get("message") or payload.get("error")
        if msg is None:
            return ""
        return f": {str(msg)[:160]}"

    @staticmethod
    def _extract_rows(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise DataUnavailable("Zetaris response must be a JSON object")
        # Prefer documented "headers"; accept a few alternate shapes seen in the wild.
        headers = (
            payload.get("headers")
            or payload.get("columns")
            or payload.get("columnNames")
            or payload.get("fields")
        )
        if headers is None and isinstance(payload.get("schema"), dict):
            headers = payload["schema"].get("fields") or payload["schema"].get("columns")
        if headers is None:
            raise DataUnavailable(
                "Zetaris response missing headers (column names required to map data rows); "
                f"keys={sorted(payload.keys())}"
            )
        if not isinstance(headers, list) or not headers:
            raise DataUnavailable("Zetaris headers must be a non-empty list")

        names: list[str] = []
        for item in headers:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("columnName") or item.get("field")
                if name is None:
                    raise DataUnavailable(
                        "Zetaris header entries must be strings or {name: ...}"
                    )
                names.append(str(name))
            else:
                raise DataUnavailable("Zetaris header entries must be strings or {name: ...}")

        data = payload.get("data")
        if data is None:
            data = payload.get("rows") or payload.get("result")
        if data is None:
            return []
        if not isinstance(data, list):
            raise DataUnavailable("Zetaris data must be a list of row arrays")

        rows: list[dict[str, Any]] = []
        for raw in data:
            if isinstance(raw, dict):
                # Some proxies return objects; coerce to header order as strings.
                rows.append(
                    {
                        name: None if raw.get(name) is None else str(raw.get(name))
                        for name in names
                    }
                )
                continue
            if not isinstance(raw, list):
                raise DataUnavailable("Zetaris data rows must be arrays")
            # Pad / trim to header width; keep values as strings (API contract).
            cells: list[Any] = []
            for index in range(len(names)):
                if index < len(raw):
                    value = raw[index]
                    cells.append(None if value is None else str(value))
                else:
                    cells.append(None)
            rows.append(dict(zip(names, cells, strict=True)))
        return rows
