# tests/test_setup.py
# 首次 API Key 配置：保存到 .env、热切换 LLM、Key 不外泄、非法输入被拒。

import types

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.config import Config
from backend.providers.llm import DeepSeekProvider, MockLLMProvider

VALID_KEY = "sk-" + "a" * 30


@pytest.fixture()
def client():
    return TestClient(main.app)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "LLM_PROVIDER=mock\n"
        "DEEPSEEK_API_KEY=\n"
        "SYSTEM_PROMPT=测试人格\n",
        encoding="utf-8",
    )
    cfg = Config()
    fake_pipeline = types.SimpleNamespace(llm=MockLLMProvider())

    monkeypatch.setattr(main, "ENV_PATH", str(env))
    monkeypatch.setattr(main, "config", cfg)
    monkeypatch.setattr(main, "pipeline", fake_pipeline)
    return env, cfg, fake_pipeline


def test_status_defaults_to_mock(client, isolated):
    resp = client.get("/api/setup/status")

    assert resp.status_code == 200
    assert resp.json()["provider"] == "mock"
    assert resp.json()["has_key"] is False
    assert resp.json()["key_masked"] == ""


def test_save_key_writes_env_and_hot_swaps(client, isolated):
    env, cfg, fake_pipeline = isolated

    resp = client.post(
        "/api/setup", json={"provider": "deepseek", "api_key": VALID_KEY}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "deepseek"
    assert data["has_key"] is True
    assert VALID_KEY not in resp.text
    assert data["key_masked"].endswith("aaaa")

    text = env.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=deepseek" in text
    assert "DEEPSEEK_API_KEY=" + VALID_KEY in text
    assert "SYSTEM_PROMPT=测试人格" in text  # 其他配置不能被覆盖

    assert cfg.llm_provider == "deepseek"
    assert cfg.deepseek_api_key == VALID_KEY
    assert isinstance(fake_pipeline.llm, DeepSeekProvider)


def test_invalid_key_is_rejected_without_changes(client, isolated):
    env, cfg, fake_pipeline = isolated

    resp = client.post(
        "/api/setup", json={"provider": "deepseek", "api_key": "not-a-key"}
    )

    assert resp.status_code == 400
    assert "sk-" in resp.json()["detail"]
    assert "LLM_PROVIDER=deepseek" not in env.read_text(encoding="utf-8")
    assert cfg.llm_provider == "mock"
    assert isinstance(fake_pipeline.llm, MockLLMProvider)


def test_deepseek_requires_a_key(client, isolated):
    resp = client.post("/api/setup", json={"provider": "deepseek"})

    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]


def test_switch_back_to_mock_keeps_saved_key(client, isolated):
    env, cfg, fake_pipeline = isolated
    client.post("/api/setup", json={"provider": "deepseek", "api_key": VALID_KEY})

    resp = client.post("/api/setup", json={"provider": "mock"})

    assert resp.status_code == 200
    assert resp.json()["provider"] == "mock"
    assert resp.json()["has_key"] is True  # Key 仍保留，方便以后切回
    assert isinstance(fake_pipeline.llm, MockLLMProvider)
    assert cfg.llm_provider == "mock"
    assert "DEEPSEEK_API_KEY=" + VALID_KEY in env.read_text(encoding="utf-8")
