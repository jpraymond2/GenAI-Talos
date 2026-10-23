"""Quoted-reply stripping before content / name extraction."""

from __future__ import annotations

import re

_ON_WROTE_RE = re.compile(
    r"(?im)^(?:on\s.+wrote:|le\s.+a\s[ée]crit\s*:)\s*$"
)
_ORIG_MSG_RE = re.compile(
    r"(?im)^-{2,}\s*original message\s*-{2,}\s*$|"
    r"^_{5,}\s*$|"
    r"^begin forwarded message\s*:?\s*$|"
    r"^forwarded message\s*:?\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)
_QUOTE_LINE_RE = re.compile(r"(?m)^>.*(?:\n|$)")


def strip_quoted_replies(body: str) -> str:
    """Remove quoted replies and forwarded blocks; keep the new content."""
    if not body:
        return ""
    text = body.replace("\r\n", "\n")
    # Cut at classic quote headers.
    for pattern in (_ON_WROTE_RE, _ORIG_MSG_RE):
        match = pattern.search(text)
        if match:
            text = text[: match.start()]
    # Drop remaining >-quoted lines.
    text = _QUOTE_LINE_RE.sub("", text)
    # Collapse blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
