"""文件上传与下载工具，支持 MD5 缓存去重。"""
import hashlib
from pathlib import Path

import aiofiles
import httpx

from utils.logger import get_logger

logger = get_logger(__name__)


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


async def save_upload_file(data: bytes, filename: str, upload_dir: str) -> tuple[str, str]:
    """
    保存上传文件，以内容 MD5 作为文件 ID（去重）。
    返回 (file_id, saved_path)。
    """
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

    return file_id, str(dest)
