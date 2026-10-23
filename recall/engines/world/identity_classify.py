"""Classify unlinked handles as person | organization | shared before scoring."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import yaml
from pathlib import Path

from recall.data.queries import InteractionRow
from recall.engines.world.anchors import normalize_email
from recall.engines.world.identity_text import strip_quoted_replies

_ROOT = Path(__file__).resolve().parents[3]
_SCORING_PATH = _ROOT / "config" / "identity_scoring.yaml"


def _load_scoring_config() -> dict[str, Any]:
    with _SCORING_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _signature_name(body: str) -> str:
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    for line in lines[-8:]:
        words = line.split()
        if 1 <= len(words) <= 4 and all(w[:1].isupper() for w in words if w.isalpha()):
            if "@" not in line and not re.search(r"\d{3}", line):
                return line
    return ""

HandleClass = Literal["person", "organization", "shared"]

_ROLE_DEFAULTS = frozenset(
    {
        "info",
        "team",
        "hello",
        "contact",
        "recruiting",
        "careers",
        "hr",
        "support",
        "noreply",
        "no-reply",
        "notifications",
        "billing",
        "admin",
    }
)


def classify_handle(
    handle_kind: str,
    handle_value: str,
    *,
    interactions: list[InteractionRow] | None = None,
    display_name: str = "",
    now: datetime | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[HandleClass, dict[str, Any]]:
    """Return (class, details) for an unlinked handle."""
    cfg = config or _load_scoring_config()
    roles = {str(r).lower() for r in (cfg.get("role_local_parts") or _ROLE_DEFAULTS)}
    details: dict[str, Any] = {"handle": handle_value, "kind": handle_kind}

    if handle_kind == "email":
        value = normalize_email(handle_value)
        local, _, domain = value.partition("@")
        local_base = local.split("+", 1)[0].lower()
        details["local"] = local_base
        details["domain"] = domain
        if local_base in roles or local_base.replace(".", "") in roles:
            details["reason"] = f"role local-part {local_base}"
            return "organization", details

    # Shared inbox: 2+ distinct signer names/signatures in 180 days.
    clock = now or datetime.now(timezone.utc)
    cutoff = clock - timedelta(days=180)
    signers: Counter[str] = Counter()
    for row in interactions or []:
        if normalize_email(row.handle_value) != normalize_email(handle_value):
            continue
        try:
            ts = datetime.fromisoformat(row.ts_utc.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        body = strip_quoted_replies(row.text or "")
        name = _signature_name(body)
        if not name and display_name:
            name = display_name.strip()
        if name and len(name.split()) >= 2:
            signers[name.lower()] += 1
    details["signers"] = dict(signers)
    if len(signers) >= 2:
        details["reason"] = "multiple distinct signatures"
        return "shared", details

    details["reason"] = "default person"
    return "person", details


def lookalike_brand_domain(domain: str, config: dict[str, Any] | None = None) -> str | None:
    """Return brand if domain is a lookalike (edit distance <= 2 or punycode)."""
    cfg = config or _load_scoring_config()
    brands = [str(b).lower() for b in (cfg.get("brand_domains") or [])]
    d = (domain or "").lower().strip(".")
    if "xn--" in d:  # punycode
        for brand in brands:
            root = brand.split(".", 1)[0]
            if root and root in d:
                return brand
    for brand in brands:
        if d == brand:
            return None
        # Compare registrable-ish labels
        d_label = d.split(".")[0]
        b_label = brand.split(".")[0]
        if _edit_distance(d_label, b_label) <= 2 and d_label != b_label:
            return brand
        # e.g. pvv.com vs pwc.com
        if d.endswith("." + brand.split(".", 1)[-1]) and _edit_distance(d_label, b_label) <= 2:
            if d_label != b_label:
                return brand
    return None


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (ca != cb)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]
