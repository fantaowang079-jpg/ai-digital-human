# tests/test_voice.py
# 语音识别接口：成功返回文字；模型/音频异常必须返回明确的 502，而不是被前端误报「没有听清」。

import base64

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.providers.stt import FasterWhisperSTTProvider


class FakeSTT:
    def transcribe(self, audio_bytes):
        return "你好，这是一次语音测试"


class FailingSTT:
    def transcribe(self, audio_bytes):
        raise RuntimeError("whisper model download failed")


@pytest.fixture()
def client():
    return TestClient(main.app)


def _payload() -> str:
    return base64.b64encode(b"fake-webm-audio-bytes").decode("ascii")


def test_stt_success(client, monkeypatch):
    monkeypatch.setattr(main, "stt_provider", FakeSTT())

    resp = client.post("/api/stt", json={"audio_base64": _payload()})

    assert resp.status_code == 200
    assert resp.json()["text"] == "你好，这是一次语音测试"


def test_stt_failure_returns_explicit_502(client, monkeypatch):
    monkeypatch.setattr(main, "stt_provider", FailingSTT())

    resp = client.post("/api/stt", json={"audio_base64": _payload()})

    assert resp.status_code == 502
    assert "语音识别失败" in resp.json()["detail"]


def test_stt_provider_sets_chinese_recognition_params(monkeypatch):
    class FakeModel:
        def __init__(self):
            self.kwargs = None

        def transcribe(self, path, **kwargs):
            self.kwargs = kwargs
            return [], None

    provider = FasterWhisperSTTProvider(
        "base",
        language="zh",
        beam_size=5,
        initial_prompt="关键词：语音、数字人、知识库",
    )
    model = FakeModel()
    monkeypatch.setattr(provider, "_get_model", lambda: model)

    assert provider.transcribe(b"fake-audio") == ""
    assert model.kwargs["language"] == "zh"
    assert model.kwargs["beam_size"] == 5
    assert "语音" in model.kwargs["initial_prompt"]
