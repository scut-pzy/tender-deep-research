"""主控调度器：串联文档处理、RAG、Policy、Critic 的完整流程。"""
import asyncio
import hashlib
import re
import json
from pathlib import Path
from typing import AsyncGenerator

from core.critic import CriticAgent
from core.doc_processor import chunk_text_by_pages, chunk_text_parent_child, process_pdf
from core.llm_client import EmbeddingClient, LLMClient, VLMClient
from core.policy import PolicyAgent
from core.rag import RAGEngine, ParentChildRAGEngine
from models.schemas import ChecklistItem, CriticFeedback, ExtractionItem, ExtractionResult
from prompts.checklist import build_checklist_prompt
from prompts.compliance_judge import (
    build_compliance_prompt,
    build_reeval_reasoning_prompt,
    build_single_compliance_prompt,
)
from prompts.chat_qa import build_chat_qa_prompt
from prompts.refine_query import build_refine_query_prompt
from utils.file_handler import download_file
from utils.logger import get_logger

logger = get_logger(__name__)


def _format_rag_context(hits: list[dict]) -> str:
    """将 RAG 检索结果格式化为 Critic 可用的上下文文本。"""
    if not hits:
        return ""
    return "\n".join(f"[第{h['page_num']}页] {h['text']}" for h in hits)


class Orchestrator:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.pages_dir = cfg["files"]["pages_dir"]
        self.upload_dir = cfg["files"]["upload_dir"]
        self.max_iterations = cfg["pipeline"]["max_iterations"]
        self.confidence_threshold = cfg["pipeline"]["confidence_threshold"]
        self.field_top_k = cfg["pipeline"].get("field_top_k", 5)
        self.rag_cfg = cfg["rag"]

        self.policy_llm = LLMClient(cfg["policy_llm"])
        self.policy_agent = PolicyAgent(self.policy_llm)
        self.critic_agent = CriticAgent(VLMClient(cfg["critic_vlm"]))
        self.embed_client = EmbeddingClient(cfg["embedding"])

    # ──────────────────────────────────────────────────────────────────────
    # RAG 构建（含缓存）
    # ──────────────────────────────────────────────────────────────────────

    def _cache_key(self, pdf_path: str) -> str:
        """以 PDF 内容 MD5 + 模型 + 分块参数 + 模式 生成缓存 key。"""
        content_md5 = hashlib.md5(Path(pdf_path).read_bytes()).hexdigest()[:12]
        model = self.embed_client.model
        mode = self.rag_cfg.get("mode", "flat")
        if mode == "parent_child":
            params = (
                f"{self.rag_cfg['parent_chunk_size']}|"
                f"{self.rag_cfg['child_chunk_size']}|"
                f"{self.rag_cfg['child_chunk_overlap']}"
            )
        else:
            params = f"{self.rag_cfg['chunk_size']}|{self.rag_cfg['chunk_overlap']}"
        raw = f"{content_md5}|{model}|{mode}|{params}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    async def _build_rag(
        self, pages: list[dict], pdf_path: str, use_cache: bool,
        on_progress=None,
    ) -> tuple[RAGEngine | ParentChildRAGEngine, bool]:
        """
        构建或加载 RAG 索引。根据 rag.mode 选择扁平或父子检索。
        返回 (rag_engine, from_cache)。

        Args:
            on_progress: 可选回调 (done, total) -> None，embedding 每批完成后调用。
        """
        vectors_dir = self.cfg["files"]["vectors_dir"]
        mode = self.rag_cfg.get("mode", "flat")
        key = self._cache_key(pdf_path)

        if mode == "parent_child":
            rag = ParentChildRAGEngine(self.embed_client, self.rag_cfg["top_k"])
            if use_cache and rag.load(vectors_dir, key):
                return rag, True
            parent_chunks, child_chunks = chunk_text_parent_child(
                pages,
                parent_chunk_size=self.rag_cfg.get("parent_chunk_size", 1024),
                parent_chunk_overlap=self.rag_cfg.get("parent_chunk_overlap", 128),
                child_chunk_size=self.rag_cfg.get("child_chunk_size", 256),
                child_chunk_overlap=self.rag_cfg.get("child_chunk_overlap", 64),
            )
            await rag.build_index(parent_chunks, child_chunks, on_progress)
            rag.save(vectors_dir, key)
            return rag, False
        else:
            rag = RAGEngine(self.embed_client, self.rag_cfg["top_k"])
            if use_cache and rag.load(vectors_dir, key):
                return rag, True
            chunks = chunk_text_by_pages(
                pages,
                self.rag_cfg["chunk_size"],
                self.rag_cfg["chunk_overlap"],
            )
            await rag.build_index(chunks, on_progress)
            rag.save(vectors_dir, key)
            return rag, False

    async def _build_rag_with_progress(
        self, pages: list[dict], pdf_path: str, use_cache: bool
    ) -> AsyncGenerator[str | tuple, None]:
        """构建 RAG 索引并以 yield 输出进度行。

        yield str  → 进度文本（⏳ Embedding 进度：...）
        最后一个 yield 是 tuple (rag, from_cache)。
        """
        progress_q: asyncio.Queue[tuple[int, int] | None] = asyncio.Queue()

        def _on_progress(done: int, total: int):
            progress_q.put_nowait((done, total))

        build_task = asyncio.create_task(
            self._build_rag(pages, pdf_path, use_cache, on_progress=_on_progress)
        )
        build_task.add_done_callback(lambda _: progress_q.put_nowait(None))

        last_pct = -1
        while True:
            item = await progress_q.get()
            if item is None:
                break
            done, total_chunks = item
            pct = int(done / total_chunks * 100) if total_chunks else 100
            if pct != last_pct:
                last_pct = pct
                yield f"  ⏳ Embedding 进度：{done}/{total_chunks}（{pct}%）\n"

        yield build_task.result()  # (rag, from_cache)

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

    async def run(self, keys: list[str], pdf_path: str, use_cache: bool = True) -> ExtractionResult:
        # Step 1: 文档预处理
        pages_data_obj = process_pdf(pdf_path, self.pages_dir)
        pages = pages_data_obj["pages"]

        # Step 2: 构建 RAG 索引（use_cache=True 时优先读缓存）
        rag, _ = await self._build_rag(pages, pdf_path, use_cache)

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
    # 核心流程（流式）— 逐字段迭代
    # ──────────────────────────────────────────────────────────────────────

    async def _refine_rag_queries(self, key: str, feedback: CriticFeedback, rag_hits: list[dict] | None = None) -> list[str]:
        """根据 Critic 反馈 + 上轮 RAG hits 让 LLM 生成新的 RAG 检索词。"""
        messages = build_refine_query_prompt(key, key, feedback.model_dump(), rag_hits=rag_hits)
        raw = await self.policy_llm.chat(messages, json_mode=True)
        try:
            data = json.loads(raw)
            queries = data.get("queries", [key])
            if not queries:
                queries = [key]
            return queries
        except (json.JSONDecodeError, KeyError):
            return [key]

    async def run_stream(
        self, keys: list[str], pdf_path: str, use_cache: bool = True
    ) -> AsyncGenerator[str, None]:
        """
        流式信息提取主流程，逐字段处理并实时 yield 进度文本。

        与非流式的 run() 不同，此方法每个字段独立执行三轮精炼，而非批量处理所有字段：
        - 轮1：对 source_page 做 Critic 核验（最快路径，大多数字段在此收敛）
        - 轮2：扩展到 RAG top-K 涉及的全部页面核验。
                若轮2页面命中（值在文档中存在）但轮1已记录值错误，
                说明 Policy 提了错误值而非检索偏差，此时回传轮1反馈给 Policy 重写。
        - 轮3：让 LLM 生成新的 RAG 检索词再次检索，解决语义检索本身偏差的情况。

        每次 yield 一段 Markdown 格式的进度文本，最后 yield 一个 JSON code block 作为最终结果。
        """
        yield "🔍 开始分析投标文件...\n\n"

        # Step 1: 文档预处理
        yield "📄 **Step 1/3: 文档预处理**\n"
        pages_data_obj = process_pdf(pdf_path, self.pages_dir)
        pages = pages_data_obj["pages"]
        total = pages_data_obj["total_pages"]
        yield f"  ✅ 共识别 **{total}** 页\n\n"

        # Step 2: 构建检索索引
        rag_mode = self.rag_cfg.get("mode", "flat")
        mode_label = "父子分块检索" if rag_mode == "parent_child" else "扁平向量检索"
        yield f"🧮 **Step 2/3: 构建检索索引（{mode_label}）**\n"

        rag = from_cache = None
        async for item in self._build_rag_with_progress(pages, pdf_path, use_cache):
            if isinstance(item, str):
                yield item
            else:
                rag, from_cache = item

        if rag_mode == "parent_child":
            ntotal = rag.child_index.ntotal if rag.child_index else 0
        else:
            ntotal = rag.index.ntotal if rag.index else 0
        if from_cache:
            yield f"  ⚡ 命中缓存，跳过 Embedding（共 **{ntotal}** 个向量）\n\n"
        else:
            yield f"  ✅ 索引构建完成：**{ntotal}** 个文本块\n\n"

        # Step 3: 逐字段处理
        yield f"🔎 **Step 3/3: 逐字段提取与核验**（共 {len(keys)} 个要素）\n\n"

        all_items: list[ExtractionItem] = []
        total_keys = len(keys)

        for idx, key in enumerate(keys, 1):
            yield f"🔑 开始处理「{key}」({idx}/{total_keys})\n"

            # ── 轮 1：RAG → Policy → Critic(source_page) ──
            yield f"  → Policy 提取...\n"
            hits = await rag.search(key, top_k=self.field_top_k)
            item = None
            has_thinking = False
            async for tag, payload in self.policy_agent.extract_single_stream(key, hits):
                if tag == "thinking":
                    yield f"  💭 {payload}"
                    has_thinking = True
                elif tag == "result":
                    item = payload
            if has_thinking:
                yield "\n"
            conf_pct = int(item.confidence * 100)
            disp_val = (item.value or "未找到").replace("\n", " ")
            disp_page = item.source_page or 0
            yield (
                f"  - 「{item.key}」= **{disp_val}**"
                f"  (第{disp_page}页, 置信度:{conf_pct}%)\n"
            )
            await asyncio.sleep(0)

            pages_to_check_r1 = [item.source_page] if item.source_page else []
            if pages_to_check_r1:
                yield f"  → Critic 核验 source_page (第{item.source_page}页)...\n"
                fb = await self.critic_agent.verify_single(item, pages, pages_to_check_r1, rag_context=_format_rag_context(hits))
                if fb.verified:
                    item.verified = True
                    yield f"  ✅ 「{key}」: {fb.comment or '核验通过'}\n"
                    yield f"✅ 「{key}」处理完成\n\n"
                    all_items.append(item)
                    await asyncio.sleep(0)
                    continue
                else:
                    actual = fb.actual_value or "未知"
                    comment_part = f"（{fb.comment}）" if fb.comment else ""
                    yield f"  ❌ 「{key}」: 图片显示为 **{actual}**{comment_part}\n"
            else:
                fb = CriticFeedback(key=key, verified=False, comment="无来源页码，无法核验")
                yield f"  ❌ 「{key}」: 无来源页码，无法核验\n"

            fb_r1 = fb  # 保存轮1反馈，供轮2通过后回传 Policy

            # ── 轮 2：扩展核验 RAG top-K 涉及的所有页面 ──
            rag_pages = sorted({h["page_num"] for h in hits})
            # 排除已检查过的 source_page
            extended_pages = [p for p in rag_pages if p not in pages_to_check_r1]
            if extended_pages:
                yield f"  → Critic 扩展核验 RAG 相关页 {extended_pages}...\n"
                fb = await self.critic_agent.verify_single(item, pages, extended_pages, rag_context=_format_rag_context(hits))
                if fb.verified:
                    # 轮2在扩展页发现了值的存在，说明文档中确实有该信息（检索没问题），
                    # 但轮1的 Critic 在 source_page 上发现值有误（Policy 提取了错误的值）。
                    # 所以策略是：用轮1的精确反馈（fb_r1）来纠正 Policy，而不是仅靠轮2的泛化通过。
                    yield f"  → 轮2页面匹配，回传轮1反馈给 Policy 重新生成...\n"
                    has_thinking_r2 = False
                    async for tag, payload in self.policy_agent.extract_single_stream(
                        key, hits, prev_item=item, feedback=fb_r1,
                    ):
                        if tag == "thinking":
                            yield f"  💭 {payload}"
                            has_thinking_r2 = True
                        elif tag == "result":
                            item = payload
                    if has_thinking_r2:
                        yield "\n"
                    conf_pct_r2 = int(item.confidence * 100)
                    disp_val_r2 = (item.value or "未找到").replace("\n", " ")
                    disp_page_r2 = item.source_page or 0
                    yield (
                        f"  - 「{item.key}」= **{disp_val_r2}**"
                        f"  (第{disp_page_r2}页, 置信度:{conf_pct_r2}%)\n"
                    )
                    # 核验新值：合并轮1和扩展页
                    all_pages_r2 = pages_to_check_r1 + extended_pages
                    yield f"  → Critic 核验新值 (页 {all_pages_r2})...\n"
                    fb = await self.critic_agent.verify_single(item, pages, all_pages_r2, rag_context=_format_rag_context(hits))
                    if fb.verified:
                        item.verified = True
                        yield f"  ✅ 「{key}」: 轮2重新生成核验通过 — {fb.comment or ''}\n"
                        yield f"✅ 「{key}」处理完成\n\n"
                        all_items.append(item)
                        await asyncio.sleep(0)
                        continue
                    else:
                        actual = fb.actual_value or "未知"
                        comment_part = f"（{fb.comment}）" if fb.comment else ""
                        yield f"  ❌ 「{key}」: 轮2重新生成仍未通过，**{actual}**{comment_part}\n"
                else:
                    actual = fb.actual_value or "未知"
                    comment_part = f"（{fb.comment}）" if fb.comment else ""
                    yield f"  ❌ 「{key}」: 扩展页仍未通过，**{actual}**{comment_part}\n"

            # ── 轮 3：重新生成 RAG 检索词 → 重新检索 → 重新提取 → 核验 ──
            if self.max_iterations >= 3:
                try:
                    yield f"  → 重新生成 RAG 检索词...\n"
                    new_queries = await self._refine_rag_queries(key, fb, rag_hits=hits)
                    yield f"  → 新检索词: {new_queries}\n"

                    # 合并多个 query 的结果
                    new_hits: list[dict] = []
                    seen_texts: set[str] = set()
                    for q in new_queries:
                        q_hits = await rag.search(q, top_k=self.field_top_k)
                        for h in q_hits:
                            if h["text"] not in seen_texts:
                                seen_texts.add(h["text"])
                                new_hits.append(h)

                    yield f"  → Policy 重新提取...\n"
                    has_thinking_r3 = False
                    async for tag, payload in self.policy_agent.extract_single_stream(
                        key, new_hits, prev_item=item, feedback=fb,
                    ):
                        if tag == "thinking":
                            yield f"  💭 {payload}"
                            has_thinking_r3 = True
                        elif tag == "result":
                            item = payload
                    if has_thinking_r3:
                        yield "\n"
                    conf_pct = int(item.confidence * 100)
                    disp_val_r3 = (item.value or "未找到").replace("\n", " ")
                    disp_page_r3 = item.source_page or 0
                    yield (
                        f"  - 「{item.key}」= **{disp_val_r3}**"
                        f"  (第{disp_page_r3}页, 置信度:{conf_pct}%)\n"
                    )

                    pages_to_check_r3 = [item.source_page] if item.source_page else []
                    if pages_to_check_r3:
                        yield f"  → Critic 核验...\n"
                        fb = await self.critic_agent.verify_single(item, pages, pages_to_check_r3, rag_context=_format_rag_context(new_hits))
                        if fb.verified:
                            item.verified = True
                            yield f"  ✅ 「{key}」: 第3轮核验通过 — {fb.comment or ''}\n"
                            yield f"✅ 「{key}」处理完成\n\n"
                            all_items.append(item)
                            await asyncio.sleep(0)
                            continue
                        else:
                            actual = fb.actual_value or "未知"
                            comment_part = f"（{fb.comment}）" if fb.comment else ""
                            yield f"  ❌ 「{key}」: 第3轮仍未通过，**{actual}**{comment_part}\n"
                except Exception as e:
                    logger.error("第3轮处理「%s」异常: %s", key, e, exc_info=True)
                    yield f"  ❌ 「{key}」: 第3轮异常 ({e})\n"

            # 达到上限，标记 verified=false
            item.verified = False
            yield f"⚠️ 「{key}」处理完成（未通过核验）\n\n"
            all_items.append(item)
            await asyncio.sleep(0)

        converged = all(it.verified for it in all_items)
        result = ExtractionResult(
            items=all_items,
            total_iterations=total_keys,
            converged=converged,
        )
        yield self._format_result_json(result)

    # ──────────────────────────────────────────────────────────────────────
    # 自由对话 — 基于文档 RAG + 已有分析结果
    # ──────────────────────────────────────────────────────────────────────

    async def chat_stream(
        self,
        question: str,
        pdf_path: str,
        context_data: dict | None = None,
        use_cache: bool = True,
    ) -> AsyncGenerator[str, None]:
        """基于文档的自由问答，流式返回 LLM 生成的 token。"""
        # 1. 文档预处理（利用页面缓存）
        pages_data_obj = process_pdf(pdf_path, self.pages_dir)
        pages = pages_data_obj["pages"]

        # 2. 构建/加载 RAG 索引
        rag, _ = await self._build_rag(pages, pdf_path, use_cache)

        # 3. RAG 检索
        hits = await rag.search(question, top_k=self.field_top_k)
        rag_context = _format_rag_context(hits)

        # 4. 构建 prompt 并流式生成
        messages = build_chat_qa_prompt(question, rag_context, context_data)
        async for tag, text in self.policy_llm.chat_stream(messages):
            if tag == "content":
                yield text

    # ──────────────────────────────────────────────────────────────────────
    # 合规核查 — Step 1: 从招标书生成审查清单
    # ──────────────────────────────────────────────────────────────────────

    async def generate_checklist_stream(
        self, pdf_path: str, use_cache: bool = True
    ) -> AsyncGenerator[str, None]:
        """从招标书提取硬性要求，生成审查清单。"""
        yield "🔍 开始分析招标书，提取硬性要求...\n\n"

        # Step 1: 文档预处理
        yield "📄 **Step 1/3: 文档预处理**\n"
        pages_data_obj = process_pdf(pdf_path, self.pages_dir)
        pages = pages_data_obj["pages"]
        total = pages_data_obj["total_pages"]
        yield f"  ✅ 共识别 **{total}** 页\n\n"

        # Step 2: RAG 索引
        rag_mode = self.rag_cfg.get("mode", "flat")
        mode_label = "父子分块检索" if rag_mode == "parent_child" else "扁平向量检索"
        yield f"🧮 **Step 2/3: 构建检索索引（{mode_label}）**\n"
        rag = from_cache = None
        async for item in self._build_rag_with_progress(pages, pdf_path, use_cache):
            if isinstance(item, str):
                yield item
            else:
                rag, from_cache = item
        if from_cache:
            yield "  ⚡ 命中缓存\n\n"
        else:
            yield "  ✅ 索引构建完成\n\n"

        # Step 3: 以预设主题 RAG 检索，然后提取审查清单
        yield "🔎 **Step 3/3: 提取硬性要求**\n"
        search_topics = [
            "投标人资格条件", "保证金", "预算限价", "工期交货期",
            "技术要求参数", "评标方法", "废标条件", "付款条件",
            "人员资质要求", "业绩要求",
        ]
        rag_results = {}
        for topic in search_topics:
            rag_results[topic] = await rag.search(topic, top_k=self.field_top_k)

        messages = build_checklist_prompt(rag_results)
        yield "  → Policy LLM 提取中...\n"
        raw = await self.policy_llm.chat(messages, json_mode=True)

        # 解析结果
        try:
            data = json.loads(raw)
            items = data.get("items", [])
        except json.JSONDecodeError:
            # 尝试从 markdown code block 提取
            m = re.search(r"```json\s*\n([\s\S]*?)\n```", raw)
            if m:
                data = json.loads(m.group(1))
                items = data.get("items", [])
            else:
                items = []

        yield f"  ✅ 共提取 **{len(items)}** 条硬性要求\n\n"

        # 输出结果 JSON
        yield "## 📋 审查清单\n```json\n" + json.dumps(items, ensure_ascii=False, indent=2) + "\n```\n"

    # ──────────────────────────────────────────────────────────────────────
    # 合规核查 — Step 2: 用审查清单核查投标书
    # ──────────────────────────────────────────────────────────────────────

    async def compliance_check_stream(
        self,
        pdf_path: str,
        checklist: list[ChecklistItem],
        use_cache: bool = True,
    ) -> AsyncGenerator[str, None]:
        """
        合规核查：对投标书逐字段做 RAG→Policy→Critic 提取循环（同信息提取），
        提取确认后再与招标要求比对，判定是否合规。
        """
        yield "🔍 开始核查投标书...\n\n"

        # Step 1: 文档预处理
        yield "📄 **Step 1/3: 投标书预处理**\n"
        pages_data_obj = process_pdf(pdf_path, self.pages_dir)
        pages = pages_data_obj["pages"]
        total = pages_data_obj["total_pages"]
        yield f"  ✅ 共识别 **{total}** 页\n\n"

        # Step 2: RAG 索引（投标书）
        rag_mode = self.rag_cfg.get("mode", "flat")
        mode_label = "父子分块检索" if rag_mode == "parent_child" else "扁平向量检索"
        yield f"🧮 **Step 2/3: 构建检索索引（{mode_label}）**\n"
        rag = from_cache = None
        async for item in self._build_rag_with_progress(pages, pdf_path, use_cache):
            if isinstance(item, str):
                yield item
            else:
                rag, from_cache = item
        if rag_mode == "parent_child":
            ntotal = rag.child_index.ntotal if rag.child_index else 0
        else:
            ntotal = rag.index.ntotal if rag.index else 0
        if from_cache:
            yield f"  ⚡ 命中缓存，跳过 Embedding（共 **{ntotal}** 个向量）\n\n"
        else:
            yield f"  ✅ 索引构建完成：**{ntotal}** 个文本块\n\n"

        # Step 3: 逐字段提取（投标书）+ 合规判定
        yield f"🔎 **Step 3/3: 逐字段核验**（共 {len(checklist)} 项）\n\n"

        result_items = []
        total_fields = len(checklist)

        for idx, cl_item in enumerate(checklist, 1):
            key = cl_item.key
            tender_req = cl_item.requirement

            yield f"🔑 开始处理「{key}」({idx}/{total_fields})\n"

            # ── A: RAG → Policy 从投标书提取该字段 ──
            yield f"  → Policy 提取（投标书）...\n"
            hits = await rag.search(key, top_k=self.field_top_k)
            bid_item: ExtractionItem | None = None
            has_thinking = False
            async for tag, payload in self.policy_agent.extract_single_stream(key, hits):
                if tag == "thinking":
                    yield f"  💭 {payload}"
                    has_thinking = True
                elif tag == "result":
                    bid_item = payload
            if has_thinking:
                yield "\n"

            if bid_item is None:
                result_items.append({
                    "key": key, "requirement": tender_req,
                    "response": "未找到", "verdict": "fail",
                    "reason": "投标书中未能提取到相关内容", "source_page": None,
                })
                yield f"⚠️ 「{key}」未能提取，跳过\n\n"
                await asyncio.sleep(0)
                continue

            conf_pct = int(bid_item.confidence * 100)
            disp_val = (bid_item.value or "未找到").replace("\n", " ")
            disp_page = bid_item.source_page or 0
            yield (
                f"  - 「{key}」= **{disp_val}**"
                f"  (第{disp_page}页, 置信度:{conf_pct}%)\n"
            )
            await asyncio.sleep(0)

            # ── B: Critic 核验投标书提取值（同信息提取流程）──
            pages_to_check_r1 = [bid_item.source_page] if bid_item.source_page else []
            fb = CriticFeedback(key=key, verified=False, comment="无来源页码")
            if pages_to_check_r1:
                yield f"  → Critic 核验 source_page (第{bid_item.source_page}页)...\n"
                fb = await self.critic_agent.verify_single(
                    bid_item, pages, pages_to_check_r1,
                    rag_context=_format_rag_context(hits),
                )
                if fb.verified:
                    bid_item.verified = True
                    yield f"  ✅ 「{key}」: {fb.comment or '核验通过'}\n"
                else:
                    actual = fb.actual_value or "未知"
                    yield f"  ❌ 「{key}」: 图片显示为 **{actual}**（{fb.comment}）\n"

            fb_r1 = fb

            # 轮 2：扩展页核验
            if not bid_item.verified:
                rag_pages = sorted({h["page_num"] for h in hits})
                extended_pages = [p for p in rag_pages if p not in pages_to_check_r1]
                if extended_pages:
                    yield f"  → Critic 扩展核验 {extended_pages}...\n"
                    fb = await self.critic_agent.verify_single(
                        bid_item, pages, extended_pages,
                        rag_context=_format_rag_context(hits),
                    )
                    if fb.verified:
                        yield f"  → 回传轮1反馈，Policy 重新提取...\n"
                        has_t = False
                        async for tag, payload in self.policy_agent.extract_single_stream(
                            key, hits, prev_item=bid_item, feedback=fb_r1,
                        ):
                            if tag == "thinking":
                                yield f"  💭 {payload}"; has_t = True
                            elif tag == "result":
                                bid_item = payload
                        if has_t:
                            yield "\n"
                        yield (
                            f"  - 「{key}」= **{(bid_item.value or '未找到').replace(chr(10),' ')}**"
                            f"  (第{bid_item.source_page or 0}页, 置信度:{int(bid_item.confidence*100)}%)\n"
                        )
                        all_pages_r2 = pages_to_check_r1 + extended_pages
                        fb = await self.critic_agent.verify_single(
                            bid_item, pages, all_pages_r2,
                            rag_context=_format_rag_context(hits),
                        )
                        if fb.verified:
                            bid_item.verified = True
                            yield f"  ✅ 「{key}」: 轮2核验通过\n"
                        else:
                            yield f"  ❌ 「{key}」: 轮2仍未通过\n"

            # 轮 3：重写检索词
            if not bid_item.verified and self.max_iterations >= 3:
                try:
                    yield f"  → 重新生成 RAG 检索词...\n"
                    new_queries = await self._refine_rag_queries(key, fb, rag_hits=hits)
                    yield f"  → 新检索词: {new_queries}\n"
                    new_hits: list[dict] = []
                    seen_texts: set[str] = set()
                    for q in new_queries:
                        for h in await rag.search(q, top_k=self.field_top_k):
                            if h["text"] not in seen_texts:
                                seen_texts.add(h["text"])
                                new_hits.append(h)
                    hits = new_hits  # 更新 hits，供后续合规判定使用
                    yield f"  → Policy 重新提取...\n"
                    has_t = False
                    async for tag, payload in self.policy_agent.extract_single_stream(
                        key, new_hits, prev_item=bid_item, feedback=fb,
                    ):
                        if tag == "thinking":
                            yield f"  💭 {payload}"; has_t = True
                        elif tag == "result":
                            bid_item = payload
                    if has_t:
                        yield "\n"
                    yield (
                        f"  - 「{key}」= **{(bid_item.value or '未找到').replace(chr(10),' ')}**"
                        f"  (第{bid_item.source_page or 0}页, 置信度:{int(bid_item.confidence*100)}%)\n"
                    )
                    pages_r3 = [bid_item.source_page] if bid_item.source_page else []
                    if pages_r3:
                        fb = await self.critic_agent.verify_single(
                            bid_item, pages, pages_r3,
                            rag_context=_format_rag_context(new_hits),
                        )
                        if fb.verified:
                            bid_item.verified = True
                            yield f"  ✅ 「{key}」: 第3轮核验通过\n"
                        else:
                            yield f"  ❌ 「{key}」: 第3轮仍未通过\n"
                except Exception as e:
                    logger.error("合规核查第3轮「%s」异常: %s", key, e, exc_info=True)

            verified_label = "✅" if bid_item.verified else "⚠️（未核验通过）"
            yield f"{verified_label} 「{key}」投标书提取完成\n"

            # ── C: 合规判定：投标书提取值 vs 招标要求 ──
            bid_response = bid_item.value or "未找到"
            judgment = {"verdict": "warn", "reason": "判定失败，请人工审核"}
            if bid_response != "未找到" and tender_req:
                yield f"  → 合规判定...\n"
                try:
                    msgs = build_single_compliance_prompt(key, tender_req, bid_response, hits)
                    raw = await self.policy_llm.chat(msgs, json_mode=True, max_tokens=512)
                    data = json.loads(raw)
                    judgment = {
                        "verdict": data.get("verdict", "warn"),
                        "reason": data.get("reason", "无法判定"),
                    }
                except Exception as e:
                    logger.error("合规判定「%s」异常: %s", key, e, exc_info=True)
            elif not tender_req:
                judgment = {"verdict": "warn", "reason": "招标要求为空，请手动填写描述后重试"}

            v = judgment["verdict"]
            emoji = "✅" if v == "pass" else "❌" if v == "fail" else "⚠️"
            yield f"  {emoji} 判定: {v} — {judgment['reason'][:60]}\n"
            yield f"{'✅' if v == 'pass' else '⚠️' if v == 'warn' else '❌'} 「{key}」处理完成\n\n"

            result_items.append({
                "key": key,
                "requirement": tender_req,
                "response": bid_response,
                "verdict": v,
                "reason": judgment["reason"],
                "source_page": bid_item.source_page,
                "source_text": bid_item.source_text,
            })
            await asyncio.sleep(0)

        # 统计
        pass_count = sum(1 for it in result_items if it.get("verdict") == "pass")
        fail_count = sum(1 for it in result_items if it.get("verdict") == "fail")
        warn_count = sum(1 for it in result_items if it.get("verdict") == "warn")
        yield f"📊 核查完成：**{pass_count}** 合规 / **{fail_count}** 不合规 / **{warn_count}** 需确认\n\n"

        yield "## 📋 合规核查报告\n```json\n" + json.dumps({"items": result_items}, ensure_ascii=False, indent=2) + "\n```\n"

    # ──────────────────────────────────────────────────────────────────────
    # 合规核查 — 单字段再评估（带人工补充信息）
    # ──────────────────────────────────────────────────────────────────────

    async def reevaluate_compliance_field_stream(
        self,
        pdf_path: str,
        field_key: str,
        requirement: str,
        current_response: str,
        current_verdict: str,
        current_reason: str,
        additional_context: str,
        use_cache: bool = True,
    ) -> AsyncGenerator[str, None]:
        """结合用户补充信息，对单个合规字段重新判定。

        流式输出分析过程 + 最终 ```json``` 代码块（前端解析 JSON 更新卡片）。
        """
        yield f"🔎 正在重新核查「{field_key}」\n\n"

        # 1) PDF + RAG
        pages_data_obj = process_pdf(pdf_path, self.pages_dir)
        pages = pages_data_obj["pages"]

        rag = from_cache = None
        async for item in self._build_rag_with_progress(pages, pdf_path, use_cache):
            if isinstance(item, str):
                yield item
            else:
                rag, from_cache = item

        # 2) RAG 检索该字段
        hits = await rag.search(field_key, top_k=self.field_top_k)

        # 3) 构建 prompt 并流式输出
        messages = build_reeval_reasoning_prompt(
            key=field_key,
            tender_requirement=requirement,
            bid_response=current_response,
            current_verdict=current_verdict,
            current_reason=current_reason,
            bid_hits=hits,
            additional_context=additional_context,
        )
        async for tag, text in self.policy_llm.chat_stream(messages):
            if tag == "content":
                yield text

    def _format_result_json(self, result: ExtractionResult) -> str:
        data = {}
        for item in result.items:
            if item.value:
                data[item.key] = {
                    "value": item.value,
                    "source_page": item.source_page,
                    "source_text": item.source_text,
                    "verified": item.verified,
                }
            else:
                data[item.key] = None
        return "## 📋 最终结果\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n"
