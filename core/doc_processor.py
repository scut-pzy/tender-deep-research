"""PDF 文档预处理：文本提取 + 页面渲染（300 DPI）。"""
import base64
from pathlib import Path

import fitz  # PyMuPDF

from utils.logger import get_logger

logger = get_logger(__name__)

DPI = 300
MATRIX = fitz.Matrix(DPI / 72, DPI / 72)


def process_pdf(pdf_path: str, pages_dir: str) -> dict:
    """
    解析 PDF，返回:
    {
        "total_pages": int,
        "pages": [
            {
                "page_num": 1,
                "text": "...",          # 纯文本，供 RAG 使用
                "image_base64": "...",  # PNG base64，供 Critic VLM 使用
            },
            ...
        ]
    }
    页面图片缓存到 pages_dir/<PDF_MD5>/page_N.png，同一 PDF 不重复渲染。
    """
    import hashlib
    pdf_md5 = hashlib.md5(Path(pdf_path).read_bytes()).hexdigest()[:12]
    img_dir = Path(pages_dir) / pdf_md5
    img_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    pages = []

    for page_num, page in enumerate(doc, start=1):
        # ── 文本提取 ──
        text = page.get_text("text").strip()

        # ── 图片渲染（缓存） ──
        img_path = img_dir / f"page_{page_num}.png"
        if not img_path.exists():
            pix = page.get_pixmap(matrix=MATRIX)
            pix.save(str(img_path))

        image_base64 = base64.b64encode(img_path.read_bytes()).decode()

        pages.append({
            "page_num": page_num,
            "text": text,
            "image_base64": image_base64,
        })

    doc.close()
    logger.info("PDF 处理完成: %s，共 %d 页", Path(pdf_path).name, total_pages)
    return {"total_pages": total_pages, "pages": pages}


def chunk_text_by_pages(
    pages: list[dict], chunk_size: int = 512, chunk_overlap: int = 64
) -> list[dict]:
    """
    将每页文本滑动切分为文本块。
    返回: [{"text": "...", "page_num": 3, "chunk_id": 0}, ...]
    """
    chunks = []
    chunk_id = 0
    for page in pages:
        text = page["text"]
        page_num = page["page_num"]
        if not text:
            continue
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk_text = text[start:end]
            if chunk_text.strip():
                chunks.append({
                    "text": chunk_text,
                    "page_num": page_num,
                    "chunk_id": chunk_id,
                })
                chunk_id += 1
            if end >= len(text):
                break
            start = end - chunk_overlap

    logger.info("文本分块完成：%d 个块", len(chunks))
    return chunks
