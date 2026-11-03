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
The three bundled demo pages and minimal UI require no external assets.

`app/` orchestration/API/judging · `sandbox/` execution · `proxy/` egress ACLs ·
`demo_docs/` fixtures · `tests/` fake and opt-in Docker tests · `deploy/` VM setup.

Detailed product documentation and visual design will follow separately.

