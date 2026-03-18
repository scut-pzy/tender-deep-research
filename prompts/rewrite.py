"""Policy 迭代重写提示词：根据 Critic 反馈修正提取结果。"""
from prompts.extract import SYSTEM_PROMPT


def build_rewrite_prompt(
    keys: list[str],
    rag_results: dict[str, list[dict]],
    prev_items: list[dict],
    critic_feedbacks: list[dict],
) -> list[dict]:
    """
    构建迭代重写的 user 消息。
    只对 Critic 标记为 verified=False 的要素重新提取，其余保持原值。
    """
    failed_keys = {fb["key"] for fb in critic_feedbacks if not fb.get("verified", True)}

    lines = ["## Critic 视觉核验反馈（以下要素需要修正）："]
    for fb in critic_feedbacks:
        if not fb.get("verified", True):
            actual = fb.get("actual_value") or "未知"
            comment = fb.get("comment", "")
            lines.append(f"- 「{fb['key']}」: 你的答案有误，图片中显示为「{actual}」。{comment}")
    lines.append("")

    lines.append("## 上一轮提取结果（供参考）：")
    for item in prev_items:
        marker = "❌ 需修正" if item["key"] in failed_keys else "✅ 已确认"
        lines.append(f"- 「{item['key']}」= {item.get('value')} (第{item.get('source_page')}页) [{marker}]")
    lines.append("")

    lines.append("## 需要重新提取的要素及参考文本：")
    for key in failed_keys:
        hits = rag_results.get(key, [])
        lines.append(f"### 「{key}」")
        if not hits:
            lines.append("（未检索到相关内容）")
        else:
            for hit in hits:
                lines.append(f"[第{hit['page_num']}页]\n{hit['text']}")
        lines.append("")

    lines.append(
        "请输出【所有】要素的最终结果（已通过的保持原值，失败的重新提取），格式不变。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]
