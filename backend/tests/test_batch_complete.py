import hashlib
from uuid import uuid4

from app.cqrs import (
    abort_run,
    attach_artifact,
    batch_complete_runs,
    list_events,
    record_metric,
    start_run,
)
from app.models import RunProjection


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def make_run(db, name, *, with_metric=True, with_artifact=True):
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


def test_batch_complete_happy_path(db):
    run1 = make_run(db, "qualified-1")
    run2 = make_run(db, "qualified-2")
    versions_before = {run1.id: run1.version, run2.id: run2.version}

    results = batch_complete_runs(
        db,
        actor="researcher",
        run_ids=[run1.id, run2.id],
        result_summary="批量完成",
    )

    assert [r["status"] for r in results] == ["completed", "completed"]
    assert [r["run_id"] for r in results] == [run1.id, run2.id]
    for source, item in zip((run1, run2), results):
        updated = item["run"]
        assert updated is not None
        assert updated.status == "completed"
        assert updated.result_summary == "批量完成"
        assert updated.finished_at is not None
        assert updated.version == versions_before[source.id] + 1
        # projection matches what the detail view would return
        stored = db.get(RunProjection, source.id)
        assert stored.status == "completed"
        assert stored.version == updated.version
        # RunCompleted event appended to the event store
        events = list_events(db, source.id)
        assert events[-1].event_type == "RunCompleted"
        assert events[-1].payload_json["result_summary"] == "批量完成"


def test_batch_complete_skips_when_materials_missing(db):
    no_metric = make_run(db, "no-metric", with_metric=False)
    no_artifact = make_run(db, "no-artifact", with_artifact=False)
    neither = make_run(db, "neither", with_metric=False, with_artifact=False)

    results = batch_complete_runs(
        db,
        actor="researcher",
        run_ids=[no_metric.id, no_artifact.id, neither.id],
        result_summary="批量完成",
    )

    assert [r["status"] for r in results] == ["skipped", "skipped", "skipped"]
    assert "指标" in results[0]["reason"]
    assert "产物" in results[1]["reason"]
    assert "指标" in results[2]["reason"] and "产物" in results[2]["reason"]
    # skipped runs stay running, no events appended
    for run in (no_metric, no_artifact, neither):
        stored = db.get(RunProjection, run.id)
        assert stored.status == "running"
        assert stored.version == run.version


def test_batch_complete_skips_terminal_and_failure_does_not_stop_rest(db):
    aborted = make_run(db, "aborted")
    aborted = abort_run(
        db,
        run_id=aborted.id,
        actor="researcher",
        reason="OOM",
        expected_version=aborted.version,
    )
    missing_id = uuid4()
    qualified = make_run(db, "qualified")

    results = batch_complete_runs(
        db,
        actor="researcher",
        run_ids=[aborted.id, missing_id, qualified.id],
        result_summary="批量完成",
    )

    assert [r["status"] for r in results] == ["skipped", "failed", "completed"]
    assert "终态" in results[0]["reason"]
    assert results[1]["reason"] == "Run 不存在"
    assert results[1]["run"] is None
    # the qualified run after the failure still completed
    assert results[2]["run"].status == "completed"
    assert db.get(RunProjection, qualified.id).status == "completed"


def test_batch_complete_duplicate_id_second_is_skipped(db):
    run = make_run(db, "dup")
    version_before = run.version

    results = batch_complete_runs(
        db,
        actor="researcher",
        run_ids=[run.id, run.id],
        result_summary="批量完成",
    )

    assert [r["status"] for r in results] == ["completed", "skipped"]
    assert db.get(RunProjection, run.id).version == version_before + 1
