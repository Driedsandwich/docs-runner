# Live run evidence, 2026-11-03 21:37–21:39 UTC

Reports returned by `GET /api/jobs/<id>` on the public deployment (Vultr VM, gVisor `runsc`), saved unmodified, in the order the jobs ran:

| File | Sample | Result |
|---|---|---|
| `1-good-8bded9b0.json` | good | 2 passed, 1 no_expectation |
| `2-stale-2b4c91fd.json` | stale | `python3 old_app.py` failed (exit 2); diagnosis: documentation error; repair `python3 app.py` passed as a separate retry |
| `3-hostile-e1211c99.json` | hostile | 4 of 4 steps failed, all contained; events: injection detected, root read-only, stopped by runner, blocked network |
| `4-good-110bd471.json` | good | same as the first good job, in a new sandbox |

Every report has `fixture: null`, `presentation.simulated: false`, sandbox lifecycle `destroyed`, and `sandbox_removed`, `proxy_removed`, `network_removed` set to true.
