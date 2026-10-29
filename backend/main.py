"""FastAPI 入口：提供数字人对话 API 并托管前端页面。"""
import base64
import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import load_config
from backend.pipeline import DigitalHumanPipeline
from backend.providers.llm import get_llm_provider
from backend.providers.stt import get_stt_provider
from backend.providers.tts import get_tts_provider

# 项目根目录（backend 的上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

config = load_config()
pipeline = DigitalHumanPipeline(
    config=config,
    llm=get_llm_provider(config),
    tts=get_tts_provider(config),
)
stt_provider = get_stt_provider(config)

app = FastAPI(title="AI 数字人对话系统", version="0.1.0")


class ChatRequest(BaseModel):
    # 形如 [{"role": "user", "content": "你好"}, ...]
    messages: list[dict]


@app.post("/api/chat")
def chat(req: ChatRequest):
    """核心接口：接收对话历史，返回数字人的文字回复与合成语音。"""
    reply_text, reply_audio = pipeline.reply(req.messages)
    return {
        "reply_text": reply_text,
        "audio_base64": base64.b64encode(reply_audio).decode("ascii"),
    }


class STTRequest(BaseModel):
    audio_base64: str  # 前端录制的 webm 音频


@app.post("/api/stt")
def stt(req: STTRequest):
    """语音识别：接收音频，返回识别文字。"""
    audio_bytes = base64.b64decode(req.audio_base64)
    text = stt_provider.transcribe(audio_bytes)
    return {"text": text}


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    """流式对话：LLM 边生成、边分句、边 TTS，逐句返回 (文本 + 音频)。"""

    def generate():
        for sentence, audio in pipeline.stream_reply(req.messages):
            chunk = {
                "text": sentence,
                "audio_base64": base64.b64encode(audio).decode("ascii"),
            }
            yield json.dumps(chunk, ensure_ascii=False) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "llm_provider": config.llm_provider,
        "tts_provider": config.tts_provider,
        "stt_provider": config.stt_provider,
    }


# 托管前端静态页面（放在最后，避免覆盖 /api 路由）
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
