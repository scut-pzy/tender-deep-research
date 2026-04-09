"""RAG 检索词重构提示词：根据 Critic 反馈生成新检索词。"""

REFINE_SYSTEM_PROMPT = """\
你是一名招标文件分析助手。当某个要素在文档中未能正确检索或核验时，你需要根据反馈生成新的语义检索词。

## 输出规则
- 严格按 JSON 格式输出，不要有任何额外说明
- 输出格式：{"queries": ["检索词1", "检索词2", ...]}
- 生成 1-3 个检索词，适合语义向量搜索
- 检索词应覆盖该要素的不同表述方式（同义词、上下文描述等）
"""


def build_refine_query_prompt(
    key: str,
    prev_query: str,
    feedback: dict,
    rag_hits: list[dict] | None = None,
) -> list[dict]:
    """
    构建检索词重构的 messages。
    - key: 要素名称
    - prev_query: 上轮使用的检索词
    - feedback: Critic 反馈 dict
    - rag_hits: 上轮 RAG 检索的 top-K 结果列表，用于辅助生成更精准的检索词
    """
    actual = feedback.get("actual_value") or "未知"
    comment = feedback.get("comment", "")

    parts = [
        f"该要素「{key}」在文档中未能正确核验。\n"
        f"- 上轮检索词：{prev_query}\n"
        f"- Critic 反馈：{comment}。图片中显示实际值为「{actual}」\n",
    ]

    if rag_hits:
        parts.append("- 上轮 RAG 检索命中的文本片段：")
        for i, h in enumerate(rag_hits, 1):
            parts.append(f"  {i}. [第{h['page_num']}页] {h['text'][:200]}")
        parts.append("")

    parts.append(
        "请根据 Critic 反馈和上轮检索结果，生成 1-3 个新的检索词（适合语义搜索）。\n"
        "技巧：从已有检索结果中提取关键数字、术语、短语，组合成更精准的检索词。\n"
        "例如：如果实际值缺少数字，而检索结果中某处出现了可能相关的数字，可构造包含该数字的检索词。\n"
        '输出 JSON：{"queries": ["检索词1", "检索词2"]}'
    )

    return [
        {"role": "system", "content": REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]
