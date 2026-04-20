"""统一的 AI API 调用封装（基于 httpx，OpenAI 兼容格式）。"""
import asyncio
import json
from typing import Any, AsyncGenerator

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试（网络/超时/5xx/429）。"""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


class LLMClient:
    """文本语言模型客户端。"""

    def __init__(self, cfg: dict):
        self.api_base = cfg["api_base"].rstrip("/")
        self.api_key = cfg["api_key"]
        self.model = cfg["model"]
        self.temperature = cfg.get("temperature", 0.3)
        self.max_tokens = cfg.get("max_tokens", 4096)
        # 重试策略（默认不重试，子类可覆盖）
        self.max_retries: int = cfg.get("max_retries", 0)
        self.retry_delay: float = cfg.get("retry_delay", 0.0)

    async def _do_chat_request(self, payload: dict) -> str:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def chat(self, messages: list[dict], json_mode: bool = False, **kwargs) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self._do_chat_request(payload)
            except Exception as e:
                last_exc = e
                if attempt >= self.max_retries or not _is_retryable(e):
                    raise
                logger.warning(
                    "%s 调用失败 [%s]：%s，%.0fs 后重试 (%d/%d)",
                    self.__class__.__name__,
                    type(e).__name__,
                    e,
                    self.retry_delay,
                    attempt + 1,
                    self.max_retries,
                )
                await asyncio.sleep(self.retry_delay)
        # 理论上不会到达
        assert last_exc is not None
        raise last_exc

    async def chat_stream(
        self, messages: list[dict], json_mode: bool = False, **kwargs
    ) -> AsyncGenerator[tuple[str, str], None]:
        """
        流式调用 LLM，逐 token yield。
        yield ("reasoning", text) — 思考过程（reasoning_content）
        yield ("content", text)   — 正文内容
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": True,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{self.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    # reasoning_content（DeepSeek-R1 等推理模型）
                    rc = delta.get("reasoning_content")
                    if rc:
                        yield ("reasoning", rc)
                    # content（正文 token）
                    ct = delta.get("content")
                    if ct:
                        yield ("content", ct)


class VLMClient(LLMClient):
    """视觉语言模型客户端，支持 base64 图片输入。"""

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        # VLM 默认启用重试：最多 10 次，每次间隔 10s（Critic 阶段稳定性要求）
        self.max_retries = cfg.get("max_retries", 10)
        self.retry_delay = cfg.get("retry_delay", 10.0)

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
