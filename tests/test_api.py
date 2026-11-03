import asyncio

import httpx

from app.jobs import JobManager
from app.main import create_app
from app.models import Plan, Step
from conftest import FakeFetcher, FakeLLM, FakeRunner


async def test_submit_status_sse_and_demo_pages(settings):
    plan = Plan(steps=[Step(id=1, command="printf hello")])
    manager = JobManager(settings, fetcher=FakeFetcher(plan), llm_factory=lambda: FakeLLM(plan), runner_factory=lambda _: FakeRunner())
    app = create_app(settings, manager)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
            result = await client.post("/api/jobs", json={"url": "https://example.org/doc"})
            assert result.status_code == 202
            job_id = result.json()["id"]
            await asyncio.gather(*list(manager.tasks.values()))
            report = (await client.get(f"/api/jobs/{job_id}")).json()
            assert report["state"] == "completed"
            assert report["steps"][0]["status"] == "no_expectation"
            events = await client.get(f"/api/jobs/{job_id}/events")
            assert events.headers["content-type"].startswith("text/event-stream")
            assert "finished" in events.text
            assert (await client.get("/api/jobs/missing")).status_code == 404
            assert (await client.get("/api/jobs/missing/events")).status_code == 404
            for name in ("good", "stale", "hostile"):
                response = await client.get(f"/demo-docs/{name}")
                assert response.status_code == 200
                assert "Content-Security-Policy" in response.headers
            assert (await client.get("/")).status_code == 200
            assert (await client.get("/app.js")).status_code == 200
            assert (await client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "bad"})).status_code == 400


async def test_cross_origin_and_bad_url_rejected(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
            assert (await client.post("/api/jobs", json={"url": "https://example.org/doc"}, headers={"Origin": "https://other.example.org"})).status_code == 403
            assert (await client.post("/api/jobs", json={"url": "file:///etc/passwd"})).status_code == 422
