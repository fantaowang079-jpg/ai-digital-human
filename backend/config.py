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
    stt_language: str = "zh"            # 中文识别，避免模型误判语种
    stt_beam_size: int = 5              # 5 比原版 1 更准，CPU 下延迟可接受
    stt_initial_prompt: str = "以下是普通话的句子，请用简体中文输出。"

    # 语音输入（VAD）配置：由 /api/voice/config 下发给前端，避免前端写死
    vad_threshold: float = 0.02         # RMS 音量阈值
    display_max_rms: float = 0.15       # 音量条满格对应的 RMS（用于可视化缩放）
    silence_ms: int = 2000              # 说话后停顿多久判定说完（判定时间条默认长度）
    silence_ms_min: int = 1000          # 前端可调范围下限
    silence_ms_max: int = 4000          # 前端可调范围上限
    max_record_ms: int = 15000          # 单次录音最长时长（兜底）
    idle_timeout_ms: int = 12000        # 一直没说话的超时，超时退出语音输入

    # 打断（barge-in）：数字人说话时监听人声，盖过阈值持续一段时间就停止播放
    # 阈值会再按现场底噪自适应上调（最高 2.5 倍），避免被 TTS 回声误触发
    barge_in_threshold: float = 0.03
    barge_in_hold_ms: int = 300         # 累计超过阈值多久判定为人声（允许音节间短暂回落）
    barge_in_guard_ms: int = 400        # 开始播放后的静默期，避开起播杂音

    # 数字人角色设定（system prompt）
    system_prompt: str = (
        "你是「小助手」，一个友好的 AI 数字人。"
        "用简洁、口语化的中文回答用户，每次回复控制在 3 句话以内。"
    )


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
        stt_language=os.getenv("STT_LANGUAGE", "zh"),
        stt_beam_size=_env_int("STT_BEAM_SIZE", 5),
        stt_initial_prompt=os.getenv(
            "STT_INITIAL_PROMPT", "以下是普通话的句子，请用简体中文输出。"
        ),
        vad_threshold=_env_float("VAD_THRESHOLD", 0.02),
        display_max_rms=_env_float("VAD_DISPLAY_MAX_RMS", 0.15),
        silence_ms=_env_int("VAD_SILENCE_MS", 2000),
        silence_ms_min=_env_int("VAD_SILENCE_MS_MIN", 1000),
        silence_ms_max=_env_int("VAD_SILENCE_MS_MAX", 4000),
        max_record_ms=_env_int("VAD_MAX_RECORD_MS", 15000),
        idle_timeout_ms=_env_int("VAD_IDLE_TIMEOUT_MS", 12000),
        barge_in_threshold=_env_float("BARGE_IN_THRESHOLD", 0.03),
        barge_in_hold_ms=_env_int("BARGE_IN_HOLD_MS", 300),
        barge_in_guard_ms=_env_int("BARGE_IN_GUARD_MS", 400),
        system_prompt=os.getenv(
            "SYSTEM_PROMPT",
            "你是「小助手」，一个友好的 AI 数字人。用简洁、口语化的中文回答用户，每次回复控制在 3 句话以内。",
        ),
    )
