# tests/test_knowledge.py
# 知识库（RAG-lite）：切词/切片、检索、上传/删除 API、上下文注入与来源事件。

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.config import Config
from backend.knowledge import (
    KnowledgeBase,
    extract_text,
    sanitize_filename,
    split_text,
    tokenize,
)
from backend.pipeline import DigitalHumanPipeline

MANUAL = """# 星尘咖啡机使用手册

星尘咖啡机 X1 的最大水箱容量是 1.8 升，建议使用纯净水。

## 保修

整机保修期为两年，人为损坏不在保修范围内。

## 清洁

每月用柠檬酸清洗一次水路，可延长机器寿命。
"""


@pytest.fixture()
def client():
    return TestClient(main.app)


@pytest.fixture()
def kb(tmp_path):
    (tmp_path / "咖啡机手册.md").write_text(MANUAL, encoding="utf-8")
    return KnowledgeBase(str(tmp_path), min_score=0.0)


def test_tokenize_chinese_bigrams_and_english():
    assert tokenize("数字人 RAG") == ["数字", "字人", "rag"]
    assert tokenize("好") == ["好"]


def test_split_text_respects_chunk_size():
    text = "第一段。" * 200
    chunks = split_text(text, chunk_size=100, overlap=10)
    assert chunks
    assert all(len(c) <= 100 for c in chunks)


def test_split_text_keeps_all_headings_in_outline():
    text = (
        "# 题库\n\n## 数据库\n\n### MySQL的索引\n\n### MySQL的事务\n\n"
        "## Agent\n\n### 什么是Agent\n"
    )
    joined = "\n".join(split_text(text))

    assert "MySQL的索引" in joined
    assert "MySQL的事务" in joined
    assert "什么是Agent" in joined


def test_split_text_binds_heading_to_body():
    text = "# 手册\n\n## 保修\n\n整机保修两年。\n\n## 清洁\n\n每月清洗一次。"
    chunks = split_text(text)

    assert any("保修" in c and "两年" in c for c in chunks)
    assert any("清洁" in c and "每月" in c for c in chunks)


def test_recursive_scan_indexes_subdirectories(tmp_path):
    sub = tmp_path / "主线剧情"
    sub.mkdir()
    (sub / "剧情.md").write_text("黑暗时代剧情：博士在石棺中醒来。", encoding="utf-8")

    kb = KnowledgeBase(str(tmp_path), min_score=0.0)

    docs = kb.stats()["documents"]
    assert [d["name"] for d in docs] == ["主线剧情/剧情.md"]
    hits = kb.search("黑暗时代剧情", top_k=1)
    assert hits and hits[0]["source"] == "主线剧情/剧情.md"


def test_parse_cache_avoids_reparsing(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("缓存测试内容。", encoding="utf-8")
    KnowledgeBase(str(tmp_path), min_score=0.0)

    import backend.knowledge as knowledge_mod

    calls = []
    real_extract = knowledge_mod.extract_text

    def counting_extract(name, data):
        calls.append(name)
        return real_extract(name, data)

    monkeypatch.setattr(knowledge_mod, "extract_text", counting_extract)
    kb = KnowledgeBase(str(tmp_path), min_score=0.0)

    assert calls == []  # 第二次启动直接复用磁盘缓存
    assert kb.search("缓存测试内容", top_k=1)


def test_changed_file_is_reparsed(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("旧内容。", encoding="utf-8")
    kb = KnowledgeBase(str(tmp_path), min_score=0.0)

    path.write_text("全新的内容，介绍量子计算机的原理。", encoding="utf-8")
    kb.refresh()

    hits = kb.search("量子计算机", top_k=1)
    assert hits and "量子计算机" in hits[0]["text"]


def test_sanitize_filename_blocks_path_traversal():
    assert sanitize_filename("../../etc/passwd.txt") == "passwd.txt"
    assert sanitize_filename("..\\..\\机密 文档.md") == "机密 文档.md"


def test_docx_extraction():
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body><w:p><w:r>'
        "<w:t>数字人知识库测试</w:t></w:r></w:p></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)

    assert "数字人知识库测试" in extract_text("test.docx", buf.getvalue())


def test_search_returns_relevant_chunk_first(kb):
    hits = kb.search("水箱容量多大", top_k=3)
    assert hits
    assert "1.8" in hits[0]["text"]
    assert hits[0]["score"] > 0


def test_search_irrelevant_query_scores_low(kb):
    hits = kb.search("今天天气怎么样", top_k=3)
    assert not hits or hits[0]["score"] < 0.1


def test_stats_and_delete(tmp_path, kb):
    stats = kb.stats()
    assert stats["total_documents"] == 1
    assert stats["total_chunks"] >= 1

    doc_id = stats["documents"][0]["id"]
    assert kb.remove_document(doc_id) is True
    assert kb.stats()["total_documents"] == 0
    assert kb.remove_document(doc_id) is False


def test_context_from_hits_contains_source_and_text(kb):
    hits = kb.search("水箱容量", top_k=2)
    context = kb.context_from_hits(hits, max_chars=3000)

    assert "【知识库资料】" in context
    assert "咖啡机手册.md" in context
    assert "1.8" in context


class FakeLLM:
    def __init__(self):
        self.messages = None

    def chat(self, messages):
        self.messages = messages
        return "好的。"

    def stream_chat(self, messages):
        self.messages = messages
        yield "好的。"


class FakeTTS:
    def synthesize(self, text):
        return b"audio"


def test_pipeline_injects_kb_context_into_system_prompt():
    llm = FakeLLM()
    pipeline = DigitalHumanPipeline(Config(), llm, FakeTTS())
    context = "【知识库资料】\n[资料1｜来源：咖啡机手册.md]\n水箱容量 1.8 升"

    list(
        pipeline.stream_reply(
            [{"role": "user", "content": "水箱容量是多少"}], kb_context=context
        )
    )

    system = llm.messages[0]
    assert system["role"] == "system"
    assert "【知识库资料】" in system["content"]
    assert "1.8" in system["content"]


@pytest.fixture()
def kb_client(tmp_path, monkeypatch):
    store = KnowledgeBase(str(tmp_path), min_score=0.0)
    monkeypatch.setattr(main, "knowledge_base", store)
    return TestClient(main.app), store


def test_kb_upload_search_list_delete_flow(kb_client):
    client, _ = kb_client

    resp = client.post(
        "/api/kb/upload",
        files={"file": ("咖啡机手册.txt", MANUAL.encode("utf-8"), "text/plain")},
    )
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["chunks"] >= 1

    overview = client.get("/api/kb").json()
    assert overview["enabled"] is True
    assert overview["total_documents"] == 1
    assert overview["documents"][0]["name"] == "咖啡机手册.txt"

    search = client.post(
        "/api/kb/search", json={"query": "水箱容量", "top_k": 3}
    ).json()
    assert search["hits"]
    assert "1.8" in search["hits"][0]["text"]

    resp = client.delete(f"/api/kb/{doc['id']}")
    assert resp.status_code == 200
    assert client.get("/api/kb").json()["total_documents"] == 0


def test_kb_upload_decodes_utf8_filename(kb_client):
    """回归：前端额外提交 UTF-8 filename 字段时，中文名要原样保存。"""
    client, _ = kb_client
    resp = client.post(
        "/api/kb/upload",
        files={"file": ("fallback.md", MANUAL.encode("utf-8"), "text/markdown")},
        data={"filename": "中文资料.md"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "中文资料.md"


def test_decode_upload_filename_restores_latin1_mojibake():
    mojibake = "咖啡机手册.md".encode("utf-8").decode("latin-1")
    assert main._decode_upload_filename(mojibake) == "咖啡机手册.md"


def test_kb_upload_rejects_unsupported_type(kb_client):
    client, _ = kb_client
    resp = client.post(
        "/api/kb/upload",
        files={"file": ("恶意.exe", b"MZ", "application/octet-stream")},
    )
    assert resp.status_code == 400


def test_kb_upload_rejects_empty_file(kb_client):
    client, _ = kb_client
    resp = client.post(
        "/api/kb/upload", files={"file": ("空.txt", b"", "text/plain")}
    )
    assert resp.status_code == 400


def test_kb_delete_missing_document_returns_404(kb_client):
    client, _ = kb_client
    assert client.delete("/api/kb/not-exist").status_code == 404


def test_kb_overview_is_not_cached(kb_client):
    client, _ = kb_client
    resp = client.get("/api/kb")
    assert resp.headers["cache-control"] == "no-store"


class CapturePipeline:
    def __init__(self):
        self.context = None

    def stream_reply(self, history, kb_context=""):
        self.context = kb_context
        yield "回答。", b"audio"

    def reply(self, history, kb_context=""):
        self.context = kb_context
        return "回答。", b"audio"


def _events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_chat_stream_sends_sources_and_injects_context(client, kb, monkeypatch):
    capture = CapturePipeline()
    monkeypatch.setattr(main, "knowledge_base", kb)
    monkeypatch.setattr(main, "pipeline", capture)
    monkeypatch.setattr(
        main,
        "config",
        Config(kb_enabled=True, kb_top_k=4, kb_min_score=0.0),
    )

    resp = client.post(
        "/api/chat/stream",
        json={
            "messages": [{"role": "user", "content": "水箱容量是多少"}],
            "use_kb": True,
        },
    )

    assert resp.status_code == 200
    events = _events(resp)
    assert events[0]["type"] == "sources"
    assert events[0]["sources"][0]["source"] == "咖啡机手册.md"
    assert events[1]["type"] == "sentence"
    assert "【知识库资料】" in capture.context
    assert "1.8" in capture.context


def test_chat_stream_can_disable_kb(client, kb, monkeypatch):
    capture = CapturePipeline()
    monkeypatch.setattr(main, "knowledge_base", kb)
    monkeypatch.setattr(main, "pipeline", capture)
    monkeypatch.setattr(
        main,
        "config",
        Config(kb_enabled=True, kb_top_k=4, kb_min_score=0.0),
    )

    resp = client.post(
        "/api/chat/stream",
        json={
            "messages": [{"role": "user", "content": "水箱容量是多少"}],
            "use_kb": False,
        },
    )

    events = _events(resp)
    assert all(e["type"] != "sources" for e in events)
    assert capture.context == ""
