"""No commands are executed: both Docker adapters are replaced with fakes."""
import asyncio
import json

import pytest

from app.jobs import JobManager
from app.judge import judge
from app.models import Evidence, Step
from sandbox.runner import DockerRunner, SandboxError
from test_docker import DemoLLM, assert_process_exhaustion_evidence, demo_plan

FORK_BOMB = ":(){ :|:& }; :"


class FakeProcess:
    def __init__(self, code, stdout=b"", stderr=b"", hang=False):
        self.returncode = None
        self.code = code
        self.hang = hang
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()

    async def wait(self):
        if self.returncode is None:
            if self.hang:
                await asyncio.Event().wait()
            self.returncode = self.code
        return self.returncode

    def kill(self):
        self.returncode = -9


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.commands = []
        self.running = {}
        self.executed = set()
        self.broken = set()
        self.stats = b"0"
        self.before = {}
        self.after = {}
        self.inspect_error = None
        self.oom = False
        self.hang = False
        self.probe_error = SandboxError("sandbox exited")
        self.stderr = b""

    async def docker(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == "version":
            return b'{"Version":"29.9.0"}'
        if args[0] == "info":
            return b'{"runc":{},"runsc":{}}'
        if args[0] == "start":
            self.running[args[1]] = True
        elif args[0] in ("kill", "stop", "rm"):
            self.running[args[-1]] = False
        elif args[0] == "inspect":
            if args[2] != "{{json .State}}":
                return b"172.30.0.2"
            if self.inspect_error:
                raise self.inspect_error
            return json.dumps({"Running": self.running.get(args[-1], False),
                               "OOMKilled": self.oom}).encode()
        elif args[0] == "stats":
            return self.stats
        elif args[0] == "logs":
            return b"DENIED 1 TCP_DENIED blocked.invalid\n"
        elif args[0] == "exec" and "/opt/probe.py" in args:
            name = args[2]
            if name in self.broken:
                raise self.probe_error
            request = json.loads(kwargs["stdin"])
            return json.dumps({"counters": self.after if name in self.executed else self.before,
                               "files": [True] * len(request["files"])}).encode()
        return b""

    async def subprocess(self, *args, **kwargs):
        assert args[:4] == ("docker", "exec", "--workdir", "/work")
        name, command = args[4], args[-1]
        assert args[5:-1] == ("/usr/bin/env", "-i", "/bin/bash", "--noprofile", "--norc", "-c")
        self.commands.append(command)
        self.executed.add(name)
        if command == FORK_BOMB:
            self.broken.add(name)
            self.running[name] = False
            return FakeProcess(128, stderr=self.stderr, hang=self.hang)
        if command == "false" or "blocked.invalid" in command or command.startswith("rm "):
            return FakeProcess(1, stderr=self.stderr)
        return FakeProcess(0, stdout=b"Hello from Docs Runner\n", stderr=self.stderr, hang=self.hang)


@pytest.fixture
def fake_docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr("sandbox.runner.docker", fake.docker)
    monkeypatch.setattr("sandbox.runner.asyncio.create_subprocess_exec", fake.subprocess)

    async def addresses(_):
        return ["127.0.0.1"]

    monkeypatch.setattr("sandbox.runner.controller_addresses", addresses)
    return fake


async def start(settings, runtime="runsc"):
    settings.docsrunner_runtime = runtime
    runner = DockerRunner("limits", settings)
    await runner.start("")
    return runner


@pytest.mark.parametrize("stats", [b"0", b"128", b"999", b"--"])
async def test_runsc_probe_loss_is_unmetered_not_a_pid_hit(settings, fake_docker, stats):
    fake_docker.stats = stats
    runner = await start(settings)
    result = await runner.run(Step(id=1, command=FORK_BOMB), 3)
    assert result.limits == ["pids_unmetered"]
    assert result.exit_code is None and not result.metrics_available
    assert not runner.alive and not fake_docker.running[runner.name]
    assert ("kill", runner.name) in fake_docker.calls
    assert not any(call[0] == "stats" for call in fake_docker.calls)
    assert_process_exhaustion_evidence(result, runner.runtime)
    assert judge(Step(id=1, command=FORK_BOMB), result)[0] == "failed"


@pytest.mark.parametrize("command,expected_code", [("true", 0), ("false", 1)])
async def test_unmetered_does_not_stop_ordinary_steps(settings, fake_docker, command, expected_code):
    # Even a guest counter or command output claiming a hit is not trusted
    # as evidence about the host's runsc cgroup.
    fake_docker.before = {"pids.events": {"max": "0"}, "memory.events": {"oom_kill": "0"}}
    fake_docker.after = {"pids.events": {"max": "1"}, "memory.events": {"oom_kill": "0"}}
    fake_docker.stderr = b"fork: Resource temporarily unavailable; pids limit reached"
    runner = await start(settings)
    for step_id in (1, 2):
        result = await runner.run(Step(id=step_id, command=command), 3)
        assert result.exit_code == expected_code
        assert result.limits == ["pids_unmetered"] and not result.metrics_available
        assert runner.alive and fake_docker.running[runner.name]
    assert not any(call[0] == "kill" for call in fake_docker.calls)


@pytest.mark.parametrize("kind", ["timeout", "oom"])
async def test_confirmed_limits_remain_separate(settings, fake_docker, kind):
    fake_docker.hang = kind == "timeout"
    fake_docker.oom = kind == "oom"
    runner = await start(settings)
    result = await runner.run(Step(id=1, command="true"), 0.01 if kind == "timeout" else 3)
    assert result.limits == [kind, "pids_unmetered"]
    assert not runner.alive
    assert judge(Step(id=1, command="true"), result)[0] == "failed"


@pytest.mark.parametrize("error", [SandboxError("offline"), TimeoutError(), json.JSONDecodeError("bad", "", 0)])
async def test_inspect_failure_preserves_evidence(settings, fake_docker, error):
    runner = await start(settings)
    fake_docker.inspect_error = error
    result = await runner.run(Step(id=1, command=FORK_BOMB), 3)
    assert result.exit_code is None
    assert result.limits == ["pids_unmetered"] and not runner.alive


@pytest.mark.parametrize("runtime,limits", [("runc", ["pids"]), ("runsc", ["pids_unmetered"])])
async def test_counter_delta_is_only_used_for_runc(settings, fake_docker, runtime, limits):
    fake_docker.before = {"pids.events": {"max": "7"}, "memory.events": {"oom_kill": "0"}}
    fake_docker.after = {"pids.events": {"max": "8"}, "memory.events": {"oom_kill": "0"}}
    runner = await start(settings, runtime)
    result = await runner.run(Step(id=1, command="true"), 3)
    assert result.limits == limits
    assert result.metrics_available is (runtime == "runc")
    assert runner.alive is (runtime == "runsc")


async def test_runc_stats_fallback_is_preserved(settings, fake_docker):
    fake_docker.stats = b"128"
    runner = await start(settings, "runc")
    result = await runner.run(Step(id=1, command=FORK_BOMB), 3)
    assert result.limits == ["pids"] and not runner.alive
    assert_process_exhaustion_evidence(result, "runc")


@pytest.mark.parametrize("runtime,exit_code,limits", [
    ("runsc", 0, ["pids_unmetered"]),
    ("runsc", None, []),
    ("runc", None, ["pids_unmetered"]),
])
def test_integration_contract_rejects_missing_evidence(runtime, exit_code, limits):
    result = Evidence(command=FORK_BOMB, exit_code=exit_code, limits=limits)
    with pytest.raises(AssertionError):
        assert_process_exhaustion_evidence(result, runtime)


async def test_hostile_document_then_good_job_with_unmetered_runsc(settings, fake_docker, monkeypatch):
    import httpx
    from app.fetch import Fetcher
    from app.main import create_app

    settings.docsrunner_runtime = "runsc"
    settings.docsrunner_step_seconds = 3
    llms = iter([DemoLLM(demo_plan("hostile")), DemoLLM(demo_plan("good"))])
    runners = {}

    def factory(job_id):
        runners[job_id] = DockerRunner(job_id, settings)
        return runners[job_id]

    # Samples are fetched over HTTP like any URL; serve them from the app itself.
    settings.docsrunner_public_origin = "https://example.org"
    fetcher = Fetcher(settings.docsrunner_public_origin)
    manager = JobManager(settings, fetcher=fetcher, llm_factory=lambda: next(llms), runner_factory=factory)
    app = create_app(settings, manager)

    async def served_document(url):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            response = await client.get(url)
            return response.status_code, dict(response.headers), response.text

    monkeypatch.setattr(fetcher, "request", served_document)
    try:
        jobs = []
        for name in ("hostile", "good"):
            job = await manager.submit(f"{settings.docsrunner_public_origin}/demo-docs/{name}", "demo-client")
            await asyncio.wait_for(asyncio.shield(manager.tasks[job.id]), 5)
            assert job.state == "completed" and job.error is None, (job.state, job.error)
            assert all(job.cleanup[key] for key in ("sandbox_removed", "proxy_removed", "network_removed"))
            jobs.append(job)
        hostile, good = jobs
        assert [s.status for s in hostile.steps] == ["failed"] * 4
        evidence = hostile.steps[-1].attempts[0].evidence
        assert evidence.limits == ["pids_unmetered"] and evidence.exit_code is None
        assert_process_exhaustion_evidence(evidence, hostile.runtime)
        assert "limit reached" not in hostile.steps[-1].reason
        events = hostile.presentation["containment_events"]
        assert "sandbox_stopped" in {e["kind"] for e in events}
        assert "killed_by_limit" not in {e["kind"] for e in events}
        assert not good.presentation["containment_events"] or "killed_by_limit" not in {
            e["kind"] for e in good.presentation["containment_events"]}
        assert [s.status for s in good.steps] == ["passed", "passed", "no_expectation"]
        assert all(s.attempts[0].evidence.limits == ["pids_unmetered"] for s in good.steps)
        assert FORK_BOMB in fake_docker.commands
        assert ("kill", runners[hostile.id].name) in fake_docker.calls
    finally:
        await manager.close()
