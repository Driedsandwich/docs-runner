from app.jobs import Job
from app.models import Evidence


def kinds(job):
    return [event["kind"] for event in job.presentation.get("containment_events", [])]


def test_unmetered_note_alone_is_not_a_limit_event():
    job = Job(id="j1", url="https://example.org/")
    job.observe_attempt(1, 1, Evidence(command="echo ok", exit_code=0, limits=["pids_unmetered"]))
    assert kinds(job) == []


def test_lost_probe_under_gvisor_reports_runner_stop_without_claiming_a_limit():
    job = Job(id="j2", url="https://example.org/")
    job.observe_attempt(4, 1, Evidence(command=":(){ :|:& }; :", exit_code=None, limits=["pids_unmetered"]))
    assert kinds(job) == ["sandbox_stopped"]
    assert "not asserted" in job.presentation["containment_events"][0]["detail"]


def test_reached_limit_reports_killed_by_limit():
    job = Job(id="j3", url="https://example.org/")
    job.observe_attempt(4, 1, Evidence(command="sleep 99", exit_code=None, limits=["timeout", "pids_unmetered"]))
    assert kinds(job) == ["killed_by_limit"]
