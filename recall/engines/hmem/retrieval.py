"""Hybrid memory retrieval over embeddings, keywords, entities, layer, and recency."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from recall.config import Settings, get_settings
from recall.engines.hmem.ledger import TrustLedgerService
from recall.engines.hmem.models import Memory, RetrievedMemory, RetrieveResult
from recall.engines.hmem.store import MemoryStoreService
from recall.llm.generator import embed
from recall.tracing import Tracer

_LAYER_WEIGHT = {"long_term": 1.0, "working": 0.6, "short_term": 0.3}
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) > 1}


def _parse_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _recency_score(timestamp: str, *, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    age_days = max(0.0, (now - _parse_ts(timestamp)).total_seconds() / 86400.0)
    # 1.0 today → ~0.5 at 30 days → approaches 0
    return math.exp(-age_days / 30.0)


class MemoryRetrievalService:
    """Hybrid retrieve(query, entity_ids, top_n, threshold)."""

    def __init__(
        self,
        store: MemoryStoreService | None = None,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
        ledger: TrustLedgerService | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._tracer = tracer if tracer is not None else Tracer.current()
        self._store = store or MemoryStoreService(settings=self._settings)
        self._ledger = ledger

    async def retrieve(
        self,
        query: str,
        entity_ids: list[str] | None = None,
        top_n: int = 5,
        threshold: float = 0.35,
    ) -> dict[str, Any]:
        """Return scored memories with why strings."""
        entity_ids = list(entity_ids or [])
        query_vecs = await embed([query], input_type="query")
        qvec = query_vecs[0] if query_vecs else []
        qtokens = _tokens(query)

        candidates = self._store.list_active()
        if entity_ids:
            # Prefer entity-linked, but still allow others with lower entity score.
            pass

        scored: list[tuple[float, str, Memory, dict[str, float]]] = []
        for mem in candidates:
            parts = self._score_parts(mem, qvec=qvec, qtokens=qtokens, entity_ids=entity_ids)
            # Superseded memories are already filtered by list_active; still apply penalty
            # if this memory supersedes another (it's the winner — no penalty).
            # Penalty applies when reading a memory that was superseded — filtered out.
            total = (
                0.45 * parts["cosine"]
                + 0.2 * parts["keyword"]
                + 0.15 * parts["entity"]
                + 0.1 * parts["layer"]
                + 0.1 * parts["recency"]
                - 0.3 * parts["superseded_penalty"]
            )
            if total < threshold:
                continue
            why = self._why(parts)
            scored.append((total, why, mem, parts))

        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[:top_n]
        # Bump access for returned memories
        for total, why, mem, parts in top:
            self._store.bump_access(mem.id)

        result = RetrieveResult(
            memories=[
                RetrievedMemory(id=mem.id, content=mem.content, score=round(total, 4), why=why)
                for total, why, mem, _ in top
            ],
            strategy="hybrid",
            retrieval_reason=(
                f"hybrid over {len(candidates)} active memories; "
                f"threshold={threshold}; entity_filter={len(entity_ids)}"
            ),
        )
        if self._tracer is not None:
            top = [
                {"id": m.id, "score": m.score}
                for m in result.memories[:5]
            ]
            self._tracer.event(
                "hmem",
                "retrieve",
                {
                    "summary": (
                        f"hmem retrieve hits={len(result.memories)} "
                        f"candidates={len(candidates)} "
                        f"top={[(t['id'], t['score']) for t in top]}"
                    ),
                    "hits": len(result.memories),
                    "candidates": len(candidates),
                    "top": top,
                },
            )
        return result.model_dump()

    def _score_parts(
        self,
        mem: Memory,
        *,
        qvec: list[float],
        qtokens: set[str],
        entity_ids: list[str],
    ) -> dict[str, float]:
        cosine = _cosine(qvec, mem.embedding) if qvec and mem.embedding else 0.0
        # Map cosine from [-1,1] to [0,1]
        cosine = max(0.0, min(1.0, (cosine + 1.0) / 2.0)) if qvec else 0.0
        # For hash embeddings, cosine is already typically in a useful range; also try raw
        raw = _cosine(qvec, mem.embedding) if qvec and mem.embedding else 0.0
        cosine = max(0.0, raw)  # sample embeddings are non-negative-ish after hash

        mtokens = _tokens(mem.content)
        if not qtokens or not mtokens:
            keyword = 0.0
        else:
            keyword = len(qtokens & mtokens) / max(len(qtokens), 1)

        if not entity_ids:
            entity = 0.5
        elif set(entity_ids) & set(mem.entity_ids):
            entity = 1.0
        else:
            entity = 0.0

        layer = _LAYER_WEIGHT.get(mem.layer, 0.3)
        recency = _recency_score(mem.timestamp)
        superseded_penalty = 0.0  # active only
        return {
            "cosine": cosine,
            "keyword": keyword,
            "entity": entity,
            "layer": layer,
            "recency": recency,
            "superseded_penalty": superseded_penalty,
        }

    @staticmethod
    def _why(parts: dict[str, float]) -> str:
        weighted = {
            "embedding": 0.45 * parts["cosine"],
            "keyword": 0.2 * parts["keyword"],
            "entity": 0.15 * parts["entity"],
            "layer": 0.1 * parts["layer"],
            "recency": 0.1 * parts["recency"],
        }
        ranked = sorted(weighted.items(), key=lambda kv: kv[1], reverse=True)
        top = [f"{name}={score:.2f}" for name, score in ranked[:3] if score > 0]
        return ", ".join(top) if top else "weak match"


def retrieve(entities: list[Any]) -> list[Any]:
    """Legacy placeholder."""
    _ = entities
    raise NotImplementedError("Use MemoryRetrievalService.retrieve")
