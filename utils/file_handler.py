import uuid
import shutil
from pathlib import Path

import aiofiles


async def save_upload(file_bytes: bytes, filename: str, upload_dir: str) -> str:
    file_id = str(uuid.uuid4())
    dest = Path(upload_dir) / f"{file_id}_{filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(file_bytes)
    return file_id, str(dest)


def cleanup_file_cache(file_id: str, cache_dir: str) -> None:
    cache = Path(cache_dir)
    for sub in ("uploads", "pages", "vectors"):
        for p in (cache / sub).glob(f"{file_id}*"):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)
