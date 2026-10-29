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
    )
