"""招标要素提取（Policy LLM）。"""
import re

from core.llm_client import LLMClient
from core.rag import RAGIndex
from models.schemas import ElementExtraction
from prompts.extract import TENDER_ELEMENTS, build_extract_prompt
from prompts.rewrite import build_rewrite_prompt
from utils.logger import get_logger

logger = get_logger(__name__)

_VALUE_RE = re.compile(r"要素值[：:]\s*(.+)", re.MULTILINE)
_CONF_RE = re.compile(r"置信度[：:]\s*([0-9.]+)", re.MULTILINE)


def _parse_response(text: str) -> tuple[str, float]:
    value_m = _VALUE_RE.search(text)
    conf_m = _CONF_RE.search(text)
    value = value_m.group(1).strip() if value_m else text.strip()
    confidence = float(conf_m.group(1)) if conf_m else 0.5
    return value, min(max(confidence, 0.0), 1.0)


async def extract_elements(
    llm: LLMClient, rag: RAGIndex
) -> list[ElementExtraction]:
    results = []
    for element in TENDER_ELEMENTS:
        chunks = await rag.search(element)
        messages = build_extract_prompt(element, chunks)
        try:
            raw = await llm.chat(messages)
            value, confidence = _parse_response(raw)
        except Exception as e:
            logger.warning("提取 [%s] 失败: %s", element, e)
            value, confidence = "未能提取", 0.0
        results.append(ElementExtraction(
            element=element,
            value=value,
            confidence=confidence,
            source_chunks=[c["chunk_id"] for c in chunks],
        ))
        logger.info("已提取 [%s]: conf=%.2f", element, confidence)
    return results


async def rewrite_elements(
    llm: LLMClient,
    rag: RAGIndex,
    elements: list[ElementExtraction],
    critic_feedback: list,
) -> list[ElementExtraction]:
    if not critic_feedback:
        return elements

    updated = []
    for elem in elements:
        relevant_fb = [
            f for f in critic_feedback if f.severity in ("medium", "high")
        ]
        if not relevant_fb:
            updated.append(elem)
            continue
        chunks = await rag.search(elem.element)
        messages = build_rewrite_prompt(
            elem.element,
            elem.value,
            [{"issue": f.issue, "suggestion": f.suggestion, "severity": f.severity} for f in relevant_fb],
            chunks,
        )
        try:
            raw = await llm.chat(messages)
            value, confidence = _parse_response(raw)
            updated.append(ElementExtraction(
                element=elem.element,
                value=value,
                confidence=confidence,
                source_chunks=elem.source_chunks,
            ))
        except Exception as e:
            logger.warning("重写 [%s] 失败: %s", elem.element, e)
            updated.append(elem)
    return updated
