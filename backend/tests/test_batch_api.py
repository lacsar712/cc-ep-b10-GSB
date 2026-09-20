import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token
from app.cqrs import attach_artifact, record_metric, start_run
from app.database import get_db
from app.main import app


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    # no `with` block: skip lifespan so it never touches the real database
    yield TestClient(app)
    app.dependency_overrides.clear()


def auth_headers(username: str, role: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(username, role)}"}


def make_qualified_run(db, name):
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name=name,
        dataset_content_sha256=sha(f"ds-{name}"),
        code_commit_sha="abc1234",
        description=None,
    )
    run = record_metric(
        db, run_id=run.id, actor="researcher", name="acc", value=0.9, step=1,
        expected_version=run.version,
    )
    run = attach_artifact(
        db, run_id=run.id, actor="researcher", name="model.bin",
        uri="file:///tmp/model.bin", content_sha256=sha(f"m-{name}"),
        media_type=None, expected_version=run.version,
    )
    return run


def test_batch_complete_endpoint_completes_qualified_runs(client, db):
    run1 = make_qualified_run(db, "api-q1")
    run2 = make_qualified_run(db, "api-q2")

    resp = client.post(
        "/api/runs/batch-complete",
        json={"run_ids": [str(run1.id), str(run2.id)], "result_summary": "批量完成"},
        headers=auth_headers("researcher", "researcher"),
    )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["status"] for r in results] == ["completed", "completed"]
    for item, run in zip(results, (run1, run2)):
        assert item["run"]["id"] == str(run.id)
        assert item["run"]["status"] == "completed"
        assert item["run"]["result_summary"] == "批量完成"
        # detail endpoint reports the same state as the batch list
        detail = client.get(
            f"/api/runs/{run.id}", headers=auth_headers("researcher", "researcher")
        )
        assert detail.status_code == 200
        assert detail.json()["status"] == item["run"]["status"]
        assert detail.json()["version"] == item["run"]["version"]


def test_batch_complete_endpoint_partial_results(client, db):
    qualified = make_qualified_run(db, "api-qualified")
    bare = start_run(
        db, actor="researcher", project="p1", name="api-bare",
        dataset_content_sha256=sha("ds-bare"), code_commit_sha="abc1234",
        description=None,
    )

    resp = client.post(
        "/api/runs/batch-complete",
        json={
            "run_ids": [str(bare.id), str(uuid4()), str(qualified.id)],
            "result_summary": "批量完成",
        },
        headers=auth_headers("researcher", "researcher"),
    )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["status"] for r in results] == ["skipped", "failed", "completed"]
    assert "材料未齐" in results[0]["reason"]
    assert results[1]["reason"] == "Run 不存在"


def test_batch_complete_endpoint_rejects_auditor(client, db):
    run = make_qualified_run(db, "api-auditor")

    resp = client.post(
        "/api/runs/batch-complete",
        json={"run_ids": [str(run.id)], "result_summary": "批量完成"},
        headers=auth_headers("auditor", "auditor"),
    )

    assert resp.status_code == 403
    assert db.get(type(run), run.id).status == "running"


def test_batch_complete_endpoint_requires_login(client, db):
    resp = client.post(
        "/api/runs/batch-complete",
        json={"run_ids": [str(uuid4())], "result_summary": "批量完成"},
    )
    assert resp.status_code == 401
