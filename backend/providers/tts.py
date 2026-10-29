"""TTS Provider：可插拔的语音合成层。"""
import asyncio
from abc import ABC, abstractmethod

from backend.config import Config


class TTSProvider(ABC):
    """语音合成统一接口。"""

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """将文本合成为音频，返回音频字节（mp3）。"""


class EdgeTTSProvider(TTSProvider):
    """微软 Edge 在线语音：免费、无需 API Key。"""

    def __init__(self, voice: str):
        self.voice = voice

    def synthesize(self, text: str) -> bytes:
        import edge_tts

        async def _run() -> bytes:
            communicate = edge_tts.Communicate(text, self.voice)
            audio = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
            return audio

        return asyncio.run(_run())


def get_tts_provider(config: Config) -> TTSProvider:
    """根据配置返回对应的 TTS 实现。"""
    if config.tts_provider == "edge":
        return EdgeTTSProvider(config.tts_voice)
    raise ValueError(f"未知的 TTS provider: {config.tts_provider}")
