"""视觉审核（Critic VLM）：对 PDF 页面图片进行分析。"""
import json
import re
from pathlib import Path

from core.llm_client import VLMClient
from models.schemas import CriticFeedback
from prompts.critic_vision import build_critic_prompt
from utils.logger import get_logger

logger = get_logger(__name__)

_JSON_RE = re.compile(r"\[.*?\]", re.DOTALL)


def _parse_feedback(raw: str, page: int) -> list[CriticFeedback]:
    m = _JSON_RE.search(raw)
    if not m:
        return []
    try:
        items = json.loads(m.group())
        feedbacks = []
        for item in items:
            feedbacks.append(CriticFeedback(
                page=page,
                issue=item.get("issue", ""),
                suggestion=item.get("suggestion", ""),
                severity=item.get("severity", "low"),
            ))
        return feedbacks
    except json.JSONDecodeError:
        logger.warning("第 %d 页 Critic 输出解析失败", page)
        return []


async def critique_pages(
    vlm: VLMClient,
    pages_dir: str,
    file_id: str,
    elements: list,
    sample_pages: int = 5,
) -> list[CriticFeedback]:
    """对文档的代表性页面进行视觉审核。"""
    page_dir = Path(pages_dir) / file_id
    page_images = sorted(page_dir.glob("page_*.png"), key=lambda p: int(p.stem.split("_")[1]))

    # 均匀采样页面，避免全量处理
    if len(page_images) > sample_pages:
        step = len(page_images) // sample_pages
        page_images = page_images[::step][:sample_pages]

    all_feedback = []
    for img_path in page_images:
        page_num = int(img_path.stem.split("_")[1])
        prompt = build_critic_prompt(page_num, [
            {"element": e.element, "value": e.value} for e in elements
        ])
        try:
            raw = await vlm.chat_with_image(prompt, str(img_path))
            feedbacks = _parse_feedback(raw, page_num)
            all_feedback.extend(feedbacks)
            logger.info("第 %d 页审核完成，发现 %d 个问题", page_num, len(feedbacks))
        except Exception as e:
            logger.warning("第 %d 页视觉审核失败: %s", page_num, e)

    return all_feedback
