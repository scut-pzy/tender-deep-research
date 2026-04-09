"""Policy LLM 智能体：招标要素提取与迭代重写。"""
import json
import re
from typing import AsyncGenerator, Optional

from core.llm_client import LLMClient
from models.schemas import ExtractionItem
from prompts.extract import build_extract_prompt
from prompts.extract_single import build_extract_single_prompt
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
            # 单对象返回（如单字段提取）：{"key": ..., "value": ...}
            if isinstance(raw_items, dict) and "key" in raw_items:
                raw_items = [raw_items]
            else:
                return [ExtractionItem(key=k) for k in keys]

        # 构建 key→item 映射
        item_map = {}
        for it in raw_items:
            if isinstance(it, dict) and "key" in it:
                # LLM 有时将 value 返回为 dict/list，需转为字符串
                raw_val = it.get("value")
                if isinstance(raw_val, (dict, list)):
                    raw_val = json.dumps(raw_val, ensure_ascii=False)
                item_map[it["key"]] = ExtractionItem(
                    key=it["key"],
                    value=raw_val,
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

    async def extract_single(
        self,
        key: str,
        hits: list[dict],
        prev_item: ExtractionItem | None = None,
        feedback=None,
    ) -> ExtractionItem:
        """单字段提取，可选带上轮结果和 Critic 反馈。"""
        prev_dict = prev_item.model_dump() if prev_item else None
        fb_dict = feedback.model_dump() if feedback else None
        messages = build_extract_single_prompt(key, hits, prev_dict, fb_dict)
        raw = await self.llm.chat(messages, json_mode=True)
        items = self._parse_response(raw, [key])
        item = items[0]
        logger.info(
            "提取「%s」= %s (第%s页, conf=%.2f)",
            item.key, item.value, item.source_page, item.confidence,
        )
        return item

    async def extract_single_stream(
        self,
        key: str,
        hits: list[dict],
        prev_item: ExtractionItem | None = None,
        feedback=None,
    ) -> AsyncGenerator[tuple[str, str | ExtractionItem], None]:
        """
        流式单字段提取。
        yield ("thinking", text) — LLM 思考过程 token
        yield ("result", ExtractionItem) — 最终提取结果
        """
        prev_dict = prev_item.model_dump() if prev_item else None
        fb_dict = feedback.model_dump() if feedback else None
        messages = build_extract_single_prompt(key, hits, prev_dict, fb_dict)

        content_buf: list[str] = []
        async for tag, text in self.llm.chat_stream(messages, json_mode=True):
            if tag == "reasoning":
                yield ("thinking", text)
            elif tag == "content":
                content_buf.append(text)

        raw = "".join(content_buf)
        items = self._parse_response(raw, [key])
        item = items[0]
        logger.info(
            "提取「%s」= %s (第%s页, conf=%.2f)",
            item.key, item.value, item.source_page, item.confidence,
        )
        yield ("result", item)

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
