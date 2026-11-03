# Docs Runner A3 UI

## Running

Start the existing FastAPI application (`uvicorn app.main:app`). Open `/`, select
`good`, `stale` or `hostile`, then choose **Run audit**. Selection alone does not
start a job. No CDN, frontend package manager or build step is required.

The default selection is `stale`. These three fixtures use the reserved URL
`https://example.org/quickstart`. The server replays inert, explicitly simulated
evidence through the existing jobs and SSE endpoints. It never calls the fetcher,
LLM, Docker, or a shell for fixture playback. Commands, including hostile probes,
are strings only. The follow-up is one separate, inert fixture job with its own
API ID; it does not recursively launch additional jobs. Labels begin at R-1042
per server process, while actual API IDs remain unique.

Editing the URL clears fixture selection and uses the existing live-audit API.
The mode note identifies this change. A live audit still requires the deployment's
existing runner and model configuration. No live audits were used in UI validation.

## Additive API fields

- `POST /api/jobs`: optional `fixture: good | stale | hostile`. Fixture requests
  must use the reserved fixture URL; normal URL submissions retain existing behavior.
- `GET /api/jobs/{id}`: `fixture`, `created_at`, `revision`, and `presentation`
  (plan, display labels, configured limits, evidence provenance, lifecycle,
  containment observations, and optional `next_job_id`).
- `GET /api/jobs/{id}/events`: same `progress` event and event IDs. Fixture events
  additionally include frozen snapshots, so replay does not collapse distinct
  observations or mutate prior evidence. Real-job progress still refreshes via GET.
- `POST /api/jobs/{id}/stop`: cancels the job, retaining results and waiting for
  cleanup. Repeated stop requests do not interrupt cleanup. Uses the existing
  origin validation and reports missing jobs as 404.
- `/styles.css`: same-origin stylesheet, covered by existing security headers.

The original failed result remains failed after a proposed fix. Existing live
repair attempts are shown separately and are never labelled “Not applied”.
Health counts original passed/failed results; unverified and pending results are
not counted as successes. Containment events do not affect health or coverage.

## Deliberate differences from the visual reference

- System sans and system mono fonts replace IBM Plex; no downloadable fonts.
- Injection detection appears at the extraction boundary on the left, as required
  by the written contract, rather than among runtime container detections.
- Mobile rows and controls are at least 44px high, so the page is taller than the
  compact visual mockup. Evidence provenance and event details can be expanded.
- IDs and timestamps come from the API. The next job actually changes to completed;
  it is never frozen at “Running” for appearance.
- Live runner behavior is retained: it shares one sandbox across steps and uses
  512 MiB / 128 PIDs / its configured timeout. The UI states this in provenance
  and displays these actual limits. Only the inert fixtures model separate
  per-step sandboxes at 256 MiB / 64 PIDs / 30 s. Missing live source-line,
  preparation, and per-event telemetry is explicitly “Not reported”, never invented.
- Fixture playback demonstrates UI state transitions, not Docker enforcement.
  Real Docker validation remains the existing opt-in test suite.

## Verification

`pytest -q` covers old behavior plus fixture validation, inertness, evidence,
independent follow-up jobs, SSE snapshots/replay, stop, redaction, CSS serving,
and cleanup protection. Existing tests are unchanged.

For optional local headless checks install the Python `playwright` package and
its Chromium browser in a development environment, start the local server, then
run `python scripts/validate_ui.py`. This script only permits localhost requests.
It writes screenshots and `ui-validation.json` under `artifacts/`. Playwright is
not an application dependency and introduces no frontend build step.

The browser checks cover 1440px, 390px, a 310px menu, keyboard trap/return,
copy/wrap/output dialog, SSE observation order/reconnect, stop, empty/loading/error,
15 steps, long URLs/output, zero verified steps, reduced motion and contrast.
