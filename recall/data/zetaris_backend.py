"""Zetaris REST backend. See docs/zetaris_notes.md for the live API contract."""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import uuid
from typing import Any, ClassVar

import httpx
from pydantic import SecretStr

from recall.config import DataBackend, Settings, ZetarisReadMode
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
# Hard stop when a query has no LIMIT of its own (safety against infinite pages).
_MAX_UNLIMITED_ROWS = 100_000
_SQL_RUN_PATH = "/sql-editor/sqls/run"
_LOGIN_PATH = "/api/auth/login"
# When JWT has no exp claim, refresh login on this wall-clock TTL.
_DEFAULT_TOKEN_TTL_S = 50 * 60
_TOKEN_SKEW_S = 60
_CONNECT_TIMEOUT_S = 2.0
_CIRCUIT_COOLDOWN_S = 60.0

# Trailing ``LIMIT n`` (optionally with ``OFFSET m``) outside string literals.
_TRAILING_LIMIT = re.compile(
    r"(?is)\s+LIMIT\s+(\d+)(?:\s+OFFSET\s+(\d+))?\s*$"
)

# Module-level circuit: after ConnectError/transport failure, skip Zetaris for 60s.
_circuit_lock = threading.Lock()
_circuit_open_until: float = 0.0


def zetaris_circuit_is_open() -> bool:
    """True while Zetaris is short-circuited to DuckDB after a transport failure."""
    return time.time() < _circuit_open_until


def trip_zetaris_circuit() -> None:
    """Mark Zetaris down until ``now + _CIRCUIT_COOLDOWN_S``."""
    global _circuit_open_until
    with _circuit_lock:
        _circuit_open_until = time.time() + _CIRCUIT_COOLDOWN_S
        logger.warning(
            "Zetaris circuit open for %.0fs (connect/transport failure)",
            _CIRCUIT_COOLDOWN_S,
        )


def clear_zetaris_circuit() -> None:
    """Reset the circuit breaker (tests / settings changes)."""
    global _circuit_open_until
    with _circuit_lock:
        _circuit_open_until = 0.0


def _http_timeout(read_seconds: float) -> httpx.Timeout:
    """Fail-fast connect (2s); keep the configured read timeout."""
    return httpx.Timeout(read_seconds, connect=_CONNECT_TIMEOUT_S)


def _is_transport_failure(exc: BaseException) -> bool:
    """Connect / network failures that should trip the circuit (not slow reads)."""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    if isinstance(exc, httpx.NetworkError):
        return True
    # httpx wraps some failures as generic TransportError.
    if isinstance(exc, httpx.TransportError) and not isinstance(
        exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)
    ):
        return True
    return False


class ZetarisBackend:
    """Remote Spark SQL reads against Zetaris ``sql-editor/sqls/run``."""

    _shared_instance: ClassVar[ZetarisBackend | None] = None
    _shared_lock: ClassVar[threading.Lock] = threading.Lock()
    _http: ClassVar[httpx.Client | None] = None
    _token: ClassVar[str | None] = None
    _token_expires_at: ClassVar[float | None] = None
    _auth_method_shared: ClassVar[str | None] = None

    def __init__(self, settings: Settings, tracer: Tracer | None = None) -> None:
        """Bind settings and an optional tracer for successful query events."""
        self._settings = settings
        self._tracer = tracer
        self._backend_name = "zetaris"
        self._cached_token: str | None = None
        self._auth_method: str | None = None  # "api_key" | "login"

    @classmethod
    def shared(cls, settings: Settings, tracer: Tracer | None = None) -> ZetarisBackend:
        """Reuse one backend so login token + keep-alive client span asks."""
        with cls._shared_lock:
            if cls._shared_instance is None:
                cls._shared_instance = cls(settings, tracer=tracer)
            else:
                cls._shared_instance._settings = settings
                cls._shared_instance._tracer = tracer
            return cls._shared_instance

    @classmethod
    def clear_shared(cls) -> None:
        """Drop process-wide client/token (tests / settings changes)."""
        with cls._shared_lock:
            if cls._http is not None:
                try:
                    cls._http.close()
                except Exception:  # noqa: BLE001
                    pass
            cls._http = None
            cls._token = None
            cls._token_expires_at = None
            cls._auth_method_shared = None
            cls._shared_instance = None
        clear_zetaris_circuit()

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
            if self._settings.data_backend is not DataBackend.ZETARIS or not rest:
                raise DataUnavailable(
                    "Zetaris skipped in SAMPLE_MODE (need DATA_BACKEND=zetaris and ZETARIS_REST_URL)"
                )

        if not rest:
            raise DataUnavailable("ZETARIS_REST_URL is not configured")

        mode = self._read_mode()
        try:
            rendered, usl_meta = prepare_zetaris_sql(
                sql,
                params,
                schema=self._settings.zetaris_schema or "recall",
                read_mode=mode.value,
                usl_namespace=self._settings.zetaris_usl_namespace
                or "lightning.metastore.recallai.recall",
            )
        except DataUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DataUnavailable(f"Zetaris SQL prepare failed: {type(exc).__name__}") from exc

        rows = self._fetch_all_pages(rest, rendered)
        extra: dict[str, Any] = {"zetaris_mode": mode.value}
        if mode is ZetarisReadMode.USL:
            extra["usl_tables"] = list(usl_meta.usl_tables) if usl_meta else []
        timed_query_event(
            self._tracer,
            backend=self._backend_name,
            source_tag=source_tag,
            sql=sql,
            row_count=len(rows),
            started=started,
            extra=extra,
        )
        return rows

    def _read_mode(self) -> ZetarisReadMode:
        mode = self._settings.zetaris_read_mode
        if isinstance(mode, ZetarisReadMode):
            return mode
        text = str(mode or "usl").strip().lower()
        return ZetarisReadMode.TABLES if text == "tables" else ZetarisReadMode.USL

    def run_raw(
        self,
        sql: str,
        *,
        source_tag: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST one SQL statement as-is (allows COMPILE USL internal semicolons)."""
        started = time.perf_counter()
        rest = self._rest_url()
        if not rest:
            raise DataUnavailable("ZETARIS_REST_URL is not configured")
        text = sql.strip().rstrip(";").strip()
        if not text:
            raise DataUnavailable("Zetaris SQL is empty")
        active_timeout = float(timeout) if timeout is not None else float(
            self._settings.http_timeout_seconds
        )
        payload = self._request_payload_with_retries(
            rest, text, timeout=active_timeout, full_errors=True
        )
        row_count = 0
        try:
            row_count = len(self._extract_rows(payload))
        except DataUnavailable:
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                row_count = len(data)
        timed_query_event(
            self._tracer,
            backend=self._backend_name,
            source_tag=source_tag,
            sql=text[:500],
            row_count=row_count,
            started=started,
        )
        return payload if isinstance(payload, dict) else {"data": payload}

    def _rest_url(self) -> str:
        return (
            self._settings.zetaris_rest_url
            or self._settings.zetaris_url
            or ""
        ).strip().rstrip("/")

    def _base_url(self) -> str:
        return (self._settings.zetaris_base_url or "").strip().rstrip("/")

    def _fetch_all_pages(self, rest: str, sql: str) -> list[dict[str, Any]]:
        """Page past the REST 1000-row cap via SQL LIMIT/OFFSET.

        Respects a trailing ``LIMIT n`` on the query (query's own cap). Stops when
        a page returns fewer rows than requested.
        """
        max_rows, base_sql = _split_trailing_limit(sql)
        page_size = _DEFAULT_LIMIT
        offset = 0
        out: list[dict[str, Any]] = []
        while True:
            if max_rows is not None and len(out) >= max_rows:
                break
            if max_rows is None and len(out) >= _MAX_UNLIMITED_ROWS:
                break
            take = page_size
            if max_rows is not None:
                take = min(page_size, max_rows - len(out))
            if take <= 0:
                break
            page_sql = f"{base_sql}\nLIMIT {take} OFFSET {offset}"
            page = self._request_with_retries(rest, page_sql)
            out.extend(page)
            if len(page) < take:
                break
            offset += len(page)
        return out

    def _request_with_retries(self, rest: str, sql: str) -> list[dict[str, Any]]:
        payload = self._request_payload_with_retries(
            rest, sql, timeout=float(self._settings.http_timeout_seconds)
        )
        return self._extract_rows(payload)

    def _request_payload_with_retries(
        self,
        rest: str,
        sql: str,
        *,
        timeout: float,
        full_errors: bool = False,
    ) -> Any:
        if zetaris_circuit_is_open():
            raise DataUnavailable("Zetaris circuit open")
        attempts = _ZETARIS_RETRIES + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._run_sql_payload(
                    rest, sql, timeout=timeout, full_errors=full_errors
                )
            except DataUnavailable as exc:
                last_error = exc
                # Never retry ConnectError / transport failures (circuit already tripped).
                if zetaris_circuit_is_open() or "transport error" in str(exc).lower():
                    raise
                if attempt + 1 >= attempts:
                    break
        raise DataUnavailable(str(last_error) if last_error else "Zetaris request failed")

    def _run_sql_payload(
        self,
        rest: str,
        sql: str,
        *,
        timeout: float,
        full_errors: bool,
    ) -> Any:
        token = self._resolve_token(timeout=timeout)
        url = f"{rest}{_SQL_RUN_PATH}"
        body = {
            "queryId": str(uuid.uuid4()),
            "sql": sql,
            "source": "SqlEditor",
            "limit": _DEFAULT_LIMIT,
        }
        response = self._post_json(url, body, token=token, timeout=timeout)

        if response.status_code == 400 and self._is_not_allowed(response):
            logger.info("Zetaris API key rejected (Not Allowed); falling back to login token")
            self._clear_token()
            # Login may ConnectError — do not retry login on transport failure.
            token = self._login(timeout=timeout)
            self._store_token(token, method="login")
            response = self._post_json(url, body, token=token, timeout=timeout)

        if response.status_code in {401, 403}:
            self._clear_token()
            token = self._login(timeout=timeout)
            self._store_token(token, method="login")
            response = self._post_json(url, body, token=token, timeout=timeout)

        if response.status_code >= 400:
            if full_errors:
                raise DataUnavailable(self._full_error(response))
            detail = self._error_detail(response)
            raise DataUnavailable(f"Zetaris HTTP {response.status_code}{detail}")

        try:
            return response.json()
        except ValueError as exc:
            raise DataUnavailable("Zetaris returned non-JSON") from exc

    def _resolve_token(self, *, timeout: float) -> str:
        if self._cached_token and self._token_still_valid():
            return self._cached_token
        with type(self)._shared_lock:
            shared = type(self)._token
            if shared and self._shared_token_still_valid():
                self._cached_token = shared
                self._auth_method = type(self)._auth_method_shared
                return shared
        api_key = _secret_text(self._settings.zetaris_api_key).strip()
        if api_key:
            self._store_token(api_key, method="api_key", expires_at=None)
            return api_key
        token = self._login(timeout=timeout)
        self._store_token(token, method="login")
        return token

    def _token_still_valid(self) -> bool:
        exp = type(self)._token_expires_at
        if exp is None:
            return bool(self._cached_token)
        return time.time() < (exp - _TOKEN_SKEW_S)

    def _shared_token_still_valid(self) -> bool:
        if type(self)._token is None:
            return False
        exp = type(self)._token_expires_at
        if exp is None:
            return True
        return time.time() < (exp - _TOKEN_SKEW_S)

    def _store_token(
        self,
        token: str,
        *,
        method: str,
        expires_at: float | None | object = ...,
    ) -> None:
        resolved: float | None
        if expires_at is ...:
            resolved = _infer_token_expiry(token)
        elif expires_at is None:
            resolved = None
        else:
            resolved = float(expires_at)  # type: ignore[arg-type]
        with type(self)._shared_lock:
            type(self)._token = token
            type(self)._token_expires_at = resolved
            type(self)._auth_method_shared = method
        self._cached_token = token
        self._auth_method = method

    def _clear_token(self) -> None:
        with type(self)._shared_lock:
            type(self)._token = None
            type(self)._token_expires_at = None
            type(self)._auth_method_shared = None
        self._cached_token = None
        self._auth_method = None

    def _http_client(self, timeout: float) -> httpx.Client:
        with type(self)._shared_lock:
            client = type(self)._http
            if client is None or client.is_closed:
                type(self)._http = httpx.Client(
                    timeout=_http_timeout(timeout),
                    limits=httpx.Limits(
                        max_keepalive_connections=8,
                        max_connections=16,
                        keepalive_expiry=30.0,
                    ),
                )
            assert type(self)._http is not None
            return type(self)._http

    def _raise_transport(self, where: str, exc: BaseException) -> None:
        """Trip the circuit on connect/transport errors; never retry login."""
        name = type(exc).__name__
        if _is_transport_failure(exc):
            trip_zetaris_circuit()
        raise DataUnavailable(f"Zetaris {where} transport error: {name}") from exc

    def _login(self, *, timeout: float) -> str:
        if zetaris_circuit_is_open():
            raise DataUnavailable("Zetaris circuit open")
        base = self._base_url()
        if not base:
            raise DataUnavailable("ZETARIS_BASE_URL is not configured for login")
        user = (self._settings.zetaris_user or "").strip()
        password = _secret_text(self._settings.zetaris_password)
        if not user or not password:
            raise DataUnavailable("Zetaris login requires ZETARIS_USER and ZETARIS_PASSWORD")
        url = f"{base}{_LOGIN_PATH}"
        try:
            client = self._http_client(timeout)
            response = client.post(
                url,
                json={"username": user, "password": password},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=_http_timeout(timeout),
            )
        except httpx.HTTPError as exc:
            self._raise_transport("login", exc)
            raise  # pragma: no cover — _raise_transport always raises
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
            client = self._http_client(timeout)
            return client.post(
                url, json=body, headers=headers, timeout=_http_timeout(timeout)
            )
        except httpx.HTTPError as exc:
            self._raise_transport("SQL", exc)
            raise  # pragma: no cover

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
    def _full_error(response: httpx.Response) -> str:
        """Full server error body for admin scripts (still never includes tokens)."""
        try:
            payload = response.json()
        except ValueError:
            text = (response.text or "").strip()
            return f"Zetaris HTTP {response.status_code}: {text[:4000]}"
        try:
            body = json.dumps(payload, indent=2, default=str)
        except TypeError:
            body = str(payload)
        return f"Zetaris HTTP {response.status_code}:\n{body[:8000]}"

    @staticmethod
    def _extract_rows(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise DataUnavailable("Zetaris response must be a JSON object")
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
                rows.append(
                    {
                        name: None if raw.get(name) is None else str(raw.get(name))
                        for name in names
                    }
                )
                continue
            if not isinstance(raw, list):
                raise DataUnavailable("Zetaris data rows must be arrays")
            cells: list[Any] = []
            for index in range(len(names)):
                if index < len(raw):
                    value = raw[index]
                    cells.append(None if value is None else str(value))
                else:
                    cells.append(None)
            rows.append(dict(zip(names, cells, strict=True)))
        return rows


def _split_trailing_limit(sql: str) -> tuple[int | None, str]:
    """Return ``(limit_or_none, sql_without_trailing_limit_offset)``."""
    match = _TRAILING_LIMIT.search(sql)
    if not match:
        return None, sql.rstrip()
    # Ignore LIMIT that sits inside a trailing string literal by requiring the
    # match to start outside quotes — _TRAILING_LIMIT only looks at the end.
    if _limit_match_inside_string(sql, match.start()):
        return None, sql.rstrip()
    return int(match.group(1)), sql[: match.start()].rstrip()


def _limit_match_inside_string(sql: str, index: int) -> bool:
    """True when ``index`` falls inside a single-quoted SQL string literal."""
    in_string = False
    i = 0
    while i < index:
        ch = sql[i]
        if in_string:
            if ch == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
        i += 1
    return in_string


def _infer_token_expiry(token: str) -> float:
    """Parse JWT ``exp`` when present; otherwise use a conservative TTL."""
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            pad = "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
            if isinstance(payload, dict) and payload.get("exp") is not None:
                return float(payload["exp"])
    except Exception:  # noqa: BLE001
        pass
    return time.time() + _DEFAULT_TOKEN_TTL_S
