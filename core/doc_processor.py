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


def chunk_text_parent_child(
    pages: list[dict],
    parent_chunk_size: int = 1024,
    parent_chunk_overlap: int = 128,
    child_chunk_size: int = 256,
    child_chunk_overlap: int = 64,
) -> tuple[list[dict], list[dict]]:
    """
    父子分块检索策略（Dify 风格）。

    先按 parent_chunk_size 切出父块，再在每个父块内按 child_chunk_size 切出子块。
    检索时用子块做向量匹配（精确定位），召回时返回对应的父块（保留上下文）。

    返回:
        (parent_chunks, child_chunks)
        - parent_chunks: [{"text": ..., "page_num": ..., "parent_id": int}, ...]
        - child_chunks:  [{"text": ..., "page_num": ..., "child_id": int, "parent_id": int}, ...]
    """
    parent_chunks: list[dict] = []
    child_chunks: list[dict] = []
    parent_id = 0
    child_id = 0

    for page in pages:
        text = page["text"]
        page_num = page["page_num"]
        if not text:
            continue

        # 切父块
        start = 0
        while start < len(text):
            end = start + parent_chunk_size
            parent_text = text[start:end]
            if not parent_text.strip():
                if end >= len(text):
                    break
                start = end - parent_chunk_overlap
                continue

            parent_chunks.append({
                "text": parent_text,
                "page_num": page_num,
                "parent_id": parent_id,
            })

            # 在父块内切子块
            c_start = 0
            while c_start < len(parent_text):
                c_end = c_start + child_chunk_size
                child_text = parent_text[c_start:c_end]
                if child_text.strip():
                    child_chunks.append({
                        "text": child_text,
                        "page_num": page_num,
                        "child_id": child_id,
                        "parent_id": parent_id,
                    })
                    child_id += 1
                if c_end >= len(parent_text):
                    break
                c_start = c_end - child_chunk_overlap

            parent_id += 1
            if end >= len(text):
                break
            start = end - parent_chunk_overlap

    logger.info(
        "父子分块完成：%d 个父块，%d 个子块", len(parent_chunks), len(child_chunks)
    )
    return parent_chunks, child_chunks
