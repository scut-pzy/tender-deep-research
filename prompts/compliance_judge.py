"""合规性判定提示词：比对招标要求与投标响应。"""


def build_single_compliance_prompt(
    key: str,
    tender_requirement: str,
    bid_response: str,
    bid_hits: list[dict],
    additional_context: str | None = None,
) -> list[dict]:
    """
    单字段合规判定 prompt：已知招标要求和投标书提取值，判断是否合规。

    additional_context: 可选的人工补充信息，将作为高优先级的判定依据。
    """
    system = """\
你是一名专业的招投标合规性审查专家。
根据招标书的要求和投标书的实际响应，判断该字段是否合规。

## 判定规则
- pass（合规）：投标内容完全满足招标要求
- fail（不合规）：投标内容明确不满足要求，或缺失必要响应
- warn（需人工确认）：信息不完整或存在模糊表述，无法自动判定

## 输出格式（严格 JSON，不加其他内容）
{"verdict": "pass", "reason": "具体判定依据（50字以内）"}"""

    rag_text = "\n".join(
        f"[第{h['page_num']}页] {h['text']}" for h in (bid_hits or [])[:3]
    )

    extra_block = ""
    if additional_context and additional_context.strip():
        extra_block = (
            "\n## 人工补充信息（高优先级，请结合此信息重新判定）：\n"
            f"{additional_context.strip()}\n"
        )

    user = f"""## 要素：{key}

## 招标书要求：
{tender_requirement}

## 投标书响应（已从投标书提取）：
{bid_response}

## 投标书原文片段（RAG 参考）：
{rag_text or "（无 RAG 内容）"}
{extra_block}
请判定投标书是否满足招标要求，输出 JSON。"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_reeval_reasoning_prompt(
    key: str,
    tender_requirement: str,
    bid_response: str,
    current_verdict: str,
    current_reason: str,
    bid_hits: list[dict],
    additional_context: str,
) -> list[dict]:
    """
    带人工补充信息的"再核查"prompt：输出 Markdown 分析 + 最终 JSON 代码块。
    用于流式场景（前端边看推理边等结果）。
    """
    system = """\
你是一名专业的招投标合规性审查专家。
用户已经生成了一份初步的合规判定，但现在提供了额外的补充信息，需要你结合原有信息与用户补充重新评估。

## 判定规则
- pass（合规）
- fail（不合规）
- warn（需人工确认）

## 输出要求
1. 先用 Markdown 简要说明你的分析过程（不超过 200 字）
2. 最后输出一个 ```json``` 代码块，格式：
```json
{"verdict": "pass|fail|warn", "reason": "简明的判定依据（80字内）", "response": "（可选）更新后的投标响应摘要"}
```
response 字段仅在用户补充信息改变了对投标响应的理解时才填写，否则可省略。"""

    rag_text = "\n".join(
        f"[第{h['page_num']}页] {h['text']}" for h in (bid_hits or [])[:3]
    )

    verdict_label = {"pass": "✅ 合规", "fail": "❌ 不合规", "warn": "⚠️ 需确认"}.get(
        current_verdict, current_verdict
    )

    user = f"""## 要素：{key}

## 招标书要求：
{tender_requirement}

## 投标书响应（已从投标书提取）：
{bid_response}

## 当前判定：
{verdict_label}
理由：{current_reason}

## 投标书原文片段（RAG 参考）：
{rag_text or "（无 RAG 内容）"}

## 用户补充信息（关键，请优先参考）：
{additional_context.strip()}

请重新评估该字段是否合规，先输出分析过程，最后在 json 代码块中给出新判定。"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


COMPLIANCE_SYSTEM_PROMPT = """\
你是一名专业的招投标合规性审查专家。你的任务是逐条比对招标书的硬性要求与投标书的实际响应，判定投标是否合规。

## 判定规则
- pass（合规）：投标内容完全满足招标要求
- fail（不合规）：投标内容明确不满足要求，或缺失必要响应
- warn（需人工确认）：信息不完整或存在模糊表述，无法自动判定

## 审核理由要求
reason 字段必须详细说明判定依据，具体包括：
- pass：说明投标书中哪些内容满足了招标要求，引用关键数据或条款
- fail：说明招标书的具体要求是什么，投标书缺少或违反了哪一点
- warn：说明哪些信息不完整或模糊，需要人工确认哪些细节
reason 不超过 100 字，必须包含具体依据，不能只写"满足要求"或"不满足要求"。

## 输出规则
- 严格按 JSON 格式输出
- 输出格式：{"items": [...]}
- 每个 item 包含：
  - key: 要求名称（与输入一致）
  - requirement: 招标要求描述（与输入一致）
  - response: 从投标书中提取到的对应内容（尽量完整）
  - verdict: pass / fail / warn
  - reason: 审核理由（详见上方要求，不超过100字）
  - source_page: 投标书中对应内容的来源页码
  - source_text: 投标书中的来源原文（80字以内）
"""


def build_compliance_prompt(
    checklist_items: list[dict],
    rag_results: dict[str, list[dict]],
) -> list[dict]:
    """
    构建合规性判定的 messages。
    - checklist_items: 审查清单条目 [{key, requirement, category, ...}, ...]
    - rag_results: 以每条要求为 key 在投标书中的 RAG 检索结果
    """
    lines = ["## 审查清单（来自招标书的硬性要求）：\n"]
    for i, item in enumerate(checklist_items, 1):
        lines.append(f"{i}. 「{item['key']}」: {item['requirement']}")
    lines.append("")

    lines.append("## 投标书中的对应内容（RAG 检索结果）：\n")
    for item in checklist_items:
        key = item["key"]
        hits = rag_results.get(key, [])
        lines.append(f"### 「{key}」相关内容：")
        if not hits:
            lines.append("（未检索到相关内容）")
        else:
            for hit in hits:
                lines.append(
                    f"[第{hit['page_num']}页] (相关度:{hit['score']:.2f})\n{hit['text']}"
                )
        lines.append("")

    lines.append(
        "请逐条比对每个要求与投标书内容，判定是否合规，按 JSON 格式输出。"
    )
    return [
        {"role": "system", "content": COMPLIANCE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]
