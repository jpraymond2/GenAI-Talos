"""Public-web context for entities. Sample mode uses a committed fixture."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from recall.config import Settings, get_settings
from recall.llm.generator import LLMUnavailable, LLMOutputError, generate_json
from recall.schemas import Evidence
from recall.tracing import Tracer

logger = logging.getLogger(__name__)

_WEB_TIMEOUT_S = 15.0
_MAX_RESULTS = 3
_CACHE_PATH = Path("data/synthetic/web_cache.json")


class WebExtract(BaseModel):
    """LLM extract shape for a single web hit."""

    text: str = Field(default="")
    confidence: float = Field(default=0.5)


async def scout(
    *,
    full_name: str,
    org: str | None = None,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
) -> list[dict[str, Any]]:
    """Return web fact dicts ``{url,title,snippet,text,source_ref,tags,ts}``.

    Sample mode reads ``data/synthetic/web_cache.json`` (no network). Live mode
    calls Tavily when ``TAVILY_API_KEY`` is set; failures yield an empty list
    and a ``web_scout_unavailable`` trace.
    """
    active = settings if settings is not None else get_settings()
    active_tracer = tracer if tracer is not None else Tracer.current()
    name = (full_name or "").strip()
    company = (org or "").strip()
    if not name:
        return []

    try:
        if active.sample_mode or not active.tavily_api_key.get_secret_value():
            return _from_cache(name, company, tracer=active_tracer)
        return await _from_tavily(name, company, settings=active, tracer=active_tracer)
    except Exception as exc:  # noqa: BLE001 — scout must never break retrieve
        logger.exception("web_scout failed")
        if active_tracer is not None:
            active_tracer.event(
                "orchestrator",
                "web_scout_unavailable",
                {"summary": f"web_scout_unavailable: {exc}", "error": str(exc)},
            )
        return []


def _from_cache(
    full_name: str,
    org: str,
    *,
    tracer: Tracer | None,
) -> list[dict[str, Any]]:
    path = _CACHE_PATH
    if not path.is_file():
        if tracer is not None:
            tracer.event(
                "orchestrator",
                "web_scout_unavailable",
                {"summary": "web_scout_unavailable: missing cache"},
            )
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    keys: list[str] = []
    if org:
        for key in payload:
            if key.lower() == org.lower():
                keys = [key]
                break
    if not keys:
        surname = full_name.split()[-1] if full_name.split() else ""
        for key in payload:
            if org and org.lower() in key.lower():
                keys.append(key)
            elif surname and surname.lower() in str(payload.get(key)).lower():
                keys.append(key)
        if org and org in payload:
            keys = [org]

    out: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for key in keys or list(payload.keys()):
        for raw in payload.get(key) or []:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "")
            snippet = str(raw.get("snippet") or "")
            url = str(raw.get("url") or "")
            if not url or url in seen_urls:
                continue
            # Prefer entries that mention the person surname when we have one.
            surname = full_name.split()[-1] if full_name.split() else ""
            blob = f"{title} {snippet}".lower()
            if surname and len(surname) > 2 and surname.lower() not in blob and org:
                # Org-keyed cache: keep even without surname in snippet.
                pass
            seen_urls.add(url)
            out.append(
                {
                    "url": url,
                    "title": title,
                    "snippet": snippet,
                    "text": f"{title}. {snippet}".strip(),
                    "source_ref": url,
                    "ts": str(raw.get("published") or "") or None,
                    "tags": ["web", "cache"],
                }
            )
            if len(out) >= _MAX_RESULTS:
                return out
    return out


async def _from_tavily(
    full_name: str,
    org: str,
    *,
    settings: Settings,
    tracer: Tracer | None,
) -> list[dict[str, Any]]:
    key = settings.tavily_api_key.get_secret_value()
    if not key:
        if tracer is not None:
            tracer.event(
                "orchestrator",
                "web_scout_unavailable",
                {"summary": "web_scout_unavailable: no TAVILY_API_KEY"},
            )
        return []
    surname = full_name.split()[-1] if full_name.split() else full_name
    query = f'"{full_name}"'
    if org:
        query = f'{query} "{org}"'
    try:
        async with httpx.AsyncClient(timeout=_WEB_TIMEOUT_S) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": query,
                    "max_results": _MAX_RESULTS,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        if tracer is not None:
            tracer.event(
                "orchestrator",
                "web_scout_unavailable",
                {"summary": f"web_scout_unavailable: {exc}", "error": str(exc)},
            )
        return []

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []

    filtered: list[dict[str, Any]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "")
        snippet = str(row.get("content") or row.get("snippet") or "")
        blob = f"{title} {snippet}".lower()
        if surname and surname.lower() not in blob:
            continue
        if org and org.lower() not in blob:
            continue
        url = str(row.get("url") or "")
        if not url:
            continue
        text = f"{title}. {snippet}".strip()
        try:
            extracted = await generate_json(
                f"Person: {full_name}\nOrg: {org}\nTitle: {title}\nSnippet: {snippet}",
                "Extract a one-sentence public fact about this person/org. "
                "Return JSON with fields text (str) and confidence (float 0-1).",
                WebExtract,
                role="web_extract",
            )
            if isinstance(extracted, WebExtract) and extracted.text:
                text = extracted.text
        except (LLMUnavailable, LLMOutputError, Exception):  # noqa: BLE001
            pass
        filtered.append(
            {
                "url": url,
                "title": title,
                "snippet": snippet,
                "text": text,
                "source_ref": url,
                "ts": None,
                "tags": ["web"],
            }
        )
        if len(filtered) >= _MAX_RESULTS:
            break
    return filtered


def evidence_from_facts(facts: list[dict[str, Any]], *, start_idx: int = 1) -> list[Evidence]:
    """Convert scout dicts into Evidence with ``source='web'``."""
    now = datetime.now(timezone.utc)
    out: list[Evidence] = []
    for i, fact in enumerate(facts, start=start_idx):
        out.append(
            Evidence(
                id=f"w{i}",
                source="web",
                source_ref=str(fact.get("source_ref") or fact.get("url") or f"w{i}"),
                ts=now,
                text=str(fact.get("text") or ""),
                url=fact.get("url"),
                tags=list(fact.get("tags") or ["web"]),
            )
        )
    return out
