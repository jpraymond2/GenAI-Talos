"""Persist identity decisions, employment assumptions, and handle links."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from recall.config import Settings, get_settings
from recall.data.queries import InteractionRow, all_handles, all_interactions
from recall.data.query_layer import QueryLayer, get_query_layer
from recall.engines.world.anchors import (
    AnchorLink,
    anchor_handle_values,
    load_anchors,
    normalize_email,
    normalize_phone,
)
from recall.engines.world.identity_scoring import (
    ScoringDecision,
    decision_to_dict,
    score_handle,
)
from recall.engines.world.models import Entity
from recall.engines.world.store import WorldStore, world_db_path
from recall.tracing import Tracer


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return digest


class IdentityService:
    """Run identity scoring over unlinked handles and persist results."""

    def __init__(
        self,
        store: WorldStore | None = None,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
        query_layer: QueryLayer | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._store = store if store is not None else WorldStore(settings=self._settings)
        self._tracer = tracer if tracer is not None else Tracer.current()
        self._layer = query_layer

    def _ql(self) -> QueryLayer:
        if self._layer is not None:
            return self._layer
        return get_query_layer(self._settings, tracer=self._tracer)

    async def run_for_ingest(self, *, run_id: str | None = None) -> dict[str, int]:
        """Score every unlinked handle once per ingest run."""
        rid = run_id or (self._tracer.run_id if self._tracer else str(uuid4()))
        layer = self._ql()
        anchors = load_anchors(settings=self._settings, layer=layer, tracer=self._tracer)
        anchor_values = anchor_handle_values(anchors)
        handles = all_handles(source_tag="world.identity.handles", layer=layer)
        interactions = all_interactions(
            source_tag="world.identity.interactions",
            since_days=400,
            limit=5000,
            settings=self._settings,
            layer=layer,
        )
        anchor_names = {link.entity_name.strip().lower() for link in anchors}
        people = [
            p
            for p in self._store.list_entities(type="person")
            if p.canonical_name.strip().lower() in anchor_names
            or (p.attributes.get("status") or "").lower() == "new"
        ]
        # Always include all contact-anchored people; also keep named people from store
        # that match anchors even if status new.
        phone_index, handles_by_entity = self._build_indexes(people, anchors, handles)
        name_to_id = {p.canonical_name.lower(): p.id for p in people}
        anchor_entity_ids = {
            name_to_id[link.entity_name.lower()]
            for link in anchors
            if link.entity_name.lower() in name_to_id
        }
        employment_rows = self._store.list_employment_assumptions()

        candidates: dict[str, dict[str, str]] = {}
        for handle in handles:
            value = handle.value
            kind = handle.kind
            if kind == "email":
                value = normalize_email(value)
                if not value or value in anchor_values:
                    continue
                key = f"email:{value}"
            elif kind in {"phone", "imessage", "wa_jid"}:
                phone = normalize_phone(value) if kind != "wa_jid" else normalize_phone(
                    value.split("@", 1)[0]
                )
                if not phone or phone in anchor_values:
                    # Also skip if the raw value is anchored.
                    if value in anchor_values or (phone and phone in anchor_values):
                        continue
                    if not phone:
                        continue
                # Skip phones already linked as anchors via contact cards.
                if phone in anchor_values:
                    continue
                key = f"phone:{phone}"
                value = phone
                kind = "phone"
            else:
                continue
            candidates[key] = {
                "kind": kind,
                "value": value,
                "display_name": handle.display_name or "",
                "body": "",
                "ts": "",
            }

        for row in interactions:
            hv = row.handle_value or ""
            entry_key = ""
            kind = "email"
            value = ""
            if "@" in hv and "whatsapp.net" not in hv.lower() and row.source in {
                "gmail",
                "email",
                "linkedin",
            }:
                value = normalize_email(hv)
                if not value or value in anchor_values:
                    continue
                entry_key = f"email:{value}"
                kind = "email"
            elif row.source in {"imessage", "whatsapp", "sms"} or hv.startswith("+") or hv.isdigit():
                phone = normalize_phone(hv.split("@", 1)[0] if "@" in hv else hv)
                if not phone or phone in anchor_values:
                    continue
                entry_key = f"phone:{phone}"
                kind = "phone"
                value = phone
            else:
                continue
            entry = candidates.setdefault(
                entry_key,
                {
                    "kind": kind,
                    "value": value,
                    "display_name": "",
                    "body": "",
                    "ts": "",
                },
            )
            if row.direction == "in" and row.text:
                # Prefer newest inbound body.
                if not entry.get("ts") or row.ts_utc >= entry.get("ts", ""):
                    entry["body"] = row.text
                    entry["ts"] = row.ts_utc
            # Push name from WhatsApp/iMessage handle display when empty.
            if not entry.get("display_name"):
                for handle in handles:
                    hv_n = handle.value
                    if kind == "email" and normalize_email(hv_n) == value:
                        entry["display_name"] = handle.display_name or ""
                    elif kind == "phone":
                        hp = normalize_phone(
                            hv_n.split("@", 1)[0] if "@" in hv_n else hv_n
                        )
                        if hp == value and handle.display_name:
                            entry["display_name"] = handle.display_name

        for value_meta in candidates.values():
            body = value_meta.get("body") or ""
            first = body.splitlines()[0] if body else ""
            if "<" in first and value_meta.get("value", "") in first.lower():
                value_meta["display_name"] = first.split("<", 1)[0].strip().strip('"')

        scored = 0
        skipped_locked = 0
        profile_embedding_cache: dict[str, list[float]] = {}
        for _key, meta in sorted(candidates.items()):
            value = meta["value"]
            kind = meta["kind"]
            locked = self._store.user_locked_handle(value)
            if locked is not None:
                skipped_locked += 1
                continue
            rejected = self._store.rejected_entity_ids_for_handle(value)
            email_ts = None
            if meta.get("ts"):
                try:
                    email_ts = datetime.fromisoformat(meta["ts"].replace("Z", "+00:00"))
                except ValueError:
                    email_ts = None
            decision = await score_handle(
                kind,
                value,
                people=people,
                interactions=interactions,
                phone_index=phone_index,
                handles_by_entity=handles_by_entity,
                display_name=meta.get("display_name", ""),
                body=meta.get("body", ""),
                run_id=f"{rid}-{_stable_id(value)}",
                settings=self._settings,
                profile_embedding_cache=profile_embedding_cache,
                employment_rows=employment_rows,
                rejected_entity_ids=rejected,
                email_ts=email_ts,
                anchor_entity_ids=anchor_entity_ids,
            )
            self.persist_decision(decision, anchors=anchors)
            scored += 1

        if self._tracer is not None:
            self._tracer.event(
                "world",
                "identity_scoring",
                {
                    "summary": f"scored={scored} locked={skipped_locked} unlinked={len(candidates)}",
                    "scored": scored,
                    "locked": skipped_locked,
                    "unlinked": len(candidates),
                },
            )
        return {"scored": scored, "locked": skipped_locked, "unlinked": len(candidates)}

    async def score_one(
        self,
        handle_kind: str,
        handle_value: str,
        *,
        display_name: str = "",
        body: str = "",
        run_id: str | None = None,
    ) -> ScoringDecision:
        """Score a single handle (used by the bench and CLI)."""
        layer = self._ql()
        anchors = load_anchors(settings=self._settings, layer=layer, tracer=self._tracer)
        handles = all_handles(source_tag="world.identity.handles", layer=layer)
        interactions = all_interactions(
            source_tag="world.identity.interactions",
            since_days=400,
            limit=5000,
            settings=self._settings,
            layer=layer,
        )
        anchor_names = {link.entity_name.strip().lower() for link in anchors}
        people = [
            p
            for p in self._store.list_entities(type="person")
            if p.canonical_name.strip().lower() in anchor_names
            or (p.attributes.get("status") or "").lower() == "new"
        ]
        phone_index, handles_by_entity = self._build_indexes(people, anchors, handles)
        name_to_id = {p.canonical_name.lower(): p.id for p in people}
        anchor_entity_ids = {
            name_to_id[link.entity_name.lower()]
            for link in anchors
            if link.entity_name.lower() in name_to_id
        }
        value = (
            normalize_email(handle_value)
            if handle_kind == "email"
            else (normalize_phone(handle_value) or handle_value)
        )
        rejected = self._store.rejected_entity_ids_for_handle(value)
        # Prefer newest inbound body for this handle if not provided.
        if not body:
            for row in sorted(interactions, key=lambda r: r.ts_utc, reverse=True):
                hv = row.handle_value or ""
                if handle_kind == "email" and normalize_email(hv) == value and row.text:
                    body = row.text
                    break
                if handle_kind != "email":
                    phone = normalize_phone(hv.split("@", 1)[0] if "@" in hv else hv)
                    if phone == value and row.text:
                        body = row.text
                        break
        if not display_name:
            for handle in handles:
                if handle_kind == "email" and normalize_email(handle.value) == value:
                    display_name = handle.display_name or ""
                    break
                if handle_kind != "email":
                    hp = normalize_phone(
                        handle.value.split("@", 1)[0] if "@" in handle.value else handle.value
                    )
                    if hp == value:
                        display_name = handle.display_name or ""
                        break
        email_ts = None
        for row in interactions:
            hv = row.handle_value or ""
            match = (
                handle_kind == "email" and normalize_email(hv) == value
            ) or (
                handle_kind != "email"
                and normalize_phone(hv.split("@", 1)[0] if "@" in hv else hv) == value
            )
            if match:
                try:
                    email_ts = datetime.fromisoformat(row.ts_utc.replace("Z", "+00:00"))
                except ValueError:
                    email_ts = None
                break
        decision = await score_handle(
            handle_kind,
            value,
            people=people,
            interactions=interactions,
            phone_index=phone_index,
            handles_by_entity=handles_by_entity,
            display_name=display_name,
            body=body,
            run_id=run_id or str(uuid4()),
            settings=self._settings,
            employment_rows=self._store.list_employment_assumptions(),
            rejected_entity_ids=rejected,
            email_ts=email_ts,
            anchor_entity_ids=anchor_entity_ids,
        )
        self.persist_decision(decision, anchors=anchors)
        return decision

    def persist_decision(
        self,
        decision: ScoringDecision,
        *,
        anchors: list[AnchorLink] | None = None,
    ) -> None:
        """Supersede prior active rows, insert new assumptions, link, ledgers."""
        _ = anchors
        handle_value = decision.handle_value
        locked = self._store.user_locked_handle(handle_value)
        if locked is not None:
            return

        self._store.supersede_active_auto_assumptions(handle_value)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # new_person: create entity before writing assumptions.
        if decision.decision == "new_person" and not decision.top_entity_id:
            hint = decision.new_person_hint or {}
            name = str(hint.get("name") or handle_value)
            entity_id = f"e_new_{_stable_id(handle_value, name)}"
            existing = self._store.get_entity(entity_id)
            if existing is None:
                entity = Entity(
                    id=entity_id,
                    type="person",
                    canonical_name=name,
                    aliases=[handle_value],
                    attributes={
                        "status": "new",
                        "org": str(hint.get("org") or ""),
                        "title": str(hint.get("title") or ""),
                        "domain": str(hint.get("domain") or ""),
                    },
                    confidence=float(decision.top_probability),
                    first_seen_at=now,
                    last_seen_at=now,
                    mention_count=1,
                    agent_id="identity_scoring",
                    run_id=decision.run_id,
                )
                self._store.upsert_entity(entity)
            decision.top_entity_id = entity_id

        for cand in decision.candidates:
            row_id = (
                f"ia_{_stable_id(handle_value, cand.entity_id or 'none', decision.run_id, str(cand.rank))}"
                f"_{uuid4().hex[:8]}"
            )
            self._store.insert_identity_assumption(
                {
                    "id": row_id,
                    "handle_kind": decision.handle_kind,
                    "handle_value": handle_value,
                    "candidate_entity_id": cand.entity_id,
                    "raw_score": cand.raw_score,
                    "probability": cand.probability,
                    "rank": cand.rank,
                    "decision": decision.decision,
                    "status": "active",
                    "scoring_version": decision.scoring_version,
                    "run_id": decision.run_id,
                    "decided_at": now,
                    "score_file": decision.score_file,
                }
            )

        for cand in decision.candidates:
            if cand.entity_id is None:
                continue
            company = decision.parsed.company
            if not company:
                continue
            f3 = next((f for f in cand.features if f.name == "f3"), None)
            f4 = next((f for f in cand.features if f.name == "f4"), None)
            f5 = next((f for f in cand.features if f.name == "f5"), None)
            support = []
            if f3:
                support.extend(f3.source_refs)
            if f4:
                support.extend(f4.source_refs)
            parts = [f.value for f in (f3, f4, f5) if f is not None]
            emp_p = sum(parts) / max(len(parts), 1)
            self._store.upsert_employment_assumption(
                {
                    "id": f"ea_{_stable_id(cand.entity_id, company)}",
                    "entity_id": cand.entity_id,
                    "company": company,
                    "probability": emp_p,
                    "supporting_refs": support,
                    "contradicting_refs": [],
                    "scoring_version": decision.scoring_version,
                    "run_id": decision.run_id,
                    "updated_at": now,
                    "status": "active",
                    "valid_from": now,
                    "valid_to": "",
                }
            )

        if decision.decision in {"auto_linked", "tentative", "new_person"} and decision.top_entity_id:
            link_id = f"hl_{_stable_id(decision.top_entity_id, handle_value)}"
            status = {
                "auto_linked": "active",
                "tentative": "assumed",
                "new_person": "new",
            }.get(decision.decision, "assumed")
            self._store.upsert_handle_link(
                {
                    "id": link_id,
                    "entity_id": decision.top_entity_id,
                    "handle_kind": decision.handle_kind,
                    "handle_value": handle_value,
                    "confidence": decision.top_probability,
                    "status": status,
                    "source_ref": decision.score_file,
                    "created_at": now,
                }
            )
            self._store.add_entity_alias(decision.top_entity_id, handle_value)

        if decision.decision == "needs_user":
            self._enqueue_question(decision, now=now)

        self._append_ledgers(decision)

    def _enqueue_question(self, decision: ScoringDecision, *, now: str) -> None:
        """Queue a clarify question grouped by top candidate pair."""
        entities = [c for c in decision.candidates if c.entity_id]
        entities = sorted(entities, key=lambda c: c.probability, reverse=True)
        cand_a = entities[0].entity_id if entities else None
        cand_b = entities[1].entity_id if len(entities) > 1 else None
        names = [c.name for c in entities[:2]]
        if len(names) >= 2:
            question = (
                f"Is {decision.handle_value} {names[0]} or {names[1]}?"
            )
        elif names:
            question = f"Is {decision.handle_value} {names[0]}?"
        else:
            question = f"Who is {decision.handle_value}?"
        evidence_hash = _stable_id(
            decision.handle_value,
            decision.score_file,
            ",".join(decision.hard_rules),
            ",".join(decision.flags),
        )
        self._store.enqueue_identity_question(
            {
                "id": f"iq_{_stable_id(decision.handle_value, evidence_hash)}",
                "handle_kind": decision.handle_kind,
                "handle_value": decision.handle_value,
                "candidate_a": cand_a,
                "candidate_b": cand_b,
                "question": question,
                "status": "open",
                "evidence_hash": evidence_hash,
                "created_at": now,
                "resolved_at": "",
            }
        )

    def list_questions(self) -> list[dict[str, Any]]:
        """Open questions grouped by candidate pair for the CLI."""
        rows = self._store.list_identity_questions(status="open")
        groups: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
        for row in rows:
            key = (row.get("candidate_a"), row.get("candidate_b"))
            groups.setdefault(key, []).append(row)
        out: list[dict[str, Any]] = []
        for (a, b), items in groups.items():
            out.append(
                {
                    "candidate_a": a,
                    "candidate_b": b,
                    "count": len(items),
                    "handles": [i["handle_value"] for i in items],
                    "questions": items,
                }
            )
        return out

    def confirm(self, handle_value: str, entity_id: str) -> dict[str, Any]:
        """User confirms a link: probability 1.0, never auto-overridden."""
        value = normalize_email(handle_value) if "@" in handle_value else (
            normalize_phone(handle_value) or handle_value
        )
        entity = self._store.get_entity(entity_id)
        if entity is None:
            raise ValueError(f"unknown entity_id: {entity_id}")
        self._store.supersede_active_auto_assumptions(value)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = {
            "id": f"ia_{_stable_id(value, entity_id, 'confirm', now)}",
            "handle_kind": "email" if "@" in value else "phone",
            "handle_value": value,
            "candidate_entity_id": entity_id,
            "raw_score": 1.0,
            "probability": 1.0,
            "rank": 1,
            "decision": "auto_linked",
            "status": "confirmed_by_user",
            "scoring_version": 3,
            "run_id": "user-confirm",
            "decided_at": now,
            "score_file": "",
        }
        self._store.insert_identity_assumption(row)
        self._store.upsert_handle_link(
            {
                "id": f"hl_{_stable_id(entity_id, value)}",
                "entity_id": entity_id,
                "handle_kind": row["handle_kind"],
                "handle_value": value,
                "confidence": 1.0,
                "status": "confirmed",
                "source_ref": "user:confirm",
                "created_at": now,
            }
        )
        self._store.add_entity_alias(entity_id, value)
        return row

    def reject(self, handle_value: str, entity_id: str) -> dict[str, Any]:
        """User rejects a candidate link."""
        value = normalize_email(handle_value) if "@" in handle_value else (
            normalize_phone(handle_value) or handle_value
        )
        self._store.supersede_active_auto_assumptions(value)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = {
            "id": f"ia_{_stable_id(value, entity_id, 'reject', now)}",
            "handle_kind": "email" if "@" in value else "phone",
            "handle_value": value,
            "candidate_entity_id": entity_id,
            "raw_score": 0.0,
            "probability": 0.0,
            "rank": 1,
            "decision": "needs_user",
            "status": "rejected_by_user",
            "scoring_version": 3,
            "run_id": "user-reject",
            "decided_at": now,
            "score_file": "",
        }
        self._store.insert_identity_assumption(row)
        return row

    def resolve_handle(self, handle_value: str) -> dict[str, Any] | None:
        """Lookup anchors / assumptions for an email in an utterance."""
        value = normalize_email(handle_value) if "@" in handle_value else (
            normalize_phone(handle_value) or handle_value
        )
        anchors = load_anchors(settings=self._settings, layer=self._ql(), tracer=self._tracer)
        for link in anchors:
            linked = (
                normalize_email(link.handle_value)
                if link.handle_kind == "email"
                else normalize_phone(link.handle_value)
            )
            if linked == value:
                for entity in self._store.list_entities(type="person"):
                    if entity.canonical_name.lower() == link.entity_name.lower():
                        return {
                            "entity_id": entity.id,
                            "name": entity.canonical_name,
                            "probability": 1.0,
                            "status": "anchor",
                            "decision": "auto_linked",
                        }
        assumption = self._store.latest_identity_for_handle(value)
        if assumption is None:
            return None
        if assumption.get("status") == "rejected_by_user":
            return None
        entity_id = assumption.get("candidate_entity_id")
        if not entity_id:
            return {
                "entity_id": None,
                "name": None,
                "probability": float(assumption.get("probability") or 0),
                "status": assumption.get("status"),
                "decision": assumption.get("decision"),
            }
        entity = self._store.get_entity(str(entity_id))
        return {
            "entity_id": entity_id,
            "name": entity.canonical_name if entity else None,
            "probability": float(assumption.get("probability") or 0),
            "status": assumption.get("status"),
            "decision": assumption.get("decision"),
        }

    def _append_ledgers(self, decision: ScoringDecision) -> None:
        logs = Path(self._settings.logs_dir) / decision.run_id.split("-")[0]
        if self._tracer is not None:
            logs = Path(self._tracer.path).parent
        logs.mkdir(parents=True, exist_ok=True)
        record = decision_to_dict(decision)
        for name in ("identity_ledger.jsonl", "world_provenance.jsonl"):
            path = logs / name
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _build_indexes(
        self,
        people: list[Entity],
        anchors: list[AnchorLink],
        handles: list[Any],
    ) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """phone_index[entity_id] and handles_by_entity[entity_id]."""
        name_to_id = {p.canonical_name.lower(): p.id for p in people}
        phone_index: dict[str, set[str]] = {p.id: set() for p in people}
        handles_by_entity: dict[str, set[str]] = {p.id: set() for p in people}

        for link in anchors:
            eid = name_to_id.get(link.entity_name.lower())
            if not eid:
                for person in people:
                    if link.entity_name.lower() in person.canonical_name.lower():
                        eid = person.id
                        break
            if not eid:
                continue
            if link.handle_kind == "phone":
                phone_index[eid].add(normalize_phone(link.handle_value))
                handles_by_entity[eid].add(normalize_phone(link.handle_value))
            else:
                handles_by_entity[eid].add(normalize_email(link.handle_value))

        for person in people:
            for alias in person.aliases:
                if "@" in alias:
                    handles_by_entity[person.id].add(normalize_email(alias))
                elif alias.startswith("+") or alias.isdigit():
                    handles_by_entity[person.id].add(normalize_phone(alias))

        for handle in handles:
            display = (handle.display_name or "").lower()
            for person in people:
                if person.canonical_name.lower() == display or display in {
                    a.lower() for a in person.aliases
                }:
                    if handle.kind == "email":
                        handles_by_entity[person.id].add(normalize_email(handle.value))
                    else:
                        norm = normalize_phone(handle.value)
                        if norm:
                            handles_by_entity[person.id].add(norm)
                            if handle.kind in {"phone", "imessage", "wa_jid"}:
                                phone_index[person.id].add(norm)
        return phone_index, handles_by_entity


def format_explain_table(
    score_file: Path | str,
    *,
    handle_value: str | None = None,
) -> str:
    """Pretty table from a score JSONL record (or legacy single JSON file)."""
    from recall.engines.world.identity_scoring import load_identity_score_record

    data = load_identity_score_record(score_file, handle_value=handle_value)
    lines = [
        f"handle: {data.get('handle', {}).get('value')}",
        f"decision: {data.get('decision')}  version={data.get('scoring_version')}",
        f"company: {data.get('parsed', {}).get('company')}  "
        f"industry: {data.get('parsed', {}).get('industry')}",
        "",
        f"{'candidate':28} {'raw':>6} {'prob%':>7}  features",
    ]
    for cand in data.get("candidates", []):
        name = cand.get("name") or "(none)"
        feats = ", ".join(
            f"{f['name']}={f['value']:.2f}→{f['contribution']:.3f}"
            for f in cand.get("features", [])
        )
        lines.append(
            f"{name[:28]:28} {cand.get('raw_score', 0):6.3f} "
            f"{cand.get('probability_pct', cand.get('probability', 0) * 100):7.2f}  {feats}"
        )
        for feat in cand.get("features", []):
            if feat.get("name") == "f7a":
                for match in feat.get("matches") or []:
                    lines.append(
                        f"  f7a: email mentions '{match.get('reference')}' — "
                        f"matches {match.get('source_ref')} "
                        f"({match.get('candidate') or name})"
                    )
            if feat.get("name") == "f7b" and feat.get("similarity") is not None:
                lines.append(
                    f"  f7b: topic cosine={float(feat['similarity']):.3f} "
                    f"value={float(feat['value']):.2f}"
                )
    if data.get("hard_rules"):
        lines.append(f"hard_rules: {', '.join(data['hard_rules'])}")
    if data.get("flags"):
        lines.append(f"flags: {', '.join(data['flags'])}")
    return "\n".join(lines)
