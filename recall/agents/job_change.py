"""Classify LinkedIn (and other) job_change evidence against resolved company context."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from recall.schemas import Brief, Evidence, TalkingPoint

_AT_COMPANY = re.compile(r"\bat\s+(.+?)\s*$", re.IGNORECASE)
_JOB_CHANGE_WINDOW_DAYS = 60


def company_from_job_change_text(text: str) -> str | None:
    """Extract employer after 'at …' from a job_change snippet."""
    cleaned = " ".join(text.strip().rstrip(".").split())
    match = _AT_COMPANY.search(cleaned)
    if not match:
        return None
    return match.group(1).strip()


def non_linkedin_corpus(evidence: dict[str, Evidence]) -> str:
    """Lowercased text from every non-LinkedIn evidence item."""
    parts = [item.text for item in evidence.values() if item.source != "linkedin"]
    return " ".join(parts).lower()


def classify_job_changes(
    evidence: dict[str, Evidence],
    *,
    now: datetime | None = None,
) -> tuple[list[Evidence], list[Evidence]]:
    """Split job_change items into (matching, trap) vs other evidence companies.

    Matching: tagged job_change, within 60 days, company appears in non-LinkedIn text.
    Trap: tagged job_change whose company does not appear in non-LinkedIn text.
    """
    if now is None:
        from recall.agents.time_context import now as frozen_now

        clock = frozen_now()
    else:
        clock = now
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    cutoff = clock - timedelta(days=_JOB_CHANGE_WINDOW_DAYS)
    corpus = non_linkedin_corpus(evidence)
    matching: list[Evidence] = []
    traps: list[Evidence] = []
    for item in evidence.values():
        if "job_change" not in item.tags:
            continue
        company = company_from_job_change_text(item.text)
        if not company:
            traps.append(item)
            continue
        ts = item.ts if item.ts.tzinfo else item.ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        if company.lower() in corpus:
            matching.append(item)
        else:
            traps.append(item)
    matching.sort(key=lambda e: e.ts, reverse=True)
    traps.sort(key=lambda e: e.ts, reverse=True)
    return matching, traps


def job_change_guidance(evidence: dict[str, Evidence], *, now: datetime | None = None) -> str:
    """Structured lines for writer/critic prompts about which job_changes to use."""
    matching, traps = classify_job_changes(evidence, now=now)
    lines = ["JOB_CHANGE_RULES:"]
    if matching:
        for item in matching:
            company = company_from_job_change_text(item.text) or "?"
            lines.append(
                f"- MUST congratulate citing only [{item.id}] "
                f"(company {company} matches other evidence)."
            )
    else:
        lines.append("- No matching job_change within 60 days.")
    if traps:
        trap_ids = ", ".join(f"[{item.id}]" for item in traps)
        lines.append(
            f"- IGNORE / DROP these wrong-person job_changes (do not cite or caveat): {trap_ids}."
        )
    return "\n".join(lines)


def trap_ids(evidence: dict[str, Evidence], *, now: datetime | None = None) -> set[str]:
    """Evidence ids that are wrong-company job_changes."""
    _, traps = classify_job_changes(evidence, now=now)
    return {item.id for item in traps}


def matching_ids(evidence: dict[str, Evidence], *, now: datetime | None = None) -> list[str]:
    """Evidence ids for matching recent job_changes (newest first)."""
    matching, _ = classify_job_changes(evidence, now=now)
    return [item.id for item in matching]


def sanitize_brief_job_changes(brief: Brief, evidence: dict[str, Evidence]) -> Brief:
    """Drop trap citations/caveats; ensure one congratulation for a matching job_change."""
    traps = trap_ids(evidence)
    matches = matching_ids(evidence)
    points: list[TalkingPoint] = []
    cited_match = False
    for point in brief.points:
        kept_ids = [eid for eid in point.evidence_ids if eid not in traps]
        if not kept_ids:
            continue
        if any(eid in matches for eid in kept_ids):
            cited_match = True
        points.append(point.model_copy(update={"evidence_ids": kept_ids}))

    if matches and not cited_match:
        mid = matches[0]
        item = evidence[mid]
        company = company_from_job_change_text(item.text) or "their new role"
        congrats = TalkingPoint(
            text=f"Congrats on the new role at {company}.",
            evidence_ids=[mid],
            kind="news",
            priority=1,
        )
        points = [congrats, *points]

    bad_tokens = set()
    for tid in traps:
        company = company_from_job_change_text(evidence[tid].text)
        if company:
            bad_tokens.add(company.lower())
    caveats = [
        c
        for c in brief.caveats
        if not any(token in c.lower() for token in bad_tokens)
        and not any(tid in c for tid in traps)
    ]
    return brief.model_copy(update={"points": points, "caveats": caveats})
