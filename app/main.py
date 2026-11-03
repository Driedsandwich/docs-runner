from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.budgets import BudgetExceeded
from app.config import Settings
from app.fetch import DEMO_DIR, FetchError, validate_url
from app.jobs import Busy, JobManager

STATIC = Path(__file__).parent / "static"


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)


def create_app(settings: Settings | None = None, manager: JobManager | None = None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.manager = manager or JobManager(settings)
        yield
        await app.state.manager.close()

    app = FastAPI(title="Docs Runner", lifespan=lifespan)

    @app.middleware("http")
    async def safe_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/jobs", status_code=202)
    async def submit(payload: JobRequest, request: Request):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != settings.docsrunner_public_origin.rstrip("/"):
            raise HTTPException(403, "Origin is not allowed")
        url = payload.url
        builtin = any(url == f"{settings.docsrunner_public_origin.rstrip('/')}/demo-docs/{name}" for name in ("good", "stale", "hostile"))
        if not builtin:
            try:
                url = validate_url(url)
            except FetchError as exc:
                raise HTTPException(422, str(exc)) from None
        try:
            job = await request.app.state.manager.submit(url, request.client.host if request.client else "unknown")
        except (BudgetExceeded, Busy) as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from None
        return {"id": job.id, "state": job.state}

    def get_job(request, job_id):
        job = request.app.state.manager.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found or expired")
        return job

    @app.get("/api/jobs/{job_id}")
    async def status(job_id: str, request: Request):
        return get_job(request, job_id).public(settings.vultr_inference_key.get_secret_value())

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request):
        job = get_job(request, job_id)
        try:
            after = max(0, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "Invalid Last-Event-ID") from None
        return StreamingResponse(request.app.state.manager.events(job, after), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.get("/demo-docs/{name}")
    async def demo(name: str):
        if name not in {"good", "stale", "hostile"}:
            raise HTTPException(404)
        return FileResponse(DEMO_DIR / f"{name}.html", media_type="text/html")

    @app.get("/robots.txt")
    async def robots():
        return PlainTextResponse("User-agent: *\nAllow: /demo-docs/\nDisallow: /api/\n")

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/app.js")
    async def script():
        return FileResponse(STATIC / "app.js", media_type="application/javascript")

    return app


app = create_app()
