"""FastAPI 入口：提供数字人对话 API 并托管前端页面。"""
import base64
import binascii
import json
import logging
import logging.handlers
import os
import re
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import load_config
from backend.knowledge import KnowledgeBase, extract_pdf_text
from backend.memory import MemoryStore, extractive_summary
from backend.pipeline import DigitalHumanPipeline
from backend.providers.llm import get_llm_provider
from backend.providers.stt import get_stt_provider
from backend.providers.tts import get_tts_provider

# 项目根目录（backend 的上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
load_dotenv(ENV_PATH)

# 日志同时写控制台和 logs/app.log，方便对方把问题日志发回来
logger = logging.getLogger("digital_human")
logger.setLevel(logging.INFO)
_log_file = os.path.join(LOG_DIR, "app.log")
if not any(
    isinstance(handler, logging.handlers.RotatingFileHandler)
    and os.path.abspath(getattr(handler, "baseFilename", ""))
    == os.path.abspath(_log_file)
    for handler in logger.handlers
):
    _file_handler = logging.handlers.RotatingFileHandler(
        _log_file,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(_file_handler)
    logging.getLogger("uvicorn.error").addHandler(_file_handler)

config = load_config()
logger.info(
    "应用启动：%s | LLM=%s | KB=%s | Memory=%s",
    BASE_DIR,
    config.llm_provider,
    config.kb_enabled,
    config.memory_enabled,
)

# 知识库目录：KB_DIR 为空时用项目根目录下的 knowledge/
kb_dir = config.kb_dir or os.path.join(BASE_DIR, "knowledge")
if not os.path.isabs(kb_dir):
    kb_dir = os.path.join(BASE_DIR, kb_dir)
knowledge_base = (
    KnowledgeBase(kb_dir, min_score=config.kb_min_score)
    if config.kb_enabled
    else None
)

# 上下文记忆：默认保存在项目根目录 data/memory.db
memory_db_path = config.memory_db_path or os.path.join(BASE_DIR, "data", "memory.db")
if not os.path.isabs(memory_db_path):
    memory_db_path = os.path.join(BASE_DIR, memory_db_path)
memory_store = (
    MemoryStore(
        memory_db_path,
        keep_verbatim=config.memory_keep_verbatim,
        summarize_after=config.memory_summarize_after,
    )
    if config.memory_enabled
    else None
)

pipeline = DigitalHumanPipeline(
    config=config,
    llm=get_llm_provider(config),
    tts=get_tts_provider(config),
    knowledge_base=knowledge_base,
    memory=memory_store,
)
stt_provider = get_stt_provider(config)


def _summarize_memory(existing: str, transcript: str) -> str:
    """把历史摘要和新对话压缩成一段长期记忆（Mock 模式用抽取式兜底）。"""
    if config.llm_provider == "mock":
        return extractive_summary(transcript)

    content = (
        "请把下面的对话压缩成一段不超过 300 字的长期记忆摘要。"
        "保留用户身份、偏好、目标、关键事实、待办和情绪状态，不要编造。\n\n"
        f"已有摘要：{existing or '无'}\n\n新对话：\n{transcript}"
    )
    try:
        return pipeline.llm.chat(
            [
                {"role": "system", "content": "你是对话记忆压缩器，只输出摘要正文。"},
                {"role": "user", "content": content},
            ]
        ).strip()
    except Exception:
        logger.exception("记忆摘要调用大模型失败，改用抽取式摘要")
        return extractive_summary(transcript)


def _remember_exchange(
    session_id: str, mode: str, history: list[dict], assistant_text: str
) -> None:
    """把一轮成功对话写入长期记忆，并在后台按需生成摘要。"""
    if memory_store is None:
        return
    user_text = next(
        (m.get("content", "") for m in reversed(history) if m.get("role") == "user"),
        "",
    )
    if not user_text or not assistant_text.strip():
        return
    try:
        memory_store.append_exchange(session_id, mode, user_text, assistant_text)
        memory_store.maybe_summarize(session_id, mode, _summarize_memory)
    except Exception:
        logger.exception("写入长期记忆失败")


def _update_env_file(values: dict[str, str]) -> None:
    """更新（或创建）.env 中的键值，保留其它配置和注释。"""
    lines: list[str] = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as fh:
            lines = fh.read().splitlines()

    remaining = dict(values)
    output: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if "=" in line and not stripped.startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(line)

    for key, value in remaining.items():
        output.append(f"{key}={value}")

    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(output) + "\n")
    os.replace(tmp, ENV_PATH)

app = FastAPI(title="AI 数字人对话系统", version="0.1.0")

MAX_AUDIO_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    # 形如 [{"role": "user", "content": "你好"}, ...]
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    # professional=专业模式「知语」；psychologist=心理医生模式「心语」
    mode: Literal["professional", "psychologist"] = "professional"
    # 浏览器本地持久化的会话 ID，用于跨刷新/重启找回上下文记忆
    session_id: str = Field(
        default="default",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    def as_history(self) -> list[dict]:
        return [m.model_dump() for m in self.messages]


@app.post("/api/chat")
def chat(req: ChatRequest):
    """核心接口：接收对话历史，返回数字人的文字回复与合成语音。"""
    history = req.as_history()
    try:
        reply_text, reply_audio = pipeline.reply(history, req.mode, req.session_id)
        sources = pipeline.retrieve_sources(history, req.mode)
    except Exception:
        logger.exception("chat failed")
        raise HTTPException(status_code=502, detail="生成失败，请稍后重试")

    _remember_exchange(req.session_id, req.mode, history, reply_text)
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

    text = stt_provider.transcribe(audio_bytes)
    return {"text": text}


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    """流式对话：LLM 边生成、边分句、边 TTS，逐句返回 (文本 + 音频)。

    流式响应一旦开始就无法再改状态码，因此生成阶段的异常必须转成
    一条 {"type": "error"} 事件，让前端能区分「正常结束」与「中途失败」。
    """

    history = req.as_history()

    def generate():
        parts: list[str] = []
        got_delta = False
        try:
            # 专业模式先推送参考来源，前端在气泡下方展示引用文件
            sources = pipeline.retrieve_sources(history, req.mode)
            if sources:
                yield json.dumps(
                    {"type": "sources", "sources": sources}, ensure_ascii=False
                ) + "\n"

            for event in pipeline.stream_events(history, req.mode, req.session_id):
                if event["type"] == "delta":
                    got_delta = True
                    parts.append(event["text"])
                    yield json.dumps(
                        {"type": "delta", "text": event["text"]},
                        ensure_ascii=False,
                    ) + "\n"
                else:
                    # 不逐词输出的 provider：用句子文本补全完整回复
                    if not got_delta:
                        parts.append(event["text"])
                    yield json.dumps(
                        {
                            "type": "sentence",
                            "text": event["text"],
                            "audio_base64": base64.b64encode(event["audio"]).decode(
                                "ascii"
                            ),
                        },
                        ensure_ascii=False,
                    ) + "\n"

            _remember_exchange(
                req.session_id, req.mode, history, "".join(parts).strip()
            )
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


# ---------------------------------------------------------------- 知识库管理
class KBSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=4, ge=1, le=20)


@app.get("/api/kb")
def kb_overview(response: Response):
    """知识库概览：是否启用、文档列表、片段总数。"""
    # 上传后前端会立刻重新拉取，禁止浏览器/代理缓存，保证列表实时更新
    response.headers["Cache-Control"] = "no-store"
    if knowledge_base is None:
        return {"enabled": False, "documents": [], "total_documents": 0, "total_chunks": 0}
    return {"enabled": True, **knowledge_base.stats()}


@app.post("/api/kb/upload")
async def kb_upload(file: UploadFile = File(...)):
    """上传知识库文档（txt / md / csv / pdf / docx），落盘后立即重建索引。"""
    if knowledge_base is None:
        raise HTTPException(status_code=400, detail="知识库未启用（KB_ENABLED=false）")

    data = await file.read()
    try:
        doc = knowledge_base.add_document(file.filename or "document.txt", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("knowledge upload failed")
        raise HTTPException(status_code=500, detail="知识库上传失败，请稍后重试")
    return doc


@app.delete("/api/kb/{doc_id}")
def kb_delete(doc_id: str):
    """按文档 ID 删除知识库文档。"""
    if knowledge_base is None:
        raise HTTPException(status_code=400, detail="知识库未启用（KB_ENABLED=false）")
    if not knowledge_base.remove_document(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"ok": True}


@app.post("/api/kb/search")
def kb_search(req: KBSearchRequest):
    """检索调试接口：直接查看某个问题会命中哪些片段。"""
    if knowledge_base is None:
        return {"enabled": False, "hits": []}
    return {"enabled": True, "hits": knowledge_base.search(req.query, top_k=req.top_k)}


@app.post("/api/kb/reload")
def kb_reload():
    """手动重建索引（外部直接往 knowledge/ 放文件后可调用）。"""
    if knowledge_base is None:
        return {"enabled": False}
    knowledge_base.refresh(force=True)
    return {"enabled": True, **knowledge_base.stats()}


@app.post("/api/pdf/extract")
def pdf_extract(file: UploadFile = File(...)):
    """PDF 转文字：上传 PDF，返回提取的纯文本、页数与字符数。"""
    filename = os.path.basename((file.filename or "document.pdf").replace("\\", "/"))
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF 过大（上限 20MB）")

    try:
        text, pages = extract_pdf_text(data)
    except Exception as exc:
        logger.warning("PDF 解析失败 %s: %s", filename, exc)
        raise HTTPException(status_code=400, detail="PDF 解析失败，请确认文件未加密或损坏")

    # 清理 PDF 排版产生的字间空格和多余空行，让预览/导出更易读
    text = re.sub(r"(?<=[一-鿿])[ \t　]+(?=[一-鿿])", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise HTTPException(
            status_code=400,
            detail="没有提取到文字，可能是扫描版/图片型 PDF，需要 OCR 才能识别",
        )
    return {"filename": filename, "text": text, "chars": len(text), "pages": pages}


# ---------------------------------------------------------------- 上下文记忆
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _check_session_id(session_id: str) -> None:
    if not _SESSION_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="session_id 格式不正确")


@app.get("/api/memory")
def memory_overview(
    session_id: str = "default",
    mode: Literal["professional", "psychologist"] = "professional",
    limit: int = 50,
):
    """查看某个会话的长期记忆：摘要 + 最近对话。"""
    _check_session_id(session_id)
    if memory_store is None:
        return {
            "enabled": False,
            "session_id": session_id,
            "mode": mode,
            "summary": "",
            "messages": [],
            "total_messages": 0,
        }
    return {
        "enabled": True,
        "session_id": session_id,
        "mode": mode,
        **memory_store.overview(session_id, mode, max(1, min(limit, 200))),
    }


@app.delete("/api/memory")
def memory_clear(
    session_id: str = "default",
    mode: Literal["professional", "psychologist"] | None = None,
):
    """清除某个会话的记忆；不传 mode 时清空两种模式。"""
    _check_session_id(session_id)
    if memory_store is None:
        return {"ok": False, "detail": "记忆未启用"}
    memory_store.clear(session_id, mode)
    return {"ok": True, "session_id": session_id, "mode": mode}


# ---------------------------------------------------------------- 首次配置
_ALLOWED_SETUP_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class SetupRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=200)


@app.get("/api/setup/status")
def setup_status():
    return {
        "provider": config.llm_provider,
        "key_configured": bool(config.deepseek_api_key),
    }


@app.post("/api/setup")
def setup(req: SetupRequest, request: Request):
    """首次使用向导：保存 DeepSeek API Key 到本机 .env，并热切换 LLM。"""
    host = request.client.host if request.client else ""
    if host not in _ALLOWED_SETUP_HOSTS:
        raise HTTPException(status_code=403, detail="只允许在本机配置 API Key")

    key = req.api_key.strip()
    if not key.startswith("sk-") or len(key) < 10:
        raise HTTPException(status_code=400, detail="API Key 格式不正确（应以 sk- 开头）")

    try:
        _update_env_file({"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": key})
    except OSError as exc:
        logger.exception("写入 .env 失败")
        raise HTTPException(status_code=500, detail=f"无法写入 .env：{exc}")

    config.llm_provider = "deepseek"
    config.deepseek_api_key = key
    os.environ["LLM_PROVIDER"] = "deepseek"
    os.environ["DEEPSEEK_API_KEY"] = key
    pipeline.llm = get_llm_provider(config)
    logger.info("API Key 已配置，LLM 切换为 deepseek")
    return {"ok": True, "provider": config.llm_provider}


@app.get("/api/health")
def health(response: Response):
    response.headers["Cache-Control"] = "no-store"
    kb_stats = knowledge_base.stats() if knowledge_base is not None else None
    return {
        "status": "ok",
        "llm_provider": config.llm_provider,
        "tts_provider": config.tts_provider,
        "stt_provider": config.stt_provider,
        "kb_enabled": config.kb_enabled,
        "kb_documents": kb_stats["total_documents"] if kb_stats else 0,
        "kb_chunks": kb_stats["total_chunks"] if kb_stats else 0,
        "modes": ["professional", "psychologist"],
        "memory_enabled": config.memory_enabled,
        "key_configured": bool(config.deepseek_api_key),
    }


# 托管前端静态页面（放在最后，避免覆盖 /api 路由）
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
