"""Policy 提取提示词：首次提取 + 迭代重写。"""

SYSTEM_PROMPT = """\
你是一名专业的招标文件分析专家，擅长从复杂的标书文档中精准提取关键信息。

## 输出规则
- 严格按 JSON 格式输出，不要有任何额外说明
- 每个要素包含字段：key、value、source_page、source_text、confidence
  - key: 要素名称（与输入完全一致）
  - value: 提取到的值（未找到则为 null）
  - source_page: 来源页码（整数，未找到则为 null）
  - source_text: 来源原文片段（50字以内，未找到则为 null）
  - confidence: 置信度（0.0~1.0 的浮点数）
- 输出格式：{"items": [...]}
"""


def build_extract_prompt(keys: list[str], rag_results: dict[str, list[dict]]) -> list[dict]:
    """构建首次要素提取的 user 消息。"""
    lines = ["## 需要提取的招标要素："]
    for i, key in enumerate(keys, 1):
        lines.append(f"{i}. {key}")

    lines.append("")
    for key in keys:
        hits = rag_results.get(key, [])
        lines.append(f"## 与「{key}」相关的内容：")
        if not hits:
            lines.append("（未检索到相关内容）")
        else:
            for hit in hits:
                lines.append(
                    f"[第{hit['page_num']}页] (相关度:{hit['score']:.2f})\n{hit['text']}"
                )
        lines.append("")

    lines.append("请按 JSON 格式输出所有要素的提取结果。")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]
