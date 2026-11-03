"""Public demos use the ordinary pipeline; preview playback is opt-in only.

HTTP/model/runner doubles are test-only. Real Docker checks remain opt-in.
"""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.fetch import DEMO_DIR, FetchError, Fetcher
from app.jobs import Job, JobManager
from app.main import create_app
from app.models import Evidence, Expectation, Guard, Plan, Step
from conftest import FakeLLM, FakeRunner
from proxy.config import render
from sandbox.runner import SandboxError


@pytest.mark.parametrize("name", ["good", "stale", "hostile"])
async def test_preview_is_unavailable_by_default(settings, monkeypatch, name):
    monkeypatch.delenv("DOCSRUNNER_UI_FIXTURES", raising=False)
    assert Settings().docsrunner_ui_fixtures is False
    manager = JobManager(settings)
    app = create_app(settings, manager)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
            response = await client.post("/api/jobs", json={"url": "https://example.org/quickstart", "fixture": name})
            assert response.status_code == 403
            assert response.json()["detail"] == "Preview data is disabled"
            assert (await client.get("/api/config")).json()["ui_fixtures"] is False
        with pytest.raises(ValueError, match="Preview data is disabled"):
            await manager.submit("https://example.org/quickstart", "client", fixture=name)
        assert not manager.jobs and not manager.tasks


def test_preview_opt_in_environment(monkeypatch):
    monkeypatch.setenv("DOCSRUNNER_UI_FIXTURES", "1")
    assert Settings().docsrunner_ui_fixtures is True


@pytest.mark.parametrize("origin", ["https://demo.example.org/path", "https://demo.example.org?x=y", "file:///tmp", "https://demo.example.org:8000"])
def test_public_origin_rejects_non_origins(origin):
    with pytest.raises(ValidationError):
        Settings(docsrunner_public_origin=origin)


def plan_for(name):
    soup = BeautifulSoup((DEMO_DIR / f"{name}.html").read_text(), "html.parser")
    steps = []
    for i, code in enumerate(soup.select("pre code"), 1):
        expected = []
        paragraph = code.parent.find_next_sibling("p").get_text()
        for kind in ("stdout", "file"):
            marker = f"Expected {kind}: "
            if marker in paragraph:
                value = paragraph.split(marker)[1]
                expected.append(Expectation(kind=kind, value=value, source_quote=marker + value))
        steps.append(Step(id=i, command=code.get_text(), expectations=expected,
                          depends_on=[1] if "Requires step 1" in paragraph else []))
    return Plan(steps=steps)


@pytest.mark.parametrize("name", ["good", "stale", "hostile"])
@pytest.mark.parametrize("preview_enabled", [False, True])
async def test_demo_api_fetch_guard_extract_execute_judge_and_sse(settings, monkeypatch, name, preview_enabled):
    settings.docsrunner_ui_fixtures = preview_enabled
    settings.docsrunner_public_origin = "https://demo.example.org"
    plan = plan_for(name)
    trace = []
    class Model(FakeLLM):
        async def guard(self, raw):
            trace.append("guard")
            self.calls += 1
            assert raw == (DEMO_DIR / f"{name}.html").read_text()
            return Guard(injection_detected="Ignore all previous" in raw, reason="Observed hidden instructions")

        async def extract(self, body):
            trace.append("extract")
            assert "Ignore all previous" not in body and "Forget previous" not in body
            assert all(s.command in body for s in plan.steps)
            return await super().extract(body)

        async def classify(self, plan):
            trace.append("classify")
            return await super().classify(plan)

    evidence = []
    for step in plan.steps:
        failed = name == "hostile" or (name == "stale" and step.id == 2)
        evidence.append(Evidence(command=step.command, exit_code=2 if failed else 0,
                                 stdout="captured stdout", duration_seconds=0.123,
                                 matched=[not failed] * len(step.expectations),
                                 limits=["timeout", "pids"] if name == "hostile" and step.id == 4 else []))
    class Runner(FakeRunner):
        name = "observed-container-name"
        async def start(self, domain):
            trace.append("start")
            assert domain == "demo.example.org"
            config = render(domain, ["93.184.216.34"])
            assert domain in next(line for line in config.splitlines() if line.startswith("acl allowed "))
            # A hostname allowlist entry must not remove the controller-IP boundary.
            assert config.index("http_access deny controller_dst") < config.index("http_access allow allowed")
            await super().start(domain)

        async def run(self, step, timeout):
            trace.append(f"run:{step.id}")
            return await super().run(step, timeout)

    llm, runner = Model(plan), Runner(evidence.copy())
    manager = JobManager(settings, llm_factory=lambda: llm, runner_factory=lambda _: runner)
    app = create_app(settings, manager)
    # Exercise Fetcher.request's IP pinning and the app's actual demo/robots routes.
    client_type = httpx.AsyncClient
    requested = []
    async def public_ips(host):
        assert host == "demo.example.org"
        return ["93.184.216.34"]

    async def serve(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "demo.example.org"
        requested.append(request.url.path)
        trace.append(request.url.path)
        async with client_type(transport=httpx.ASGITransport(app=app), base_url=settings.docsrunner_public_origin) as client:
            return await client.get(request.url.path)

    monkeypatch.setattr("app.fetch.public_ips", public_ips)
    monkeypatch.setattr("app.fetch.httpx.AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(serve), **kw))
    async with app.router.lifespan_context(app):
        async with client_type(transport=httpx.ASGITransport(app=app), base_url=settings.docsrunner_public_origin) as client:
            urls = (await client.get("/api/config")).json()["demo_urls"]
            assert urls[name] == f"https://demo.example.org/demo-docs/{name}"
            response = await client.post("/api/jobs", json={"url": urls[name]})
            assert response.status_code == 202
            job_id = response.json()["id"]
            await asyncio.gather(*list(manager.tasks.values()))
            report = (await client.get(f"/api/jobs/{job_id}")).json()
            assert report["state"] == "completed" and report["error"] is None
            assert report["fixture"] is None and report["presentation"]["simulated"] is False
            assert requested == ["/robots.txt", f"/demo-docs/{name}"]
            assert trace[:6] == ["/robots.txt", f"/demo-docs/{name}", "guard", "extract", "classify", "start"]
            assert runner.commands == [s.command for s in plan.steps]
            assert [s["attempts"][0]["evidence"] for s in report["steps"]] == [e.model_dump() for e in evidence]
            expected = {"good": ["passed", "passed", "no_expectation"], "stale": ["passed", "failed"], "hostile": ["failed"] * 4}
            assert [s["status"] for s in report["steps"]] == expected[name]
            assert report["llm_calls"] == llm.calls >= 3 and runner.cleaned
            assert report["presentation"]["sandbox_id"] == runner.name
            assert report["presentation"]["lifecycle"] == "destroyed"
            assert "next_job_id" not in report["presentation"] and len(manager.jobs) == 1
            events = report["presentation"]["containment_events"]
            assert [e["kind"] for e in events] == (["injection_detected", "killed_by_limit", "blocked_network"] if name == "hostile" else ["blocked_network"])
            for event in events:
                assert event["run_id"] == job_id and event["observed_at"]
                assert event["sandbox_id"] == (None if event["boundary"] == "extraction" else runner.name)
            assert events[-1]["evidence"]["destination"] == "blocked.invalid"
            if name == "hostile":
                assert events[1]["evidence"] == evidence[-1].model_dump()
                assert events[1]["step_id"] == 4 and events[1]["attempt"] == 1
            sse = await client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "1"})
            snapshots = [json.loads(line[6:])["snapshot"] for line in sse.text.splitlines() if line.startswith("data: ")]
            assert snapshots[-1] == report
            assert snapshots[0]["phase"] == "guarding" and snapshots[0]["steps"] == []
            assert any(s["presentation"]["lifecycle"] == "destroying" for s in snapshots)


@pytest.mark.parametrize("url", ["https://demo.example.org/demo-docs/hostile", "https://demo.example.org/other"])
async def test_demo_cannot_bypass_private_dns(monkeypatch, url):
    async def private(host):
        raise FetchError("Private destinations are forbidden")
    monkeypatch.setattr("app.fetch.public_ips", private)
    with pytest.raises(FetchError, match="Private destinations"):
        await Fetcher("https://demo.example.org").fetch(url)


async def test_demo_still_checks_robots_and_redirects(monkeypatch):
    fetcher = Fetcher("https://demo.example.org")
    requested = []
    responses = iter([(200, {}, "User-agent: *\nAllow: /"),
                      (302, {"location": "http://127.0.0.1/private"}, "")])
    original = fetcher.request
    async def request(url):
        requested.append(url)
        if urlsplit(url).hostname == "127.0.0.1":
            return await original(url)
        return next(responses)
    monkeypatch.setattr(fetcher, "request", request)
    with pytest.raises(FetchError, match="forbidden"):
        await fetcher.fetch("https://demo.example.org/demo-docs/good")
    assert requested[-1] == "http://127.0.0.1/robots.txt"


def test_containment_requires_observations():
    job = Job("test", "https://example.org", presentation={"sandbox_id": "actual-id"})
    job.observe_attempt(1, 1, Evidence(command="rm -rf /", exit_code=1, stderr="refusing to remove root"))
    assert not job.presentation.get("containment_events")
    observed = Evidence(command="touch /probe", exit_code=1, stderr="Read-only file system", duration_seconds=0.25)
    job.observe_attempt(2, 1, observed)
    event, = job.presentation["containment_events"]
    assert event["kind"] == "read_only_root" and event["evidence"] == observed.model_dump()
    assert event["step_id"] == 2 and event["sandbox_id"] == "actual-id"


async def test_snapshot_sse_redaction_and_failure_without_preview(settings):
    from conftest import FakeFetcher
    plan = Plan(steps=[Step(id=1, command="true")])
    def unavailable(_):
        raise SandboxError("Docker unavailable")
    manager = JobManager(settings, fetcher=FakeFetcher(plan), llm_factory=lambda: FakeLLM(plan), runner_factory=unavailable)
    job = await manager.submit("https://demo.example.org/demo-docs/good", "client")
    await manager.tasks[job.id]
    assert job.state == "failed" and not job.steps and job.fixture is None
    assert job.presentation["simulated"] is False and job.cleanup["not_created"]
    # A non-sensitive marker exercises exact-value redaction, including JSON escapes.
    from pydantic import SecretStr
    marker = 'redaction-test-"-marker'
    settings.vultr_inference_key = SecretStr(marker)
    job.presentation["diagnostic"] = marker
    await job.emit("finished")
    updates = [json.loads(item.split("data: ")[1]) async for item in manager.events(job)]
    assert updates[-1]["snapshot"]["presentation"]["diagnostic"] == "[REDACTED]"
    assert marker not in str(updates)


def test_ui_defaults_to_live_and_discloses_preview():
    script = Path("app/static/app.js").read_text()
    html = Path("app/static/index.html").read_text()
    assert 'fixture = null' in script
    assert 'fixture ? fixtureURL : demoURLs[value]' in script
    assert '$("preview").checked = false' in script
    assert 'JSON.stringify({url, ...(fixture ? {fixture} : {})})' in script
    assert 'Preview data' in script and 'id="preview-control" hidden' in html
    assert 'Sample (live audit)' in html and 'Sample (fixture)' not in html
