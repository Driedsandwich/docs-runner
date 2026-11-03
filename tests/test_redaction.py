from app.jobs import Job
from app.models import Step, StepResult
from app.redact import redact


def test_runtime_secret_is_redacted_from_every_public_string():
    # Entirely synthetic sentinel, not an actual credential.
    secret = "synthetic-sensitive-sentinel"
    job = Job("test", "https://example.org/doc")
    job.error = secret
    job.injection = {"reason": secret}
    job.steps = [StepResult(step=Step(id=1, command="printf " + secret), status="needs_credential", reason=secret)]
    assert secret not in str(job.public(secret))
    assert "[REDACTED]" in str(job.public(secret))


def test_common_credential_assignments_are_redacted():
    assert "placeholder-value" not in redact("API_KEY=placeholder-value")
    assert "placeholder-value" not in redact("Authorization: Bearer placeholder-value")
