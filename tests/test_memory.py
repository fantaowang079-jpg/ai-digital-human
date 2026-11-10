# tests/test_memory.py
# 上下文记忆：持久化、模式隔离、回忆检索、滚动摘要、API 与流式逐词事件。

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.config import Config
from backend.memory import MemoryStore, extractive_summary
from backend.pipeline import DigitalHumanPipeline


class FakeLLM:
    def __init__(self):
        self.messages = None

    def chat(self, messages):
        self.messages = messages
        return "好的。"

    def stream_chat(self, messages):
        self.messages = messages
        yield "第一"
        yield "句。"


class FakeTTS:
    def synthesize(self, text):
        return b"audio"


class ApiFakePipeline:
    def reply(self, history, mode="professional", session_id=None):
        return "你好。", b"audio"

    def retrieve_sources(self, history, mode="professional"):
        return []

    def stream_events(self, history, mode="professional", session_id=None):
        yield {"type": "delta", "text": "你好。"}
        yield {"type": "sentence", "text": "你好。", "audio": b"audio"}


@pytest.fixture()
def client():
    return TestClient(main.app)


# ---------------------------------------------------------------- MemoryStore
def test_append_and_recent_messages(tmp_path):
    store = MemoryStore(str(tmp_path / "m.db"))

    count = store.append_exchange("s1", "professional", "我叫小王", "你好，小王")
    assert count == 2

    messages = store.recent_messages("s1", "professional")
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "我叫小王"


def test_duplicate_exchange_is_not_stored_twice(tmp_path):
    store = MemoryStore(str(tmp_path / "m.db"))
    store.append_exchange("s1", "professional", "我叫小王", "你好，小王")
    store.append_exchange("s1", "professional", "我叫小王", "你好，小王")

    assert store.count("s1", "professional") == 2


def test_memory_is_isolated_by_mode(tmp_path):
    store = MemoryStore(str(tmp_path / "m.db"))
    store.append_exchange("s1", "professional", "数据库索引怎么优化", "可以建联合索引")
    store.append_exchange("s1", "psychologist", "今天有点累", "听起来很不容易")

    professional = store.overview("s1", "professional")
    psychologist = store.overview("s1", "psychologist")
    assert "数据库" in professional["messages"][0]["content"]
    assert "数据库" not in psychologist["messages"][0]["content"]


def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "m.db")
    store = MemoryStore(path)
    store.append_exchange("s1", "professional", "记住我叫小王", "好的，小王")

    reopened = MemoryStore(path)
    assert reopened.recent_messages("s1", "professional")[0]["content"] == "记住我叫小王"


def test_recall_finds_relevant_old_message(tmp_path):
    store = MemoryStore(str(tmp_path / "m.db"))
    store.append_exchange(
        "s1", "professional", "我住在杭州，平时喜欢喝龙井茶", "杭州是个好地方"
    )
    store.append_exchange(
        "s1", "professional", "今天讲讲数据库索引", "索引可以加快查询"
    )

    hits = store.recall("s1", "professional", "我住在哪个城市", limit=2)

    assert hits
    assert "杭州" in hits[0]["content"]


def test_recall_excludes_current_history(tmp_path):
    store = MemoryStore(str(tmp_path / "m.db"))
    store.append_exchange("s1", "professional", "我住在杭州", "好的")

    hits = store.recall(
        "s1",
        "professional",
        "我住在杭州",
        exclude={("user", "我住在杭州"), ("assistant", "好的")},
    )

    assert hits == []


def test_rolling_summary(tmp_path):
    store = MemoryStore(
        str(tmp_path / "m.db"), keep_verbatim=1, summarize_after=2
    )
    for i in range(3):
        store.append_exchange("s1", "professional", f"用户消息{i}", f"助手回复{i}")

    calls = []

    def fake_summarize(existing, transcript):
        calls.append(transcript)
        return "用户最近在聊日常。"

    summary = store.summarize_now("s1", "professional", fake_summarize)

    assert summary == "用户最近在聊日常。"
    assert store.get_summary("s1", "professional") == "用户最近在聊日常。"
    assert calls and "用户消息0" in calls[0]
    # 已经覆盖的旧消息不会重复摘要
    assert store.summarize_now("s1", "professional", fake_summarize) is None


def test_extractive_summary_fallback():
    text = "用户：我叫小王\n助手：你好\n用户：我住在杭州"
    summary = extractive_summary(text)

    assert "我叫小王" in summary
    assert "我住在杭州" in summary


# ---------------------------------------------------------------- Pipeline
def test_pipeline_injects_long_term_memory(tmp_path):
    store = MemoryStore(str(tmp_path / "m.db"))
    store.append_exchange(
        "s1", "professional", "我叫小王，住在杭州。", "好的，小王。"
    )
    cfg = Config(memory_enabled=True)
    pipeline = DigitalHumanPipeline(
        cfg, FakeLLM(), FakeTTS(), knowledge_base=None, memory=store
    )

    messages = pipeline.build_messages(
        [{"role": "user", "content": "我住在哪里？"}], "professional", "s1"
    )

    system = messages[0]["content"]
    assert "【长期记忆】" in system
    assert "杭州" in system


def test_psychologist_mode_also_uses_memory(tmp_path):
    store = MemoryStore(str(tmp_path / "m.db"))
    store.append_exchange("s1", "psychologist", "我最近总失眠", "听起来很难受")
    cfg = Config(memory_enabled=True)
    pipeline = DigitalHumanPipeline(
        cfg, FakeLLM(), FakeTTS(), knowledge_base=None, memory=store
    )

    system = pipeline.build_messages(
        [{"role": "user", "content": "我该怎么办"}], "psychologist", "s1"
    )[0]["content"]

    assert "【长期记忆】" in system
    assert "失眠" in system
    assert "【知识库资料】" not in system


def test_stream_events_emit_deltas_then_sentence(tmp_path):
    cfg = Config(memory_enabled=False)
    pipeline = DigitalHumanPipeline(cfg, FakeLLM(), FakeTTS())

    events = list(
        pipeline.stream_events([{"role": "user", "content": "你好"}], "psychologist")
    )

    assert [e["type"] for e in events] == ["delta", "delta", "sentence"]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "第一句。"
    assert events[-1]["audio"] == b"audio"


# ---------------------------------------------------------------- API
@pytest.fixture()
def memory_client(tmp_path, monkeypatch):
    store = MemoryStore(str(tmp_path / "api.db"))
    monkeypatch.setattr(main, "memory_store", store)
    monkeypatch.setattr(main.pipeline, "memory", store)
    return TestClient(main.app), store


def test_memory_api_lists_and_clears(memory_client):
    client, store = memory_client
    store.append_exchange("s_api", "professional", "我叫小王", "你好小王")

    resp = client.get(
        "/api/memory", params={"session_id": "s_api", "mode": "professional"}
    )
    data = resp.json()
    assert resp.status_code == 200
    assert data["enabled"] is True
    assert data["total_messages"] == 2
    assert data["messages"][0]["content"] == "我叫小王"

    resp = client.delete(
        "/api/memory", params={"session_id": "s_api", "mode": "professional"}
    )
    assert resp.json()["ok"] is True
    assert (
        client.get(
            "/api/memory", params={"session_id": "s_api", "mode": "professional"}
        ).json()["total_messages"]
        == 0
    )


def test_memory_api_rejects_invalid_session_id(memory_client):
    client, _ = memory_client
    resp = client.get("/api/memory", params={"session_id": "bad id!"})
    assert resp.status_code == 400


def test_chat_writes_exchange_to_memory(memory_client, monkeypatch):
    client, store = memory_client
    monkeypatch.setattr(main, "pipeline", ApiFakePipeline())

    resp = client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "记住我叫小王"}],
            "session_id": "s_chat",
        },
    )

    assert resp.status_code == 200
    overview = store.overview("s_chat", "professional")
    assert [m["content"] for m in overview["messages"]] == ["记住我叫小王", "你好。"]


def test_chat_rejects_invalid_session_id(client):
    resp = client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "你好"}],
            "session_id": "bad id!",
        },
    )
    assert resp.status_code == 422
