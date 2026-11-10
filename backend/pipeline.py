"""编排层：把「知识库检索 → LLM 思考 → TTS 说话」串成完整链路。"""
import logging

from backend.config import Config
from backend.knowledge import KnowledgeBase
from backend.providers.llm import LLMProvider
from backend.providers.tts import TTSProvider

logger = logging.getLogger("digital_human.pipeline")


class DigitalHumanPipeline:
    """数字人对话编排核心：组装各 provider 并串联调用。"""

    def __init__(
        self,
        config: Config,
        llm: LLMProvider,
        tts: TTSProvider,
        knowledge_base: KnowledgeBase | None = None,
    ):
        self.config = config
        self.llm = llm
        self.tts = tts
        self.knowledge_base = knowledge_base

    def build_messages(self, history: list[dict]) -> list[dict]:
        """组装消息：角色设定 + 知识库检索上下文 + 对话历史。"""
        system_prompt = self.config.system_prompt
        context = self._build_kb_context(history)
        if context:
            system_prompt = f"{system_prompt}\n\n{context}"
        return [{"role": "system", "content": system_prompt}, *history]

    def _build_kb_context(self, history: list[dict]) -> str:
        """检索与当前问题相关的知识库片段，拼成 system prompt 上下文。

        没有命中时返回空串（保持普通闲聊能力）；开启 KB_STRICT 后，即使没命中
        也会注入一条「不要编造」的约束。
        """
        if self.knowledge_base is None or not self.config.kb_enabled:
            return ""

        query = self._retrieval_query(history)
        if not query:
            return ""

        try:
            hits = self.knowledge_base.search(query, top_k=self.config.kb_top_k)
        except Exception:
            # 知识库故障不能拖垮对话，降级为普通回复
            logger.exception("知识库检索失败，本轮降级为无知识库模式")
            hits = []

        if hits:
            return self._format_context(hits)
        if self.config.kb_strict:
            return (
                "【知识库资料】\n"
                "本次没有检索到与用户问题相关的资料。若问题需要知识库依据，"
                "请明确回答「知识库中没有找到相关信息」，不要编造。"
            )
        return ""

    def _format_context(self, hits: list[dict]) -> str:
        lines = [
            "【知识库资料】",
            "下面是检索到的资料。回答时请优先依据这些资料；资料没有覆盖的内容不要编造，"
            "可以明确告诉用户「知识库中没有找到相关信息」。",
        ]
        used = sum(len(line) for line in lines)
        for i, hit in enumerate(hits, 1):
            block = f"\n[资料{i}｜来源：{hit['source']}]\n{hit['text'].strip()}"
            if used + len(block) > self.config.kb_max_context_chars:
                break
            lines.append(block)
            used += len(block)
        return "\n".join(lines)

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

    def reply(self, history: list[dict]) -> tuple[str, bytes]:
        """输入对话历史，返回 (回复文本, 回复音频字节)。"""
        reply_text = self.llm.chat(self.build_messages(history))
        reply_audio = self.tts.synthesize(reply_text)
        return reply_text, reply_audio

    def stream_reply(self, history: list[dict]):
        """流式回复：LLM 边生成、边分句、边 TTS，逐个 yield (句子文本, 音频字节)。

        相比 reply() 一次生成全部，本方法让前端在首句生成后即可开始播放，
        显著降低首字延迟。
        """
        messages = self.build_messages(history)
        buffer = ""
        for delta in self.llm.stream_chat(messages):
            buffer += delta
            while True:
                sentence, rest = self._pop_sentence(buffer)
                if sentence is None:
                    break
                buffer = rest
                audio = self.tts.synthesize(sentence)
                yield sentence, audio
        # 收尾：输出剩余未成句的文本
        tail = buffer.strip()
        if tail:
            audio = self.tts.synthesize(tail)
            yield tail, audio

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
