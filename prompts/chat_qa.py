"""自由对话 prompt — 基于文档 RAG 上下文和已有分析结果回答用户问题。"""


def build_chat_qa_prompt(
    question: str,
    rag_context: str,
    context_data: dict | None = None,
) -> list[dict]:
    """构建自由对话的消息列表。

    Args:
        question: 用户的自由问题
        rag_context: RAG 检索到的文档片段（已格式化）
        context_data: 可选的已有分析结果（提取结果或合规报告）
    """
    system = (
        "你是一个专业的招投标文件分析助手。请根据提供的文档内容和分析结果，"
        "准确回答用户的问题。\n\n"
        "要求：\n"
        "- 回答必须基于文档内容，不要编造信息\n"
        "- 如果文档中没有相关内容，请明确说明\n"
        "- 引用具体页码和原文片段以增加可信度\n"
        "- 使用清晰的 Markdown 格式\n"
    )

    if rag_context:
        system += f"\n## 文档相关内容\n{rag_context}\n"

    is_compliance = isinstance(context_data, list)

    if context_data:
        system += "\n## 已有分析结果\n"
        if isinstance(context_data, dict):
            for k, v in context_data.items():
                if v:
                    system += f"- **{k}**: {v}\n"
        elif is_compliance:
            # 合规报告格式 — 展示完整字段，便于 LLM 决定是否 patch
            for item in context_data:
                if isinstance(item, dict):
                    key = item.get("key", "")
                    verdict = item.get("verdict", "")
                    reason = item.get("reason", "")
                    response = item.get("response", "")
                    requirement = item.get("requirement", "")
                    system += (
                        f"- **{key}** [{verdict}]\n"
                        f"  - 招标要求: {requirement}\n"
                        f"  - 投标响应: {response}\n"
                        f"  - 判定依据: {reason}\n"
                    )

    if is_compliance:
        system += (
            "\n## 合规报告修改说明\n"
            "如果用户在对话中要求修改某条合规结论（例如说明事实、提供新值、指定应有判定），\n"
            "**请在回答末尾**追加一个 JSON 代码块，列出需要更新的条目。格式：\n\n"
            "```json\n"
            '{"updates": [\n'
            '  {"key": "招标方式", "verdict": "合规", "response": "公开招标", "reason": "用户补充说明..."}\n'
            "]}\n"
            "```\n\n"
            "规则：\n"
            "- 只输出**需要改动**的条目；不需要修改时不要输出 JSON 代码块。\n"
            "- `key` 必须与已有条目完全一致。\n"
            "- `verdict` 只能是 `合规`/`不合规`/`警告` 之一。\n"
            "- 其它字段（response/reason）可按需省略，省略即保留原值。\n"
            "- 正文先用 Markdown 解释判断依据，JSON 放在最后。\n"
        )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
