"""从招标书中提取硬性要求，生成审查清单。"""

CHECKLIST_SYSTEM_PROMPT = """\
你是一名专业的招标文件审查专家。你的任务是从招标文件中提取所有硬性要求和限定条件，生成一份审查清单（Checklist）。

## 提取重点
- 预算/金额上限或下限
- 资质等级要求（如建筑资质、ISO认证）
- 保证金比例和金额
- 业绩要求（类似项目经验数量、金额）
- 人员资质要求（项目经理、技术负责人等）
- 时间限制（工期、交货期、投标截止时间）
- 技术参数硬性指标
- 响应性要求（必须满足的条款）
- 废标条件
- 付款条件
- 其他强制性条款

## 输出规则
- 严格按 JSON 格式输出
- 输出格式：{"items": [...]}
- 每个 item 包含：
  - key: 简短的要求名称（如"投标保证金"、"资质等级"）
  - requirement: 具体的要求描述（如"投标保证金不低于投标总价的2%"）
  - category: 类别（资质要求/财务要求/技术要求/时间要求/其他要求）
  - source_page: 来源页码（整数）
  - source_text: 来源原文（50字以内）
- 仅提取明确的、可量化或可判定的硬性要求，不要提取模糊描述
- 如果找不到硬性要求，返回空列表
"""


def build_checklist_prompt(rag_results: dict[str, list[dict]]) -> list[dict]:
    """构建审查清单提取的 messages。"""
    lines = ["## 请从以下招标文件内容中提取所有硬性要求和限定条件：\n"]

    for topic, hits in rag_results.items():
        lines.append(f"### 「{topic}」相关内容：")
        if not hits:
            lines.append("（未检索到相关内容）")
        else:
            for hit in hits:
                lines.append(
                    f"[第{hit['page_num']}页] (相关度:{hit['score']:.2f})\n{hit['text']}"
                )
        lines.append("")

    lines.append("请提取所有硬性要求，按 JSON 格式输出。")
    return [
        {"role": "system", "content": CHECKLIST_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]
