"""Critic VLM 视觉核验提示词。"""

_CRITIC_SYSTEM = """\
你是一名招标文件审核专家。你会收到一张 PDF 页面的截图，以及需要核验的要素信息。
请仔细看图，核实每个要素的提取值是否与页面内容**语义一致**。

## 核验标准
- 提取值可以是摘要或总结，不要求逐字匹配原文
- 只要关键信息（数字、金额、日期、百分比、比例等）准确，即视为通过
- **如果提取值本身不完整（如应有数字却缺失、日期/金额/百分比为空），必须判定不通过**，无论其他证据如何
- 除了页面截图，你还可能收到 RAG 检索文本作为辅助证据。使用 RAG 证据时须同时满足：
  1. RAG 文本必须**明确提及同一要素**（而非碰巧包含相同数字）
  2. RAG 文本中的值与页面内容不矛盾
- 仅当提取值完整且正确，但当前页信息不完整时，才可用 RAG 文本辅助确认
- 仅当提取值与页面内容存在事实性错误（如数字错误、含义相反）时才判定不通过

## 输出格式（JSON数组）
[
  {
    "key": "要素名称",
    "verified": true 或 false,
    "actual_value": "页面中对应信息的原文（无论通过与否都必须填写）",
    "comment": "核验依据：在页面什么位置看到了什么内容（中文，30字以内，通过与否都必须填写）"
  }
]
注意：若页面中确实存在该信息且提取的关键信息正确，verified=true；仅当有事实性错误时 verified=false。actual_value 和 comment 无论通过与否都必须填写。
"""


def build_batch_critic_prompt(page_num: int, items: list[dict], rag_context: str = "") -> str:
    """
    批量核验同一页上的多个要素。
    items: [{"key": ..., "value": ..., "source_text": ...}, ...]
    rag_context: RAG 检索上下文（可包含其他页面的文本片段）
    """
    lines = [f"这是招标文件第 {page_num} 页的截图。\n\n## 待核验信息："]
    for i, item in enumerate(items, 1):
        lines.append(
            f"{i}. 要素：「{item['key']}」\n"
            f"   提取值：{item['value']}\n"
            f"   来源片段：{item.get('source_text', '（无）')}"
        )
    if rag_context:
        lines.append(f"\n## RAG 检索上下文（来自多页的相关文本）：\n{rag_context}")
    lines.append(
        "\n请仔细查看图片，并结合 RAG 检索上下文（如有），核验以上每条信息的关键内容是否有据可依。"
        "提取值可以是摘要/总结，不要求逐字匹配，但数字、金额、日期、百分比必须准确。"
        "如果当前页面信息不完整，但 RAG 上下文中有明确支撑且与页面不矛盾，视为通过。"
        '\n\n必须严格按以下 JSON 格式输出，字段名不能更改：\n'
        '[{"key": "要素名称", "verified": true或false, "actual_value": "页面中看到的实际值（通过也要填）", "comment": "核验依据，说明在页面哪个区域确认"}]'
        "\n顺序与上方列表一致，直接输出 JSON，不要加其他文字。"
    )
    return "\n".join(lines)


def build_critic_prompt(page_num: int, key: str, value: str, source_text: str = "") -> str:
    """单个要素核验（单条调用版本）。"""
    return (
        f"这是招标文件第 {page_num} 页的截图。\n\n"
        f"待核验：要素「{key}」，提取值为「{value}」。\n"
        f"来源片段：{source_text or '（无）'}\n\n"
        "请仔细看图，确认提取值是否与页面内容完全一致。\n"
        "输出格式：只输出单个 JSON 对象（非数组）。"
    )


CRITIC_SYSTEM_PROMPT = _CRITIC_SYSTEM
