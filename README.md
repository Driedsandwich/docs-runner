# Docs Runner

Execute public README/quickstart steps inside disposable, credential-free Docker
sandboxes and report documented expectations with evidence. All model calls use
Vultr Serverless Inference. Python 3.12 + FastAPI; MIT.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/pytest
.venv/bin/uvicorn app.main:app --no-access-log
```

Configure the names in `.env.example` in the process environment. Local Uvicorn
does not auto-read `.env`. Real jobs require Docker, the built sandbox/proxy images,
an egress network and valid Vultr settings. See [VM deployment](deploy/README.md).
The A3 containment-map UI and three bundled demo pages require no external assets.
Select `good`, `stale`, or `hostile`, then **Run audit** to replay inert fixture
evidence through the jobs/SSE API. Fixture playback requires no Docker or model
credentials and executes no document commands. Editing the URL switches to the
existing live-audit backend.

`app/` orchestration/API/judging · `sandbox/` execution · `proxy/` egress ACLs ·
`demo_docs/` fixtures · `tests/` fake and opt-in Docker tests · `deploy/` VM setup.

See [UI structure and states](app/static/LAYOUT.md) and
[UI/API notes and local browser verification](app/static/UI_NOTES.md).

