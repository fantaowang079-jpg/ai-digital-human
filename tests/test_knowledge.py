# tests/test_knowledge.py
# 知识库（RAG-lite）回归测试：切词/切片、检索排序、上传/删除 API、提示词注入。

import io
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
    """回归：连续标题（题库/大纲）不能被后一个标题覆盖丢失。"""
    text = (
        "# 题库\n\n## 数据库\n\n### MySQL的索引\n\n### MySQL的事务\n\n"
        "## Agent\n\n### 什么是Agent\n"
    )
    chunks = split_text(text)
    joined = "\n".join(chunks)

    assert "MySQL的索引" in joined
    assert "MySQL的事务" in joined
    assert "什么是Agent" in joined
    assert len(chunks) >= 5


def test_split_text_binds_heading_to_body():
    text = "# 手册\n\n## 保修\n\n整机保修两年。\n\n## 清洁\n\n每月清洗一次。"
    chunks = split_text(text)

    assert any("保修" in c and "两年" in c for c in chunks)
    assert any("清洁" in c and "每月" in c for c in chunks)


def test_search_outline_questions(tmp_path):
    text = "# 题库\n\n## 数据库\n\n### MySQL的索引\n\n### MySQL的事务\n"
    (tmp_path / "题库.md").write_text(text, encoding="utf-8")
    kb = KnowledgeBase(str(tmp_path), min_score=0.0)

    hits = kb.search("MySQL的索引", top_k=1)
    assert hits
    assert "MySQL的索引" in hits[0]["text"]


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
    # 默认 min_score=0 时可能返回低分结果，但绝不能把无关内容当高相关
    assert not hits or hits[0]["score"] < 0.1


def test_stats_and_delete(tmp_path, kb):
    stats = kb.stats()
    assert stats["total_documents"] == 1
    assert stats["total_chunks"] >= 1

    doc_id = stats["documents"][0]["id"]
    assert kb.remove_document(doc_id) is True
    assert kb.stats()["total_documents"] == 0
    assert kb.remove_document(doc_id) is False


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


def test_professional_mode_injects_kb_context_and_citation_rules(kb):
    cfg = Config(kb_top_k=2, kb_min_score=0.0, kb_enabled=True)
    llm = FakeLLM()
    pipeline = DigitalHumanPipeline(cfg, llm, FakeTTS(), knowledge_base=kb)

    text, audio = pipeline.reply(
        [{"role": "user", "content": "水箱容量是多少"}], "professional"
    )

    assert text == "好的。"
    assert audio == b"audio"
    system = llm.messages[0]
    assert system["role"] == "system"
    assert "知语" in system["content"]
    assert "【知识库资料】" in system["content"]
    assert "参考来源" in system["content"]
    assert "1.8" in system["content"]
    assert system["content"].index("【知识库资料】") > system["content"].index("你是")


def test_professional_mode_without_hits_forbids_guessing(tmp_path):
    kb = KnowledgeBase(str(tmp_path), min_score=0.9)
    (tmp_path / "无关.md").write_text("这是一篇与咖啡机无关的文档。", encoding="utf-8")
    kb.refresh(force=True)
    cfg = Config(kb_enabled=True, kb_min_score=0.9)
    llm = FakeLLM()
    pipeline = DigitalHumanPipeline(cfg, llm, FakeTTS(), knowledge_base=kb)

    pipeline.reply(
        [{"role": "user", "content": "量子计算机怎么造"}], "professional"
    )

    assert "没有检索到" in llm.messages[0]["content"]


def test_psychologist_mode_skips_knowledge_base(kb):
    cfg = Config(kb_top_k=2, kb_min_score=0.0, kb_enabled=True)
    llm = FakeLLM()
    pipeline = DigitalHumanPipeline(cfg, llm, FakeTTS(), knowledge_base=kb)

    pipeline.reply(
        [{"role": "user", "content": "水箱容量是多少"}], "psychologist"
    )

    system = llm.messages[0]["content"]
    assert "心语" in system
    assert "【知识库资料】" not in system
    assert "1.8" not in system
    assert pipeline.retrieve_sources(
        [{"role": "user", "content": "水箱容量是多少"}], "psychologist"
    ) == []


def test_professional_mode_returns_deduplicated_sources(kb):
    cfg = Config(kb_top_k=6, kb_min_score=0.0, kb_enabled=True)
    pipeline = DigitalHumanPipeline(cfg, FakeLLM(), FakeTTS(), knowledge_base=kb)

    sources = pipeline.retrieve_sources(
        [{"role": "user", "content": "水箱容量是多少"}], "professional"
    )

    assert sources
    assert sources[0]["source"] == "咖啡机手册.md"
    assert len({s["source"] for s in sources}) == len(sources)


@pytest.fixture()
def kb_client(tmp_path, monkeypatch):
    store = KnowledgeBase(str(tmp_path), min_score=0.0)
    monkeypatch.setattr(main, "knowledge_base", store)
    monkeypatch.setattr(main.pipeline, "knowledge_base", store)
    return TestClient(main.app), store


def test_kb_upload_search_list_delete_flow(kb_client):
    client, store = kb_client

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
        "/api/kb/upload",
        files={"file": ("空.txt", b"", "text/plain")},
    )
    assert resp.status_code == 400


def test_kb_delete_missing_document_returns_404(kb_client):
    client, _ = kb_client
    assert client.delete("/api/kb/not-exist").status_code == 404


def test_kb_overview_is_not_cached(kb_client):
    client, _ = kb_client
    resp = client.get("/api/kb")
    assert resp.headers["cache-control"] == "no-store"
