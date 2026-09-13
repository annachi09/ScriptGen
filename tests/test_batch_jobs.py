"""
Pure-logic tests for web.batch_jobs's BatchJobStore - the in-memory job
record/list/cancel bookkeeping around the Date Anomaly Batch page's
background thread. No DB, no FastAPI, no threading involved here: these
tests drive BatchJob/BatchJobStore directly and never call start_batch
(which is what actually spins up a thread and touches app.db.mssql) -
see tests/test_web_api.py for the integration tests that exercise the
real background-thread path through the HTTP API.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.batch_jobs import BatchJob, BatchJobStore, STATUS_DONE, STATUS_QUEUED, STATUS_RUNNING


def test_create_assigns_a_unique_id_and_queued_status():
    store = BatchJobStore()
    job = store.create(created_by="alice", niss_list=["A", "B"], threshold=0, program="JIRA-1")
    assert job.id
    assert job.status == STATUS_QUEUED
    assert job.niss_total == 2
    assert job.processed == 0
    assert job.results == []


def test_create_lowest_billing_period_only_defaults_to_false():
    store = BatchJobStore()
    job = store.create(created_by="alice", niss_list=["A"], threshold=0, program="JIRA-1")
    assert job.lowest_billing_period_only is False


def test_create_lowest_billing_period_only_can_be_set_true():
    store = BatchJobStore()
    job = store.create(
        created_by="alice", niss_list=["A"], threshold=0, program="JIRA-1", lowest_billing_period_only=True,
    )
    assert job.lowest_billing_period_only is True
    assert job.to_public_dict()["lowest_billing_period_only"] is True


def test_create_two_jobs_get_different_ids():
    store = BatchJobStore()
    j1 = store.create(created_by="alice", niss_list=["A"], threshold=0, program="JIRA-1")
    j2 = store.create(created_by="alice", niss_list=["B"], threshold=0, program="JIRA-1")
    assert j1.id != j2.id


def test_get_returns_none_for_unknown_id():
    store = BatchJobStore()
    assert store.get("does-not-exist") is None


def test_get_returns_the_job_by_id():
    store = BatchJobStore()
    job = store.create(created_by="alice", niss_list=["A"], threshold=0, program="JIRA-1")
    assert store.get(job.id) is job


def test_list_for_user_only_returns_that_users_jobs():
    store = BatchJobStore()
    store.create(created_by="alice", niss_list=["A"], threshold=0, program="JIRA-1")
    bob_job = store.create(created_by="bob", niss_list=["B"], threshold=0, program="JIRA-1")
    bob_jobs = store.list_for_user("bob")
    assert [j.id for j in bob_jobs] == [bob_job.id]


def test_list_for_user_sorted_newest_first():
    store = BatchJobStore()
    j1 = store.create(created_by="alice", niss_list=["A"], threshold=0, program="JIRA-1")
    j1.created_at_utc = "2026-01-01T00:00:00+00:00"
    j2 = store.create(created_by="alice", niss_list=["B"], threshold=0, program="JIRA-1")
    j2.created_at_utc = "2026-01-02T00:00:00+00:00"
    result = store.list_for_user("alice")
    assert [j.id for j in result] == [j2.id, j1.id]


def test_list_for_user_respects_limit():
    store = BatchJobStore()
    for i in range(5):
        store.create(created_by="alice", niss_list=[f"N{i}"], threshold=0, program="JIRA-1")
    assert len(store.list_for_user("alice", limit=2)) == 2


def test_request_cancel_on_queued_job_succeeds():
    store = BatchJobStore()
    job = store.create(created_by="alice", niss_list=["A"], threshold=0, program="JIRA-1")
    assert store.request_cancel(job.id) is True
    assert job.cancel_requested is True


def test_request_cancel_on_running_job_succeeds():
    store = BatchJobStore()
    job = store.create(created_by="alice", niss_list=["A"], threshold=0, program="JIRA-1")
    job.status = STATUS_RUNNING
    assert store.request_cancel(job.id) is True


def test_request_cancel_on_done_job_fails():
    store = BatchJobStore()
    job = store.create(created_by="alice", niss_list=["A"], threshold=0, program="JIRA-1")
    job.status = STATUS_DONE
    assert store.request_cancel(job.id) is False
    assert job.cancel_requested is False


def test_request_cancel_on_unknown_job_fails():
    store = BatchJobStore()
    assert store.request_cancel("does-not-exist") is False


def test_to_public_dict_exposes_expected_fields():
    job = BatchJob(
        id="abc123", created_by="alice", created_at_utc="2026-01-01T00:00:00+00:00",
        niss_list=["A", "B"], threshold=5, program="JIRA-1",
    )
    d = job.to_public_dict()
    assert d["job_id"] == "abc123"
    assert d["niss_total"] == 2
    assert d["status"] == STATUS_QUEUED
    assert d["processed"] == 0
    assert d["results"] == []
    assert d["combined_sql"] is None
    assert d["error"] is None


def test_to_public_dict_started_and_finished_default_to_none():
    # A freshly-created (still queued) job hasn't started running yet -
    # the frontend's progress/rate display (RJ, 2026-09-13) uses this to
    # know there's nothing meaningful to show yet.
    job = BatchJob(
        id="abc123", created_by="alice", created_at_utc="2026-01-01T00:00:00+00:00",
        niss_list=["A"], threshold=0, program="JIRA-1",
    )
    d = job.to_public_dict()
    assert d["started_at_utc"] is None
    assert d["finished_at_utc"] is None


def test_to_public_dict_exposes_started_and_finished_once_set():
    job = BatchJob(
        id="abc123", created_by="alice", created_at_utc="2026-01-01T00:00:00+00:00",
        niss_list=["A"], threshold=0, program="JIRA-1",
    )
    job.started_at_utc = "2026-01-01T00:00:05+00:00"
    job.finished_at_utc = "2026-01-01T00:00:15+00:00"
    d = job.to_public_dict()
    assert d["started_at_utc"] == "2026-01-01T00:00:05+00:00"
    assert d["finished_at_utc"] == "2026-01-01T00:00:15+00:00"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
