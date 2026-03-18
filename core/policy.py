"""Policy LLM 智能体：招标要素提取与迭代重写。"""
import json
import re
from typing import Optional

from core.llm_client import LLMClient
from models.schemas import ExtractionItem
from prompts.extract import build_extract_prompt
from prompts.rewrite import build_rewrite_prompt
from utils.logger import get_logger

logger = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]+?)```", re.IGNORECASE)


class PolicyAgent:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def _parse_response(self, raw: str, keys: list[str]) -> list[ExtractionItem]:
        """解析 LLM 返回的 JSON，容错处理各种异常格式。"""
        # 尝试直接解析
        text = raw.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试从代码块中提取
            m = _JSON_BLOCK_RE.search(text)
            if m:
                try:
                    data = json.loads(m.group(1))
                except json.JSONDecodeError:
                    data = None
            else:
                data = None

        if data is None:
            logger.warning("JSON 解析失败，返回默认结果")
            return [ExtractionItem(key=k) for k in keys]

        raw_items = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            return [ExtractionItem(key=k) for k in keys]

        # 构建 key→item 映射
        item_map = {}
        for it in raw_items:
            if isinstance(it, dict) and "key" in it:
                item_map[it["key"]] = ExtractionItem(
                    key=it["key"],
                    value=it.get("value"),
                    source_page=it.get("source_page"),
                    source_text=it.get("source_text"),
                    confidence=float(it.get("confidence", 0.5)),
                )

        # 按原始 keys 顺序返回，未解析到的补默认值
        return [item_map.get(k, ExtractionItem(key=k)) for k in keys]

    async def extract(
        self, keys: list[str], rag_results: dict[str, list[dict]]
    ) -> list[ExtractionItem]:
        """首次提取所有招标要素。"""
        messages = build_extract_prompt(keys, rag_results)
        raw = await self.llm.chat(messages, json_mode=True)
        items = self._parse_response(raw, keys)
        for item in items:
            logger.info(
                "提取「%s」= %s (第%s页, conf=%.2f)",
                item.key, item.value, item.source_page, item.confidence,
            )
        return items

    async def rewrite(
        self,
        keys: list[str],
        rag_results: dict[str, list[dict]],
        prev_items: list[ExtractionItem],
        critic_feedbacks: list,
    ) -> list[ExtractionItem]:
        """根据 Critic 反馈重写失败的要素。"""
        prev_dicts = [it.model_dump() for it in prev_items]
        fb_dicts = [fb.model_dump() for fb in critic_feedbacks]
        messages = build_rewrite_prompt(keys, rag_results, prev_dicts, fb_dicts)
        raw = await self.llm.chat(messages, json_mode=True)
        items = self._parse_response(raw, keys)
        for item in items:
            logger.info(
                "重写「%s」= %s (第%s页, conf=%.2f)",
                item.key, item.value, item.source_page, item.confidence,
            )
        return items
