"""招标文件深度分析服务 — OpenAI 兼容 API 入口。"""
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.orchestrator import Orchestrator
from models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamResponse,
    ChecklistRequest,
    Choice,
    ChoiceMessage,
    ComplianceCheckRequest,
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
for key in ("upload_dir", "pages_dir", "vectors_dir", "history_dir"):
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
async def upload_file(file: UploadFile = File(...), file_type: str = Form("tender")):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    content = await file.read()
    max_bytes = CFG["files"]["max_size_mb"] * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {CFG['files']['max_size_mb']}MB 限制")

    if file_type not in ("tender", "bid"):
        file_type = "tender"

    file_id, _ = await save_upload_file(content, file.filename, CFG["files"]["upload_dir"], file_type)
    logger.info("文件上传: %s → id=%s type=%s", file.filename, file_id, file_type)
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
    keys, file_url, file_id = orchestrator.parse_user_input(messages)
    if not keys:
        raise HTTPException(status_code=400, detail="未能从消息中解析出要素清单，请检查输入格式")

    # 定位 PDF 文件
    upload_dir = Path(CFG["files"]["upload_dir"])
    if file_id:
        # 通过已上传的 file_id 直接找本地文件
        matches = [p for p in upload_dir.glob(f"{file_id}.*") if p.suffix == ".pdf"]
        if not matches:
            raise HTTPException(status_code=404, detail=f"file_id '{file_id}' 对应的文件不存在，请先上传")
        pdf_path = str(matches[0])
    elif file_url:
        try:
            pdf_path = await download_file(file_url, str(upload_dir))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"文件下载失败: {e}")
    else:
        raise HTTPException(status_code=400, detail="请在消息中提供文件（上传后的 file_id 或 PDF URL）")

    # 流式响应
    if req.stream:
        return StreamingResponse(
            _stream_response(keys, pdf_path, req.use_cache),
            media_type="text/event-stream",
        )

    # 非流式响应
    try:
        result = await orchestrator.run(keys, pdf_path, req.use_cache)
    except Exception as e:
        logger.exception("分析失败")
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")

    content = orchestrator._format_result_markdown(result)
    return ChatCompletionResponse(
        choices=[Choice(message=ChoiceMessage(content=content))],
        usage=Usage(),
    )


def _save_history(history_id: str, pdf_path: str, keys: list[str], result_chunk: str, stream_log: list[str] | None = None):
    """从 markdown code block 提取 JSON 并保存历史记录。"""
    import re as _re
    history_dir = Path(CFG["files"]["history_dir"])
    # Extract JSON from fenced code block
    m = _re.search(r"```json\s*\n([\s\S]*?)\n```", result_chunk)
    if not m:
        return
    try:
        result_data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return
    entry = {
        "id": history_id,
        "timestamp": time.time(),
        "filename": Path(pdf_path).name,
        "pdf_path": pdf_path,
        "keys": keys,
        "result": result_data,
        "stream_log": stream_log or [],
    }
    (history_dir / f"{history_id}.json").write_text(
        json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _stream_response(keys: list[str], pdf_path: str, use_cache: bool = True):
    """SSE 生成器：逐步推送分析进度。"""
    resp_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())
    summary_chunks: list[str] = []
    stream_log: list[str] = []
    in_summary = False

    # 首帧：role
    first = ChatCompletionStreamResponse(
        id=resp_id,
        created=created,
        choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
    )
    yield f"data: {first.model_dump_json()}\n\n"

    try:
        async for chunk_text in orchestrator.run_stream(keys, pdf_path, use_cache):
            stream_log.append(chunk_text)
            if chunk_text.startswith("## 📋"):
                in_summary = True
            if in_summary:
                summary_chunks.append(chunk_text)
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

    # 保存历史记录
    if summary_chunks:
        try:
            history_id = uuid.uuid4().hex[:12]
            _save_history(history_id, pdf_path, keys, "".join(summary_chunks), stream_log)
        except Exception:
            logger.exception("保存历史记录失败")

    # 终止帧
    done = ChatCompletionStreamResponse(
        id=resp_id,
        created=created,
        choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
    )
    yield f"data: {done.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


# ── /v1/files/list（列出已上传文件及缓存状态）─────────────────────────────
@app.get("/v1/files/list")
async def list_files(type: str | None = None):
    upload_dir = Path(CFG["files"]["upload_dir"])
    vectors_dir = Path(CFG["files"]["vectors_dir"])
    files = []
    for p in sorted(upload_dir.glob("*.pdf")):
        file_id = p.stem
        # 读取原始文件名元数据
        meta_path = upload_dir / f"{file_id}.meta.json"
        display_name = p.name
        file_type = "tender"  # 默认值（兼容旧文件）
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                display_name = meta.get("filename", p.name)
                file_type = meta.get("file_type", "tender")
            except Exception:
                pass
        # 按类型过滤
        if type and file_type != type:
            continue
        # 计算该文件的向量缓存 key，检查是否已 chunk 化
        cache_key = orchestrator._cache_key(str(p))
        has_vectors = (
            (vectors_dir / f"{cache_key}.pkl").exists()
            or (vectors_dir / f"{cache_key}_pc.pkl").exists()
        )
        files.append({
            "id": file_id,
            "filename": display_name,
            "size": p.stat().st_size,
            "file_type": file_type,
            "has_vectors": has_vectors,
            "cache_key": cache_key,
        })
    return {"files": files}


# ── /v1/files/{file_id}（删除文件）────────────────────────────────────────
@app.delete("/v1/files/{file_id}")
async def delete_file(file_id: str):
    upload_dir = Path(CFG["files"]["upload_dir"])
    matches = list(upload_dir.glob(f"{file_id}.*"))
    pdf_files = [f for f in matches if f.suffix == ".pdf"]
    if not pdf_files:
        raise HTTPException(status_code=404, detail=f"file_id '{file_id}' 不存在")
    for f in pdf_files:
        f.unlink(missing_ok=True)
    # 删除元数据
    meta_path = upload_dir / f"{file_id}.meta.json"
    meta_path.unlink(missing_ok=True)
    logger.info("文件已删除: id=%s", file_id)
    return {"deleted": file_id}


# ── /v1/history ────────────────────────────────────────────────────────────
@app.get("/v1/history")
async def list_history():
    history_dir = Path(CFG["files"]["history_dir"])
    items = []
    for p in sorted(history_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result = data.get("result", {})
            items.append({
                "id": data["id"],
                "timestamp": data["timestamp"],
                "filename": data.get("filename", ""),
                "fields": data.get("keys", []),
                "field_count": len(result),
            })
        except Exception:
            continue
    return {"history": items}


@app.get("/v1/history/{history_id}")
async def get_history(history_id: str):
    history_dir = Path(CFG["files"]["history_dir"])
    fp = history_dir / f"{history_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail="历史记录不存在")
    data = json.loads(fp.read_text(encoding="utf-8"))
    return data


# ── /v1/compliance（合规核查）──────────────────────────────────────────────

def _resolve_file(file_id: str) -> str:
    """根据 file_id 找到本地 PDF 路径。"""
    upload_dir = Path(CFG["files"]["upload_dir"])
    matches = [p for p in upload_dir.glob(f"{file_id}.*") if p.suffix == ".pdf"]
    if not matches:
        raise HTTPException(status_code=404, detail=f"file_id '{file_id}' 对应的文件不存在")
    return str(matches[0])


@app.post("/v1/compliance/checklist")
async def compliance_checklist(req: ChecklistRequest):
    pdf_path = _resolve_file(req.file_id)
    return StreamingResponse(
        _compliance_stream(orchestrator.generate_checklist_stream(pdf_path, req.use_cache)),
        media_type="text/event-stream",
    )


@app.post("/v1/compliance/check")
async def compliance_check(req: ComplianceCheckRequest):
    pdf_path = _resolve_file(req.file_id)
    return StreamingResponse(
        _compliance_stream(orchestrator.compliance_check_stream(pdf_path, req.checklist, req.use_cache)),
        media_type="text/event-stream",
    )


async def _compliance_stream(gen):
    """将合规核查的 async generator 包装为 SSE 格式。"""
    resp_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())
    stream_log: list[str] = []

    # 首帧
    first = ChatCompletionStreamResponse(
        id=resp_id, created=created,
        choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
    )
    yield f"data: {first.model_dump_json()}\n\n"

    try:
        async for chunk_text in gen:
            stream_log.append(chunk_text)
            chunk = ChatCompletionStreamResponse(
                id=resp_id, created=created,
                choices=[StreamChoice(delta=DeltaMessage(content=chunk_text))],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"
    except Exception as e:
        logger.exception("合规核查流式失败")
        err = ChatCompletionStreamResponse(
            id=resp_id, created=created,
            choices=[StreamChoice(delta=DeltaMessage(content=f"\n\n❌ 分析出错: {e}"))],
        )
        yield f"data: {err.model_dump_json()}\n\n"

    # 终止帧
    done = ChatCompletionStreamResponse(
        id=resp_id, created=created,
        choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
    )
    yield f"data: {done.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


# ── /health ──────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}


# ── 静态文件（必须放在所有 API 路由之后）────────────────────────────────
app.mount("/", StaticFiles(directory="web", html=True), name="web")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=CFG["server"]["host"],
        port=CFG["server"]["port"],
        reload=False,
    )
