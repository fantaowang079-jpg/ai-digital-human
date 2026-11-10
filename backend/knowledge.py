"""本地知识库：把文档切片、建索引，并按与用户问题的相关度检索。

设计目标（贴合本项目「无 GPU、零额外服务」的定位）：
- **零模型依赖**：中文按「CJK 单字/二元组 + 英文数字词」切词，TF-IDF 余弦相似度检索，
  小规模知识库（几十篇文档、几千段）检索耗时为毫秒级，不需要向量数据库。
- **多格式**：txt / md / csv / pdf / docx；上传后落盘到 knowledge/，重启自动重建索引。
- **自动刷新**：每次检索前比对文件 mtime / size，文档有增删改就自动重建，无需手动操作。

对外主要接口：
    kb = KnowledgeBase(root)
    kb.search("问题", top_k=4)       -> [{"source", "text", "score", ...}]
    kb.add_document(name, bytes)     -> 文档元信息
    kb.remove_document(doc_id)       -> bool
    kb.stats()                       -> 文档列表 + 统计
"""
from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import re
import threading
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from xml.etree import ElementTree

logger = logging.getLogger("digital_human.knowledge")

SUPPORTED_EXTS = {".txt", ".md", ".markdown", ".csv", ".pdf", ".docx"}
MAX_FILE_BYTES = 10 * 1024 * 1024

# 一次匹配一个「中文连续段」或「英文/数字/下划线标识符」
_TOKEN_RUN = re.compile(r"[一-鿿]+|[A-Za-z0-9_]+")
_CJK_RUN = re.compile(r"[一-鿿]+")
_ALNUM_PARTS = re.compile(r"[A-Za-z]+|[0-9]+")


def tokenize(text: str) -> list[str]:
    """把文本切成检索词：中文用相邻二元组（单字段保留单字），英文数字按词。

    例：「数字人 RAG」-> ["数字", "字人", "rag"]
    不用 jieba 等分词器，避免额外依赖；二元组对中文专有名词召回已经够用。
    """
    tokens: list[str] = []
    for run in _TOKEN_RUN.findall(text.lower()):
        if _CJK_RUN.fullmatch(run):
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
        else:
            tokens.append(run)
            # 「GTX1650」同时产出 gtx / 1650，兼容中英文混排与无空格写法
            parts = _ALNUM_PARTS.findall(run)
            if len(parts) > 1:
                tokens.extend(parts)
    return tokens


def split_text(text: str, chunk_size: int = 420, overlap: int = 60) -> list[str]:
    """按空行分段，再合并/切分成不超过 chunk_size 的片段。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    overlap = max(0, min(overlap, chunk_size // 2))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    pending_heading = ""

    for para in paragraphs:
        # Markdown 标题：结束上一段，并和下一段正文绑在一起，避免标题单独成块
        if para.startswith("#") and len(para) <= 80:
            if buf:
                chunks.append(buf)
                buf = ""
            pending_heading = para
            continue
        if pending_heading:
            para = f"{pending_heading}\n{para}"
            pending_heading = ""

        if len(para) > chunk_size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split_long(para, chunk_size, overlap))
            continue

        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) <= chunk_size:
            buf = candidate
        else:
            if buf:
                chunks.append(buf)
            buf = para

    if pending_heading:
        chunks.append(pending_heading)
    if buf:
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]


def _split_long(text: str, chunk_size: int, overlap: int) -> list[str]:
    """超长段落先按句末标点切句，再对仍然超长的句子做硬切（带重叠）。"""
    sentences = [s.strip() for s in re.split(r"(?<=[。！？!?；;\n])", text) if s.strip()]
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        while len(sent) > chunk_size:
            chunks.append(sent[:chunk_size])
            sent = sent[chunk_size - overlap :]
        if len(buf) + len(sent) <= chunk_size:
            buf += sent
        else:
            if buf:
                chunks.append(buf)
            buf = sent
    if buf:
        chunks.append(buf)
    return chunks


def _decode(data: bytes) -> str:
    """尽量正确地解码文本文件（UTF-8 / GBK 系列兜底）。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 依赖已写入 requirements
        raise ValueError("解析 PDF 需要安装 pypdf：pip install pypdf") from exc

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_text(data: bytes) -> str:
    """解析 .docx：它本质是 zip 包，正文在 word/document.xml，用标准库即可提取。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            info = zf.getinfo("word/document.xml")
            if info.file_size > 20 * 1024 * 1024:  # 防 zip bomb
                raise ValueError("docx 正文过大")
            xml_bytes = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("不是有效的 docx 文件") from exc

    root = ElementTree.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for p in root.iter(f"{{{_W_NS}}}p"):
        texts = [t.text or "" for t in p.iter(f"{{{_W_NS}}}t")]
        if any(t.strip() for t in texts):
            paragraphs.append("".join(texts))
    return "\n\n".join(paragraphs)


def extract_text(name: str, data: bytes) -> str:
    """按扩展名从文件字节中提取纯文本。"""
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        return _pdf_text(data)
    if ext == ".docx":
        return _docx_text(data)
    return _decode(data)


def sanitize_filename(filename: str) -> str:
    """去掉路径和非法字符，只保留安全文件名。"""
    base = os.path.basename(filename.replace("\\", "/"))
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    if not base:
        base = "document.txt"
    root, ext = os.path.splitext(base)
    ext = ext.lower()
    if len(root) > 80:
        root = root[:80]
    return root + ext


@dataclass
class Chunk:
    doc_id: str
    source: str
    index: int
    text: str
    weights: Counter = field(default_factory=Counter)
    norm: float = 0.0


@dataclass
class Document:
    doc_id: str
    name: str
    path: str
    size: int
    mtime_ns: int
    chunks: int = 0
    error: str | None = None


class KnowledgeBase:
    """本地文档知识库：文件索引 + TF-IDF 检索 + 上传/删除管理。"""

    def __init__(
        self,
        root: str,
        chunk_size: int = 420,
        overlap: int = 60,
        min_score: float = 0.05,
        max_file_bytes: int = MAX_FILE_BYTES,
    ):
        self.root = os.path.abspath(root)
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_score = min_score
        self.max_file_bytes = max_file_bytes

        os.makedirs(self.root, exist_ok=True)
        self._lock = threading.RLock()
        self._docs: dict[str, Document] = {}
        self._chunks: list[Chunk] = []
        self._idf: dict[str, float] = {}
        self._signature: tuple = ()
        self.refresh(force=True)

    # ---------------------------------------------------------------- 索引
    def _scan_signature(self) -> tuple:
        entries = []
        for entry in os.scandir(self.root):
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if os.path.splitext(entry.name)[1].lower() not in SUPPORTED_EXTS:
                continue
            stat = entry.stat()
            entries.append((entry.name, stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(entries))

    def refresh(self, force: bool = False) -> None:
        """扫描目录，文件有变化时重建索引（线程安全，可重复调用）。"""
        with self._lock:
            signature = self._scan_signature()
            if not force and signature == self._signature:
                return

            docs: dict[str, Document] = {}
            chunks: list[Chunk] = []
            for name, mtime_ns, size in signature:
                path = os.path.join(self.root, name)
                doc_id = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
                doc = Document(
                    doc_id=doc_id,
                    name=name,
                    path=path,
                    size=size,
                    mtime_ns=mtime_ns,
                )
                try:
                    with open(path, "rb") as fh:
                        data = fh.read()
                    text = extract_text(name, data)
                    pieces = split_text(text, self.chunk_size, self.overlap)
                    if not pieces:
                        raise ValueError("文件里没有可用的文字")
                    for i, piece in enumerate(pieces):
                        chunks.append(
                            Chunk(
                                doc_id=doc_id,
                                source=name,
                                index=i,
                                text=piece,
                                weights=Counter(tokenize(piece)),
                            )
                        )
                    doc.chunks = len(pieces)
                except Exception as exc:  # 单个文件失败不影响其它文档
                    doc.error = str(exc)
                    logger.warning("知识库文档解析失败 %s: %s", name, exc)
                docs[doc_id] = doc

            df = Counter()
            for chunk in chunks:
                df.update(chunk.weights.keys())
            total = len(chunks)
            idf = {
                token: math.log((total + 1) / (freq + 1)) + 1.0
                for token, freq in df.items()
            }
            for chunk in chunks:
                weights = {
                    token: (1.0 + math.log(count)) * idf.get(token, 1.0)
                    for token, count in chunk.weights.items()
                }
                chunk.norm = math.sqrt(sum(w * w for w in weights.values()))
                chunk.weights = Counter(weights)  # 索引阶段直接存 TF-IDF 权重

            self._docs = docs
            self._chunks = chunks
            self._idf = idf
            self._signature = signature

            indexed = sum(1 for d in docs.values() if not d.error)
            logger.info(
                "知识库索引完成：%d 篇文档（%d 篇正常）/ %d 个片段",
                len(docs),
                indexed,
                total,
            )

    # ---------------------------------------------------------------- 检索
    def search(
        self,
        query: str,
        top_k: int = 4,
        min_score: float | None = None,
    ) -> list[dict]:
        """返回与 query 最相关的片段（按余弦相似度降序）。"""
        self.refresh()
        query = (query or "").strip()
        if not query or not self._chunks:
            return []

        threshold = self.min_score if min_score is None else min_score
        q_tf = Counter(tokenize(query))
        q_weights = {
            token: (1.0 + math.log(count)) * self._idf.get(token, 1.0)
            for token, count in q_tf.items()
        }
        q_norm = math.sqrt(sum(w * w for w in q_weights.values()))
        if q_norm == 0:
            return []

        scored: list[tuple[float, Chunk]] = []
        for chunk in self._chunks:
            if chunk.norm == 0:
                continue
            dot = sum(
                weight * chunk.weights[token]
                for token, weight in q_weights.items()
                if token in chunk.weights
            )
            if dot <= 0:
                continue
            score = dot / (q_norm * chunk.norm)
            if score >= threshold:
                scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "doc_id": chunk.doc_id,
                "source": chunk.source,
                "index": chunk.index,
                "text": chunk.text,
                "score": round(score, 4),
            }
            for score, chunk in scored[: max(1, top_k)]
        ]

    # ---------------------------------------------------------------- 管理
    def add_document(self, filename: str, data: bytes) -> dict:
        """保存上传文件并重建索引；无法解析时不落盘、直接报错。"""
        name = sanitize_filename(filename)
        ext = os.path.splitext(name)[1].lower()
        if ext not in SUPPORTED_EXTS:
            raise ValueError(
                "不支持的文件类型，仅支持：" + " / ".join(sorted(SUPPORTED_EXTS))
            )
        if not data:
            raise ValueError("文件为空")
        if len(data) > self.max_file_bytes:
            raise ValueError(
                f"文件过大（上限 {self.max_file_bytes // (1024 * 1024)}MB）"
            )

        # 先校验能否解析，避免把坏文件写进知识库
        text = extract_text(name, data)
        if not text.strip():
            raise ValueError("文件里没有可用的文字")

        with self._lock:
            name = self._unique_name(name)
            path = os.path.join(self.root, name)
            with open(path, "wb") as fh:
                fh.write(data)
            self.refresh(force=True)

            doc_id = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
            doc = self._docs.get(doc_id)
            if doc is None or doc.error:
                # 理论上不会发生（解析已在上方校验）；兜底回滚
                try:
                    os.remove(path)
                except OSError:
                    pass
                self.refresh(force=True)
                raise ValueError(doc.error if doc else "文件解析失败")
            return self._doc_dict(doc)

    def remove_document(self, doc_id: str) -> bool:
        with self._lock:
            self.refresh()
            doc = self._docs.get(doc_id)
            if doc is None:
                return False
            # 确保只删除知识库目录内的文件
            root = os.path.realpath(self.root)
            target = os.path.realpath(doc.path)
            if os.path.dirname(target) != root:
                return False
            try:
                os.remove(target)
            except FileNotFoundError:
                pass
            self.refresh(force=True)
            return True

    def _unique_name(self, name: str) -> str:
        root, ext = os.path.splitext(name)
        candidate = name
        i = 2
        while os.path.exists(os.path.join(self.root, candidate)):
            candidate = f"{root}({i}){ext}"
            i += 1
        return candidate

    def _doc_dict(self, doc: Document) -> dict:
        return {
            "id": doc.doc_id,
            "name": doc.name,
            "size": doc.size,
            "chunks": doc.chunks,
            "error": doc.error,
        }

    def stats(self) -> dict:
        self.refresh()
        docs = sorted(self._docs.values(), key=lambda d: d.name.lower())
        return {
            "documents": [self._doc_dict(d) for d in docs],
            "total_documents": len(docs),
            "total_chunks": len(self._chunks),
        }

    # ---------------------------------------------------------------- 上下文
    def context_for(self, query: str, top_k: int = 4, max_chars: int = 3000) -> str:
        """把检索结果拼成可直接注入 system prompt 的上下文块（无命中返回 ""）。"""
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""

        lines = [
            "【知识库资料】",
            "下面是检索到的资料。回答时请优先依据这些资料；资料没有覆盖的内容不要编造，"
            "可以明确告诉用户「知识库中没有找到相关信息」。",
        ]
        used = sum(len(line) for line in lines)
        for i, hit in enumerate(hits, 1):
            block = f"\n[资料{i}｜来源：{hit['source']}]\n{hit['text'].strip()}"
            if used + len(block) > max_chars:
                break
            lines.append(block)
            used += len(block)
        return "\n".join(lines)


