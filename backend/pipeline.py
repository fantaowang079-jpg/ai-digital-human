"""编排层：把 LLM、TTS 等模块串成「思考 → 说话」的完整链路。"""
from backend.config import Config
from backend.providers.llm import LLMProvider
from backend.providers.tts import TTSProvider


class DigitalHumanPipeline:
    """数字人对话编排核心：组装各 provider 并串联调用。"""

    def __init__(self, config: Config, llm: LLMProvider, tts: TTSProvider):
        self.config = config
        self.llm = llm
        self.tts = tts

    def build_messages(
        self, history: list[dict], kb_context: str = ""
    ) -> list[dict]:
        """在对话历史前插入数字人角色设定；有知识库命中时追加资料块。"""
        system = self.config.system_prompt
        if kb_context:
            system = system + "\n\n" + kb_context
        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        return messages

    def reply(
        self, history: list[dict], kb_context: str = ""
    ) -> tuple[str, bytes]:
        """输入对话历史，返回 (回复文本, 回复音频字节)。"""
        reply_text = self.llm.chat(self.build_messages(history, kb_context))
        reply_audio = self.tts.synthesize(reply_text)
        return reply_text, reply_audio

    def stream_reply(self, history: list[dict], kb_context: str = ""):
        """流式回复：LLM 边生成、边分句、边 TTS，逐个 yield (句子文本, 音频字节)。

        相比 reply() 一次生成全部，本方法让前端在首句生成后即可开始播放，
        显著降低首字延迟。
        """
        messages = self.build_messages(history, kb_context)
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
