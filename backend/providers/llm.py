"""LLM Provider：可插拔的大模型接入层。

新增模型只需实现 LLMProvider 接口并在 get_llm_provider 中注册，
上层编排逻辑无需改动。
"""
import json
from abc import ABC, abstractmethod

import requests

from backend.config import Config


class LLMProvider(ABC):
    """大模型统一接口。"""

    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """根据对话消息列表生成回复，返回文本。

        messages 形如 [{"role": "system"|"user"|"assistant", "content": "..."}]
        """

    def stream_chat(self, messages: list[dict]):
        """流式生成回复，逐个 yield 文本片段。

        默认实现一次性返回完整回复；支持流式的 provider 可覆盖本方法。
        """
        yield self.chat(messages)


class MockLLMProvider(LLMProvider):
    """占位模型：无需 API Key 即可跑通「LLM → TTS → 口型」全链路。"""

    def chat(self, messages: list[dict]) -> str:
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        return (
            f"（Mock 回复）我听到你说：「{last_user}」。"
            "配置 DeepSeek API Key 后，这里会变成真实的大模型回复。"
        )


class DeepSeekProvider(LLMProvider):
    """DeepSeek 官方 API（OpenAI 兼容协议）。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(self, messages: list[dict]) -> str:
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "thinking": {"type": "disabled"},
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def stream_chat(self, messages: list[dict]):
        """流式生成：逐个 yield 文本片段（SSE）。"""
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": messages,
                "stream": True,
                "thinking": {"type": "disabled"},
            },
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8") if isinstance(line, bytes) else line
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                delta = data["choices"][0]["delta"].get("content", "")
                if delta:
                    yield delta
            except (json.JSONDecodeError, KeyError, IndexError):
                continue


def get_llm_provider(config: Config) -> LLMProvider:
    """根据配置返回对应的 LLM 实现。"""
    if config.llm_provider == "deepseek":
        if not config.deepseek_api_key:
            raise ValueError("LLM_PROVIDER=deepseek 但未设置 DEEPSEEK_API_KEY")
        return DeepSeekProvider(
            config.deepseek_api_key, config.deepseek_base_url, config.deepseek_model
        )
    return MockLLMProvider()
