import re

from app.models import Decision, Plan, Step


def credential_required(command: str) -> bool:
    return bool(re.search(
        r"(?i)(api[_-]?key|access[_-]?token|secret|password|authorization|bearer\s|"
        r"(?:aws|gcloud|az|gh|docker|npm)\s+(?:auth|login|configure)|"
        r"<[^>]*(?:key|token|credential)[^>]*>|\b(?:sk|ghp|github_pat)[_-][a-z0-9]{8,})",
        command,
    ))


def classify(step: Step, decision: Decision) -> Decision:
    if credential_required(step.command):
        return Decision(step_id=step.id, action="needs_credential", reason="Credential-related command; never executed")
    if re.search(r"(?i)(\bsudo\b|\bsystemctl\b|\bdocker\s|\bkubectl\s|\bread\s+-[sp])", step.command):
        return Decision(step_id=step.id, action="unsupported", reason="Requires privileged, interactive or external infrastructure")
    return decision


class SourceMismatch(ValueError):
    """The model's plan cites text that is not in the document."""


def verify_plan_source(plan: Plan, body: str) -> None:
    # Models may extract expectations, but may not invent the pass criteria.
    normalized = " ".join(body.split())
    for step in plan.steps:
        if " ".join(step.command.split()) not in normalized:
            raise SourceMismatch("Extracted command is not present in the document body")
        for expected in step.expectations:
            if " ".join(expected.source_quote.split()) not in normalized or expected.value not in expected.source_quote:
                raise SourceMismatch("Extracted expectation lacks a verbatim document source")

