# Single Ubuntu VM

Target: Ubuntu 24.04 on a CPU Vultr VM, Docker Engine **28+**, Compose v2,
cgroup v2, iptables enabled. Start with 2 vCPU / 4 GiB RAM; measure on the VM.
Use a dedicated VM for this demonstration. Do not place other workloads or
credentials in sandbox images. The controller's Docker socket is a trusted,
host-administrator-equivalent boundary.

1. Install Docker Engine using [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
   Confirm `docker version` and `docker info` (server >=28, cgroup v2).
   Keep Docker's bridge isolation/firewall rules enabled. The runner rejects
   older engines rather than weakening network isolation.
2. Clone the repository to `/opt/docsrunner`. Copy `.env.example` to `.env`,
   `chmod 600 .env`, and fill it on the VM only:
   - `VULTR_INFERENCE_BASE_URL=https://api.vultrinference.com/v1`
   - `VULTR_INFERENCE_KEY`: your inference key
   - `DOCSRUNNER_MODEL_PLAN`, `DOCSRUNNER_MODEL_EXTRACT`, `DOCSRUNNER_MODEL_GUARD`:
     model IDs actually available to the account. **要確認**: current IDs, quotas,
     `max_tokens` support, JSON adherence and latency for the chosen models.
   - `DOCSRUNNER_PUBLIC_ORIGIN=https://your-domain`, `DOCSRUNNER_DOMAIN=your-domain`
   - `DOCSRUNNER_DAILY_LLM_LIMIT=1000`, `DOCSRUNNER_RATE_PER_HOUR=10`
   Do not leave numeric settings blank. Never commit `.env`, include it in an
   image, paste it into logs, or pass it to a sandbox/proxy.
3. Point the domain's A record at the VM. Open TCP 80/443 and your restricted
   SSH port in the Vultr firewall. No Docker API, proxy or web port is published.
4. Build once and start:

   ```sh
   cd /opt/docsrunner
   docker network create docsrunner-egress
   docker compose --env-file .env -f deploy/compose.yaml build web proxy sandbox
   docker compose --env-file .env -f deploy/compose.yaml up -d web caddy
   ```

   The `proxy`/`sandbox` Compose services are image-build definitions only.
   The controller launches one sandbox plus one dedicated Squid sidecar and
   isolated internal network per job. Caddy obtains and renews HTTPS certificates.
5. Install the crash recovery timer:

   ```sh
   sudo cp deploy/docsrunner-reap.service deploy/docsrunner-reap.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now docsrunner-reap.timer
   ```

## gVisor

Follow the current [gVisor installation instructions](https://gvisor.dev/docs/user_guide/install/)
to add its apt repository and install `runsc`. Then:

```sh
sudo runsc install
sudo systemctl restart docker
docker info --format '{{json .Runtimes}}'
```

The runner picks `runsc` when registered; otherwise it records `runc`. If runsc
is registered but broken, jobs fail closed; there is no silent runtime fallback.
`DOCSRUNNER_RUNTIME=auto` is the default. Set `runc` or `runsc` to select an
explicit runtime; an unregistered runtime fails before creating any resources.
**要確認**: chosen Vultr VM/kernel support, gVisor resource counter visibility,
and performance of Python/npm. See [Docker quick start](https://gvisor.dev/docs/user_guide/quick_start/docker/).

## Verify before the public demo

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/pip install --no-deps -e .
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest -m docker -v \
  -o junit_family=legacy --junitxml=docker-results.xml
curl -fsS https://your-domain/healthz
```

Run good → stale → hostile → good in the UI. Check original vs repaired evidence,
injection flag, blocked domains, resource limits, cleanup and the next job's
health. The integration suite builds sandbox and proxy images once per session,
creates and removes its own egress network, and tests runc plus registered runsc.
Missing runsc is reported as skipped; a broken registered runtime fails.
Set `RUN_DOCKER_BUILD=0` to reuse already built images on subsequent test runs.
Docker Engine 28+ and cgroup v2 are required. Without the Docker CLI the suite
is skipped; an unreachable daemon, build failure or missing image is an error.
The JUnit report includes runtime/image IDs, step evidence, job reports and cleanup
results. All model responses are fixtures; no Vultr key or inference call is needed.
Coverage includes a real denied domain (`example.com`), PyPI through the proxy,
direct/metadata egress, CPU loops, fork exhaustion, OOM, root deletion (also with
GNU rm's root guard disabled), and hostile → good bundled document jobs.
All dangerous commands run only through Docker. Host filesystem checks use a
temporary sentinel and verify the absence of bind mounts; they are not a proof
against every possible container escape.

Use a single Uvicorn worker. Active jobs/reports/SSE live in memory (1 hour,
maximum 200 reports); daily UTC LLM and per-client hourly counters persist in
the `budgets` volume. A restart loses reports but does not reset budgets. The
timer removes managed resources older than ten minutes after crashes. Clients
see no raw IP logs; Caddy is the only trusted forwarded-IP source. The private
control subnet must not overlap the VM's existing networks.

The 300-second work deadline covers fetch/model/execution; bounded cleanup runs
after that deadline. Step timeout kills the entire sandbox, so remaining steps
are unsupported and no repair is run in a new sandbox. A repair never replaces
the original failed verdict. Shell cwd/environment do not persist between steps;
files in /work do. Commands needing `cd` must include it in the command itself.

## Boundaries and remaining limitations

- Exact hostname allowlist, no wildcard subdomains. HTTPS CONNECT passes encrypted
  traffic to allowed hosts; this is not a content/exfiltration filter for those
  hosts. The sandbox has no credentials. HTTP POST is blocked; encrypted HTTPS
  methods are not inspected. Downloads and package install scripts remain untrusted.
- Docker's isolated internal bridge has no host bridge address. Only the job's
  proxy is attached; sandbox external DNS forwarding is disabled. Squid rejects
  private/link-local/metadata destinations, the controller origin's resolved IPs,
  and nonstandard ports; IPv6 is disabled in the container. Confirm
  direct Internet, host and other-job access are blocked on the actual VM.
- Proxy logs store denied domain names only, with bounded rotation. Audit evidence
  is a bounded sample; `audit_complete=false` means omissions are possible. Direct
  packets blocked by network isolation are not proxy denial events.
- Resource counters are read from cgroup v2; `metrics_available=false` means a
  specific OOM/pid event could not be observed. Timeout and Docker OOMKilled still
  supply evidence when available; do not infer OOM from exit code 137 alone.
- Public fetching supports bounded UTF-8 HTML/Markdown/text, strict robots.txt,
  at most four page redirects, no query strings/auth/login pages. GitHub rendered
  pages may be denied by robots; use a public raw document URL if permitted.
  Bundled demo URLs load their exact local fixture without granting local-network
  fetch permission. No JavaScript rendering or credentials are supported.
- Command and expected-output quotes are checked against source text, but semantic
  extraction can still be wrong. Reports expose the source quotes for inspection.
  Model-suggested repairs are advisory, policy-checked and limited to one attempt.
- No public GitHub repository or public demo has been provisioned by this source
  delivery. Set those up after VM containment and real Vultr calls are verified.

References: [Vultr inference API](https://api.vultrinference.com/docs/reference),
[Docker bridge gateway modes](https://docs.docker.com/engine/network/port-publishing/#gateway-modes),
[Squid http_access](https://www.squid-cache.org/Doc/config/http_access/).

