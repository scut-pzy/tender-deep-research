"""Critic VLM 智能体：通过页面图片视觉核验 Policy 提取结果。"""
import json
import re
from collections import defaultdict
from typing import AsyncGenerator

from core.llm_client import VLMClient
from models.schemas import CriticFeedback, ExtractionItem
from prompts.critic_vision import CRITIC_SYSTEM_PROMPT, build_batch_critic_prompt
from utils.logger import get_logger

logger = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]+?)```", re.IGNORECASE)


class CriticAgent:
    def __init__(self, vlm: VLMClient):
        self.vlm = vlm

    def _parse_feedback(self, raw: str, items: list[dict]) -> list[CriticFeedback]:
        text = raw.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = _JSON_BLOCK_RE.search(text)
            if m:
                try:
                    data = json.loads(m.group(1))
                except json.JSONDecodeError:
                    data = None
            else:
                data = None

        if data is None:
            logger.warning("Critic JSON 解析失败")
            return [CriticFeedback(key=it["key"], verified=False, comment="解析失败") for it in items]

        raw_list = data if isinstance(data, list) else [data]
        feedbacks = []
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            # 兼容 VLM 可能返回中文字段名的情况
            key = entry.get("key") or entry.get("要素") or entry.get("要素名称", "")
            verified_raw = entry.get("verified") if "verified" in entry else entry.get("是否准确")
            verified = bool(verified_raw) if verified_raw is not None else False
            actual_value = entry.get("actual_value") or entry.get("实际值")
            comment = entry.get("comment") or entry.get("说明") or entry.get("备注", "")
            feedbacks.append(CriticFeedback(
                key=key,
                verified=verified,
                actual_value=actual_value,
                comment=comment,
            ))
        return feedbacks

    async def verify(
        self, items: list[ExtractionItem], pages_data: list[dict]
    ) -> list[CriticFeedback]:
        """
        对所有提取结果进行视觉核验。
        - 按 source_page 分组，同一页一次 VLM 调用
        - 无 source_page 的要素直接标记为"无法核验"
        """
        # 建立 page_num → base64 映射
        page_map: dict[int, str] = {p["page_num"]: p["image_base64"] for p in pages_data}

        # 按页码分组（无页码的单独处理）
        grouped: dict[int, list[dict]] = defaultdict(list)
        unverifiable: list[CriticFeedback] = []

        for item in items:
            if item.source_page is None or item.value is None:
                unverifiable.append(CriticFeedback(
                    key=item.key,
                    verified=False,
                    comment="无来源页码或值为空，无法核验",
                ))
            else:
                grouped[item.source_page].append({
                    "key": item.key,
                    "value": item.value,
                    "source_text": item.source_text or "",
                })

        all_feedbacks: list[CriticFeedback] = list(unverifiable)

        for page_num, page_items in grouped.items():
            if page_num not in page_map:
                for it in page_items:
                    all_feedbacks.append(CriticFeedback(
                        key=it["key"], verified=False, comment=f"第{page_num}页图片不存在"
                    ))
                continue

            prompt = build_batch_critic_prompt(page_num, page_items)
            try:
                raw = await self.vlm.chat_with_image(
                    text_prompt=prompt,
                    image_base64_list=[page_map[page_num]],
                )
                feedbacks = self._parse_feedback(raw, page_items)
                all_feedbacks.extend(feedbacks)
                for fb in feedbacks:
                    mark = "✅" if fb.verified else "❌"
                    logger.info(
                        "%s 「%s」 %s | %s",
                        mark, fb.key,
                        f"实际值: {fb.actual_value}" if fb.actual_value else "",
                        fb.comment or "",
                    )
            except Exception as e:
                logger.warning("第 %d 页 VLM 调用失败: %s", page_num, e)
                for it in page_items:
                    all_feedbacks.append(CriticFeedback(
                        key=it["key"], verified=False, comment=f"VLM调用失败: {e}"
                    ))

        return all_feedbacks

    async def verify_single(
        self,
        item: ExtractionItem,
        pages_data: list[dict],
        pages_to_check: list[int],
        rag_context: str = "",
    ) -> CriticFeedback:
        """
        对单个 ExtractionItem 在指定页码列表上逐页核验。
        找到第一个 verified=True 即返回；全部失败返回最后一个反馈。
        """
        if item.source_page is None or item.value is None:
            return CriticFeedback(
                key=item.key,
                verified=False,
                comment="无来源页码或值为空，无法核验",
            )

        page_map: dict[int, str] = {p["page_num"]: p["image_base64"] for p in pages_data}
        item_dict = {
            "key": item.key,
            "value": item.value,
            "source_text": item.source_text or "",
        }

        last_fb = CriticFeedback(key=item.key, verified=False, comment="未能核验")
        for page_num in pages_to_check:
            if page_num not in page_map:
                last_fb = CriticFeedback(
                    key=item.key, verified=False, comment=f"第{page_num}页图片不存在"
                )
                continue

            prompt = build_batch_critic_prompt(page_num, [item_dict], rag_context=rag_context)
            try:
                raw = await self.vlm.chat_with_image(
                    text_prompt=prompt,
                    image_base64_list=[page_map[page_num]],
                )
                fbs = self._parse_feedback(raw, [item_dict])
                fb = fbs[0] if fbs else CriticFeedback(key=item.key, verified=False, comment="解析失败")
                mark = "✅" if fb.verified else "❌"
                logger.info(
                    "%s 「%s」(第%d页) %s | %s",
                    mark, fb.key, page_num,
                    f"实际值: {fb.actual_value}" if fb.actual_value else "",
                    fb.comment or "",
                )
                if fb.verified:
                    return fb
                last_fb = fb
            except Exception as e:
                logger.warning("第 %d 页 VLM 调用失败: %s", page_num, e)
                last_fb = CriticFeedback(key=item.key, verified=False, comment=f"VLM调用失败: {e}")

        return last_fb

    async def verify_stream(
        self, items: list[ExtractionItem], pages_data: list[dict]
    ) -> AsyncGenerator[list[CriticFeedback], None]:
        """
        流式核验：每完成一页（或一批无法核验的要素）立即 yield。
        每次 yield 一个 list[CriticFeedback]，调用方可逐个处理。
        """
        page_map: dict[int, str] = {p["page_num"]: p["image_base64"] for p in pages_data}

        grouped: dict[int, list[dict]] = defaultdict(list)
        unverifiable: list[CriticFeedback] = []

        for item in items:
            if item.source_page is None or item.value is None:
                unverifiable.append(CriticFeedback(
                    key=item.key,
                    verified=False,
                    comment="无来源页码或值为空，无法核验",
                ))
            else:
                grouped[item.source_page].append({
                    "key": item.key,
                    "value": item.value,
                    "source_text": item.source_text or "",
                })

        # Yield unverifiable items immediately
        if unverifiable:
            yield unverifiable

        for page_num, page_items in grouped.items():
            if page_num not in page_map:
                fbs = [CriticFeedback(
                    key=it["key"], verified=False, comment=f"第{page_num}页图片不存在"
                ) for it in page_items]
                yield fbs
                continue

            prompt = build_batch_critic_prompt(page_num, page_items)
            try:
                raw = await self.vlm.chat_with_image(
                    text_prompt=prompt,
                    image_base64_list=[page_map[page_num]],
                )
                feedbacks = self._parse_feedback(raw, page_items)
                for fb in feedbacks:
                    mark = "✅" if fb.verified else "❌"
                    logger.info(
                        "%s 「%s」 %s | %s",
                        mark, fb.key,
                        f"实际值: {fb.actual_value}" if fb.actual_value else "",
                        fb.comment or "",
                    )
                yield feedbacks
            except Exception as e:
                logger.warning("第 %d 页 VLM 调用失败: %s", page_num, e)
                fbs = [CriticFeedback(
                    key=it["key"], verified=False, comment=f"VLM调用失败: {e}"
                ) for it in page_items]
                yield fbs
