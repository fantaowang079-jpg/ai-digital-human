# tests/test_voice_config.py
# 语音面板参数接口：前端依赖这些字段决定判定时间条的默认值/可调范围。

from fastapi.testclient import TestClient

import backend.main as main


def test_voice_config_returns_panel_settings():
    client = TestClient(main.app)

    resp = client.get("/api/voice/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["vad_threshold"] > 0
    assert data["display_max_rms"] >= data["vad_threshold"]
    assert data["silence_ms_min"] <= data["silence_ms"] <= data["silence_ms_max"]
    assert data["max_record_ms"] > 0
    assert data["idle_timeout_ms"] > 0
    # 打断阈值必须高于普通 VAD，否则数字人会把自己的声音当成用户插话
    assert data["barge_in_threshold"] > data["vad_threshold"]
    assert data["barge_in_hold_ms"] > 0
    assert data["barge_in_guard_ms"] >= 0


def test_voice_config_default_silence_is_two_seconds():
    client = TestClient(main.app)

    data = client.get("/api/voice/config").json()

    assert data["silence_ms"] == 2000
    assert data["silence_ms_min"] == 1000
    assert data["silence_ms_max"] == 4000
