"""主流程编排器：提取→审核→重写，迭代至置信度达标。"""
import statistics
from typing import AsyncGenerator

from core.critic import critique_pages
from core.doc_processor import process_pdf
from core.llm_client import VLMClient, build_clients
from core.policy import extract_elements, rewrite_elements
from core.rag import RAGIndex
from models.schemas import AnalysisResponse, IterationResult
from utils.logger import get_logger

logger = get_logger(__name__)


class Orchestrator:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        policy_cfg = cfg["policy_llm"]
        critic_cfg = cfg["critic_vlm"]
        self.max_iterations = cfg["orchestrator"]["max_iterations"]
        self.confidence_threshold = cfg["orchestrator"]["confidence_threshold"]
        self.pages_dir = cfg["server"]["cache_dir"] + "/pages"
        self.vectors_dir = cfg["server"]["cache_dir"] + "/vectors"
        self.rag_cfg = cfg["rag"]

        from core.llm_client import LLMClient
        self.policy_llm = LLMClient(policy_cfg)
        self.critic_vlm = VLMClient(critic_cfg)

        embed_cfg = cfg["embedding"]
        import httpx
        from openai import AsyncOpenAI
        embed_client = AsyncOpenAI(
            api_key=embed_cfg["api_key"],
            base_url=embed_cfg["api_base"],
            http_client=httpx.AsyncClient(timeout=60),
        )
        self.embed_client = embed_client
        self.embed_model = embed_cfg["model"]
        self.embed_dims = embed_cfg.get("dimensions", 1024)

    async def run(self, file_id: str, pdf_path: str) -> AsyncGenerator[dict, None]:
        """主流程，yield 进度事件和最终结果。"""
        yield {"event": "start", "message": "开始处理 PDF 文档…"}

        # 1. 文档处理
        chunks, total_pages = process_pdf(pdf_path, self.pages_dir, file_id)
        yield {"event": "progress", "message": f"文档解析完成，共 {total_pages} 页，{len(chunks)} 个文本块"}

        # 2. 构建 RAG 索引
        rag = RAGIndex(
            self.embed_client,
            self.embed_model,
            self.embed_dims,
            self.rag_cfg["top_k"],
        )
        await rag.build(chunks)
        rag.save(self.vectors_dir, file_id)
        yield {"event": "progress", "message": "向量索引构建完成"}

        all_iterations: list[IterationResult] = []
        elements = []
        critic_feedback = []

        for i in range(1, self.max_iterations + 1):
            yield {"event": "iteration_start", "iteration": i, "message": f"第 {i} 轮分析开始"}

            # 3. 要素提取（首轮）或重写（后续轮）
            if i == 1:
                elements = await extract_elements(self.policy_llm, rag)
                yield {"event": "progress", "message": f"第 {i} 轮：提取 {len(elements)} 个要素"}
            else:
                elements = await rewrite_elements(self.policy_llm, rag, elements, critic_feedback)
                yield {"event": "progress", "message": f"第 {i} 轮：基于审核反馈重写要素"}

            # 4. 视觉审核
            critic_feedback = await critique_pages(
                self.critic_vlm, self.pages_dir, file_id, elements
            )
            yield {"event": "progress", "message": f"第 {i} 轮：视觉审核发现 {len(critic_feedback)} 个问题"}

            # 5. 计算整体置信度
            overall_conf = statistics.mean(e.confidence for e in elements) if elements else 0.0

            iteration_result = IterationResult(
                iteration=i,
                elements=elements,
                critic_feedback=critic_feedback,
                overall_confidence=overall_conf,
                summary=f"第 {i} 轮置信度 {overall_conf:.2f}，发现 {len(critic_feedback)} 个视觉问题",
            )
            all_iterations.append(iteration_result)
            yield {"event": "iteration_end", "iteration": i, "confidence": overall_conf}

            if overall_conf >= self.confidence_threshold:
                yield {"event": "converged", "message": f"第 {i} 轮达到置信度阈值 {self.confidence_threshold}，停止迭代"}
                break

        final = AnalysisResponse(
            file_id=file_id,
            iterations=all_iterations,
            final_summary=self._build_summary(all_iterations[-1]),
            total_pages=total_pages,
        )
        yield {"event": "done", "result": final.model_dump()}

    def _build_summary(self, last: IterationResult) -> str:
        lines = [f"招标文件分析报告（置信度 {last.overall_confidence:.0%}）", ""]
        for elem in last.elements:
            lines.append(f"【{elem.element}】{elem.value}（置信度：{elem.confidence:.0%}）")
        return "\n".join(lines)
