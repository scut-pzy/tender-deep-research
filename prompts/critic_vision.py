"""视觉审核提示词（用于 VLM 对页面图片的审核）。"""


def build_critic_prompt(page_num: int, extracted_elements: list[dict]) -> str:
    elements_summary = "\n".join(
        f"- {e['element']}: {e['value']}" for e in extracted_elements
    )
    return (
        f"这是招标文件第 {page_num} 页的图片。\n\n"
        f"当前已提取的关键要素：\n{elements_summary}\n\n"
        "请仔细审查该页面，识别：\n"
        "1. 文本提取遗漏或错误的信息\n"
        "2. 表格、印章、签字等重要视觉元素\n"
        "3. 可能影响投标的关键截止日期或条款\n\n"
        "输出格式（JSON数组）：\n"
        '[{"issue": "问题描述", "suggestion": "建议", "severity": "low|medium|high"}]'
        "\n如无问题输出：[]"
    )
