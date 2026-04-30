"""招标文件深度分析服务 — OpenAI 兼容 API 入口。"""
import asyncio
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
    ComplianceReevalRequest,
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
    """上传 PDF 或 Word 文件。以内容 MD5 作为 file_id，重复上传同一文件会返回相同 id。
    file_type: "tender"（招标书）或 "bid"（投标书），用于前端区分文件用途。
    """
    allowed_exts = (".pdf", ".doc", ".docx")
    if not file.filename.lower().endswith(allowed_exts):
        raise HTTPException(status_code=400, detail="仅支持 PDF / Word（.doc/.docx）文件")

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
    """核心分析接口（OpenAI 兼容）。
    - mode="extract"（默认）：解析 messages 中的要素清单和文件引用，执行 RAG→Policy→Critic 提取流程。
    - mode="chat"：对已上传文件做自由问答，需提供 file_id 和可选的 context_data。
    支持流式（stream=True）和非流式响应。
    """
    # ── Chat 模式：自由对话 ──
    if req.mode == "chat":
        return await _handle_chat(req)

    # ── Extract 模式：字段提取（原有逻辑）──
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


def _save_compliance_history(
    history_id: str,
    pdf_path: str,
    checklist_keys: list[str],
    stream_log: list[str],
):
    """保存合规核查历史记录（含 stream_log 和 JSON 结果）。"""
    import re as _re
    history_dir = Path(CFG["files"]["history_dir"])
    full_text = "".join(stream_log)
    # 从最后一个 json code block 提取合规报告
    m = _re.search(r"```json\s*\n([\s\S]*?)\n```", full_text)
    result_data = {}
    if m:
        try:
            result_data = json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    entry = {
        "id": history_id,
        "type": "compliance",
        "timestamp": time.time(),
        "filename": Path(pdf_path).name,
        "pdf_path": pdf_path,
        "keys": checklist_keys,
        "result": result_data,
        "stream_log": stream_log,
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
    except asyncio.CancelledError:
        logger.info("客户端断开，流式分析已取消")
        return
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


# ── Chat 模式处理 ─────────────────────────────────────────────────────────

async def _handle_chat(req: ChatCompletionRequest):
    """处理自由对话请求。"""
    if not req.file_id:
        raise HTTPException(status_code=400, detail="对话模式需要提供 file_id")

    pdf_path = _resolve_file(req.file_id)

    # 取最后一条 user message 作为问题
    question = ""
    for msg in reversed(req.messages):
        if msg.role == "user":
            question = msg.content if isinstance(msg.content, str) else str(msg.content)
            break
    if not question:
        raise HTTPException(status_code=400, detail="未找到用户问题")

    if req.stream:
        return StreamingResponse(
            _chat_stream_response(question, pdf_path, req.context_data, req.use_cache),
            media_type="text/event-stream",
        )

    # 非流式：收集全部文本
    chunks = []
    async for text in orchestrator.chat_stream(question, pdf_path, req.context_data, req.use_cache):
        chunks.append(text)
    content = "".join(chunks)
    return ChatCompletionResponse(
        choices=[Choice(message=ChoiceMessage(content=content))],
        usage=Usage(),
    )


async def _chat_stream_response(
    question: str, pdf_path: str, context_data: dict | None, use_cache: bool
):
    """将自由对话的 async generator 包装为 SSE 格式。"""
    resp_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())

    # 首帧：role
    first = ChatCompletionStreamResponse(
        id=resp_id, created=created,
        choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
    )
    yield f"data: {first.model_dump_json()}\n\n"

    try:
        async for text in orchestrator.chat_stream(question, pdf_path, context_data, use_cache):
            chunk = ChatCompletionStreamResponse(
                id=resp_id, created=created,
                choices=[StreamChoice(delta=DeltaMessage(content=text))],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"
    except asyncio.CancelledError:
        logger.info("客户端断开，对话已取消")
        return
    except Exception as e:
        logger.exception("对话流式失败")
        err = ChatCompletionStreamResponse(
            id=resp_id, created=created,
            choices=[StreamChoice(delta=DeltaMessage(content=f"\n\n❌ 出错: {e}"))],
        )
        yield f"data: {err.model_dump_json()}\n\n"

    # 终止帧
    done = ChatCompletionStreamResponse(
        id=resp_id, created=created,
        choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
    )
    yield f"data: {done.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


# ── /v1/files/list（列出已上传文件及缓存状态）─────────────────────────────
@app.get("/v1/files/list")
async def list_files(type: str | None = None):
    """列出已上传的 PDF 文件，并附带向量缓存状态（has_vectors）。
    type: 可选过滤，"tender" 或 "bid"。
    """
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
    """删除已上传文件及其元数据。向量缓存（cache/vectors/）不随之删除，需手动清理。"""
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
    """列出所有历史提取/合规核查记录的摘要（不含完整 stream_log）。"""
    history_dir = Path(CFG["files"]["history_dir"])
    items = []
    for p in sorted(history_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result = data.get("result", {})
            rec_type = data.get("type", "extract")
            if rec_type == "compliance":
                count = len(result.get("items", [])) if isinstance(result, dict) else 0
            else:
                count = len(result) if isinstance(result, dict) else 0
            items.append({
                "id": data["id"],
                "type": rec_type,
                "timestamp": data["timestamp"],
                "filename": data.get("filename", ""),
                "fields": data.get("keys", []),
                "field_count": count,
            })
        except Exception:
            continue
    return {"history": items}


@app.get("/v1/history/{history_id}")
async def get_history(history_id: str):
    """获取单条历史记录的完整内容（含 stream_log 和 result JSON）。"""
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
    """从招标书提取硬性要求，生成审查清单（Checklist）。流式返回进度和 JSON 结果。"""
    pdf_path = _resolve_file(req.file_id)
    return StreamingResponse(
        _compliance_stream(orchestrator.generate_checklist_stream(pdf_path, req.use_cache)),
        media_type="text/event-stream",
    )


@app.post("/v1/compliance/check")
async def compliance_check(req: ComplianceCheckRequest):
    """对投标书逐字段做合规核查。依据 checklist 中的招标要求，提取投标书中对应响应并判定是否合规。流式返回进度和最终合规报告，并自动保存历史记录。"""
    pdf_path = _resolve_file(req.file_id)
    return StreamingResponse(
        _compliance_stream(
            orchestrator.compliance_check_stream(pdf_path, req.checklist, req.use_cache),
            save_history=True,
            pdf_path=pdf_path,
            checklist_keys=[c.key for c in req.checklist],
        ),
        media_type="text/event-stream",
    )


@app.post("/v1/compliance/reevaluate")
async def compliance_reevaluate(req: ComplianceReevalRequest):
    """基于用户补充信息重新核查单个字段。"""
    pdf_path = _resolve_file(req.file_id)
    return StreamingResponse(
        _compliance_stream(orchestrator.reevaluate_compliance_field_stream(
            pdf_path=pdf_path,
            field_key=req.field_key,
            requirement=req.requirement,
            current_response=req.current_response,
            current_verdict=req.current_verdict,
            current_reason=req.current_reason,
            additional_context=req.additional_context,
            use_cache=req.use_cache,
        )),
        media_type="text/event-stream",
    )


async def _compliance_stream(
    gen,
    save_history: bool = False,
    pdf_path: str | None = None,
    checklist_keys: list[str] | None = None,
):
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
    except asyncio.CancelledError:
        logger.info("客户端断开，合规核查已取消")
        return
    except Exception as e:
        logger.exception("合规核查流式失败")
        err = ChatCompletionStreamResponse(
            id=resp_id, created=created,
            choices=[StreamChoice(delta=DeltaMessage(content=f"\n\n❌ 分析出错: {e}"))],
        )
        yield f"data: {err.model_dump_json()}\n\n"

    # 保存合规核查历史
    if save_history and pdf_path and stream_log:
        try:
            history_id = uuid.uuid4().hex[:12]
            _save_compliance_history(history_id, pdf_path, checklist_keys or [], stream_log)
        except Exception:
            logger.exception("保存合规核查历史失败")

    # 终止帧
    done = ChatCompletionStreamResponse(
        id=resp_id, created=created,
        choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
    )
    yield f"data: {done.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


# ── /v1/config（设置面板读写）────────────────────────────────────────────────

def _mask_key(key: str) -> str:
    """将 API Key 脱敏，仅保留前6位和后4位。"""
    if not key or len(key) <= 12:
        return "***"
    return key[:6] + "***" + key[-4:]


@app.get("/v1/config")
async def get_config():
    """返回当前运行配置（API Key 脱敏后返回，供设置面板展示）。"""
    import copy
    cfg = copy.deepcopy(CFG)
    for section in ("policy_llm", "critic_vlm", "embedding"):
        if section in cfg and "api_key" in cfg[section]:
            cfg[section]["api_key"] = _mask_key(cfg[section]["api_key"])
    return cfg


@app.patch("/v1/config")
async def patch_config(updates: dict):
    """热更新运行配置并重载 AI 客户端。

    支持的顶层 key：policy_llm / critic_vlm / embedding / rag / pipeline / server
    API Key 若传入掩码值（含 '***'）则忽略，不覆盖原值。
    """
    allowed_sections = {"policy_llm", "critic_vlm", "embedding", "rag", "pipeline", "server"}
    for section, values in updates.items():
        if section not in allowed_sections:
            continue
        if not isinstance(values, dict):
            continue
        if section not in CFG:
            CFG[section] = {}
        for k, v in values.items():
            # 跳过掩码值，避免覆盖真实 Key
            if k == "api_key" and isinstance(v, str) and "***" in v:
                continue
            CFG[section][k] = v

    orchestrator.reload_clients()

    # 将新 API Key 写回 .env（只写非掩码的 Key，且仅当值实际变化时）
    try:
        env_path = Path(__file__).parent / ".env"
        _persist_env_keys(updates, env_path)
    except Exception:
        logger.warning("写入 .env 失败，配置已在内存中生效但重启后需重新设置")

    return {"status": "ok"}


def _persist_env_keys(updates: dict, env_path: Path) -> None:
    """将 updates 中的 api_key 写入 .env，使重启后仍然有效。"""
    # 收集所有不含掩码的新 Key
    new_keys: dict[str, str] = {}
    for section in ("policy_llm", "critic_vlm", "embedding"):
        key_val = updates.get(section, {}).get("api_key", "")
        if key_val and "***" not in key_val:
            new_keys["DASHSCOPE_API_KEY"] = key_val  # 目前三者共用同一个环境变量

    if not new_keys:
        return

    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    existing = {line.split("=", 1)[0]: i for i, line in enumerate(lines) if "=" in line and not line.startswith("#")}

    for var, val in new_keys.items():
        if var in existing:
            lines[existing[var]] = f"{var}={val}"
        else:
            lines.append(f"{var}={val}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(".env 已更新: %s", list(new_keys.keys()))


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
