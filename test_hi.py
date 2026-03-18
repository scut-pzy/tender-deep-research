"""快速测试：验证 DashScope API 连通性（hi 对话）。"""
import asyncio
import sys
from pathlib import Path

# 确保能 import 项目模块
sys.path.insert(0, str(Path(__file__).parent))

from core.llm_client import LLMClient
from utils.config_loader import load_config

CFG = load_config()


async def main():
    client = LLMClient(CFG["policy_llm"])
    print(f"模型: {client.model}")
    print(f"接口: {client.api_base}")
    print("发送: hi\n")

    reply = await client.chat([
        {"role": "user", "content": "hi"},
    ])

    print(f"回复: {reply}")


if __name__ == "__main__":
    asyncio.run(main())
