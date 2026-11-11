"""FastAPI 入口：提供数字人对话 API 并托管前端页面。"""
import base64
import binascii
import json
import logging
import os
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import load_config
from backend.knowledge import MAX_FILE_BYTES, SUPPORTED_EXTS, KnowledgeBase
from backend.pipeline import DigitalHumanPipeline
from backend.providers.llm import get_llm_provider
from backend.providers.stt import get_stt_provider
from backend.providers.tts import get_tts_provider

# 项目根目录（backend 的上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)

config = load_config()
pipeline = DigitalHumanPipeline(
    config=config,
    llm=get_llm_provider(config),
    tts=get_tts_provider(config),
)
stt_provider = get_stt_provider(config)

logger = logging.getLogger("digital_human")

# 本地知识库：启动时扫描 knowledge/ 并建索引；目录不存在会自动创建
try:
    knowledge_base = (
        KnowledgeBase(
            config.kb_dir,
            chunk_size=config.kb_chunk_size,
            overlap=config.kb_overlap,
            min_score=config.kb_min_score,
        )
        if config.kb_enabled
        else None
    )
except Exception:
    logger.exception("knowledge base init failed")
    knowledge_base = None

app = FastAPI(title="AI 数字人对话系统", version="0.1.0")

MAX_AUDIO_BYTES = 5 * 1024 * 1024


def _decode_upload_filename(name: str) -> str:
    """multipart 的 filename 常被按 Latin-1 解码，这里还原成 UTF-8 中文名。"""
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _mask_key(key: str) -> str:
    """只回显 Key 的头尾，避免前端/日志暴露完整 Key。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:3] + "..." + key[-4:]


def _safe_env_value(value: str) -> str:
    """配置值只允许单行，防止写入 .env 时注入额外配置。"""
    return value.replace("\r", "").replace("\n", "").strip()


def _update_env_file(path: str, updates: dict[str, str]) -> None:
    """原地更新 .env 中指定的键，保留注释和其他配置。"""
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={_safe_env_value(remaining.pop(key))}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={_safe_env_value(value)}")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    # 形如 [{"role": "user", "content": "你好"}, ...]
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    use_kb: bool = True  # 是否检索本地知识库

    def as_history(self) -> list[dict]:
        return [m.model_dump() for m in self.messages]


class SetupRequest(BaseModel):
    provider: Literal["mock", "deepseek"] = "deepseek"
    api_key: str = Field(default="", max_length=300)
    model: str | None = Field(default=None, max_length=100)
    base_url: str | None = Field(default=None, max_length=300)


class KBSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=4, ge=1, le=20)


def _last_user_text(history: list[dict]) -> str:
    for message in reversed(history):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _kb_lookup(history: list[dict], use_kb: bool) -> tuple[str, list[dict]]:
    """检索知识库，返回 (注入 prompt 的上下文, 去重后的来源列表)。"""
    if not use_kb or knowledge_base is None:
        return "", []
    query = _last_user_text(history).strip()
    if not query:
        return "", []
    try:
        hits = knowledge_base.search(query, top_k=config.kb_top_k)
        context = knowledge_base.context_from_hits(
            hits, max_chars=config.kb_max_context_chars
        )
    except Exception:
        # 检索失败不能影响正常对话，退回无知识库模式
        logger.exception("knowledge base search failed")
        return "", []

    sources: list[dict] = []
    seen: set[str] = set()
    for hit in hits:
        source = hit["source"]
        if source in seen:
            continue
        seen.add(source)
        sources.append(
            {
                "source": source,
                "score": hit["score"],
                "index": hit["index"],
                "snippet": hit["text"][:120],
            }
        )
    return context, sources


@app.post("/api/chat")
def chat(req: ChatRequest):
    """核心接口：接收对话历史，返回数字人的文字回复与合成语音。"""
    history = req.as_history()
    kb_context, sources = _kb_lookup(history, req.use_kb)
    try:
        if kb_context:
            reply_text, reply_audio = pipeline.reply(history, kb_context=kb_context)
        else:
            reply_text, reply_audio = pipeline.reply(history)
    except Exception:
        logger.exception("chat failed")
        raise HTTPException(status_code=502, detail="生成失败，请稍后重试")

    return {
        "reply_text": reply_text,
        "audio_base64": base64.b64encode(reply_audio).decode("ascii"),
        "sources": sources,
    }


class STTRequest(BaseModel):
    audio_base64: str  # 前端录制的 webm 音频


@app.post("/api/stt")
def stt(req: STTRequest):
    """语音识别：接收音频，返回识别文字。"""
    try:
        audio_bytes = base64.b64decode(req.audio_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="音频数据格式错误")

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="音频为空")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音频过大，请缩短录音")

    try:
        text = stt_provider.transcribe(audio_bytes)
    except Exception:
        # 音频解码失败、模型加载失败等都转成明确的上游错误，避免裸 500
        logger.exception("stt failed")
        raise HTTPException(status_code=502, detail="语音识别失败，请重试")
    return {"text": text}


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    """流式对话：LLM 边生成、边分句、边 TTS，逐句返回 (文本 + 音频)。

    流式响应一旦开始就无法再改状态码，因此生成阶段的异常必须转成
    一条 {"type": "error"} 事件，让前端能区分「正常结束」与「中途失败」。
    """

    history = req.as_history()
    kb_context, sources = _kb_lookup(history, req.use_kb)

    def generate():
        try:
            # 先告诉前端本轮引用了哪些文件
            if sources:
                yield json.dumps(
                    {"type": "sources", "sources": sources}, ensure_ascii=False
                ) + "\n"
            stream = (
                pipeline.stream_reply(history, kb_context=kb_context)
                if kb_context
                else pipeline.stream_reply(history)
            )
            for sentence, audio in stream:
                chunk = {
                    "type": "sentence",
                    "text": sentence,
                    "audio_base64": base64.b64encode(audio).decode("ascii"),
                }
                yield json.dumps(chunk, ensure_ascii=False) + "\n"
        except Exception:
            logger.exception("chat stream failed")
            yield json.dumps(
                {"type": "error", "message": "生成失败，请稍后重试"},
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "llm_provider": config.llm_provider,
        "tts_provider": config.tts_provider,
        "stt_provider": config.stt_provider,
    }


@app.get("/api/voice/config")
def voice_config():
    """语音输入面板参数：前端启动时读取，判定时间条范围/默认值以后端为准。"""
    return {
        "vad_threshold": config.vad_threshold,
        "display_max_rms": config.display_max_rms,
        "silence_ms": config.silence_ms,
        "silence_ms_min": config.silence_ms_min,
        "silence_ms_max": config.silence_ms_max,
        "max_record_ms": config.max_record_ms,
        "idle_timeout_ms": config.idle_timeout_ms,
        "barge_in_threshold": config.barge_in_threshold,
        "barge_in_hold_ms": config.barge_in_hold_ms,
        "barge_in_guard_ms": config.barge_in_guard_ms,
    }


@app.get("/api/setup/status")
def setup_status():
    """当前大模型配置（不含完整 Key）。"""
    return {
        "provider": config.llm_provider,
        "has_key": bool(config.deepseek_api_key),
        "key_masked": _mask_key(config.deepseek_api_key),
        "model": config.deepseek_model,
        "base_url": config.deepseek_base_url,
    }


@app.post("/api/setup")
def setup(req: SetupRequest):
    """保存 API Key 并热切换 LLM，无需重启服务。

    本机使用接口（服务只监听 127.0.0.1），因此没有额外鉴权。
    """
    key = req.api_key.strip()
    if key:
        if any(ch.isspace() for ch in key) or not key.startswith("sk-") or len(key) < 20:
            raise HTTPException(
                status_code=400,
                detail="API Key 格式不正确：应以 sk- 开头，且不包含空格",
            )
    elif req.provider == "deepseek" and not config.deepseek_api_key:
        raise HTTPException(status_code=400, detail="请先填写 DeepSeek API Key")

    model = req.model.strip() if req.model else None
    base_url = req.base_url.strip().rstrip("/") if req.base_url else None

    updates = {"LLM_PROVIDER": req.provider}
    if key:
        updates["DEEPSEEK_API_KEY"] = key
    if model:
        updates["DEEPSEEK_MODEL"] = model
    if base_url:
        updates["DEEPSEEK_BASE_URL"] = base_url

    try:
        _update_env_file(ENV_PATH, updates)
        config.llm_provider = req.provider
        if key:
            config.deepseek_api_key = key
        if model:
            config.deepseek_model = model
        if base_url:
            config.deepseek_base_url = base_url
        # 热切换：下一轮对话立即使用新 provider
        pipeline.llm = get_llm_provider(config)
    except Exception:
        logger.exception("save setup failed")
        raise HTTPException(status_code=500, detail="保存配置失败，请检查文件夹写入权限")

    logger.info(
        "llm provider switched to %s (key configured: %s)",
        req.provider,
        bool(config.deepseek_api_key),
    )
    return setup_status()


def _kb_overview() -> dict:
    if knowledge_base is None:
        return {
            "enabled": False,
            "documents": [],
            "total_documents": 0,
            "total_chunks": 0,
            "supported_exts": sorted(SUPPORTED_EXTS),
            "max_file_mb": MAX_FILE_BYTES // (1024 * 1024),
        }
    data = knowledge_base.stats()
    data.update(
        {
            "enabled": True,
            "supported_exts": sorted(SUPPORTED_EXTS),
            "max_file_mb": MAX_FILE_BYTES // (1024 * 1024),
        }
    )
    return data


@app.get("/api/kb")
def kb_overview(response: Response):
    """知识库概览：文档列表、片段数、支持格式。"""
    response.headers["Cache-Control"] = "no-store"
    return _kb_overview()


@app.post("/api/kb/upload")
async def kb_upload(
    file: UploadFile = File(...), filename: str | None = Form(None)
):
    """上传文档并重建索引；无法解析的文件不会落盘。

    前端会额外提交一个 UTF-8 的 filename 表单字段，避免 multipart
    header 里的中文文件名被按 Latin-1 解码后丢失。
    """
    if knowledge_base is None:
        raise HTTPException(status_code=400, detail="知识库未启用")
    data = await file.read()
    if filename and filename.strip():
        name = filename.strip()
    else:
        name = _decode_upload_filename(file.filename or "document.txt")
    try:
        return knowledge_base.add_document(name, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("kb upload failed")
        raise HTTPException(status_code=500, detail="保存文档失败，请检查写入权限")


@app.delete("/api/kb/{doc_id}")
def kb_delete(doc_id: str):
    if knowledge_base is None:
        raise HTTPException(status_code=400, detail="知识库未启用")
    if not knowledge_base.remove_document(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"ok": True}


@app.post("/api/kb/reload")
def kb_reload():
    if knowledge_base is None:
        raise HTTPException(status_code=400, detail="知识库未启用")
    knowledge_base.refresh(force=True)
    return _kb_overview()


@app.post("/api/kb/search")
def kb_search(req: KBSearchRequest):
    """调试/预览用：直接查看某个问题会命中哪些片段。"""
    if knowledge_base is None:
        return {"hits": []}
    try:
        return {"hits": knowledge_base.search(req.query, top_k=req.top_k)}
    except Exception:
        logger.exception("kb search failed")
        raise HTTPException(status_code=500, detail="检索失败")


# 托管前端静态页面（放在最后，避免覆盖 /api 路由）
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
