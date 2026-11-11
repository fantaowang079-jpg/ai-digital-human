# tests/test_stt_behavior.py
# STT 行为：上游异常要转成 502；中文识别参数必须真正传给 faster-whisper。

import base64

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.providers.stt import FasterWhisperSTTProvider


class RaisingSTT:
    def transcribe(self, audio_bytes):
        raise RuntimeError("model failed to load")


class EchoSTT:
    def transcribe(self, audio_bytes):
        return "你好，我是小助手"


@pytest.fixture()
def client():
    return TestClient(main.app)


def test_stt_provider_failure_returns_502(client, monkeypatch):
    monkeypatch.setattr(main, "stt_provider", RaisingSTT())
    audio = base64.b64encode(b"fake-webm-bytes").decode("ascii")

    resp = client.post("/api/stt", json={"audio_base64": audio})

    assert resp.status_code == 502
    assert "语音识别失败" in resp.json()["detail"]


def test_stt_success_returns_text(client, monkeypatch):
    monkeypatch.setattr(main, "stt_provider", EchoSTT())
    audio = base64.b64encode(b"fake-webm-bytes").decode("ascii")

    resp = client.post("/api/stt", json={"audio_base64": audio})

    assert resp.status_code == 200
    assert resp.json()["text"] == "你好，我是小助手"


class FakeWhisperModel:
    def __init__(self):
        self.kwargs = None

    def transcribe(self, path, **kwargs):
        self.kwargs = kwargs
        return [], None


def test_faster_whisper_uses_chinese_params():
    provider = FasterWhisperSTTProvider(
        model_size="base",
        language="zh",
        beam_size=5,
        initial_prompt="以下是普通话的句子。",
    )
    fake = FakeWhisperModel()
    provider._model = fake  # 跳过真实模型加载

    assert provider.transcribe(b"fake-audio") == ""
    assert fake.kwargs["language"] == "zh"
    assert fake.kwargs["beam_size"] == 5
    assert fake.kwargs["initial_prompt"] == "以下是普通话的句子。"
    assert fake.kwargs["condition_on_previous_text"] is False
