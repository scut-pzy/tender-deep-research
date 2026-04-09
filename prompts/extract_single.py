"""Policy 单字段提取提示词。"""
from prompts.extract import SYSTEM_PROMPT


def build_extract_single_prompt(
    key: str,
    hits: list[dict],
    prev_item: dict | None = None,
    feedback: dict | None = None,
) -> list[dict]:
    """
    构建单字段提取的 messages。
    - key: 要素名称
    - hits: RAG 检索命中结果
    - prev_item: 上轮提取结果（dict，来自 ExtractionItem.model_dump()）
    - feedback: 上轮 Critic 反馈（dict，来自 CriticFeedback.model_dump()）
    """
    lines = [f"## 需要提取的招标要素：「{key}」\n"]

    lines.append("## 相关内容（RAG 检索结果）：")
    if not hits:
        lines.append("（未检索到相关内容）")
    else:
        for hit in hits:
            lines.append(
                f"[第{hit['page_num']}页] (相关度:{hit['score']:.2f})\n{hit['text']}"
            )
    lines.append("")

    if prev_item and feedback:
        lines.append("## 上轮提取结果及反馈：")
        prev_val = prev_item.get("value") or "未找到"
        prev_page = prev_item.get("source_page") or "未知"
        lines.append(f"上轮提取值：{prev_val}（第{prev_page}页）")
        actual = feedback.get("actual_value") or "未知"
        comment = feedback.get("comment", "")
        lines.append(f"Critic 反馈：❌ 图片中显示实际值为「{actual}」。{comment}")
        lines.append("")

    lines.append(
        "请按 JSON 格式输出该要素的提取结果：\n"
        '{"key": "要素名称", "value": "...", "source_page": ..., '
        '"source_text": "...", "confidence": ...}'
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]
