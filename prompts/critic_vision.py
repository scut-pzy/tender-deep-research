"""Critic VLM 视觉核验提示词。"""

_CRITIC_SYSTEM = """\
你是一名招标文件审核专家。你会收到一张 PDF 页面的截图，以及需要核验的要素信息。
请仔细看图，核实每个要素的提取值是否与页面中的内容完全一致。

## 输出格式（JSON数组）
[
  {
    "key": "要素名称",
    "verified": true 或 false,
    "actual_value": "图片中真实的值（若verified=true则为null）",
    "comment": "简短说明（中文，30字以内）"
  }
]
注意：若页面中确实存在该信息且提取正确，verified=true；否则 verified=false 并给出实际值。
"""


def build_batch_critic_prompt(page_num: int, items: list[dict]) -> str:
    """
    批量核验同一页上的多个要素。
    items: [{"key": ..., "value": ..., "source_text": ...}, ...]
    """
    lines = [f"这是招标文件第 {page_num} 页的截图。\n\n## 待核验信息："]
    for i, item in enumerate(items, 1):
        lines.append(
            f"{i}. 要素：「{item['key']}」\n"
            f"   提取值：{item['value']}\n"
            f"   来源片段：{item.get('source_text', '（无）')}"
        )
    lines.append(
        "\n请仔细查看图片，核验以上每条信息是否准确。"
        "特别注意数字、金额大小写、日期、百分比等容易出错的字段。"
        "\n输出 JSON 数组，顺序与上方列表一致。"
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
