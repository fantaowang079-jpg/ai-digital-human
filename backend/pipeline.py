"""编排层：把「知识库检索 → LLM 思考 → TTS 说话」串成完整链路。"""
import logging

from backend.config import Config
from backend.knowledge import KnowledgeBase
from backend.memory import MemoryStore
from backend.providers.llm import LLMProvider
from backend.providers.tts import TTSProvider

logger = logging.getLogger("digital_human.pipeline")

MODE_PROFESSIONAL = "professional"
MODE_PSYCHOLOGIST = "psychologist"
SUPPORTED_MODES = (MODE_PROFESSIONAL, MODE_PSYCHOLOGIST)


class DigitalHumanPipeline:
    """数字人对话编排核心：组装各 provider 并串联调用。"""

    def __init__(
        self,
        config: Config,
        llm: LLMProvider,
        tts: TTSProvider,
        knowledge_base: KnowledgeBase | None = None,
        memory: MemoryStore | None = None,
    ):
        self.config = config
        self.llm = llm
        self.tts = tts
        self.knowledge_base = knowledge_base
        self.memory = memory

    def build_messages(
        self,
        history: list[dict],
        mode: str = MODE_PROFESSIONAL,
        session_id: str | None = None,
    ) -> list[dict]:
        """组装消息：模式人格 + 长期记忆 +（仅专业模式）知识库上下文 + 对话历史。"""
        if mode not in SUPPORTED_MODES:
            raise ValueError(f"未知模式: {mode}")

        system_prompt = self._system_prompt(mode)
        memory_block = self._memory_context(session_id, mode, history)
        if memory_block:
            system_prompt = f"{system_prompt}\n\n{memory_block}"
        if mode == MODE_PROFESSIONAL:
            context = self._build_kb_context(history)
            if context:
                system_prompt = f"{system_prompt}\n\n{context}"
        return [{"role": "system", "content": system_prompt}, *history]

    def _memory_context(
        self, session_id: str | None, mode: str, history: list[dict]
    ) -> str:
        """读取滚动摘要与相关旧对话，拼成长期记忆上下文。"""
        if self.memory is None or not session_id:
            return ""

        user_texts = [
            m.get("content", "")
            for m in reversed(history)
            if m.get("role") == "user" and m.get("content")
        ]
        query = user_texts[0] if user_texts else ""
        exclude = {
            (m.get("role"), m.get("content"))
            for m in history
            if m.get("role") and m.get("content")
        }

        try:
            summary = self.memory.get_summary(session_id, mode)
            recalls = self.memory.recall(
                session_id, mode, query, exclude=exclude, limit=3
            )
            # 刷新页面后前端历史为空，补上最近几条旧对话，保证能接上话
            recent = [
                item
                for item in self.memory.recent_messages(
                    session_id, mode, limit=8
                )
                if (item["role"], item["content"]) not in exclude
            ][-4:]
        except Exception:
            logger.exception("读取长期记忆失败，本轮不使用记忆")
            return ""

        recent_pairs = {(item["role"], item["content"]) for item in recent}
        recalls = [
            item for item in recalls if (item["role"], item["content"]) not in recent_pairs
        ]

        if not summary and not recalls and not recent:
            return ""

        lines = [
            "【长期记忆】",
            "以下是这位用户之前聊过的内容，用于保持对话连续性；"
            "只在和当前问题相关时参考，不要机械复述。",
        ]
        if summary:
            lines.append(f"记忆摘要：{summary}")
        if recent:
            lines.append("最近对话回忆：")
            for item in recent:
                role = "用户" if item["role"] == "user" else "助手"
                lines.append(f"- [{role}] {item['content'][:300]}")
        if recalls:
            lines.append("相关回忆：")
            for item in recalls:
                role = "用户" if item["role"] == "user" else "助手"
                lines.append(f"- [{role}] {item['content']}")
        if mode == MODE_PROFESSIONAL:
            lines.append(
                "注意：长期记忆仅用于理解用户背景和保持连续性；"
                "涉及专业事实的答案仍必须以【知识库资料】为依据。"
            )
        return "\n".join(lines)

    def _system_prompt(self, mode: str) -> str:
        if mode == MODE_PSYCHOLOGIST:
            prompt = self.config.psychologist_prompt
        else:
            prompt = self.config.professional_prompt
        extra = (self.config.system_prompt or "").strip()
        if extra and extra not in prompt:
            prompt = f"{prompt}\n\n通用要求：{extra}"
        return prompt

    def _kb_available(self) -> bool:
        return self.knowledge_base is not None and self.config.kb_enabled

    def _professional_top_k(self) -> int:
        # 专业模式多取一些片段，尽量把知识库里的相关字段都交给模型
        return max(1, self.config.kb_top_k, 6)

    def _professional_max_chars(self) -> int:
        return max(1000, self.config.kb_max_context_chars, 4000)

    def _build_kb_context(self, history: list[dict]) -> str:
        """专业模式：检索知识库，拼成强制引用资料的上下文。

        专业模式始终严格：没命中时明确要求模型回答「知识库中没有找到相关信息」，
        不允许用模型自身知识编造。
        """
        if not self._kb_available():
            return (
                "【知识库资料】\n"
                "当前知识库未启用，无法依据资料回答。请告知用户知识库暂不可用，"
                "不要凭记忆或猜测回答专业问题。"
            )

        query = self._retrieval_query(history)
        if not query:
            return ""

        try:
            hits = self.knowledge_base.search(
                query, top_k=self._professional_top_k()
            )
        except Exception:
            # 知识库故障不能拖垮对话，但专业模式仍要求不要编造
            logger.exception("知识库检索失败，专业模式降级为未命中")
            hits = []

        if hits:
            return self._format_context(hits)
        return (
            "【知识库资料】\n"
            "本次没有检索到与用户问题相关的资料。请明确回答「知识库中没有找到相关信息」，"
            "并说明还缺少什么信息；不要使用知识库以外的知识，也不要编造。"
        )

    def _format_context(self, hits: list[dict]) -> str:
        lines = [
            "【知识库资料】",
            "下面是检索到的资料。回答时只能依据这些资料，不要使用资料以外的知识；"
            "资料没有覆盖的内容不要编造。每个关键结论后用（参考：文件名）标注来源，"
            "回答末尾单独列出「参考来源：文件名」。"
            "如果资料不足以回答，请明确说「知识库中没有找到相关信息」。",
        ]
        max_chars = self._professional_max_chars()
        used = sum(len(line) for line in lines)
        for i, hit in enumerate(hits, 1):
            block = f"\n[资料{i}｜来源：{hit['source']}]\n{hit['text'].strip()}"
            if used + len(block) > max_chars:
                break
            lines.append(block)
            used += len(block)
        return "\n".join(lines)

    def retrieve_sources(
        self, history: list[dict], mode: str = MODE_PROFESSIONAL
    ) -> list[dict]:
        """返回本轮引用的知识库文档（去重），用于前端展示参考来源。"""
        if mode != MODE_PROFESSIONAL or not self._kb_available():
            return []
        query = self._retrieval_query(history)
        if not query:
            return []
        try:
            hits = self.knowledge_base.search(
                query, top_k=self._professional_top_k()
            )
        except Exception:
            logger.exception("知识库检索失败，无法返回参考来源")
            return []

        seen: set[str] = set()
        sources: list[dict] = []
        for hit in hits:
            name = hit["source"]
            if name in seen:
                continue
            seen.add(name)
            sources.append(
                {"source": name, "snippet": hit["text"].strip()[:120]}
            )
        return sources

    @staticmethod
    def _retrieval_query(history: list[dict]) -> str:
        """取最近的问题做检索；追问很短时拼上上一轮用户问题，补全指代。"""
        user_texts = [
            m.get("content", "")
            for m in history
            if m.get("role") == "user" and m.get("content")
        ]
        if not user_texts:
            return ""
        query = user_texts[-1]
        if len(query) < 12 and len(user_texts) >= 2:
            query = f"{user_texts[-2]}\n{query}"
        return query

    def reply(
        self,
        history: list[dict],
        mode: str = MODE_PROFESSIONAL,
        session_id: str | None = None,
    ) -> tuple[str, bytes]:
        """输入对话历史，返回 (回复文本, 回复音频字节)。"""
        reply_text = self.llm.chat(self.build_messages(history, mode, session_id))
        reply_audio = self.tts.synthesize(reply_text)
        return reply_text, reply_audio

    def stream_events(
        self,
        history: list[dict],
        mode: str = MODE_PROFESSIONAL,
        session_id: str | None = None,
    ):
        """流式事件：先逐块 yield 文本 delta，再按句 yield 音频。

        event 形如：
          {"type": "delta", "text": "..."}                    # 前端逐词上屏
          {"type": "sentence", "text": "...", "audio": b"..."} # 前端排队播放
        """
        messages = self.build_messages(history, mode, session_id)
        buffer = ""
        for delta in self.llm.stream_chat(messages):
            if not delta:
                continue
            buffer += delta
            yield {"type": "delta", "text": delta}
            while True:
                sentence, rest = self._pop_sentence(buffer)
                if sentence is None:
                    break
                buffer = rest
                audio = self.tts.synthesize(sentence)
                yield {"type": "sentence", "text": sentence, "audio": audio}
        # 收尾：输出剩余未成句的文本
        tail = buffer.strip()
        if tail:
            audio = self.tts.synthesize(tail)
            yield {"type": "sentence", "text": tail, "audio": audio}

    def stream_reply(
        self,
        history: list[dict],
        mode: str = MODE_PROFESSIONAL,
        session_id: str | None = None,
    ):
        """兼容旧接口：只 yield (句子文本, 音频字节)，不输出逐词 delta。"""
        for event in self.stream_events(history, mode, session_id):
            if event["type"] == "sentence":
                yield event["text"], event["audio"]

    @staticmethod
    def _pop_sentence(text: str) -> tuple[str | None, str]:
        """从文本中切出第一个完整句子，返回 (句子, 剩余) 或 (None, 原文本)。"""
        for i, ch in enumerate(text):
            if ch in "。！？!?；;":
                return text[: i + 1], text[i + 1 :]
        if "\n" in text:
            i = text.index("\n")
            return text[: i + 1], text[i + 1 :]
        return None, text
