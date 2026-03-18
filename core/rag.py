"""基于 FAISS 的 RAG 语义检索引擎。"""
import math

import faiss
import numpy as np

from core.llm_client import EmbeddingClient
from utils.logger import get_logger

logger = get_logger(__name__)

_EMBED_BATCH = 32  # 每批向量化的文本块数


class RAGEngine:
    def __init__(self, embed_client: EmbeddingClient, top_k: int = 5):
        self.embed_client = embed_client
        self.top_k = top_k
        self.dimensions = embed_client.dimensions
        self.index: faiss.IndexFlatIP | None = None
        self.chunks: list[dict] = []

    async def _embed_batched(self, texts: list[str]) -> np.ndarray:
        """分批调用 embedding API，L2 归一化后返回。"""
        all_vecs = []
        for i in range(0, len(texts), _EMBED_BATCH):
            batch = texts[i: i + _EMBED_BATCH]
            vecs = await self.embed_client.embed(batch)
            all_vecs.extend(vecs)

        arr = np.array(all_vecs, dtype=np.float32)
        faiss.normalize_L2(arr)     # 归一化后内积 == 余弦相似度
        return arr

    async def build_index(self, chunks: list[dict]) -> None:
        """从文本块列表构建 FAISS 内积索引。"""
        self.chunks = chunks
        texts = [c["text"] for c in chunks]
        logger.info("构建向量索引，共 %d 个文本块（批大小 %d）…", len(texts), _EMBED_BATCH)
        vecs = await self._embed_batched(texts)
        self.index = faiss.IndexFlatIP(self.dimensions)
        self.index.add(vecs)
        logger.info("索引构建完成")

    async def search(self, query: str) -> list[dict]:
        """检索与 query 最相关的 top_k 个文本块。"""
        if self.index is None:
            raise RuntimeError("索引尚未构建，请先调用 build_index()")
        q_arr = await self._embed_batched([query])
        scores, indices = self.index.search(q_arr, self.top_k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            chunk = dict(self.chunks[idx])
            chunk["score"] = float(score)
            results.append(chunk)
        return results

    async def search_for_keys(self, keys: list[str]) -> dict[str, list[dict]]:
        """对每个招标要素分别检索，返回 {key: [相关文本块, ...]} 映射。"""
        result = {}
        for key in keys:
            hits = await self.search(key)
            result[key] = hits
            pages = sorted({h["page_num"] for h in hits})
            logger.info("「%s」→ 在第 %s 页找到相关内容", key, pages)
        return result
