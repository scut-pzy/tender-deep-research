"""PDF 文档处理：文本提取 + 页面渲染。"""
import json
from pathlib import Path

import fitz  # PyMuPDF

from utils.logger import get_logger

logger = get_logger(__name__)

DPI = 150  # 页面渲染分辨率


def process_pdf(pdf_path: str, pages_dir: str, file_id: str) -> tuple[list[dict], int]:
    """
    解析 PDF，返回 (chunks, total_pages)。
    每个 chunk: {chunk_id, text, page, image_path}
    同时将每页渲染为 PNG 保存到 pages_dir/{file_id}/page_{n}.png。
    """
    out_dir = Path(pages_dir) / file_id
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    chunks = []

    for page_num, page in enumerate(doc):
        # 渲染页面图片
        mat = fitz.Matrix(DPI / 72, DPI / 72)
        pix = page.get_pixmap(matrix=mat)
        img_path = str(out_dir / f"page_{page_num + 1}.png")
        pix.save(img_path)

        # 提取文本块
        blocks = page.get_text("blocks")
        for i, block in enumerate(blocks):
            text = block[4].strip()
            if not text:
                continue
            chunks.append({
                "chunk_id": f"{file_id}_p{page_num + 1}_b{i}",
                "text": text,
                "page": page_num + 1,
                "image_path": img_path,
            })

    doc.close()
    logger.info("PDF 处理完成: %d 页, %d 文本块", total_pages, len(chunks))
    return chunks, total_pages
