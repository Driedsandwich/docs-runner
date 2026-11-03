import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from app.budgets import BudgetExceeded, BudgetStore
from app.config import Settings
from app.llm import InvalidModelOutput, LLM
from app.models import Guard
from conftest import FakeClient

VALID = json.dumps({"injection_detected": True, "reason": "Hidden instruction"})


def make(settings, answers):
    store = BudgetStore(settings.docsrunner_db, settings.docsrunner_daily_llm_limit, settings.docsrunner_rate_per_hour)
    client = FakeClient(answers)
    return LLM(settings, store, client), client


async def test_invalid_json_retries_once_and_counts(settings):
    llm, client = make(settings, ["invalid", VALID])
    result = await llm.guard("untrusted hidden HTML")
    assert result.injection_detected
    assert llm.calls == 2
    assert len(client.requests) == 2
    assert "untrusted_data" in client.requests[0]["messages"][1]["content"]


async def test_invalid_schema_fails_closed(settings):
    llm, client = make(settings, ['{"injection_detected": false, "reason": "ok", "run": true}', "[]", VALID])
    with pytest.raises(InvalidModelOutput):
        await llm.guard("doc")
    assert len(client.requests) == 2


async def test_per_job_cap_includes_retry(settings):
    settings.docsrunner_max_llm_calls = 1
    llm, client = make(settings, ["bad", VALID])
    with pytest.raises(BudgetExceeded):
        await llm.guard("doc")
    assert len(client.requests) == 1


async def test_failed_api_calls_consume_budget(settings):
    llm, _ = make(settings, [RuntimeError("synthetic failure")])
    with pytest.raises(RuntimeError):
        await llm.guard("doc")
    assert llm.calls == 1


async def test_exact_twenty_call_ceiling(settings):
    llm, client = make(settings, [VALID] * 21)
    for _ in range(20):
        await llm.guard("doc")
    with pytest.raises(BudgetExceeded):
        await llm.guard("doc")
    assert len(client.requests) == 20


async def test_daily_cap_blocks_next_job_client(settings):
    settings.docsrunner_daily_llm_limit = 1
    first, _ = make(settings, [VALID])
    await first.guard("doc")
    second, client = make(settings, [VALID])
    with pytest.raises(BudgetExceeded):
        await second.guard("doc")
    assert len(client.requests) == 0


def test_daily_budget_is_atomic_and_survives_restart(settings):
    store = BudgetStore(settings.docsrunner_db, 7, 2)
    def reserve(_):
        try:
            store.llm()
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(20))) == 7
    restarted = BudgetStore(settings.docsrunner_db, 7, 2)
    with pytest.raises(BudgetExceeded):
        restarted.llm()


def test_rate_limit_does_not_store_client_address(settings):
    store = BudgetStore(settings.docsrunner_db, 10, 2)
    for _ in range(2):
        store.client("192.0.2.10")
    with pytest.raises(BudgetExceeded):
        store.client("192.0.2.10")
    store.client("192.0.2.11")
    with store.connect() as db:
        assert "192.0.2." not in repr(db.execute("SELECT * FROM counters").fetchall())


def test_non_vultr_endpoint_rejected():
    with pytest.raises(ValidationError):
        Settings(vultr_inference_base_url="https://example.org/v1")

