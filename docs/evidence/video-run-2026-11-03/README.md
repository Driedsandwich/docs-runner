# Evidence for the video walkthrough, 2026-11-03 23:00–23:06 UTC

The video in `docs/video/` was recorded by driving the public deployment with Playwright (Chromium, 1920×1080). These are the four reports returned by `GET /api/jobs/<id>` for the jobs shown in the video, saved unmodified:

| File | Sample | Result |
|---|---|---|
| `1-good-8421342d.json` | good | 2 passed, 1 no_expectation |
| `2-stale-0301c2c2.json` | stale | `python3 old_app.py` failed (exit 2); repair `python3 app.py` passed as a separate retry |
| `3-hostile-1389d4cc.json` | hostile | 4 of 4 steps failed, all contained; events: injection detected, root read-only, stopped by runner, blocked network |
| `4-good-e3a2963a.json` | good | same as the first good job, in a new sandbox |

Every report has `fixture: null`, `presentation.simulated: false` and runtime `runsc`. Waiting time is shortened in the edit and labelled "Wait shortened" on screen; no step, result or event was removed.
