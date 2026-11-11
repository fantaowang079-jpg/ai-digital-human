"""STT Provider：可插拔的语音识别层。"""
import os
import tempfile
from abc import ABC, abstractmethod

from backend.config import Config


class STTProvider(ABC):
    """语音识别统一接口。"""

    @abstractmethod
    def transcribe(self, audio_bytes: bytes) -> str:
        """将音频字节转成文字。"""


class FasterWhisperSTTProvider(STTProvider):
    """本地 Faster-Whisper：离线、免费，CPU 即可运行。

    模型首次使用时从 HuggingFace 下载（国内走 hf-mirror 镜像加速）。
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        language: str = "zh",
        beam_size: int = 5,
        initial_prompt: str = "",
    ):
        self.model_size = model_size
        self.device = device
        self.language = language or None
        self.beam_size = max(1, beam_size)
        self.initial_prompt = initial_prompt or None
        self._model = None  # 懒加载：首次识别时才下载并加载模型

    def _get_model(self):
        if self._model is None:
            # 国内优先走镜像下载模型
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type="int8"
            )
        return self._model

    def transcribe(self, audio_bytes: bytes) -> str:
        # Faster-Whisper 通过 PyAV(ffmpeg) 解码，浏览器录的 webm/opus 可直接识别
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            segments, _ = self._get_model().transcribe(
                tmp_path,
                beam_size=self.beam_size,
                language=self.language,
                initial_prompt=self.initial_prompt,
            )
            return "".join(seg.text for seg in segments).strip()
        finally:
            os.unlink(tmp_path)


def get_stt_provider(config: Config) -> STTProvider:
    """根据配置返回对应的 STT 实现。"""
    if config.stt_provider == "faster-whisper":
        return FasterWhisperSTTProvider(
            config.stt_model_size,
            language=config.stt_language,
            beam_size=config.stt_beam_size,
            initial_prompt=config.stt_initial_prompt,
        )
    raise ValueError(f"未知的 STT provider: {config.stt_provider}")
