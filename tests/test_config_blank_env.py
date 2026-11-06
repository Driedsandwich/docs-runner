from app.config import Settings


def test_blank_values_from_env_example_use_defaults(monkeypatch):
    for name in ("VULTR_INFERENCE_BASE_URL", "DOCSRUNNER_DAILY_LLM_LIMIT", "DOCSRUNNER_RATE_PER_HOUR"):
        monkeypatch.setenv(name, "")
    settings = Settings()
    assert settings.vultr_inference_base_url == "https://api.vultrinference.com/v1"
    assert settings.docsrunner_daily_llm_limit == 1000
    assert settings.docsrunner_rate_per_hour == 10
