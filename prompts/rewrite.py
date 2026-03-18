"""基于 Critic 反馈的要素重写提示词。"""


def build_rewrite_prompt(
    element: str,
    current_value: str,
    critic_feedbacks: list[dict],
    context_chunks: list[dict],
) -> list[dict]:
    feedback_text = "\n".join(
        f"- [{f['severity'].upper()}] {f['issue']} → {f['suggestion']}"
        for f in critic_feedbacks
    )
    context = "\n\n".join(
        f"[第{c['page']}页]\n{c['text']}" for c in context_chunks
    )
    return [
        {
            "role": "system",
            "content": (
                "你是一名专业的招标文件分析专家。"
                "根据视觉审核反馈对已提取的要素进行修正和完善。"
                "输出格式：\n要素值：<修正后内容>\n置信度：<0~1的小数>"
            ),
        },
        {
            "role": "user",
            "content": (
                f"要素：【{element}】\n"
                f"当前提取值：{current_value}\n\n"
                f"视觉审核反馈：\n{feedback_text}\n\n"
                f"参考原文：\n{context}\n\n"
                "请根据反馈修正该要素的提取结果。"
            ),
        },
    ]
