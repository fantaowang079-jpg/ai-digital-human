"""全局配置：从环境变量 / .env 读取。"""
import os
from dataclasses import dataclass

DEFAULT_PROFESSIONAL_PROMPT = (
    "你是「知语」，一个严谨的知识库问答助手，当前处于「专业模式」。\n"
    "工作要求：\n"
    "1. 只能依据【知识库资料】回答，不要使用资料以外的知识，也不要编造。\n"
    "2. 尽量完整地引用资料中的关键信息，不要遗漏与问题相关的字段和数据。\n"
    "3. 每个关键结论后用（参考：文件名）标注来源，回答末尾单独列出「参考来源：文件名」。\n"
    "4. 如果资料不足以回答，明确说「知识库中没有找到相关信息」，并说明还缺少什么，不要猜测。\n"
    "5. 用简洁、口语化的中文回答；可以分点，控制在 6 句话以内。"
)

DEFAULT_PSYCHOLOGIST_PROMPT = (
    "你是「心语」，一位温暖、耐心的心理倾听者，当前处于「心理医生模式」。\n"
    "工作要求：\n"
    "1. 以共情和倾听为主，用自然、口语化的中文聊天，不要像客服一样生硬。\n"
    "2. 不要使用知识库资料，也不要引用文件；可以问开放式问题，引导用户表达感受。\n"
    "3. 不做医学诊断、不开药、不替代专业心理咨询。\n"
    "4. 如果用户提到自伤、自杀或严重危机，请温和而明确地建议立即联系身边信任的人，"
    "或当地心理援助/急救热线；不要说教，也不要轻描淡写。\n"
    "5. 每次回复控制在 3-4 句话，语气温和，不评判。"
)


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

    # 两种模式的人格设定（可用环境变量覆盖）
    professional_prompt: str = DEFAULT_PROFESSIONAL_PROMPT
    psychologist_prompt: str = DEFAULT_PSYCHOLOGIST_PROMPT

    # 通用补充要求（两种模式都会追加，可为空）
    system_prompt: str = "用简洁、口语化的中文回答。"

    # 知识库（RAG-lite）配置
    kb_enabled: bool = True
    kb_dir: str = ""                    # 为空时使用项目根目录下的 knowledge/
    kb_top_k: int = 4                   # 每次检索注入的片段数
    kb_min_score: float = 0.05          # 相关度阈值，低于该值视为未命中
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
        professional_prompt=os.getenv("PROFESSIONAL_PROMPT")
        or DEFAULT_PROFESSIONAL_PROMPT,
        psychologist_prompt=os.getenv("PSYCHOLOGIST_PROMPT")
        or DEFAULT_PSYCHOLOGIST_PROMPT,
        system_prompt=os.getenv("SYSTEM_PROMPT", "用简洁、口语化的中文回答。"),
        kb_enabled=_env_bool("KB_ENABLED", True),
        kb_dir=os.getenv("KB_DIR", ""),
        kb_top_k=_env_int("KB_TOP_K", 4),
        kb_min_score=_env_float("KB_MIN_SCORE", 0.05),
        kb_max_context_chars=_env_int("KB_MAX_CONTEXT_CHARS", 3000),
    )
