"""上下文记忆：本地 SQLite 持久化 + 滚动摘要 + 相关回忆检索。

设计：
- 每个浏览器一个 session_id，对话按（session_id, mode）隔离保存，重启后仍在。
- 最近 N 条消息原样保留；更早的对话攒够一批后自动压缩成「记忆摘要」，
  避免提示词无限变长。
- 每次提问用中文二元组 + TF-IDF 余弦相似度，从旧对话里挑出相关片段，
  让数字人能够主动「想起」以前说过的话。
"""
from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
import time
from collections import Counter

from backend.knowledge import tokenize

logger = logging.getLogger("digital_human.memory")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, mode, id);
CREATE TABLE IF NOT EXISTS summaries (
    session_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    summary TEXT NOT NULL,
    covered_until INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (session_id, mode)
);
"""


def extractive_summary(transcript: str, max_chars: int = 400) -> str:
    """不依赖大模型的兜底摘要：抽取用户发言的主要句子。"""
    lines = [line.strip() for line in transcript.splitlines() if line.strip()]
    if not lines:
        return ""
    picked = [line for line in lines if line.startswith("用户：")]
    if not picked:
        picked = lines
    text = "；".join(line.split("：", 1)[-1] for line in picked[:10])
    return text[:max_chars]


class MemoryStore:
    """本地会话记忆：消息表 + 滚动摘要表。"""

    def __init__(
        self,
        path: str,
        keep_verbatim: int = 12,
        summarize_after: int = 12,
    ):
        self.path = os.path.abspath(path)
        self.keep_verbatim = max(2, keep_verbatim)
        self.summarize_after = max(2, summarize_after)

        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._summarizing: set[tuple[str, str]] = set()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------ 写入
    def append_exchange(
        self, session_id: str, mode: str, user_text: str, assistant_text: str
    ) -> int:
        """保存一轮对话；完全重复的一轮（重试）不会重复落库。"""
        user_text = (user_text or "").strip()
        assistant_text = (assistant_text or "").strip()
        if not user_text:
            return self.count(session_id, mode)

        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id=? AND mode=? ORDER BY id DESC LIMIT 2",
                (session_id, mode),
            ).fetchall()
            if (
                len(rows) >= 2
                and rows[0]["role"] == "assistant"
                and rows[0]["content"] == assistant_text
                and rows[1]["role"] == "user"
                and rows[1]["content"] == user_text
            ):
                return self.count(session_id, mode)

            if not (
                rows
                and rows[0]["role"] == "user"
                and rows[0]["content"] == user_text
            ):
                self._conn.execute(
                    "INSERT INTO messages(session_id, mode, role, content, created_at) "
                    "VALUES (?, ?, 'user', ?, ?)",
                    (session_id, mode, user_text, time.time()),
                )
            if assistant_text:
                self._conn.execute(
                    "INSERT INTO messages(session_id, mode, role, content, created_at) "
                    "VALUES (?, ?, 'assistant', ?, ?)",
                    (session_id, mode, assistant_text, time.time()),
                )
        return self.count(session_id, mode)

    def set_summary(
        self, session_id: str, mode: str, summary: str, covered_until: int
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO summaries(session_id, mode, summary, covered_until, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, mode) DO UPDATE SET "
                "summary=excluded.summary, covered_until=excluded.covered_until, "
                "updated_at=excluded.updated_at",
                (session_id, mode, summary, covered_until, time.time()),
            )

    # ------------------------------------------------------------ 读取
    def count(self, session_id: str, mode: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id=? AND mode=?",
                (session_id, mode),
            ).fetchone()
        return int(row["n"])

    def get_summary(self, session_id: str, mode: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT summary FROM summaries WHERE session_id=? AND mode=?",
                (session_id, mode),
            ).fetchone()
        return row["summary"] if row else ""

    def _summary_covered_until(self, session_id: str, mode: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT covered_until FROM summaries WHERE session_id=? AND mode=?",
                (session_id, mode),
            ).fetchone()
        return int(row["covered_until"]) if row else 0

    def recent_messages(
        self, session_id: str, mode: str, limit: int = 50
    ) -> list[dict]:
        """返回最近的对话，按时间正序。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content, created_at FROM messages "
                "WHERE session_id=? AND mode=? ORDER BY id DESC LIMIT ?",
                (session_id, mode, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def overview(self, session_id: str, mode: str, limit: int = 50) -> dict:
        messages = self.recent_messages(session_id, mode, limit)
        return {
            "summary": self.get_summary(session_id, mode),
            "messages": messages,
            "total_messages": self.count(session_id, mode),
        }

    # ------------------------------------------------------------ 回忆
    def recall(
        self,
        session_id: str,
        mode: str,
        query: str,
        exclude: set[tuple[str, str]] | None = None,
        limit: int = 3,
        min_score: float = 0.12,
        max_candidates: int = 400,
    ) -> list[dict]:
        """从旧对话中检索与当前问题相关的片段。"""
        query = (query or "").strip()
        if not query:
            return []

        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id=? AND mode=? ORDER BY id DESC LIMIT ?",
                (session_id, mode, max_candidates),
            ).fetchall()

        exclude = exclude or set()
        candidates = [
            row
            for row in rows
            if (row["role"], row["content"]) not in exclude
            and len(row["content"].strip()) >= 2
        ]
        if not candidates:
            return []

        q_tf = Counter(tokenize(query, drop_stopwords=True))
        if not q_tf:
            return []

        tokenized: list[tuple[sqlite3.Row, Counter]] = []
        df: Counter = Counter()
        for row in candidates:
            tf = Counter(tokenize(row["content"], drop_stopwords=True))
            tokenized.append((row, tf))
            df.update(tf.keys())

        total = len(tokenized)
        idf = {
            token: math.log((total + 1) / (freq + 1)) + 1.0
            for token, freq in df.items()
        }

        def weights(tf: Counter) -> dict[str, float]:
            return {
                token: (1.0 + math.log(count)) * idf.get(token, 1.0)
                for token, count in tf.items()
            }

        q_weights = weights(q_tf)
        q_norm = math.sqrt(sum(w * w for w in q_weights.values()))
        if q_norm == 0:
            return []

        scored = []
        for row, tf in tokenized:
            r_weights = weights(tf)
            r_norm = math.sqrt(sum(w * w for w in r_weights.values()))
            if r_norm == 0:
                continue
            dot = sum(
                weight * r_weights[token]
                for token, weight in q_weights.items()
                if token in r_weights
            )
            if dot <= 0:
                continue
            score = dot / (q_norm * r_norm)
            if score >= min_score:
                scored.append((score, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "role": row["role"],
                "content": row["content"][:300],
                "score": round(score, 4),
            }
            for score, row in scored[: max(1, limit)]
        ]

    # ------------------------------------------------------------ 摘要
    def _unsummarized_old(
        self, session_id: str, mode: str
    ) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content FROM messages "
                "WHERE session_id=? AND mode=? ORDER BY id ASC",
                (session_id, mode),
            ).fetchall()
        if len(rows) <= self.keep_verbatim:
            return []
        cutoff = rows[-self.keep_verbatim - 1]["id"]
        covered_until = self._summary_covered_until(session_id, mode)
        return [row for row in rows if covered_until < row["id"] <= cutoff]

    def summarize_now(
        self, session_id: str, mode: str, summarize_fn
    ) -> str | None:
        """同步执行一次摘要（测试与后台线程共用）。"""
        old = self._unsummarized_old(session_id, mode)
        if len(old) < self.summarize_after:
            return None
        return self._do_summarize(session_id, mode, old, summarize_fn)

    def maybe_summarize(
        self, session_id: str, mode: str, summarize_fn
    ) -> bool:
        """超过阈值时在后台线程压缩旧对话，不阻塞当前回复。"""
        key = (session_id, mode)
        with self._lock:
            if key in self._summarizing:
                return False
            old = self._unsummarized_old(session_id, mode)
            if len(old) < self.summarize_after:
                return False
            self._summarizing.add(key)

        def worker():
            try:
                self._do_summarize(session_id, mode, old, summarize_fn)
            finally:
                with self._lock:
                    self._summarizing.discard(key)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _do_summarize(
        self, session_id: str, mode: str, old: list[sqlite3.Row], summarize_fn
    ) -> str | None:
        existing = self.get_summary(session_id, mode)
        transcript = "\n".join(
            f"{'用户' if row['role'] == 'user' else '助手'}：{row['content']}"
            for row in old
        )
        try:
            summary = (summarize_fn(existing, transcript) or "").strip()
        except Exception:
            logger.exception("记忆摘要失败")
            summary = ""
        if not summary:
            summary = extractive_summary(transcript, max_chars=400)
        if not summary:
            return None

        summary = summary[:800]
        covered_until = max(row["id"] for row in old)
        self.set_summary(session_id, mode, summary, covered_until)
        logger.info(
            "记忆摘要已更新：session=%s mode=%s 覆盖 %d 条消息",
            session_id,
            mode,
            len(old),
        )
        return summary

    # ------------------------------------------------------------ 清除
    def clear(self, session_id: str, mode: str | None = None) -> None:
        with self._lock, self._conn:
            if mode is None:
                self._conn.execute(
                    "DELETE FROM messages WHERE session_id=?", (session_id,)
                )
                self._conn.execute(
                    "DELETE FROM summaries WHERE session_id=?", (session_id,)
                )
            else:
                self._conn.execute(
                    "DELETE FROM messages WHERE session_id=? AND mode=?",
                    (session_id, mode),
                )
                self._conn.execute(
                    "DELETE FROM summaries WHERE session_id=? AND mode=?",
                    (session_id, mode),
                )
