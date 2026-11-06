import asyncio
import dataclasses
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from app.budgets import BudgetExceeded, BudgetStore
from app.config import Settings
from app.fetch import Fetcher, FetchError
from app.judge import judge
from app.llm import InvalidModelOutput, LLM
from app.models import Attempt, Plan, StepResult
from app.policy import SourceMismatch, classify, verify_plan_source
from app.redact import redact
from sandbox.runner import DockerRunner, SandboxError

log = logging.getLogger("docsrunner.jobs")

TERMINAL = {"completed", "failed", "timed_out", "cancelled"}


class Busy(Exception):
    pass


@dataclass
class Job:
    id: str
    url: str
    state: str = "queued"
    phase: str = "queued"
    created_at: float = field(default_factory=time.time)
    injection: dict | None = None
    steps: list[StepResult] = field(default_factory=list)
    error: str | None = None
    llm_calls: int = 0
    runtime: str | None = None
    cleanup: dict | None = None
    events: list[dict] = field(default_factory=list)
    fixture: str | None = None
    presentation: dict = field(default_factory=dict)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    def public(self, secret="", *, data=None):
        data = data if data is not None else {"id": self.id, "url": self.url, "state": self.state, "phase": self.phase,
                "injection": self.injection, "steps": [s.model_dump() for s in self.steps],
                "error": self.error, "llm_calls": self.llm_calls, "runtime": self.runtime, "cleanup": self.cleanup,
                "fixture": self.fixture, "presentation": self.presentation, "created_at": self.created_at, "revision": len(self.events)}
        # Sanitize values before JSON encoding, preserving valid JSON syntax.
        def clean(value):
            if isinstance(value, str):
                return redact(value, secret)
            if isinstance(value, list):
                return [clean(v) for v in value]
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            return value
        return clean(data)

    def observe(self, kind, detail, evidence, *, step_id=None, attempt=None, boundary="runtime"):
        events = self.presentation.setdefault("containment_events", [])
        events.append({
            "id": f"{self.id}:{len(events) + 1}", "kind": kind, "detail": detail,
            "run_id": self.id, "step_id": step_id, "attempt": attempt,
            "boundary": boundary, "observed_at": time.time(),
            "sandbox_id": self.presentation.get("sandbox_id") if boundary == "runtime" else None,
            "evidence": evidence,
        })

    def observe_attempt(self, step_id, attempt, evidence):
        # pids_unmetered is a visibility note on every gVisor step, not a limit hit.
        if evidence.reached_limits:
            self.observe("killed_by_limit", "Runner reported a resource or time limit and stopped the sandbox.",
                         evidence.model_dump(), step_id=step_id, attempt=attempt)
        elif evidence.exit_code is None:
            detail = "The runner lost its probe of the sandbox during this step and stopped the sandbox."
            if "pids_unmetered" in evidence.limits:
                detail += " gVisor PID-limit events are not metered here, so the cause is not asserted."
            self.observe("sandbox_stopped", detail, evidence.model_dump(), step_id=step_id, attempt=attempt)
        if "read-only file system" in evidence.stderr.lower() and evidence.exit_code != 0:
            self.observe("read_only_root", "Command stderr reports a read-only filesystem error.",
                         evidence.model_dump(), step_id=step_id, attempt=attempt)

    async def emit(self, phase: str):
        self.phase = phase
        async with self.changed:
            event = {"id": len(self.events) + 1, "phase": phase, "state": self.state}
            # Freeze real observations too, preserving intermediate states on replay.
            event["snapshot"] = self.public()
            event["snapshot"]["revision"] = event["id"]
            self.events.append(event)
            self.changed.notify_all()


class JobManager:
    def __init__(self, settings: Settings, *, fetcher=None, llm_factory=None, runner_factory=None):
        self.settings = settings
        self.budgets = BudgetStore(settings.docsrunner_db, settings.docsrunner_daily_llm_limit, settings.docsrunner_rate_per_hour)
        self.fetcher = fetcher or Fetcher(settings.docsrunner_public_origin)
        self.llm_factory = llm_factory or (lambda: LLM(settings, self.budgets))
        self.runner_factory = runner_factory or (lambda job_id: DockerRunner(job_id, settings))
        self.jobs: dict[str, Job] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.fixture_sequence = 1042

    async def submit(self, url: str, address: str, fixture: str | None = None) -> Job:
        if fixture and not self.settings.docsrunner_ui_fixtures:
            raise ValueError("Preview data is disabled")
        async with self.lock:
            if len(self.tasks) >= 2:
                raise Busy("Two jobs are already running")
            self.budgets.client(address)
            now = time.time()
            for job_id, job in list(self.jobs.items()):
                if job_id not in self.tasks and (now - job.created_at > 3600 or len(self.jobs) >= 200):
                    del self.jobs[job_id]
            job = Job(uuid.uuid4().hex, url, fixture=fixture)
            if fixture:
                from app.fixtures import prepare
                prepare(job, f"R-{self.fixture_sequence}")
                self.fixture_sequence += 1
            self.jobs[job.id] = job
            task = asyncio.create_task(self.execute_fixture(job) if fixture else self.execute(job))
            self.tasks[job.id] = task
            task.add_done_callback(lambda _: self.tasks.pop(job.id, None))
            return job

    async def execute_fixture(self, job: Job, continuation=False):
        from app.fixtures import playback, prepare
        await playback(job, continuation=continuation)
        # One independent inert follow-up demonstrates recovery, never a real audit.
        if job.state == "completed" and not continuation and len(self.tasks) < 2:
            follow = Job(uuid.uuid4().hex, job.url, fixture="good")
            prepare(follow, f"R-{self.fixture_sequence}", continuation=True)
            self.fixture_sequence += 1
            self.jobs[follow.id] = follow
            task = asyncio.create_task(self.execute_fixture(follow, continuation=True))
            self.tasks[follow.id] = task
            task.add_done_callback(lambda _: self.tasks.pop(follow.id, None))
            job.presentation["next_job_id"] = follow.id
        await job.emit("finished")

    async def stop(self, job: Job):
        task = self.tasks.get(job.id)
        if task and job.phase not in {"finished", "cleaning_up"} and task.cancelling() == 0:
            # Let a just-created task enter its cleanup-protected body first.
            await asyncio.sleep(0)
            if task.done() or task.cancelling() or job.phase in {"finished", "cleaning_up"}:
                return job
            job.error = "Stopped by request. Captured evidence is preserved."
            task.cancel()
        return job

    async def execute(self, job: Job):
        llm = runner = None
        deadline = asyncio.timeout(self.settings.docsrunner_job_seconds)
        try:
            async with deadline:
                job.state = "running"
                job.presentation = {"simulated": False, "label": job.id,
                                    "limits": {"cpus": 1, "memory_mib": 512, "seconds": self.settings.docsrunner_step_seconds, "pids": 128},
                                    "isolation": "One disposable sandbox shared across this job's steps", "no_secrets": True,
                                    "lifecycle": "not_created", "containment_events": []}
                await job.emit("fetching")
                document = await self.fetcher.fetch(job.url)
                secret = self.settings.vultr_inference_key.get_secret_value()
                raw, body = redact(document.raw, secret), redact(document.body, secret)
                llm = self.llm_factory()
                await job.emit("guarding")
                job.injection = (await llm.guard(raw)).model_dump()
                if job.injection["injection_detected"]:
                    job.observe("injection_detected", job.injection["reason"], job.injection.copy(), boundary="extraction")
                await job.emit("extracting")
                plan = await llm.extract(body)
                if len(plan.steps) > self.settings.docsrunner_max_steps:
                    raise BudgetExceeded("Step limit exceeded")
                verify_plan_source(plan, body)
                job.presentation["plan"] = [{"id": s.id, "title": f"Step {s.id:02}", "command": s.command} for s in plan.steps]
                await job.emit("classifying")
                decisions = await llm.classify(plan)
                by_id = {d.step_id: d for d in decisions.decisions}
                if len(by_id) != len(decisions.decisions) or set(by_id) != {s.id for s in plan.steps}:
                    raise InvalidModelOutput("Classification must cover each step exactly once")
                outcomes = {}
                for step in plan.steps:
                    decision = classify(step, by_id[step.id])
                    if any(outcomes.get(dep) not in {"passed", "no_expectation"} for dep in step.depends_on):
                        result = StepResult(step=step, status="unsupported", reason="A required earlier step did not succeed")
                    elif decision.action != "run":
                        result = StepResult(step=step, status=decision.action, reason=decision.reason)
                    elif runner is not None and not runner.alive:
                        result = StepResult(step=step, status="unsupported", reason="Sandbox stopped after a limit or infrastructure failure")
                    else:
                        if runner is None:
                            runner = self.runner_factory(job.id)
                            job.presentation["lifecycle"] = "creating"
                            await job.emit("starting_sandbox")
                            await runner.start(document.domain)
                            job.runtime = runner.runtime
                            job.presentation.update(lifecycle="isolated", sandbox_id=getattr(runner, "name", None))
                        await job.emit(f"running:{step.id}")
                        evidence = await runner.run(step, self.settings.docsrunner_step_seconds)
                        job.observe_attempt(step.id, 1, evidence)
                        status, reason = judge(step, evidence)
                        result = StepResult(step=step, status=status, reason=reason,
                                            attempts=[Attempt(evidence=evidence, status=status, reason=reason)])
                        # Keep the original failed attempt even if diagnosis fails or times out.
                        job.steps.append(result)
                        if status == "failed":
                            await job.emit(f"diagnosing:{step.id}")
                            result.diagnosis = await llm.diagnose(step, evidence, [s.command for s in plan.steps])
                            command = result.diagnosis.suggested_command
                            if command and runner.alive:
                                repaired = step.model_copy(update={"command": command, "depends_on": []})
                                # Revalidate generated repairs, including NULs and credential policy.
                                repaired = type(step).model_validate(repaired.model_dump())
                                guard = await llm.guard(command)
                                if not guard.injection_detected:
                                    classified = await llm.classify(Plan(steps=[repaired]))
                                    if len(classified.decisions) != 1 or classified.decisions[0].step_id != step.id:
                                        raise InvalidModelOutput("Repair classification is incomplete")
                                    repair_decision = classify(repaired, classified.decisions[0])
                                    if repair_decision.action == "run":
                                        await job.emit(f"retrying:{step.id}")
                                        retry = await runner.run(repaired, self.settings.docsrunner_step_seconds)
                                        job.observe_attempt(step.id, 2, retry)
                                        retry_status, retry_reason = judge(step, retry)
                                        result.attempts.append(Attempt(evidence=retry, status=retry_status, reason=retry_reason))
                                        result.retry_status = retry_status
                                    else:
                                        result.retry_status = repair_decision.action
                                else:
                                    result.retry_status = "unsupported"
                        outcomes[step.id] = result.retry_status if result.retry_status in {"passed", "no_expectation"} else result.status
                        await job.emit(f"judged:{step.id}")
                        continue
                    job.steps.append(result)
                    outcomes[step.id] = result.status
                    await job.emit(f"judged:{step.id}")
                job.state = "completed"
        except TimeoutError:
            if deadline.expired():
                job.state, job.error = "timed_out", "Whole-job deadline reached"
            else:
                job.state, job.error = "failed", "An external operation timed out"
        except asyncio.CancelledError:
            job.state, job.error = "cancelled", job.error or "Service shutdown"
        except (BudgetExceeded, FetchError, InvalidModelOutput, SourceMismatch) as exc:
            job.state, job.error = "failed", str(exc)
        except (SandboxError, FileNotFoundError) as exc:
            log.warning("job %s sandbox error: %s", job.id, redact(str(exc), self.settings.vultr_inference_key.get_secret_value()))
            job.state, job.error = "failed", "Sandbox infrastructure unavailable; inspect VM setup"
        except Exception as exc:
            # Never expose SDK exceptions, keys, payloads or tracebacks through the report;
            # keep a redacted line in the server log so operators can tell causes apart.
            log.error("job %s failed: %s: %s", job.id, type(exc).__name__,
                      redact(str(exc), self.settings.vultr_inference_key.get_secret_value())[:500])
            job.state, job.error = "failed", "Processing failed; check configuration or document format"
        finally:
            job.llm_calls = llm.calls if llm else 0
            if runner is not None:
                job.presentation["lifecycle"] = "destroying"
            await job.emit("cleaning_up")
            if runner is not None:
                try:
                    job.cleanup = dataclasses.asdict(await runner.cleanup())
                    if getattr(runner, "created", None) is False:
                        job.cleanup["not_created"] = True
                    elif getattr(runner, "created", None):
                        job.presentation["sandbox_id"] = runner.name
                    for destination in job.cleanup.get("denied_destinations") or []:
                        job.observe("blocked_network", "Proxy denial log collected during cleanup; request time was not recorded.",
                                    {"destination": destination, "source": "proxy denial log",
                                     "audit_complete": job.cleanup.get("audit_complete", False)})
                    if not all(job.cleanup.get(k) for k in ("sandbox_removed", "proxy_removed", "network_removed")):
                        job.state, job.error = "failed", "Cleanup incomplete; VM operator must remove managed resources"
                except Exception:
                    job.cleanup = {"sandbox_removed": False, "proxy_removed": False, "network_removed": False}
                    job.state, job.error = "failed", "Cleanup could not be verified"
            else:
                job.cleanup = {"not_created": True, "sandbox_removed": True, "proxy_removed": True,
                               "network_removed": True, "denied_destinations": [], "audit_complete": True}
            if job.cleanup.get("sandbox_removed") and not job.cleanup.get("not_created"):
                job.presentation["destroyed_at"] = time.time()
            job.presentation["lifecycle"] = ("not_created" if job.cleanup.get("not_created") else
                                             "destroyed" if job.cleanup.get("sandbox_removed") else "unconfirmed")
            if llm:
                try:
                    await llm.close()
                except Exception:
                    pass
            await job.emit("finished")

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def events(self, job: Job, after: int = 0):
        while True:
            async with job.changed:
                pending = [event for event in job.events if event["id"] > after]
                if not pending and job.phase != "finished":
                    try:
                        await asyncio.wait_for(job.changed.wait(), timeout=15)
                    except TimeoutError:
                        pass
                    pending = [event for event in job.events if event["id"] > after]
            if not pending:
                if job.phase == "finished":
                    return
                yield ": heartbeat\n\n"
            for event in pending:
                after = event["id"]
                safe_event = job.public(self.settings.vultr_inference_key.get_secret_value(), data=event)
                yield f"id: {after}\nevent: progress\ndata: {json.dumps(safe_event)}\n\n"
            if job.phase == "finished":
                return

