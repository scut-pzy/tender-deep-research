"""系统中所有数据结构定义（OpenAI兼容 + 内部类型）。"""
import time
import uuid
from typing import Any, Optional, Union

from pydantic import BaseModel, Field


# ── OpenAI 请求模型 ────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: Union[str, list]  # list 用于多模态内容


class ChatCompletionRequest(BaseModel):
    model: str = "tender-research"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    use_cache: bool = True   # True=优先读向量缓存；False=强制重新切分并向量化


# ── OpenAI 非流式响应 ──────────────────────────────────────────────────────

class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:8]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "tender-research"
    choices: list[Choice]
    usage: Usage = Field(default_factory=Usage)


# ── OpenAI 流式响应 ────────────────────────────────────────────────────────

class DeltaMessage(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None


class StreamChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: Optional[str] = None


class ChatCompletionStreamResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:8]}")
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "tender-research"
    choices: list[StreamChoice]


# ── 文件上传响应 ───────────────────────────────────────────────────────────

class FileUploadResponse(BaseModel):
    id: str
    object: str = "file"
    filename: str
    purpose: str = "assistants"
    created_at: int = Field(default_factory=lambda: int(time.time()))
    size: int = 0


# ── 内部数据类型 ───────────────────────────────────────────────────────────

class ExtractionItem(BaseModel):
    key: str
    value: Optional[str] = None
    source_page: Optional[int] = None
    source_text: Optional[str] = None
    confidence: float = 0.0
    verified: bool = False


class CriticFeedback(BaseModel):
    key: str
    verified: bool
    actual_value: Optional[str] = None
    comment: str = ""


class ExtractionResult(BaseModel):
    items: list[ExtractionItem]
    total_iterations: int
    converged: bool
