"""
招标文件要素提取 — 极简客户端
================================
用法：
  1. 修改下方 PDF_PATH 和 KEYS
  2. 确保服务端已启动：python main.py
  3. 运行：python run.py

输出：流式打印分析过程 + 最终提取表格
"""
import sys
from pathlib import Path

from openai import OpenAI

# ══════════════════════════════════════════════════════
#  ✏️  在这里填写你的输入
# ══════════════════════════════════════════════════════

PDF_PATH = "cache/uploads/政府采购货物类公开招标文件示范文本（试行）.pdf"

KEYS = [
    "投标保证金比例",
    "合同当事人定义",
]

SERVER_URL = "http://localhost:8000"   # 服务端地址

# ══════════════════════════════════════════════════════

def main():
    client = OpenAI(api_key="not-needed", base_url=f"{SERVER_URL}/v1")

    # ── Step 1: 上传文件 ──────────────────────────────
    pdf = Path(PDF_PATH)
    if not pdf.exists():
        print(f"❌ 文件不存在: {PDF_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"📤 上传文件: {pdf.name} ...", end=" ", flush=True)
    with open(pdf, "rb") as f:
        file_obj = client.files.create(file=(pdf.name, f, "application/pdf"), purpose="assistants")
    print(f"✅ file_id = {file_obj.id}")

    # ── Step 2: 构造消息 ──────────────────────────────
    keys_text = "\n".join(f"{i+1}. {k}" for i, k in enumerate(KEYS))
    message = f"[file_id:{file_obj.id}]\n请提取以下标书要素：\n{keys_text}"

    # ── Step 3: 流式对话，实时打印中间过程 ───────────
    print(f"\n{'─'*55}")
    print(f"📑 文件 : {pdf.name}")
    print(f"🔑 要素 : {KEYS}")
    print(f"{'─'*55}\n")

    stream = client.chat.completions.create(
        model="tender-research",
        messages=[{"role": "user", "content": message}],
        stream=True,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            print(delta, end="", flush=True)

    print()  # 末尾换行


if __name__ == "__main__":
    main()
