"""Pre-routing on the synthetic world: role / company / date questions find the person; overview questions get answered."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "data" / "synthetic" / "tables"


@pytest.fixture(scope="module")
def pr(tmp_path_factory: pytest.TempPathFactory):
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("preroute")
    mp.setenv("SAMPLE_MODE", "true")
    mp.setenv("DATA_BACKEND", "duckdb")
    mp.setenv("SAMPLE_DATA_DIR", str(TABLES))
    mp.setenv("LOGS_DIR", str(tmp / "logs"))
    from recall.config import get_settings

    get_settings.cache_clear()
    import recall.data.queries as queries
    from recall.data.duckdb_backend import DuckDBBackend
    from recall.data.query_layer import get_query_layer
    from recall.engines.world import store as store_mod
    from recall.engines.world.ingest import IngestPipelineService
    from recall.server import preroute

    mp.setattr(queries, "SAMPLE_ANCHOR", datetime(2026, 10, 26, 22, 0, tzinfo=timezone.utc))
    db = tmp / "world.db"
    mp.setattr(store_mod, "world_db_path", lambda settings=None: db)
    DuckDBBackend.clear_connection_cache()
    settings = get_settings()
    st = store_mod.WorldStore(path=db, settings=settings)
    IngestPipelineService(store=st, settings=settings).run(get_query_layer(settings))
    st.close()
    preroute._cache = None
    # conftest resets env/settings before every test, so keep this world's settings and pass them in.
    real_plan, real_load = preroute.plan, preroute.load_world
    mp.setattr(preroute, "plan", lambda text, s=None: real_plan(text, settings))
    mp.setattr(preroute, "load_world", lambda s=None: real_load(settings))
    yield preroute
    preroute._cache = None
    mp.undo()
    get_settings.cache_clear()
    DuckDBBackend.clear_connection_cache()


def _who(pr, text: str) -> str | None:
    p = pr.plan(text)
    if not p.entity_id:
        return None
    return next(x.name for x in pr.load_world().people if x.id == p.entity_id)


def test_role_company_date_and_hint_find_the_person(pr) -> None:
    assert "Kassem" in (_who(pr, "What do I owe my landlord?") or "")
    assert _who(pr, "Anything I need to answer my mom about?") == "Nadia Lefebvre"
    assert _who(pr, "Prep me for the Énergie Boréale call") == "Élodie Gagnon"
    assert _who(pr, "Who am I meeting on November 6 and what should I know?") == "Rachel Goldberg"
    assert "MBA" in (_who(pr, "Brief me on Omar from my MBA") or "")


def test_ambiguous_name_still_goes_to_the_agent(pr) -> None:
    plan = pr.plan("I'm meeting Omar")
    assert plan.answer is None and plan.entity_id is None


def test_overview_answers_cite_sources(pr) -> None:
    cal = pr.plan("What's on my calendar Friday?").answer
    assert cal is not None and any("Plumber" in p.text for p in cal.final_brief.points)
    bday = pr.plan("Whose birthday is coming up?").answer
    assert bday is not None and "Isabelle" in bday.final_brief.points[0].text
    owed = pr.plan("What do I owe people this week?").answer
    assert owed is not None and len(owed.final_brief.points) >= 4
    for state in (cal, bday, owed):
        for p in state.final_brief.points:
            assert p.evidence_ids and all(e in state.evidence for e in p.evidence_ids)
    lunch = pr.plan("Am I having lunch with Priya on the 21st?").answer
    assert lunch is not None and "cancel" in lunch.final_brief.headline.lower()
