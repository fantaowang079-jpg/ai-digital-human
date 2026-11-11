# tests/test_setup.py
# 首次使用向导：查询配置状态、保存 API Key、热切换 LLM、非法 Key 校验。

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.providers.llm import DeepSeekProvider


@pytest.fixture()
def client():
    return TestClient(main.app)


@pytest.fixture()
def setup_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=mock\nDEEPSEEK_API_KEY=\nSYSTEM_PROMPT=测试用提示词\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "ENV_PATH", str(env_path))
    monkeypatch.setattr(main.config, "llm_provider", "mock")
    monkeypatch.setattr(main.config, "deepseek_api_key", "")

    original_llm = main.pipeline.llm
    yield env_path
    main.pipeline.llm = original_llm


def test_setup_status_when_not_configured(client, setup_env):
    resp = client.get("/api/setup/status")

    assert resp.status_code == 200
    assert resp.json() == {"provider": "mock", "key_configured": False}


def test_setup_saves_key_and_hot_swaps_provider(client, setup_env):
    resp = client.post("/api/setup", json={"api_key": "sk-test-1234567890"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "provider": "deepseek"}

    env_text = setup_env.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=deepseek" in env_text
    assert "DEEPSEEK_API_KEY=sk-test-1234567890" in env_text
    assert "SYSTEM_PROMPT=测试用提示词" in env_text  # 保留其它配置

    assert main.config.llm_provider == "deepseek"
    assert isinstance(main.pipeline.llm, DeepSeekProvider)

    status = client.get("/api/setup/status").json()
    assert status == {"provider": "deepseek", "key_configured": True}


def test_setup_rejects_malformed_key(client, setup_env):
    resp = client.post("/api/setup", json={"api_key": "not-a-deepseek-key"})

    assert resp.status_code == 400
    assert "格式" in resp.json()["detail"]
    # 配置没有被改动
    assert main.config.llm_provider == "mock"


def test_setup_rejects_empty_key(client, setup_env):
    resp = client.post("/api/setup", json={"api_key": ""})

    assert resp.status_code == 422
