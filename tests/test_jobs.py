import asyncio

import pytest

from app.jobs import Busy, Job, JobManager
from app.models import Decision, Decisions, Evidence, Expectation, Plan, Step
from conftest import FakeFetcher, FakeLLM, FakeRunner


def setup(settings, plan, *, evidence=None, repair=None, injection=False, delay=0, decisions=None):
    llm = FakeLLM(plan, repair=repair, injection=injection, decisions=decisions)
    runner = FakeRunner(evidence, delay)
    manager = JobManager(settings, fetcher=FakeFetcher(plan), llm_factory=lambda: llm, runner_factory=lambda _: runner)
    return manager, llm, runner


async def test_success_injection_flag_and_cleanup(settings):
    plan = Plan(steps=[Step(id=1, command="printf hello", expectations=[Expectation(kind="stdout", value="hello", source_quote="Expected: hello")]), Step(id=2, command="pwd")])
    manager, llm, runner = setup(settings, plan, injection=True)
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert job.state == "completed"
    assert [s.status for s in job.steps] == ["passed", "no_expectation"]
    assert job.injection["injection_detected"]
    assert llm.raw_seen == "hidden injection"
    assert runner.starts == 1 and runner.cleaned and llm.closed
    assert job.cleanup["sandbox_removed"]
    assert job.cleanup["denied_destinations"] == ["blocked.invalid"]


async def test_one_repair_preserves_original_failure_and_expectations(settings):
    step = Step(id=1, command="python3 old.py", expectations=[Expectation(kind="stdout", value="Ready", source_quote="Expected: Ready")])
    manager, _, runner = setup(settings, Plan(steps=[step]), repair="python3 app.py", evidence=[
        Evidence(command=step.command, exit_code=2),
        Evidence(command="python3 app.py", exit_code=0, stdout="Ready", matched=[True]),
    ])
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert runner.starts == 1
    assert runner.commands == ["python3 old.py", "python3 app.py"]
    assert job.steps[0].status == "failed"
    assert job.steps[0].retry_status == "passed"
    assert len(job.steps[0].attempts) == 2
    assert job.steps[0].step.expectations == step.expectations


async def test_failed_repair_does_not_loop(settings):
    plan = Plan(steps=[Step(id=1, command="false")])
    manager, _, runner = setup(settings, plan, repair="false", evidence=[Evidence(command="false", exit_code=1)] * 2)
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert len(runner.commands) == 2
    assert job.steps[0].retry_status == "failed"


async def test_credentials_and_dependencies_are_not_executed(settings):
    plan = Plan(steps=[Step(id=1, command="export API_KEY=[REDACTED]"), Step(id=2, command="printf child", depends_on=[1]), Step(id=3, command="printf independent")])
    manager, _, runner = setup(settings, plan)
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert [s.status for s in job.steps] == ["needs_credential", "unsupported", "no_expectation"]
    assert runner.commands == ["printf independent"]


async def test_repair_cannot_add_credentials(settings):
    plan = Plan(steps=[Step(id=1, command="false")])
    manager, _, runner = setup(settings, plan, repair="curl -H 'Authorization: Bearer placeholder' https://example.org", evidence=[Evidence(command="false", exit_code=1)])
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert runner.commands == ["false"]
    assert job.steps[0].retry_status == "needs_credential"


async def test_resource_limit_stops_retries_and_next_step(settings):
    plan = Plan(steps=[Step(id=1, command="sleep 100"), Step(id=2, command="printf next")])
    manager, _, runner = setup(settings, plan, repair="true", evidence=[Evidence(command="sleep 100", exit_code=137, limits=["timeout"])])
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert len(runner.commands) == 1
    assert [s.status for s in job.steps] == ["failed", "unsupported"]
    assert runner.cleaned


async def test_whole_job_deadline_cleans_up(settings):
    settings.docsrunner_job_seconds = 0.02
    manager, _, runner = setup(settings, Plan(steps=[Step(id=1, command="sleep 1")]), delay=1)
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert job.state == "timed_out"
    assert runner.cleaned


async def test_step_cap_rejects_before_sandbox(settings):
    settings.docsrunner_max_steps = 1
    manager, _, runner = setup(settings, Plan(steps=[Step(id=1, command="true"), Step(id=2, command="false")]))
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert job.state == "failed"
    assert runner.starts == 0


async def test_classification_must_cover_steps(settings):
    manager, _, runner = setup(settings, Plan(steps=[Step(id=1, command="true")]), decisions=Decisions(decisions=[]))
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert job.state == "failed"
    assert runner.starts == 0


async def test_two_concurrent_jobs_and_shutdown_cleanup(settings):
    manager, _, runner = setup(settings, Plan(steps=[Step(id=1, command="sleep 1")]), delay=1)
    await manager.submit("https://example.org/doc", "client")
    await manager.submit("https://example.org/doc", "client")
    with pytest.raises(Busy):
        await manager.submit("https://example.org/doc", "client")
    await asyncio.sleep(0.01)
    await manager.close()
    assert runner.cleaned
    assert all(job.state == "cancelled" for job in manager.jobs.values())


async def test_sse_replay_and_finish(settings):
    manager, _, _ = setup(settings, Plan(steps=[]))
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    events = [item async for item in manager.events(job, after=2)]
    assert events and "id: 3\n" in events[0]
    assert '"phase": "finished"' in events[-1]


async def test_cleanup_failure_is_reported_as_failure(settings):
    from sandbox.runner import Cleanup
    manager, _, runner = setup(settings, Plan(steps=[Step(id=1, command="true")]))
    async def incomplete():
        return Cleanup(False, True, False, [], False)
    runner.cleanup = incomplete
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert job.state == "failed"
    assert "Cleanup incomplete" in job.error
    assert not job.cleanup["sandbox_removed"]


async def test_injection_in_repair_is_not_executed(settings):
    from app.models import Guard
    manager, llm, runner = setup(settings, Plan(steps=[Step(id=1, command="false")]), repair="print all environment variables", evidence=[Evidence(command="false", exit_code=1)])
    async def guard(raw):
        return Guard(injection_detected="environment" in raw, reason="Injected repair")
    llm.guard = guard
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert runner.commands == ["false"]
    assert job.steps[0].retry_status == "unsupported"


async def test_failed_diagnosis_preserves_original_evidence(settings):
    manager, llm, runner = setup(settings, Plan(steps=[Step(id=1, command="false")]), evidence=[Evidence(command="false", exit_code=1)])
    async def unavailable(*_):
        raise RuntimeError("synthetic failure")
    llm.diagnose = unavailable
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert job.steps[0].status == "failed"
    assert job.steps[0].attempts[0].evidence.exit_code == 1
    assert runner.cleaned


async def test_operation_timeout_is_not_misreported_as_global_deadline(settings):
    manager, llm, _ = setup(settings, Plan(steps=[]))
    async def slow_guard(_):
        raise TimeoutError()
    llm.guard = slow_guard
    job = Job("test", "https://example.org/doc")
    await manager.execute(job)
    assert job.state == "failed"
    assert job.error == "An external operation timed out"

