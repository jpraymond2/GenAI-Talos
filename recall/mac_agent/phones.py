"""E.164 phone normalization (default region CA)."""

from __future__ import annotations

import re

import phonenumbers
from phonenumbers import NumberParseException


def normalize_e164(value: str, *, default_region: str = "CA") -> str:
    """Normalize a phone / WhatsApp JID local-part to E.164, or '' if unparsable."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    digits = re.sub(r"[^\d+]", "", raw)
    if not digits:
        return ""
    try:
        parsed = phonenumbers.parse(digits, default_region)
    except NumberParseException:
        # Fallback: keep +digits if we already have a long digit string.
        only = re.sub(r"\D", "", raw)
        return f"+{only}" if len(only) >= 8 else ""
    if not phonenumbers.is_possible_number(parsed):
        only = re.sub(r"\D", "", raw)
        return f"+{only}" if len(only) >= 8 else ""
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
