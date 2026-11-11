"""STT Provider：可插拔的语音识别层。"""
import os
import tempfile
from abc import ABC, abstractmethod

from backend.config import Config


def _normalize_text(text: str) -> str:
    """去掉标点和空白，只保留文字/数字，用于判断 prompt 回声。"""
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _is_prompt_echo(text: str, prompt: str) -> bool:
    """识别结果是否只是 initial_prompt 的片段（噪音时常见）。"""
    t = _normalize_text(text)
    p = _normalize_text(prompt)
    if len(t) < 4 or not p:
        return False
    return t in p or p in t


class STTProvider(ABC):
    """语音识别统一接口。"""

    @abstractmethod
    def transcribe(self, audio_bytes: bytes) -> str:
        """将音频字节转成文字。"""


class FasterWhisperSTTProvider(STTProvider):
    """本地 Faster-Whisper：离线、免费，CPU 即可运行。

    优先加载包内 models/faster-whisper-<size>/ 本地模型（完全离线）；
    没有内置模型时才从 HuggingFace 下载（国内走 hf-mirror 镜像加速）。
    """

    def __init__(
        self,
        model_size: str = "base",
        model_path: str = "",
        device: str = "cpu",
        language: str = "zh",
        beam_size: int = 5,
        initial_prompt: str = "",
    ):
        self.model_size = model_size
        self.model_path = model_path
        self.device = device
        self.language = language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt
        self._model = None  # 懒加载：首次识别时才下载并加载模型

    def _get_model(self):
        if self._model is None:
            # 国内优先走镜像下载模型（仅在使用 model_size 时才需要联网）
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            from faster_whisper import WhisperModel

            # 包内内置模型优先，直接按本地目录加载，完全离线
            source = self.model_path if self.model_path else self.model_size
            self._model = WhisperModel(
                source, device=self.device, compute_type="int8"
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
                language=self.language or None,
                beam_size=self.beam_size,
                initial_prompt=self.initial_prompt or None,
                # 每段独立识别，避免上一句错误影响下一句
                condition_on_previous_text=False,
                # 纯噪音/静音更容易被判成“没有人说话”
                no_speech_threshold=0.5,
            )
            text = "".join(seg.text for seg in segments).strip()
            # 纯噪音时 Whisper 有时会把 initial_prompt 吐出来，按“没听清”处理
            if self.initial_prompt and _is_prompt_echo(text, self.initial_prompt):
                return ""
            return text
        finally:
            os.unlink(tmp_path)


def get_stt_provider(config: Config) -> STTProvider:
    """根据配置返回对应的 STT 实现。"""
    if config.stt_provider == "faster-whisper":
        return FasterWhisperSTTProvider(
            model_size=config.stt_model_size,
            model_path=config.stt_model_path,
            language=config.stt_language,
            beam_size=config.stt_beam_size,
            initial_prompt=config.stt_initial_prompt,
        )
    raise ValueError(f"未知的 STT provider: {config.stt_provider}")
