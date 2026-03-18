"""招标文件深度分析服务 — OpenAI 兼容 API 入口。"""
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from core.orchestrator import Orchestrator
from models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamResponse,
    Choice,
    ChoiceMessage,
    DeltaMessage,
    FileUploadResponse,
    StreamChoice,
    Usage,
)
from utils.config_loader import load_config
from utils.file_handler import download_file, save_upload_file
from utils.logger import get_logger

logger = get_logger(__name__)

# ── 配置加载（自动展开 .env 中的环境变量）──────────────────────────────────
CFG = load_config()

# 确保缓存目录存在
for key in ("upload_dir", "pages_dir", "vectors_dir"):
    Path(CFG["files"][key]).mkdir(parents=True, exist_ok=True)

orchestrator: Orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator
    orchestrator = Orchestrator(CFG)
    logger.info("Orchestrator 初始化完成")
    yield
    logger.info("服务关闭")


# ── FastAPI ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Tender Deep Research",
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── /v1/models ──────────────────────────────────────────────────────────────
@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [{
            "id": "tender-research",
            "object": "model",
            "created": int(time.time()),
            "owned_by": "tender-deep-research",
        }],
    }


# ── /v1/files（文件上传）────────────────────────────────────────────────────
@app.post("/v1/files", response_model=FileUploadResponse)
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    content = await file.read()
    max_bytes = CFG["files"]["max_size_mb"] * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {CFG['files']['max_size_mb']}MB 限制")

    file_id, _ = await save_upload_file(content, file.filename, CFG["files"]["upload_dir"])
    logger.info("文件上传: %s → id=%s", file.filename, file_id)
    return FileUploadResponse(
        id=file_id,
        filename=file.filename,
        size=len(content),
    )


# ── /v1/chat/completions（核心接口）────────────────────────────────────────
@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    messages = [m.model_dump() for m in req.messages]

    # 解析用户输入
    keys, file_url = orchestrator.parse_user_input(messages)
    if not keys:
        raise HTTPException(status_code=400, detail="未能从消息中解析出要素清单，请检查输入格式")
    if not file_url:
        raise HTTPException(status_code=400, detail="未能从消息中找到 PDF URL，请在消息中包含文件链接")

    # 下载文件
    try:
        pdf_path = await download_file(file_url, CFG["files"]["upload_dir"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件下载失败: {e}")

    # 流式响应
    if req.stream:
        return StreamingResponse(
            _stream_response(keys, pdf_path),
            media_type="text/event-stream",
        )

    # 非流式响应
    try:
        result = await orchestrator.run(keys, pdf_path)
    except Exception as e:
        logger.exception("分析失败")
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")

    content = orchestrator._format_result_markdown(result)
    return ChatCompletionResponse(
        choices=[Choice(message=ChoiceMessage(content=content))],
        usage=Usage(),
    )


async def _stream_response(keys: list[str], pdf_path: str):
    """SSE 生成器：逐步推送分析进度。"""
    resp_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())

    # 首帧：role
    first = ChatCompletionStreamResponse(
        id=resp_id,
        created=created,
        choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
    )
    yield f"data: {first.model_dump_json()}\n\n"

    try:
        async for chunk_text in orchestrator.run_stream(keys, pdf_path):
            chunk = ChatCompletionStreamResponse(
                id=resp_id,
                created=created,
                choices=[StreamChoice(delta=DeltaMessage(content=chunk_text))],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"
    except Exception as e:
        logger.exception("流式分析失败")
        err_chunk = ChatCompletionStreamResponse(
            id=resp_id,
            created=created,
            choices=[StreamChoice(delta=DeltaMessage(content=f"\n\n❌ 分析出错: {e}"))],
        )
        yield f"data: {err_chunk.model_dump_json()}\n\n"

    # 终止帧
    done = ChatCompletionStreamResponse(
        id=resp_id,
        created=created,
        choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
    )
    yield f"data: {done.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


# ── /health ──────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=CFG["server"]["host"],
        port=CFG["server"]["port"],
        reload=False,
    )
