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
import json
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
CACHE_FILENAME = ".kb_cache.json"
CACHE_VERSION = 1

# 一次匹配一个「中文连续段」或「英文/数字/下划线标识符」
_TOKEN_RUN = re.compile(r"[一-鿿]+|[A-Za-z0-9_]+")
_CJK_RUN = re.compile(r"[一-鿿]+")
_ALNUM_PARTS = re.compile(r"[A-Za-z]+|[0-9]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# 查询侧停用词：疑问词/功能词不参与匹配，避免「是什么」「怎么办」误命中无关标题
_STOPWORDS = {
    "什么", "是什", "么是", "怎么", "么办", "为什", "如何", "为何", "哪些", "哪个",
    "哪几", "有哪", "多少", "是否", "可以", "一下", "这个", "那个", "这些", "那些",
    "这种", "那种", "请问", "帮我", "告诉", "我们", "你们", "他们", "它们", "然后",
    "那么", "如果", "因为", "所以", "但是", "而且", "还是", "就是", "没有", "需要",
    "应该", "可能", "一些", "时候", "现在", "以及", "关于", "对于", "通过", "进行",
    "一个", "的是", "了的", "怎样", "么样", "是不", "不是", "了吗", "么了", "么回",
}

# 检索词覆盖率下限：命中的查询词权重至少要占问题总权重的 25%
_MIN_QUERY_COVERAGE = 0.25


def tokenize(text: str, drop_stopwords: bool = False) -> list[str]:
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
    if drop_stopwords:
        tokens = [t for t in tokens if t not in _STOPWORDS]
    return tokens


def split_text(text: str, chunk_size: int = 420, overlap: int = 60) -> list[str]:
    """按 Markdown 标题划分章节，再把每节正文切成不超过 chunk_size 的片段。

    每个片段都带上标题路径（如「## MySQL系列 / ### MySQL的索引」），
    纯标题章节（例如只有问题列表、没有答案的题库）也会各自成为一段，
    避免连续标题互相覆盖导致内容丢失。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    overlap = max(0, min(overlap, chunk_size // 2))

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    # PDF 常见「剧  情  文  库」这种字间空格，会把中文二元组打散，先合并
    text = re.sub(r"(?<=[一-鿿])[ \t　]+(?=[一-鿿])", "", text)
    # 让「标题紧跟正文、中间没有空行」的情况也能把标题单独拆出来
    text = re.sub(r"(?m)^(#{1,6}\s+.*)$", r"\1\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    # 先按标题层级切成 (标题路径, 正文段落列表) 的章节
    sections: list[tuple[list[str], list[str]]] = []
    heading_stack: list[tuple[int, str]] = []
    body: list[str] = []

    for para in paragraphs:
        match = _HEADING_RE.match(para)
        if match:
            if heading_stack or body:
                sections.append(([h for _, h in heading_stack], body))
            level = len(match.group(1))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, para.strip()))
            body = []
        else:
            body.append(para)

    if heading_stack or body:
        sections.append(([h for _, h in heading_stack], body))

    chunks: list[str] = []
    for headings, body_paras in sections:
        prefix = "\n".join(headings)
        body_text = "\n\n".join(body_paras).strip()
        if body_text:
            content = f"{prefix}\n\n{body_text}" if prefix else body_text
            chunks.extend(_split_long(content, chunk_size, overlap))
        elif prefix:
            # 纯标题章节：标题本身就是检索内容（常见于题库/大纲）
            chunks.append(prefix)
    return [c.strip() for c in chunks if c.strip()]


def _split_long(text: str, chunk_size: int, overlap: int) -> list[str]:
    """超长章节先按句末标点/换行切句，再对仍然超长的句子做硬切（带重叠）。

    切句时保留结尾的标点和换行，避免问答内容被粘成一团。
    """
    sentences = re.findall(r"[^。！？!?；;\n]+[。！？!?；;\n]?", text)
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if not sent.strip():
            continue
        while len(sent) > chunk_size:
            chunks.append(sent[:chunk_size])
            sent = sent[chunk_size - overlap :]
        if len(buf) + len(sent) <= chunk_size:
            buf += sent
        else:
            if buf.strip():
                chunks.append(buf)
            buf = sent
    if buf.strip():
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
        # 解析缓存：{相对路径: ((mtime_ns, size), 切片列表, 错误信息)}
        # 内存缓存避免重复解析；同时持久化到磁盘，重启后直接复用解析结果
        self._cache: dict[str, tuple[tuple[int, int], list[str], str | None]] = (
            self._load_cache()
        )
        self.refresh(force=True)

    # ---------------------------------------------------------------- 索引
    def _scan_signature(self) -> tuple:
        """递归扫描知识库目录，返回 (相对路径, mtime, size) 列表。"""
        entries = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d != "__pycache__"
            )
            for filename in filenames:
                if filename.startswith("."):
                    continue
                if os.path.splitext(filename)[1].lower() not in SUPPORTED_EXTS:
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(path)
                except OSError:  # 扫描期间文件被删除
                    continue
                rel = os.path.relpath(path, self.root).replace(os.sep, "/")
                entries.append((rel, stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(entries))

    @property
    def _cache_path(self) -> str:
        return os.path.join(self.root, CACHE_FILENAME)

    def _load_cache(self) -> dict:
        """从磁盘加载上次的解析结果；文件没变就不必重新解析 PDF。"""
        if not os.path.exists(self._cache_path):
            return {}
        try:
            with open(self._cache_path, encoding="utf-8") as fh:
                payload = json.load(fh)
            if (
                payload.get("version") != CACHE_VERSION
                or payload.get("chunk_size") != self.chunk_size
                or payload.get("overlap") != self.overlap
            ):
                logger.info("知识库缓存版本或切片参数不匹配，忽略旧缓存")
                return {}
            cache = {}
            for rel, item in payload.get("documents", {}).items():
                pieces = item.get("chunks")
                if not isinstance(pieces, list) or not all(
                    isinstance(piece, str) for piece in pieces
                ):
                    continue
                cache[rel] = (
                    (int(item["mtime_ns"]), int(item["size"])),
                    pieces,
                    item.get("error"),
                )
            logger.info("知识库解析缓存已加载：%d 篇文档", len(cache))
            return cache
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.warning("知识库缓存读取失败，将重新解析：%s", exc)
            return {}

    def _save_cache(self) -> None:
        payload = {
            "version": CACHE_VERSION,
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
            "documents": {
                rel: {
                    "mtime_ns": meta[0][0],
                    "size": meta[0][1],
                    "chunks": meta[1],
                    "error": meta[2],
                }
                for rel, meta in self._cache.items()
            },
        }
        tmp = self._cache_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self._cache_path)
        except OSError as exc:
            logger.warning("知识库缓存写入失败：%s", exc)
            try:
                os.remove(tmp)
            except OSError:
                pass

    def refresh(self, force: bool = False) -> None:
        """扫描目录，文件有变化时重建索引（线程安全，可重复调用）。"""
        with self._lock:
            signature = self._scan_signature()
            if not force and signature == self._signature:
                return

            docs: dict[str, Document] = {}
            chunks: list[Chunk] = []
            new_cache: dict[str, tuple[tuple[int, int], list[str], str | None]] = {}
            seen_texts: set[str] = set()  # 同一份文档重复放两份时不要重复索引
            for rel, mtime_ns, size in signature:
                path = os.path.join(self.root, *rel.split("/"))
                doc_id = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]
                doc = Document(
                    doc_id=doc_id,
                    name=rel,
                    path=path,
                    size=size,
                    mtime_ns=mtime_ns,
                )

                cached = self._cache.get(rel)
                if cached is not None and cached[0] == (mtime_ns, size):
                    pieces, error = cached[1], cached[2]
                else:
                    pieces, error = [], None
                    try:
                        with open(path, "rb") as fh:
                            data = fh.read()
                        text = extract_text(rel, data)
                        pieces = split_text(text, self.chunk_size, self.overlap)
                        if not pieces:
                            raise ValueError("文件里没有可用的文字")
                        logger.info("知识库解析文档：%s（%d 段）", rel, len(pieces))
                    except Exception as exc:  # 单个文件失败不影响其它文档
                        error = str(exc)
                        pieces = []
                        logger.warning("知识库文档解析失败 %s: %s", rel, exc)
                new_cache[rel] = ((mtime_ns, size), pieces, error)

                doc.chunks = len(pieces)
                doc.error = error
                for i, piece in enumerate(pieces):
                    if piece in seen_texts:
                        continue
                    seen_texts.add(piece)
                    chunks.append(
                        Chunk(
                            doc_id=doc_id,
                            source=rel,
                            index=i,
                            text=piece,
                            weights=Counter(tokenize(piece)),
                        )
                    )
                docs[doc_id] = doc

            self._cache = new_cache

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
            self._save_cache()

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
        q_tf = Counter(tokenize(query, drop_stopwords=True))
        if not q_tf:
            return []
        q_weights = {
            token: (1.0 + math.log(count)) * self._idf.get(token, 1.0)
            for token, count in q_tf.items()
        }
        q_norm = math.sqrt(sum(w * w for w in q_weights.values()))
        q_weight_sum = sum(q_weights.values())
        if q_norm == 0 or q_weight_sum == 0:
            return []

        scored: list[tuple[float, float, Chunk]] = []
        for chunk in self._chunks:
            if chunk.norm == 0:
                continue
            dot = 0.0
            matched_weight = 0.0
            for token, weight in q_weights.items():
                chunk_weight = chunk.weights.get(token)
                if chunk_weight:
                    dot += weight * chunk_weight
                    matched_weight += weight
            if dot <= 0:
                continue
            # 只命中「是什么」这类个别词、而问题核心词全部没中的片段直接丢弃
            coverage = matched_weight / q_weight_sum
            if coverage < _MIN_QUERY_COVERAGE:
                continue
            score = dot / (q_norm * chunk.norm)
            if score >= threshold:
                scored.append((score, coverage, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "doc_id": chunk.doc_id,
                "source": chunk.source,
                "index": chunk.index,
                "text": chunk.text,
                "score": round(score, 4),
                "coverage": round(coverage, 3),
            }
            for score, coverage, chunk in scored[: max(1, top_k)]
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

        # 先校验能否解析，避免把坏文件写进知识库；切片结果顺手放进缓存，
        # 后面 refresh 就不用再解析一遍（PDF 尤其明显）
        text = extract_text(name, data)
        if not text.strip():
            raise ValueError("文件里没有可用的文字")
        pieces = split_text(text, self.chunk_size, self.overlap)
        if not pieces:
            raise ValueError("文件里没有可用的文字")

        with self._lock:
            name = self._unique_name(name)
            path = os.path.join(self.root, name)
            with open(path, "wb") as fh:
                fh.write(data)
            stat = os.stat(path)
            self._cache[name] = ((stat.st_mtime_ns, stat.st_size), pieces, None)
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
            # 确保只删除知识库目录内的文件（支持子目录）
            root = os.path.realpath(self.root)
            target = os.path.realpath(doc.path)
            try:
                inside = os.path.commonpath([root, target]) == root
            except ValueError:  # 不同盘符
                inside = False
            if not inside:
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


