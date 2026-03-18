"""统一的 LLM/VLM 客户端，基于 OpenAI 兼容接口。"""
import base64
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI

from utils.logger import get_logger

logger = get_logger(__name__)


class LLMClient:
    def __init__(self, cfg: dict):
        self.model = cfg["model"]
        self.temperature = cfg.get("temperature", 0.3)
        self.max_tokens = cfg.get("max_tokens", 4096)
        self._client = AsyncOpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["api_base"],
            http_client=httpx.AsyncClient(timeout=120),
        )

    async def chat(self, messages: list[dict], **kwargs) -> str:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
        )
        return resp.choices[0].message.content


class VLMClient(LLMClient):
    """支持图片输入的视觉语言模型客户端。"""

    async def chat_with_image(
        self, text_prompt: str, image_path: str, **kwargs
    ) -> str:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        suffix = Path(image_path).suffix.lstrip(".")
        mime = f"image/{suffix if suffix != 'jpg' else 'jpeg'}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                    {"type": "text", "text": text_prompt},
                ],
            }
        ]
        return await self.chat(messages, **kwargs)


def build_clients(cfg: dict) -> tuple[LLMClient, VLMClient, Any]:
    """根据配置构建 policy/critic/embedding 客户端。"""
    policy = LLMClient(cfg["policy_llm"])
    critic = VLMClient(cfg["critic_vlm"])

    embed_cfg = cfg["embedding"]
    embed_client = AsyncOpenAI(
        api_key=embed_cfg["api_key"],
        base_url=embed_cfg["api_base"],
        http_client=httpx.AsyncClient(timeout=60),
    )
    return policy, critic, embed_client, embed_cfg["model"], embed_cfg.get("dimensions", 1024)
