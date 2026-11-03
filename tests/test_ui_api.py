"""UI additions use the existing jobs/SSE endpoints and explicitly enabled inert server fixtures."""
import asyncio
import json

import httpx
import pytest

from app import fixtures
from app.jobs import Job, JobManager
from app.main import create_app


async def settle(manager):
    while manager.tasks:
        await asyncio.gather(*list(manager.tasks.values()))
        await asyncio.sleep(0)


@pytest.mark.parametrize("fixture,expected", [
    ("good", ["passed", "passed", "passed", "needs_credential", "no_expectation", "unsupported"]),
    ("stale", ["passed", "passed", "failed", "needs_credential", "no_expectation", "unsupported"]),
    ("hostile", ["passed", "passed", "failed", "needs_credential", "no_expectation", "unsupported"]),
])
async def test_inert_fixture_contract_and_independent_next_job(settings, monkeypatch, fixture, expected):
    settings.docsrunner_ui_fixtures = True
    monkeypatch.setattr(fixtures, "TICK", 0)

    class NeverFetch:
        async def fetch(self, *_):
            pytest.fail("Fixture must never fetch a document")

    def never(*_):
        pytest.fail("Fixture must never create an LLM or runner")

    manager = JobManager(settings, fetcher=NeverFetch(), llm_factory=never, runner_factory=never)
    app = create_app(settings, manager)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
            response = await client.post("/api/jobs", json={"url": fixtures.FIXTURE_URL, "fixture": fixture})
            assert response.status_code == 202
            job_id = response.json()["id"]
            await settle(manager)
            report = (await client.get(f"/api/jobs/{job_id}")).json()
            assert report["fixture"] == fixture
            assert report["state"] == "completed"
            assert report["llm_calls"] == 0 and report["runtime"] is None
            assert [s["status"] for s in report["steps"]] == expected
            presentation = report["presentation"]
            assert presentation["simulated"] is True
            assert presentation["limits"] == {"cpus": 1, "memory_mib": 256, "seconds": 30, "pids": 64}
            details = presentation["step_details"]
            ids = [d["sandbox_id"] for d in details.values() if d["sandbox_id"]]
            assert len(ids) == len(set(ids)) == 4
            for number in ("4", "6"):
                assert details[number]["sandbox_id"] is None
                assert report["steps"][int(number) - 1]["attempts"] == []
            assert all(d["source_line"] and d["preparation"] and d["fixture"] for d in details.values())
            assert presentation["lifecycle"] == "destroyed" and presentation["destroyed_at"]
            if fixture != "good":
                failed = report["steps"][2]
                assert failed["attempts"][0]["evidence"]["command"] == "python server.py --port 8080"
                assert failed["attempts"][0]["evidence"]["exit_code"] == 2
                assert failed["attempts"][0]["evidence"]["duration_seconds"] == 0.18
                assert failed["diagnosis"]["suggested_command"] == "python app.py --port 8080"
                assert len(failed["attempts"]) == 1 and failed["retry_status"] is None
            events = presentation["containment_events"]
            if fixture == "hostile":
                assert [e["kind"] for e in events] == ["injection_detected", "blocked_network", "killed_by_limit", "read_only_root"]
                assert all(e["run_id"] == job_id and e["observed_at"] and e["evidence"] for e in events)
                assert events[0]["boundary"] == "extraction" and events[0]["sandbox_id"] is None
                assert all(e["boundary"] == "runtime" and e["sandbox_id"] for e in events[1:])
                assert events[2]["evidence"]["observed_pids"] == 64
            else:
                assert events == []
            next_id = presentation["next_job_id"]
            assert next_id and next_id != job_id
            following = (await client.get(f"/api/jobs/{next_id}")).json()
            assert following["state"] == "completed"
            assert following["presentation"]["next_job_id"] is None
            assert following["presentation"]["step_details"]["1"]["sandbox_id"] not in ids
            replay = await client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "1"})
            snapshots = [json.loads(line[6:])["snapshot"] for line in replay.text.splitlines() if line.startswith("data: ")]
            assert snapshots[-1] == report
            assert snapshots[0]["state"] == "running"
            assert snapshots[0]["cleanup"] is None
            assert snapshots[0]["revision"] == 2
            if fixture == "hostile":
                observed_counts = [len(s["presentation"]["containment_events"]) for s in snapshots]
                assert set(observed_counts) >= {1, 2, 3, 4}
                assert observed_counts == sorted(observed_counts)


async def test_fixture_validation_stylesheet_and_stop(settings, monkeypatch):
    settings.docsrunner_ui_fixtures = True
    monkeypatch.setattr(fixtures, "TICK", 0.02)
    manager = JobManager(settings)
    app = create_app(settings, manager)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
            assert (await client.post("/api/jobs", json={"url": "https://other.example.org", "fixture": "hostile"})).status_code == 422
            assert (await client.post("/api/jobs", json={"url": fixtures.FIXTURE_URL, "fixture": "unknown"})).status_code == 422
            response = await client.post("/api/jobs", json={"url": fixtures.FIXTURE_URL, "fixture": "hostile"})
            job_id = response.json()["id"]
            assert (await client.post(f"/api/jobs/{job_id}/stop", headers={"Origin": "https://other.example.org"})).status_code == 403
            # Stop after evidence exists, preserving it through confirmed cleanup.
            while not manager.jobs[job_id].steps:
                await asyncio.sleep(0.005)
            original = manager.jobs[job_id].steps[0].model_dump()
            assert (await client.post(f"/api/jobs/{job_id}/stop")).status_code == 202
            assert (await client.post(f"/api/jobs/{job_id}/stop")).status_code == 202
            await settle(manager)
            report = (await client.get(f"/api/jobs/{job_id}")).json()
            assert report["state"] == "cancelled" and report["phase"] == "finished"
            assert report["steps"][0] == original
            assert report["cleanup"]["sandbox_removed"] is True
            assert report["presentation"]["next_job_id"] is None
            assert (await client.post(f"/api/jobs/{job_id}/stop")).status_code == 202
            assert (await client.post("/api/jobs/missing/stop")).status_code == 404
            css = await client.get("/styles.css")
            assert css.status_code == 200 and "text/css" in css.headers["content-type"]
            assert "prefers-reduced-motion" in css.text
            assert "default-src 'self'" in css.headers["content-security-policy"]


async def test_immediate_stop_finishes_without_claiming_created_sandbox(settings, monkeypatch):
    settings.docsrunner_ui_fixtures = True
    monkeypatch.setattr(fixtures, "TICK", 0.02)
    manager = JobManager(settings)
    job = await manager.submit(fixtures.FIXTURE_URL, "client", fixture="good")
    await manager.stop(job)
    await settle(manager)
    assert job.phase == "finished" and job.state == "cancelled"
    assert job.presentation["lifecycle"] == "not_created"
    assert job.presentation["destroyed_at"] is None


def test_presentation_metadata_is_redacted():
    job = Job("test", fixtures.FIXTURE_URL, presentation={"plan": [{"title": "private-sentinel"}]})
    assert "private-sentinel" not in json.dumps(job.public("private-sentinel"))


async def test_stop_does_not_interrupt_cleanup(settings, monkeypatch):
    settings.docsrunner_ui_fixtures = True
    monkeypatch.setattr(fixtures, "TICK", 0.02)
    manager = JobManager(settings)
    job = await manager.submit(fixtures.FIXTURE_URL, "client", fixture="good")
    while job.phase != "cleaning_up":
        await asyncio.sleep(0.001)
    await manager.stop(job)
    await settle(manager)
    assert job.phase == "finished"
    assert job.state == "completed"
    assert job.cleanup["sandbox_removed"]
