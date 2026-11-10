"""FastAPI 入口：提供数字人对话 API 并托管前端页面。"""
import base64
import binascii
import json
import logging
import os
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import load_config
from backend.knowledge import KnowledgeBase
from backend.pipeline import DigitalHumanPipeline
from backend.providers.llm import get_llm_provider
from backend.providers.stt import get_stt_provider
from backend.providers.tts import get_tts_provider

# 项目根目录（backend 的上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

config = load_config()

# 知识库目录：KB_DIR 为空时用项目根目录下的 knowledge/
kb_dir = config.kb_dir or os.path.join(BASE_DIR, "knowledge")
if not os.path.isabs(kb_dir):
    kb_dir = os.path.join(BASE_DIR, kb_dir)
knowledge_base = (
    KnowledgeBase(kb_dir, min_score=config.kb_min_score)
    if config.kb_enabled
    else None
)

pipeline = DigitalHumanPipeline(
    config=config,
    llm=get_llm_provider(config),
    tts=get_tts_provider(config),
    knowledge_base=knowledge_base,
)
stt_provider = get_stt_provider(config)

logger = logging.getLogger("digital_human")

app = FastAPI(title="AI 数字人对话系统", version="0.1.0")

MAX_AUDIO_BYTES = 5 * 1024 * 1024


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    # 形如 [{"role": "user", "content": "你好"}, ...]
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)

    def as_history(self) -> list[dict]:
        return [m.model_dump() for m in self.messages]


@app.post("/api/chat")
def chat(req: ChatRequest):
    """核心接口：接收对话历史，返回数字人的文字回复与合成语音。"""
    try:
        reply_text, reply_audio = pipeline.reply(req.as_history())
    except Exception:
        logger.exception("chat failed")
        raise HTTPException(status_code=502, detail="生成失败，请稍后重试")

    return {
        "reply_text": reply_text,
        "audio_base64": base64.b64encode(reply_audio).decode("ascii"),
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

    def generate():
        try:
            for sentence, audio in pipeline.stream_reply(req.as_history()):
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
    }


# 托管前端静态页面（放在最后，避免覆盖 /api 路由）
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
