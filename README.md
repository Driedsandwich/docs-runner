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

Configure the names listed in [VM deployment](deploy/README.md) in the process environment. Local Uvicorn
does not auto-read `.env`. Real jobs require Docker, the built sandbox/proxy images,
an egress network and valid Vultr settings. See [VM deployment](deploy/README.md).
The A3 containment-map UI and three bundled demo pages require no external assets.
Set `DOCSRUNNER_PUBLIC_ORIGIN` to this app's public HTTP(S) origin. Select `good`,
`stale`, or `hostile`, then **Run audit** to fetch `/demo-docs/<name>` through the
normal robots/DNS/HTTP checks and run the full guard → extraction → Docker →
judgment pipeline. Docker and Vultr inference configuration are required for
all three samples. Results and containment events come from those jobs.

Fixture playback is disabled by default, including direct API requests. For
local UI development only, start with `DOCSRUNNER_UI_FIXTURES=1` and select the
**Preview data** checkbox. Samples still default to live audits even when this
option is enabled. Preview evidence is explicitly labelled and never calls a
model or Docker. Do not enable this setting in the public deployment.

`app/` orchestration/API/judging · `sandbox/` execution · `proxy/` egress ACLs ·
`demo_docs/` sample documents · `tests/` fake and opt-in Docker tests · `deploy/` VM setup.

See [UI structure and states](app/static/LAYOUT.md) and
[UI/API notes and local browser verification](app/static/UI_NOTES.md).

