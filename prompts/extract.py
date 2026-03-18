"""招标要素提取提示词。"""

TENDER_ELEMENTS = [
    "项目名称",
    "采购单位",
    "预算金额",
    "投标截止时间",
    "资质要求",
    "技术规格",
    "评分标准",
    "交货/服务期限",
    "付款条件",
    "联系方式",
]


def build_extract_prompt(element: str, context_chunks: list[dict]) -> list[dict]:
    context = "\n\n".join(
        f"[第{c['page']}页]\n{c['text']}" for c in context_chunks
    )
    return [
        {
            "role": "system",
            "content": (
                "你是一名专业的招标文件分析专家。"
                "根据提供的招标文件片段，精准提取指定要素信息，并给出置信度(0~1)。"
                "输出格式：\n要素值：<内容>\n置信度：<0~1的小数>"
            ),
        },
        {
            "role": "user",
            "content": f"请从以下招标文件内容中提取【{element}】：\n\n{context}",
        },
    ]
