# tests/test_update.py
# 自动检查更新：版本比较、Gitee/GitHub 查询结果、网络失败、关闭开关。

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.version import APP_VERSION


@pytest.fixture()
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def clear_update_cache():
    with main._UPDATE_LOCK:
        main._UPDATE_CACHE.update({"at": 0.0, "ttl": 0, "payload": None})
    yield
    with main._UPDATE_LOCK:
        main._UPDATE_CACHE.update({"at": 0.0, "ttl": 0, "payload": None})


def _release(version: str) -> dict:
    return {
        "version": version,
        "release_url": "https://example.com/release",
        "download_url": "https://example.com/ai-digital-human-windows.zip",
        "notes": "release notes",
        "source": "test",
    }


def test_version_key_and_compare():
    assert main._version_key("v1.2.3") == (1, 2, 3)
    assert main._version_key("1.0.3") == (1, 0, 3)
    assert main._is_newer("v1.0.4", "1.0.3")
    assert not main._is_newer("1.0.3", "v1.0.3")
    assert not main._is_newer("voice-original", "1.0.3")


class _FakeResponse:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._payload


def test_query_latest_release_falls_back_to_gitee_tags(monkeypatch):
    def fake_get(url, **kwargs):
        if "/releases" in url:
            return _FakeResponse([])
        if "/tags" in url:
            return _FakeResponse(
                [
                    {"name": "voice-original"},
                    {"name": "v1.0.2"},
                    {"name": "v1.0.4"},
                ]
            )
        return _FakeResponse({}, ok=False, status_code=500)

    monkeypatch.setattr(main.requests, "get", fake_get)

    data = main._query_latest_release()

    assert data is not None
    assert data["version"] == "v1.0.4"
    assert data["source"] == "gitee-tag"
    assert data["download_url"].endswith(
        "/v1.0.4/ai-digital-human-windows.zip"
    )


def test_update_reports_new_version(client, monkeypatch):
    monkeypatch.setattr(
        main, "_query_latest_release", lambda: _release("v99.0.0")
    )

    resp = client.get("/api/update?refresh=1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["current"] == APP_VERSION
    assert data["latest"] == "v99.0.0"
    assert data["update_available"] is True
    assert data["download_url"].endswith("ai-digital-human-windows.zip")


def test_update_reports_no_new_version(client, monkeypatch):
    monkeypatch.setattr(
        main, "_query_latest_release", lambda: _release(APP_VERSION)
    )

    data = client.get("/api/update?refresh=1").json()

    assert data["update_available"] is False
    assert data["latest"] == APP_VERSION


def test_update_reports_network_error(client, monkeypatch):
    monkeypatch.setattr(main, "_query_latest_release", lambda: None)

    data = client.get("/api/update?refresh=1").json()

    assert data["enabled"] is True
    assert data["update_available"] is False
    assert "检查更新失败" in data["error"]


def test_update_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(main.config, "update_check_enabled", False)

    data = client.get("/api/update").json()

    assert data["enabled"] is False
    assert data["update_available"] is False


def test_health_includes_version(client):
    assert client.get("/api/health").json()["version"] == APP_VERSION
