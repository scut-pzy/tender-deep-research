"""招标文件深度分析服务入口。"""
import json
from pathlib import Path

import yaml
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from core.orchestrator import Orchestrator
from models.schemas import UploadResponse
from utils.file_handler import save_upload
from utils.logger import get_logger

logger = get_logger(__name__)

# ── 配置加载 ────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent / "config.yaml"
with open(CFG_PATH, encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

UPLOAD_DIR = Path(CFG["server"]["cache_dir"]) / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

orchestrator = Orchestrator(CFG)

# ── FastAPI ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Tender Deep Research", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/upload", response_model=UploadResponse)
async def upload_tender(file: UploadFile = File(...)):
    """上传招标 PDF 文件，返回 file_id 供后续分析使用。"""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    content = await file.read()
    file_id, saved_path = await save_upload(content, file.filename, str(UPLOAD_DIR))

    # 快速预解析获取页数
    import fitz
    doc = fitz.open(saved_path)
    total_pages = len(doc)
    doc.close()

    logger.info("文件上传成功: %s (%d 页)", file.filename, total_pages)
    return UploadResponse(
        file_id=file_id,
        filename=file.filename,
        total_pages=total_pages,
        message="上传成功，可调用 /analyze/{file_id} 开始分析",
    )


@app.get("/analyze/{file_id}")
async def analyze_tender(file_id: str):
    """SSE 流式返回招标文件分析进度和结果。"""
    # 查找已上传的 PDF
    matches = list(UPLOAD_DIR.glob(f"{file_id}_*.pdf"))
    if not matches:
        raise HTTPException(status_code=404, detail="文件不存在，请先上传")
    pdf_path = str(matches[0])

    async def event_generator():
        async for event in orchestrator.run(file_id, pdf_path):
            yield {"data": json.dumps(event, ensure_ascii=False)}

    return EventSourceResponse(event_generator())


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
