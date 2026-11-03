# Docs Runner A3 UI

## Running

Start the FastAPI app and open `/`. Select `good`, `stale` or `hostile`, then
**Run audit**. Selection alone does not start a job. The default sample is
`stale`. All three submit the public `/demo-docs/<name>` URLs from `/api/config`,
derived from `DOCSRUNNER_PUBLIC_ORIGIN`, with no fixture field. They use the
ordinary HTTP fetch, injection guard, extraction, classification, Docker runner
and judgment pipeline. Docker and Vultr settings are required; see
[VM setup](../../deploy/README.md). There is no local-file or simulated fallback.

Editing the URL uses the same live pipeline. The A3 layout, evidence panels,
containment column, lifecycle animation, stop and reconnect behavior are shared.
Live jobs use one sandbox per job, shared across steps, with actual runner limits
(512 MiB, 128 PIDs, 1 CPU, configured step timeout). Retries appear separately;
the original failure remains failed. Missing source lines/metrics are not invented.

## Development preview only

`DOCSRUNNER_UI_FIXTURES=1 uvicorn app.main:app` enables an optional **Preview data**
checkbox. It is unchecked by default. Only after explicitly checking it do sample
buttons submit inert fixtures. The mode note, run header and evidence disclosure
say **Preview data**. Public deployments must leave this setting unset or `0`.
The API rejects fixture requests with HTTP 403 when disabled, and the manager
also rejects direct playback submission. Preview playback does not fetch, call
models or Docker, and launches at most one independent inert follow-up fixture.
Live jobs never launch a pretend follow-up; run another audit to observe recovery.

## API and observations

- `GET /api/config`: public demo URLs and whether UI preview is enabled. No keys.
- `POST /api/jobs`: normal `{url}`. Optional `fixture: good | stale | hostile`
  requires preview opt-in and the reserved `https://example.org/quickstart` URL.
- `GET /api/jobs/{id}`: actual steps, attempts, runtime, call count, cleanup and
  presentation metadata. Fixture jobs have `fixture` set and `simulated: true`.
- SSE `progress` events contain frozen snapshots for both live and preview jobs.
  Reconnect preserves observations and state transitions; values are redacted
  before delivery, including snapshots.
- `POST /api/jobs/{id}/stop`: preserves captured evidence and awaits cleanup.
  Repeated stop requests do not interrupt cleanup.

Injection events contain the guard result and belong to the extraction boundary.
Limit events contain the runner's exact attempt evidence. A read-only event is
added only when a failing attempt's stderr reports “Read-only file system”; a
command's destructive name or GNU rm's root guard is not sufficient evidence.
Network events are derived from denied destinations in the proxy cleanup log.
Their timestamps mean evidence collection, not the instant of the request.
Audit completeness and unavailable metrics remain visible in the evidence.
Containment observations do not contribute to the doc-health score.

## Verification

`pytest -q` covers preview opt-in/inertness, live sample routing through served
HTML and the ordinary pipeline, DNS/redirect checks, real-data event projection,
SSE replay/redaction, stop and cleanup. Model and runner doubles are test-only;
Docker enforcement remains the opt-in suite in `tests/test_docker.py`.

For optional developer visual checks, enable preview on the local server, install
Playwright in the development environment and run `python scripts/validate_ui.py`.
The script explicitly selects **Preview data** and permits only localhost browser
requests. It writes to `artifacts/`. This checks presentation, not real execution.
No browser tools were used for the real-demo change.
