import asyncio
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.fetch import Document
from app.models import Decisions, Decision, Diagnosis, Evidence, Guard, Plan, Step
from sandbox.runner import Cleanup


@pytest.fixture
def settings(tmp_path):
    return Settings(docsrunner_db=str(tmp_path / "budgets.sqlite3"), docsrunner_model_plan="test-model",
                    docsrunner_model_extract="test-model", docsrunner_model_guard="test-model")


class FakeClient:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.requests = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])


class FakeFetcher:
    def __init__(self, plan=None, raw="hidden injection"):
        self.plan = plan or Plan(steps=[Step(id=1, command="printf hello")])
        body = "\n".join(s.command + "\n" + "\n".join(e.source_quote for e in s.expectations) for s in self.plan.steps)
        self.document = Document("https://example.org/doc", "example.org", raw, body)

    async def fetch(self, url):
        return self.document


class FakeLLM:
    def __init__(self, plan, *, injection=False, repair=None, decisions=None):
        self.plan, self.injection, self.repair = plan, injection, repair
        self.decisions = decisions
        self.calls = 0
        self.closed = False
        self.raw_seen = None

    async def guard(self, raw):
        self.calls += 1
        self.raw_seen = raw
        return Guard(injection_detected=self.injection and raw == "hidden injection", reason="Fixture detection")

    async def extract(self, body):
        self.calls += 1
        return self.plan

    async def classify(self, plan):
        self.calls += 1
        return self.decisions or Decisions(decisions=[Decision(step_id=s.id, action="run", reason="Fixture") for s in plan.steps])

    async def diagnose(self, step, evidence):
        self.calls += 1
        return Diagnosis(category="documentation", explanation="Old filename", suggested_command=self.repair)

    async def close(self):
        self.closed = True


class FakeRunner:
    def __init__(self, evidence=None, delay=0):
        self.evidence = list(evidence or [])
        self.delay = delay
        self.commands = []
        self.alive = False
        self.runtime = "fake"
        self.starts = 0
        self.cleaned = False

    async def start(self, domain):
        self.starts += 1
        self.alive = True

    async def run(self, step, timeout):
        self.commands.append(step.command)
        await asyncio.sleep(self.delay)
        result = self.evidence.pop(0) if self.evidence else Evidence(command=step.command, exit_code=0, matched=[True] * len(step.expectations))
        if result.limits:
            self.alive = False
        return result

    async def cleanup(self):
        self.cleaned = True
        self.alive = False
        return Cleanup(True, True, True, ["blocked.invalid"], True)

