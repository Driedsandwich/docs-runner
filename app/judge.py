from app.models import Evidence, Step, Status


def judge(step: Step, evidence: Evidence) -> tuple[Status, str]:
    if evidence.limits:
        return "failed", "Execution limit reached: " + ", ".join(evidence.limits)
    if evidence.exit_code != 0:
        return "failed", f"Command exited with code {evidence.exit_code}"
    if not step.expectations:
        return "no_expectation", "The document states no expected output or file"
    if len(evidence.matched) != len(step.expectations) or not all(evidence.matched):
        return "failed", "Documented expectations did not all match"
    return "passed", "Exit code 0 and all documented expectations matched"

