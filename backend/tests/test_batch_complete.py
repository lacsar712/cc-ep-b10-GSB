import hashlib
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import router
from app.cqrs import (
    attach_artifact,
    batch_complete_runs,
    complete_run,
    list_events,
    record_metric,
    start_run,
)
from app.database import Base, get_db
from app.models import RunProjection


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # JSONB not available on SQLite — remap via create_all with JSON
    from sqlalchemy import JSON
    from sqlalchemy.dialects.postgresql import JSONB

    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(_type, compiler, **kw):
        return "JSON"

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def make_run(db, *, name, with_metric=True, with_artifact=True):
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name=name,
        dataset_content_sha256=sha(f"ds-{name}"),
        code_commit_sha="abc1234",
        description=None,
    )
    if with_metric:
        run = record_metric(
            db,
            run_id=run.id,
            actor="researcher",
            name="acc",
            value=0.9,
            step=1,
            expected_version=run.version,
        )
    if with_artifact:
        run = attach_artifact(
            db,
            run_id=run.id,
            actor="researcher",
            name="model.bin",
            uri="file:///tmp/model.bin",
            content_sha256=sha(f"model-{name}"),
            media_type="application/octet-stream",
            expected_version=run.version,
        )
    return run


def outcomes_by_id(results):
    return {r["run_id"]: r for r in results}


def test_batch_complete_two_qualified_runs(db):
    run1 = make_run(db, name="qualified-1")
    run2 = make_run(db, name="qualified-2")
    pre_versions = {run1.id: run1.version, run2.id: run2.version}

    results = batch_complete_runs(
        db,
        actor="researcher",
        run_ids=[run1.id, run2.id],
        result_summary="批量完成",
    )

    assert [r["outcome"] for r in results] == ["success", "success"]
    for run, item in zip((run1, run2), results):
        assert item["status"] == "completed"
        assert item["version"] == pre_versions[run.id] + 1
        stored = db.get(RunProjection, run.id)
        assert stored.status == "completed"
        assert stored.result_summary == "批量完成"
        assert stored.finished_at is not None
        assert list_events(db, run.id)[-1].event_type == "RunCompleted"


def test_batch_complete_skips_missing_materials(db):
    no_artifact = make_run(db, name="metric-only", with_artifact=False)
    no_metric = make_run(db, name="artifact-only", with_metric=False)

    results = outcomes_by_id(
        batch_complete_runs(
            db,
            actor="researcher",
            run_ids=[no_artifact.id, no_metric.id],
            result_summary="批量完成",
        )
    )

    assert results[no_artifact.id]["outcome"] == "skipped"
    assert "产物" in results[no_artifact.id]["reason"]
    assert results[no_metric.id]["outcome"] == "skipped"
    assert "指标" in results[no_metric.id]["reason"]
    assert db.get(RunProjection, no_artifact.id).status == "running"
    assert db.get(RunProjection, no_metric.id).status == "running"


def test_batch_complete_skips_terminal(db):
    run = make_run(db, name="already-done")
    run = complete_run(
        db,
        run_id=run.id,
        actor="researcher",
        result_summary="先完成",
        expected_version=run.version,
    )

    results = batch_complete_runs(
        db,
        actor="researcher",
        run_ids=[run.id],
        result_summary="批量完成",
    )

    assert results[0]["outcome"] == "skipped"
    assert "终态" in results[0]["reason"]
    assert db.get(RunProjection, run.id).result_summary == "先完成"


def test_batch_complete_failure_does_not_cancel_following(db):
    missing = uuid4()
    good = make_run(db, name="good")

    results = batch_complete_runs(
        db,
        actor="researcher",
        run_ids=[missing, good.id],
        result_summary="批量完成",
    )

    assert results[0]["outcome"] == "failed"
    assert results[0]["run_id"] == missing
    assert results[1]["outcome"] == "success"
    assert db.get(RunProjection, good.id).status == "completed"


def make_client(session):
    app = FastAPI()
    app.include_router(router)

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def login(client, username, password):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_batch_complete_api_rejects_anonymous(db):
    client = make_client(db)
    resp = client.post(
        "/api/runs/batch-complete",
        json={"run_ids": [str(uuid4())], "result_summary": "x"},
    )
    assert resp.status_code == 401


def test_batch_complete_api_rejects_auditor(db):
    client = make_client(db)
    run = make_run(db, name="qualified")
    headers = login(client, "auditor", "audit123456")

    resp = client.post(
        "/api/runs/batch-complete",
        json={"run_ids": [str(run.id)], "result_summary": "批量完成"},
        headers=headers,
    )

    assert resp.status_code == 403
    assert db.get(RunProjection, run.id).status == "running"


def test_batch_complete_api_happy_path(db):
    client = make_client(db)
    run1 = make_run(db, name="qualified-1")
    run2 = make_run(db, name="qualified-2")
    headers = login(client, "researcher", "lab123456")

    resp = client.post(
        "/api/runs/batch-complete",
        json={"run_ids": [str(run1.id), str(run2.id)], "result_summary": "批量完成"},
        headers=headers,
    )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["outcome"] for r in results] == ["success", "success"]
    for run in (run1, run2):
        detail = client.get(f"/api/runs/{run.id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["status"] == "completed"
