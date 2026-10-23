"""Name normalization, nicknames, and transliteration for identity scoring."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_VARIANTS_PATH = _ROOT / "config" / "name_variants.yaml"
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U0001F1E0-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)
_COMPANY_PAREN_RE = re.compile(r"\(([^)]+)\)\s*$")


@lru_cache(maxsize=1)
def load_name_variants(path: str | None = None) -> list[frozenset[str]]:
    """Load nickname/transliteration clusters as frozensets of normalized forms."""
    target = Path(path) if path else _VARIANTS_PATH
    if not target.exists():
        return []
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    clusters: list[frozenset[str]] = []
    for group in raw.get("variants") or []:
        norms = {normalize_name_token(str(item)) for item in group if str(item).strip()}
        if norms:
            clusters.append(frozenset(norms))
    return clusters


def strip_accents(text: str) -> str:
    """Remove combining marks (Élie -> Elie) while keeping non-Latin letters."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name_token(token: str) -> str:
    """Lowercase, strip accents/emoji/punctuation for one name token."""
    cleaned = _EMOJI_RE.sub("", token or "")
    cleaned = strip_accents(cleaned)
    cleaned = cleaned.lower().strip()
    cleaned = re.sub(r"[^\w\s]", "", cleaned, flags=re.UNICODE)
    return cleaned.strip()


def normalize_person_name(name: str) -> tuple[str, str | None]:
    """Return (normalized full name, optional company hint from '(Company)')."""
    raw = (name or "").strip()
    company: str | None = None
    match = _COMPANY_PAREN_RE.search(raw)
    if match:
        company = match.group(1).strip()
        raw = _COMPANY_PAREN_RE.sub("", raw).strip()
    raw = _EMOJI_RE.sub("", raw)
    raw = strip_accents(raw)
    tokens = [normalize_name_token(t) for t in raw.split() if normalize_name_token(t)]
    return " ".join(tokens), company


def name_variant_set(token: str) -> set[str]:
    """All known variant forms for a token, including itself."""
    norm = normalize_name_token(token)
    out = {norm} if norm else set()
    for cluster in load_name_variants():
        if norm in cluster:
            out |= set(cluster)
    return out


def names_match_normalized(a: str, b: str) -> bool:
    """True when normalized names match allowing variant tokens in order."""
    na, _ = normalize_person_name(a)
    nb, _ = normalize_person_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta = na.split()
    tb = nb.split()
    if len(ta) != len(tb):
        # Allow first+last vs full if every token of the shorter matches a variant of the longer.
        shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        used: set[int] = set()
        for token in shorter:
            variants = name_variant_set(token)
            found = False
            for index, other in enumerate(longer):
                if index in used:
                    continue
                if other in variants or bool(variants & name_variant_set(other)):
                    used.add(index)
                    found = True
                    break
            if not found:
                return False
        return True
    for left, right in zip(ta, tb, strict=True):
        if left == right:
            continue
        if not (name_variant_set(left) & name_variant_set(right)):
            return False
    return True


def parse_display_name(display: str) -> dict[str, Any]:
    """Structured display-name parse for scoring."""
    normalized, company = normalize_person_name(display)
    return {
        "raw": display,
        "normalized": normalized,
        "company_hint": company,
        "tokens": normalized.split() if normalized else [],
    }
