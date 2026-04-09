"""文件上传与下载工具，支持 MD5 缓存去重，自动将 Word 转 PDF。"""
import hashlib
import json
import io
from pathlib import Path

import aiofiles
import httpx

from utils.logger import get_logger

logger = get_logger(__name__)


def convert_word_to_pdf(data: bytes, filename: str) -> bytes:
    """
    将 Word（.doc/.docx）字节转换为 PDF 字节。
    使用 mammoth（docx→HTML）+ weasyprint（HTML→PDF）。
    """
    import mammoth
    import weasyprint

    suffix = Path(filename).suffix.lower()
    if suffix not in (".doc", ".docx"):
        raise ValueError(f"不支持的格式: {suffix}")

    # mammoth 只支持 .docx；.doc 也尝试，实际效果取决于内容
    result = mammoth.convert_to_html(io.BytesIO(data))
    if result.messages:
        for msg in result.messages:
            logger.debug("mammoth 转换警告: %s", msg)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<style>
  body {{ font-family: "PingFang SC","Microsoft YaHei",SimSun,sans-serif;
         font-size: 12pt; line-height: 1.8; margin: 2cm; color: #111; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
  td, th {{ border: 1px solid #aaa; padding: 4px 8px; }}
  h1,h2,h3,h4 {{ margin: 12px 0 6px; }}
  p {{ margin: 4px 0; }}
  img {{ max-width: 100%; }}
</style>
</head><body>{result.value}</body></html>"""

    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    logger.info("Word 转 PDF 完成: %s → %d bytes", filename, len(pdf_bytes))
    return pdf_bytes


def _md5_of_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _md5_of_url(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


async def download_file(url: str, upload_dir: str) -> str:
    """
    从 URL 下载文件，以 URL 的 MD5 作为文件名缓存到 upload_dir。
    若已缓存则直接返回路径，不重复下载。
    """
    cache_key = _md5_of_url(url)
    out_dir = Path(upload_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 找已缓存文件（扩展名不定，匹配前缀）
    existing = list(out_dir.glob(f"{cache_key}.*"))
    if existing:
        logger.info("缓存命中: %s", existing[0])
        return str(existing[0])

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    # 尝试从 Content-Disposition 或 URL 推断扩展名
    content_type = resp.headers.get("content-type", "")
    if "pdf" in content_type or url.lower().endswith(".pdf"):
        ext = ".pdf"
    else:
        ext = Path(url.split("?")[0]).suffix or ".bin"

    dest = out_dir / f"{cache_key}{ext}"
    async with aiofiles.open(dest, "wb") as f:
        await f.write(resp.content)

    logger.info("下载完成: %s → %s", url, dest)
    return str(dest)


async def save_upload_file(data: bytes, filename: str, upload_dir: str, file_type: str | None = None) -> tuple[str, str]:
    """
    保存上传文件，以内容 MD5 作为文件 ID（去重）。
    Word 文件（.doc/.docx）自动转换为 PDF 后保存。
    返回 (file_id, saved_path)。
    """
    suffix = Path(filename).suffix.lower()
    if suffix in (".doc", ".docx"):
        try:
            data = convert_word_to_pdf(data, filename)
            filename = Path(filename).stem + ".pdf"
            logger.info("Word 已转换为 PDF: %s", filename)
        except Exception as e:
            logger.error("Word 转 PDF 失败: %s", e, exc_info=True)
            raise ValueError(f"Word 转 PDF 失败: {e}") from e

    file_id = _md5_of_bytes(data)
    out_dir = Path(upload_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(filename).suffix or ".pdf"
    dest = out_dir / f"{file_id}{ext}"

    if dest.exists():
        logger.info("文件已存在（MD5相同），跳过写入: %s", dest)
    else:
        async with aiofiles.open(dest, "wb") as f:
            await f.write(data)
        logger.info("文件保存成功: %s", dest)

    # 保存原始文件名元数据（含文件类型）
    meta_path = out_dir / f"{file_id}.meta.json"
    meta = {"filename": filename, "file_type": file_type or "tender"}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False))

    return file_id, str(dest)
