import pytest
from pydantic import ValidationError

from app.judge import judge
from app.models import Decision, Evidence, Expectation, Plan, Step
from app.policy import classify, verify_plan_source


@pytest.mark.parametrize("exit_code,limits,expectations,matched,status", [
    (0, [], [], [], "no_expectation"),
    (1, [], [], [], "failed"),
    (0, ["timeout"], [], [], "failed"),
    (0, ["oom"], [], [], "failed"),
    (0, ["pids"], [], [], "failed"),
    (0, ["pids_unmetered"], [], [], "no_expectation"),
    (0, ["pids_unmetered"], ["hello"], [True], "passed"),
    (1, ["pids_unmetered"], [], [], "failed"),
    (None, ["pids_unmetered"], [], [], "failed"),
    (0, ["timeout", "pids_unmetered"], [], [], "failed"),
    (None, [], [], [], "failed"),
    (0, [], ["hello"], [True], "passed"),
    (0, [], ["hello"], [False], "failed"),
    (0, [], ["hello"], [], "failed"),
    (3, [], ["hello"], [True], "failed"),
])
def test_judgment(exit_code, limits, expectations, matched, status):
    step = Step(id=1, command="printf hello", expectations=[Expectation(kind="stdout", value=s, source_quote=s) for s in expectations])
    evidence = Evidence(command=step.command, exit_code=exit_code, limits=limits, matched=matched)
    assert judge(step, evidence)[0] == status


def test_unmetered_is_not_reported_as_a_limit_hit():
    step = Step(id=1, command="false")
    evidence = Evidence(command=step.command, limits=["pids_unmetered"])
    status, reason = judge(step, evidence)
    assert status == "failed"
    assert "could not be observed" in reason and "not metered" in reason
    assert "limit reached" not in reason


def test_ordinary_failure_under_gvisor_has_no_metering_note():
    step = Step(id=1, command="python3 old_app.py")
    status, reason = judge(step, Evidence(command=step.command, exit_code=2, limits=["pids_unmetered"]))
    assert status == "failed" and reason == "Command exited with code 2"


@pytest.mark.parametrize("command,status", [
    ("export API_KEY='<key>'", "needs_credential"),
    ("gh auth login", "needs_credential"),
    ("curl -H 'Authorization: Bearer placeholder' https://example.org", "needs_credential"),
    ("sudo apt install foo", "unsupported"),
    ("docker run python", "unsupported"),
    ("printf hello", "run"),
    ("rm -rf /", "run"),
])
def test_classification_cannot_override_credentials(command, status):
    step = Step(id=1, command=command)
    assert classify(step, Decision(step_id=1, action="run", reason="model says safe")).action == status


@pytest.mark.parametrize("path", ["../key", "/etc/passwd", "a/../../b", ".", "bad\x00file"])
def test_invalid_file_expectations(path):
    with pytest.raises(ValidationError):
        Expectation(kind="file", value=path, source_quote=path)


def test_expectations_must_have_document_provenance():
    plan = Plan(steps=[Step(id=1, command="printf hello", expectations=[Expectation(kind="stdout", value="hello", source_quote="Expected: hello")])])
    verify_plan_source(plan, "printf hello\nExpected: hello")
    with pytest.raises(ValueError):
        verify_plan_source(plan, "printf hello\nNo expectations")
    with pytest.raises(ValueError):
        verify_plan_source(plan, "printf other\nExpected: hello")


@pytest.mark.parametrize("steps", [
    [Step(id=1, command="x"), Step(id=1, command="y")],
    [Step(id=1, command="x", depends_on=[2])],
    [Step(id=1, command="x", depends_on=[1])],
])
def test_plan_rejects_cycles_and_duplicate_ids(steps):
    with pytest.raises(ValidationError):
        Plan(steps=steps)


def test_hostile_demo_exfiltration_is_not_mistaken_for_a_credential_request():
    from app.fetch import DEMO_DIR
    from bs4 import BeautifulSoup
    soup = BeautifulSoup((DEMO_DIR / "hostile.html").read_text(), "html.parser")
    commands = [node.get_text() for node in soup.select("pre code")]
    assert len(commands) == 4
    for index, command in enumerate(commands, 1):
        decision = Decision(step_id=index, action="run", reason="Sandbox demo")
        assert classify(Step(id=index, command=command), decision).action == "run"



@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', '```\n{"a": 1}```', '  {"a": 1}  '])
def test_strip_fence_keeps_only_the_json_object(text):
    from app.llm import strip_fence
    assert strip_fence(text) == '{"a": 1}'
