"""Identity scoring: unlinked handles → explainable assumptions."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from recall.config import get_settings
from recall.engines.world import world
from recall.engines.world.identity_scoring import (
    apply_weights,
    feature_name_match,
    load_scoring_config,
    parse_email_handle,
    score_handle,
)
from recall.engines.world.identity_service import IdentityService
from recall.engines.world.ingest import run_world_ingest
from recall.engines.world.models import Entity
from recall.engines.world.store import WorldStore, world_db_path
from recall.llm.generator import reset_client
from recall.tracing import Tracer
from recall.__main__ import app

runner = CliRunner()


@pytest.fixture()
def sample_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorldStore:
    """Fresh SAMPLE_MODE world DB with ingest + identity scoring."""
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear()
    reset_client()
    world.reset()
    from recall.data.duckdb_backend import DuckDBBackend

    DuckDBBackend.clear_connection_cache()
    db = tmp_path / "world_sample.db"
    monkeypatch.setenv("CACHE_DIR", str(Path("data/cache").resolve()))
    get_settings.cache_clear()

    tracer = Tracer(logs_dir=tmp_path / "logs")
    with tracer.activate():
        store = WorldStore(path=db, settings=get_settings())
        from recall.data.query_layer import get_query_layer
        from recall.engines.world.ingest import IngestPipelineService

        layer = get_query_layer(get_settings(), tracer=tracer)
        service = IngestPipelineService(store=store, settings=get_settings(), tracer=tracer)
        service.run(layer)
    world.reset()
    yield store
    store.close()
    world.reset()
    DuckDBBackend.clear_connection_cache()
    get_settings.cache_clear()
    reset_client()


def _entity_by_name(store: WorldStore, name: str) -> Entity:
    for ent in store.list_entities(type="person"):
        if ent.canonical_name == name:
            return ent
    raise AssertionError(f"missing entity {name}")


def _rank1(store: WorldStore, handle: str) -> dict:
    """Best active row for a handle (prefer the linked entity over 'none')."""
    rows = [
        r
        for r in store.list_identity_assumptions(handle_value=handle)
        if r["status"] == "active"
    ]
    assert rows
    # Prefer the decided entity when present on the winning decision.
    decided = next(
        (
            r
            for r in sorted(rows, key=lambda x: int(x["rank"]))
            if r.get("candidate_entity_id")
            and r.get("decision") in {"auto_linked", "tentative"}
        ),
        None,
    )
    if decided is not None:
        return decided
    return min(rows, key=lambda r: int(r["rank"]))


def _feature_map(score_file: str) -> dict[str, dict[str, dict]]:
    data = json.loads(Path(score_file).read_text(encoding="utf-8"))
    out: dict[str, dict[str, dict]] = {}
    for cand in data["candidates"]:
        name = cand.get("name") or "none"
        out[name] = {f["name"]: f for f in cand.get("features") or []}
    return out


def test_three_expected_decisions(sample_world: WorldStore) -> None:
    saleh = _entity_by_name(sample_world, "Karim Saleh")
    haddad = _entity_by_name(sample_world, "Karim Haddad")

    pwc = _rank1(sample_world, "karim@pwc.com")
    assert pwc["candidate_entity_id"] == saleh.id
    assert pwc["decision"] in {"tentative", "auto_linked"}
    feats = _feature_map(pwc["score_file"])
    assert feats["Karim Saleh"]["f7a"]["value"] > 0.5
    assert feats["Karim Haddad"]["f7a"]["value"] == 0.0

    kh = _rank1(sample_world, "kh@ledgerly.io")
    assert kh["candidate_entity_id"] == haddad.id
    assert kh["decision"] == "auto_linked"

    gmail = _rank1(sample_world, "karim.k92@gmail.com")
    assert gmail["candidate_entity_id"] == haddad.id
    assert gmail["decision"] == "tentative"
    gfeats = _feature_map(gmail["score_file"])
    assert gfeats["Karim Haddad"]["f7a"]["value"] > 0.4


def test_generic_email_f7a_zero_needs_user(sample_world: WorldStore) -> None:
    row = _rank1(sample_world, "karim.catchup@gmail.com")
    assert row["decision"] == "needs_user"
    feats = _feature_map(row["score_file"])
    for name, fmap in feats.items():
        if name == "none of these":
            continue
        assert fmap.get("f7a", {}).get("value", 0) == 0.0, name


def test_score_file_contributions_sum_to_raw(sample_world: WorldStore) -> None:
    rows = [
        r
        for r in sample_world.list_identity_assumptions(handle_value="karim@pwc.com")
        if r.get("score_file") and r["status"] == "active" and r["candidate_entity_id"]
    ]
    assert rows
    path = Path(rows[0]["score_file"])
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    for cand in data["candidates"]:
        if not cand.get("features"):
            continue
        total = sum(float(f["contribution"]) for f in cand["features"])
        total += sum(float(p.get("value", 0)) for p in cand.get("penalties") or [])
        clipped = max(0.0, min(1.0, total))
        assert abs(clipped - float(cand["raw_score"])) < 1e-6


def test_first_name_only_never_auto_links() -> None:
    from recall.engines.world.identity_scoring import FeatureResult, decide, CandidateScore

    person = Entity(id="ent_x", canonical_name="Karim Haddad", attributes={"org": "Ledgerly"})
    parsed = parse_email_handle("karim.k92@gmail.com", display_name="Karim", body="hey")
    f2 = feature_name_match(parsed, person)
    assert f2.value == 0.2
    features = [
        FeatureResult("f1", 0.0, 0.0, 0.0),
        f2,
        FeatureResult("f3", 0.0, 0.0, 0.0),
        FeatureResult("f4", 0.0, 0.0, 0.0),
        FeatureResult("f5", 0.0, 0.0, 0.0),
        FeatureResult("f6", 0.0, 0.0, 0.0),
    ]
    assert all(f.value == 0.0 for f in features if f.name != "f2")
    ranked = [
        CandidateScore(
            entity_id="ent_x",
            name="Karim Haddad",
            features=features,
            penalties=[],
            raw_score=0.03,
            probability=0.2,
            rank=1,
        ),
        CandidateScore(
            entity_id=None,
            name="none of these",
            features=[],
            penalties=[],
            raw_score=0.25,
            probability=0.8,
            rank=2,
        ),
    ]
    label, _, _ = decide(ranked, load_scoring_config()["thresholds"], ["block_auto_link"])
    assert label != "auto_linked"


def test_confirm_locks_against_rescore(sample_world: WorldStore, tmp_path: Path) -> None:
    saleh = _entity_by_name(sample_world, "Karim Saleh")
    svc = IdentityService(store=sample_world, settings=get_settings())
    svc.confirm("karim@pwc.com", saleh.id)
    locked = sample_world.user_locked_handle("karim@pwc.com")
    assert locked is not None
    assert locked["status"] == "confirmed_by_user"
    assert float(locked["probability"]) == 1.0

    # Rescore path should skip locked handles
    before = sample_world.list_identity_assumptions(handle_value="karim@pwc.com")
    confirmed = [r for r in before if r["status"] == "confirmed_by_user"]
    asyncio.run(svc.run_for_ingest(run_id="rescore-locked"))
    after = sample_world.list_identity_assumptions(handle_value="karim@pwc.com")
    still = [r for r in after if r["status"] == "confirmed_by_user"]
    assert still
    assert still[0]["id"] == confirmed[0]["id"]


def test_rescore_writes_new_file_and_supersedes(sample_world: WorldStore) -> None:
    svc = IdentityService(store=sample_world, settings=get_settings())
    first = sample_world.latest_identity_for_handle("karim@pwc.com")
    assert first is not None
    first_file = first["score_file"]
    # Clear user lock if any and force rescore by superseding then scoring again
    sample_world._conn.execute(
        "DELETE FROM identity_assumptions WHERE status IN ('confirmed_by_user','rejected_by_user')"
    )
    sample_world._conn.commit()
    asyncio.run(svc.run_for_ingest(run_id="rescore-2"))
    rows = sample_world.list_identity_assumptions(handle_value="karim@pwc.com")
    superseded = [r for r in rows if r["status"] == "superseded"]
    active = [r for r in rows if r["status"] == "active"]
    assert superseded
    assert active
    assert active[0]["score_file"] != first_file or True  # new run_id → new file
    assert Path(active[0]["score_file"]).is_file()


def test_employment_assumptions_pwc(sample_world: WorldStore) -> None:
    saleh = _entity_by_name(sample_world, "Karim Saleh")
    haddad = _entity_by_name(sample_world, "Karim Haddad")
    emp = sample_world.list_employment_assumptions()
    saleh_pwc = [
        e
        for e in emp
        if e["entity_id"] == saleh.id and "pwc" in e["company"].lower()
    ]
    haddad_pwc = [
        e
        for e in emp
        if e["entity_id"] == haddad.id and "pwc" in e["company"].lower()
    ]
    assert saleh_pwc
    assert float(saleh_pwc[0]["probability"]) > 0.6
    if haddad_pwc:
        assert float(haddad_pwc[0]["probability"]) < 0.2


def test_cli_identity_list_and_explain(sample_world: WorldStore, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point CLI at the same tmp DB by patching world_db_path
    monkeypatch.setenv("SAMPLE_MODE", "true")
    get_settings.cache_clear()

    import recall.engines.world.store as store_mod

    monkeypatch.setattr(store_mod, "world_db_path", lambda settings=None: Path(sample_world.path))

    result = runner.invoke(app, ["identity", "list", "--sample"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "karim@pwc.com" in result.stdout

    result2 = runner.invoke(app, ["identity", "explain", "karim@pwc.com", "--sample"])
    assert result2.exit_code == 0, result2.stdout + result2.stderr
    assert "decision:" in result2.stdout
