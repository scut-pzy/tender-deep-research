"""
初始化脚本：从 cache/uploads/ 中的 PDF 重新生成 cache/pages/ 页面截图。

git clone 项目后首次运行，或新增 PDF 后补全截图时使用：
    python init_pages.py

依赖：PyMuPDF（requirements.txt 中已包含）
"""
import hashlib
import sys
from pathlib import Path

# 兼容从项目根目录直接运行
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import fitz  # PyMuPDF

UPLOADS_DIR = ROOT / "cache" / "uploads"
PAGES_DIR   = ROOT / "cache" / "pages"
DPI = 300
MATRIX = fitz.Matrix(DPI / 72, DPI / 72)


def render_pdf(pdf_path: Path) -> None:
    """将单个 PDF 渲染为页面截图，存入 cache/pages/<md5>/ 目录。"""
    data = pdf_path.read_bytes()
    # 用文件内容 MD5 的前12位作为目录名（与 doc_processor.process_pdf 保持一致）
    pdf_md5 = hashlib.md5(data).hexdigest()[:12]
    img_dir = PAGES_DIR / pdf_md5
    img_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    total = len(doc)
    rendered = 0

    for page_num, page in enumerate(doc, start=1):
        img_path = img_dir / f"page_{page_num}.png"
        if img_path.exists():
            continue  # 已有缓存，跳过
        pix = page.get_pixmap(matrix=MATRIX)
        pix.save(str(img_path))
        rendered += 1

    doc.close()

    if rendered:
        print(f"  ✅ {pdf_path.name}  →  {pdf_md5}/  ({rendered}/{total} 页已渲染)")
    else:
        print(f"  ⚡ {pdf_path.name}  →  {pdf_md5}/  (全部 {total} 页命中缓存，跳过)")


def main() -> None:
    pdfs = sorted(UPLOADS_DIR.glob("*.pdf"))
    if not pdfs:
        print("cache/uploads/ 中没有找到 PDF 文件，请先上传文档再运行此脚本。")
        return

    print(f"共发现 {len(pdfs)} 个 PDF，开始生成页面截图（{DPI} DPI）...\n")
    for pdf_path in pdfs:
        render_pdf(pdf_path)

    print("\n✅ 全部完成。")


if __name__ == "__main__":
    main()
