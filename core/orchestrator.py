"""主控调度器：串联文档处理、RAG、Policy、Critic 的完整流程。"""
import re
import json
from typing import AsyncGenerator

from core.critic import CriticAgent
from core.doc_processor import chunk_text_by_pages, process_pdf
from core.llm_client import EmbeddingClient, LLMClient, VLMClient
from core.policy import PolicyAgent
from core.rag import RAGEngine
from models.schemas import CriticFeedback, ExtractionItem, ExtractionResult
from utils.file_handler import download_file
from utils.logger import get_logger

logger = get_logger(__name__)


class Orchestrator:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.pages_dir = cfg["files"]["pages_dir"]
        self.upload_dir = cfg["files"]["upload_dir"]
        self.max_iterations = cfg["pipeline"]["max_iterations"]
        self.confidence_threshold = cfg["pipeline"]["confidence_threshold"]
        self.rag_cfg = cfg["rag"]

        self.policy_agent = PolicyAgent(LLMClient(cfg["policy_llm"]))
        self.critic_agent = CriticAgent(VLMClient(cfg["critic_vlm"]))
        self.embed_client = EmbeddingClient(cfg["embedding"])

    # ──────────────────────────────────────────────────────────────────────
    # 输入解析
    # ──────────────────────────────────────────────────────────────────────

    def parse_user_input(self, messages: list[dict]) -> tuple[list[str], str | None, str | None]:
        """
        从对话消息中解析出：
        - keys:    需要提取的要素清单
        - file_url: HTTP(S) URL（或 None）
        - file_id:  已上传文件的 ID，格式 [file_id:xxx]（或 None）

        支持格式：
          1. 编号列表："1. 项目名称\n2. 投标总价"
          2. 符号列表："- 项目名称\n- 投标总价"
          3. JSON 数组：'["项目名称","投标总价"]'
          4. 混合 + URL："请分析 https://xxx/a.pdf\n提取: 1.项目名称 2.投标总价"
          5. 混合 + file_id："[file_id:abc123]\n1. 投标总价"
        """
        # 合并所有用户消息
        text = " ".join(
            m["content"] if isinstance(m["content"], str) else ""
            for m in messages
            if m.get("role") == "user"
        )

        # 提取 file_id 引用
        fid_match = re.search(r"\[file_id:([^\]]+)\]", text)
        file_id = fid_match.group(1).strip() if fid_match else None

        # 提取 HTTP URL
        url_match = re.search(r"https?://\S+\.pdf\S*", text, re.IGNORECASE)
        file_url = url_match.group(0).rstrip("。，,.") if url_match else None

        # 提取要素清单
        keys: list[str] = []

        # 把 [file_id:xxx] 从文本中去掉，避免干扰 key 提取
        clean_text = re.sub(r"\[file_id:[^\]]+\]", "", text).strip()

        # JSON 数组（排除 file_id 格式）
        json_match = re.search(r"\[([^\]]+)\]", clean_text)
        if json_match:
            try:
                keys = json.loads(f"[{json_match.group(1)}]")
            except Exception:
                pass

        # 编号列表 / 符号列表
        if not keys:
            items = re.findall(r"(?:^|[\n,，、])[\s]*(?:\d+[.、。]|[-*•])\s*([^\n,，、\d]{2,20})", clean_text)
            keys = [k.strip() for k in items if k.strip()]

        # 逗号/顿号分隔
        if not keys:
            m = re.search(r"[提取分析：:]+\s*(.+)", clean_text)
            if m:
                raw = m.group(1)
                keys = [k.strip() for k in re.split(r"[,，、\n]", raw) if k.strip()]

        return keys, file_url, file_id

    # ──────────────────────────────────────────────────────────────────────
    # 核心流程（非流式）
    # ──────────────────────────────────────────────────────────────────────

    async def run(self, keys: list[str], pdf_path: str) -> ExtractionResult:
        # Step 1: 文档预处理
        pages_data_obj = process_pdf(pdf_path, self.pages_dir)
        pages = pages_data_obj["pages"]

        # Step 2: 构建 RAG 索引
        chunks = chunk_text_by_pages(
            pages,
            self.rag_cfg["chunk_size"],
            self.rag_cfg["chunk_overlap"],
        )
        rag = RAGEngine(self.embed_client, self.rag_cfg["top_k"])
        await rag.build_index(chunks)

        # Step 3: RAG 检索
        rag_results = await rag.search_for_keys(keys)

        items: list[ExtractionItem] = []
        feedbacks: list[CriticFeedback] = []
        converged = False

        for iteration in range(1, self.max_iterations + 1):
            logger.info("==== 第 %d 轮 ====", iteration)

            # Step 4: Policy 提取 / 重写
            if iteration == 1:
                items = await self.policy_agent.extract(keys, rag_results)
            else:
                items = await self.policy_agent.rewrite(keys, rag_results, items, feedbacks)

            # Step 5: Critic 验证
            feedbacks = await self.critic_agent.verify(items, pages)

            failed = [fb for fb in feedbacks if not fb.verified]
            logger.info("第 %d 轮：%d 个要素，%d 个失败", iteration, len(items), len(failed))

            # 更新已通过要素的 verified 状态
            verified_keys = {fb.key for fb in feedbacks if fb.verified}
            for item in items:
                if item.key in verified_keys:
                    item.verified = True

            if not failed:
                converged = True
                logger.info("全部通过核验，提前收敛")
                break

        return ExtractionResult(
            items=items,
            total_iterations=iteration,
            converged=converged,
        )

    # ──────────────────────────────────────────────────────────────────────
    # 核心流程（流式）
    # ──────────────────────────────────────────────────────────────────────

    async def run_stream(
        self, keys: list[str], pdf_path: str
    ) -> AsyncGenerator[str, None]:
        yield "🔍 开始分析投标文件...\n\n"

        # Step 1
        yield "📄 **Step 1/5: 文档预处理**\n"
        pages_data_obj = process_pdf(pdf_path, self.pages_dir)
        pages = pages_data_obj["pages"]
        total = pages_data_obj["total_pages"]
        yield f"  ✅ 共识别 **{total}** 页\n\n"

        # Step 2
        yield "🧮 **Step 2/5: 构建检索索引**\n"
        chunks = chunk_text_by_pages(
            pages,
            self.rag_cfg["chunk_size"],
            self.rag_cfg["chunk_overlap"],
        )
        rag = RAGEngine(self.embed_client, self.rag_cfg["top_k"])
        await rag.build_index(chunks)
        yield f"  ✅ 索引构建完成：**{len(chunks)}** 个文本块\n\n"

        # Step 3
        yield "🔎 **Step 3/5: RAG 语义检索**\n"
        rag_results = await rag.search_for_keys(keys)
        for key, hits in rag_results.items():
            pages_hits = sorted({h["page_num"] for h in hits})
            yield f"  - 「{key}」→ 在第 {pages_hits} 页找到相关内容\n"
        yield "  ✅ 检索完成\n\n"

        items: list[ExtractionItem] = []
        feedbacks: list[CriticFeedback] = []
        converged = False
        iteration = 0

        for iteration in range(1, self.max_iterations + 1):
            # Step 4
            yield f"📝 **Step 4/5: Policy LLM 提取 (第{iteration}轮)**\n"
            if iteration == 1:
                items = await self.policy_agent.extract(keys, rag_results)
            else:
                items = await self.policy_agent.rewrite(keys, rag_results, items, feedbacks)
            for item in items:
                conf_pct = int(item.confidence * 100)
                yield (
                    f"  - 「{item.key}」= **{item.value}**"
                    f"  (第{item.source_page}页, 置信度:{conf_pct}%)\n"
                )
            yield "\n"

            # Step 5
            yield f"👁️ **Step 5/5: Critic VLM 视觉核验 (第{iteration}轮)**\n"
            feedbacks = await self.critic_agent.verify(items, pages)
            for fb in feedbacks:
                if fb.verified:
                    yield f"  ✅ 「{fb.key}」: 确认正确\n"
                else:
                    actual = fb.actual_value or "未知"
                    yield f"  ❌ 「{fb.key}」: 图片显示为 **{actual}**，非提取值\n"
            yield "\n"

            failed = [fb for fb in feedbacks if not fb.verified]
            verified_keys = {fb.key for fb in feedbacks if fb.verified}
            for item in items:
                if item.key in verified_keys:
                    item.verified = True

            if not failed:
                converged = True
                yield "🎉 **全部通过核验！**\n\n"
                break
            else:
                yield f"🔄 有 **{len(failed)}** 个要素未通过，进入下一轮...\n\n"

        result = ExtractionResult(
            items=items,
            total_iterations=iteration,
            converged=converged,
        )
        yield self._format_result_markdown(result)

    def _format_result_markdown(self, result: ExtractionResult) -> str:
        lines = ["## 📋 最终提取结果\n"]
        lines.append("| 要素 | 提取值 | 来源页 | 核验 |")
        lines.append("|------|--------|--------|------|")
        for item in result.items:
            mark = "✅" if item.verified else "⚠️"
            page = f"第{item.source_page}页" if item.source_page else "—"
            lines.append(f"| {item.key} | {item.value or '未找到'} | {page} | {mark} |")
        lines.append("")
        lines.append(
            f"> 共迭代 **{result.total_iterations}** 轮，"
            f"{'已完全收敛' if result.converged else '达到最大迭代次数'}"
        )
        return "\n".join(lines)
