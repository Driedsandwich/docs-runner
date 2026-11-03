from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Expectation(StrictModel):
    kind: Literal["stdout", "stderr", "file"]
    value: str = Field(min_length=1, max_length=1024)
    source_quote: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def safe_file(self):
        if self.kind == "file":
            path = PurePosixPath(self.value)
            if path.is_absolute() or ".." in path.parts or self.value == "." or "\x00" in self.value:
                raise ValueError("Expected files must be relative to /work")
        return self


class Step(StrictModel):
    id: int = Field(ge=1, le=15)
    command: str = Field(min_length=1, max_length=8000)
    depends_on: list[int] = Field(default_factory=list, max_length=14)
    expectations: list[Expectation] = Field(default_factory=list, max_length=12)

    @field_validator("command")
    @classmethod
    def no_nul(cls, value):
        if "\x00" in value:
            raise ValueError("NUL is not supported")
        return value


class Plan(StrictModel):
    steps: list[Step] = Field(max_length=15)

    @model_validator(mode="after")
    def ordered(self):
        seen = set()
        for step in self.steps:
            if step.id in seen or any(dep not in seen for dep in step.depends_on):
                raise ValueError("Steps must have unique ids and refer only to earlier steps")
            seen.add(step.id)
        return self


class Guard(StrictModel):
    injection_detected: bool
    reason: str = Field(max_length=2000)


class Decision(StrictModel):
    step_id: int = Field(ge=1, le=15)
    action: Literal["run", "needs_credential", "unsupported"]
    reason: str = Field(max_length=2000)


class Decisions(StrictModel):
    decisions: list[Decision] = Field(max_length=15)


class Diagnosis(StrictModel):
    category: Literal["environment", "documentation", "unknown"]
    explanation: str = Field(max_length=2000)
    suggested_command: str | None = Field(default=None, max_length=8000)


class Evidence(StrictModel):
    command: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0
    # pids_unmetered is a visibility annotation, never proof of a limit hit.
    limits: list[Literal["timeout", "oom", "pids", "pids_unmetered"]] = Field(default_factory=list)
    matched: list[bool] = Field(default_factory=list)
    metrics_available: bool = False

    @property
    def reached_limits(self) -> list[str]:
        return [limit for limit in self.limits if limit != "pids_unmetered"]


Status = Literal["passed", "failed", "needs_credential", "no_expectation", "unsupported"]


class Attempt(StrictModel):
    evidence: Evidence
    status: Status
    reason: str


class StepResult(StrictModel):
    step: Step
    status: Status
    reason: str
    attempts: list[Attempt] = Field(default_factory=list)
    diagnosis: Diagnosis | None = None
    retry_status: Status | None = None

