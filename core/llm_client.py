"""统一的 AI API 调用封装（基于 httpx，OpenAI 兼容格式）。"""
import json
from typing import Any

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class LLMClient:
    """文本语言模型客户端。"""

    def __init__(self, cfg: dict):
        self.api_base = cfg["api_base"].rstrip("/")
        self.api_key = cfg["api_key"]
        self.model = cfg["model"]
        self.temperature = cfg.get("temperature", 0.3)
        self.max_tokens = cfg.get("max_tokens", 4096)

    async def chat(self, messages: list[dict], json_mode: bool = False, **kwargs) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()

        data = resp.json()
        return data["choices"][0]["message"]["content"]


class VLMClient(LLMClient):
    """视觉语言模型客户端，支持 base64 图片输入。"""

    async def chat_with_image(
        self, text_prompt: str, image_base64_list: list[str], mime: str = "image/png", **kwargs
    ) -> str:
        """
        image_base64_list: 若干张图片的 base64 字符串（不含 data URI 前缀）
        """
        content: list[dict] = []
        for b64 in image_base64_list:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        content.append({"type": "text", "text": text_prompt})

        return await self.chat([{"role": "user", "content": content}], **kwargs)


class EmbeddingClient:
    """文本向量化客户端。"""

    def __init__(self, cfg: dict):
        self.api_base = cfg["api_base"].rstrip("/")
        self.api_key = cfg["api_key"]
        self.model = cfg["model"]
        self.dimensions = cfg.get("dimensions", 1024)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self.api_base}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
            )
            resp.raise_for_status()

        data = resp.json()
        # data["data"] 按 index 排序，直接取 embedding
        return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
