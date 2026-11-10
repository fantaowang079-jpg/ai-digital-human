"""全局配置：从环境变量 / .env 读取。"""
import os
from dataclasses import dataclass


@dataclass
class Config:
    # LLM 配置
    llm_provider: str = "mock"          # mock | deepseek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"

    # TTS 配置
    tts_provider: str = "edge"          # edge
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    # STT 配置
    stt_provider: str = "faster-whisper"
    stt_model_size: str = "base"        # tiny/base/small/medium/large

    # 数字人角色设定（system prompt）
    system_prompt: str = (
        "你是「小助手」，一个友好的 AI 数字人。"
        "用简洁、口语化的中文回答用户，每次回复控制在 3 句话以内。"
    )

    # 知识库（RAG-lite）配置
    kb_enabled: bool = True
    kb_dir: str = ""                    # 为空时使用项目根目录下的 knowledge/
    kb_top_k: int = 4                   # 每次检索注入的片段数
    kb_min_score: float = 0.05          # 相关度阈值，低于该值视为未命中
    kb_strict: bool = False             # true：未命中时要求模型明确回答「不知道」
    kb_max_context_chars: int = 3000    # 注入上下文的最大字符数


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def load_config() -> Config:
    """从环境变量读取配置，未设置时使用默认值。"""
    return Config(
        llm_provider=os.getenv("LLM_PROVIDER", "mock"),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        tts_provider=os.getenv("TTS_PROVIDER", "edge"),
        tts_voice=os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
        stt_provider=os.getenv("STT_PROVIDER", "faster-whisper"),
        stt_model_size=os.getenv("STT_MODEL_SIZE", "base"),
        system_prompt=os.getenv(
            "SYSTEM_PROMPT",
            "你是「小助手」，一个友好的 AI 数字人。用简洁、口语化的中文回答用户，每次回复控制在 3 句话以内。",
        ),
        kb_enabled=_env_bool("KB_ENABLED", True),
        kb_dir=os.getenv("KB_DIR", ""),
        kb_top_k=_env_int("KB_TOP_K", 4),
        kb_min_score=_env_float("KB_MIN_SCORE", 0.05),
        kb_strict=_env_bool("KB_STRICT", False),
        kb_max_context_chars=_env_int("KB_MAX_CONTEXT_CHARS", 3000),
    )
