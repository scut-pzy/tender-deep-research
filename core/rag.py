"""基于 FAISS 的 RAG 语义检索引擎。"""
import pickle
from pathlib import Path

import faiss
import numpy as np

from core.llm_client import EmbeddingClient
from utils.logger import get_logger

logger = get_logger(__name__)

_EMBED_BATCH = 10  # 每批向量化的文本块数（text-embedding-v4 限制最多10条/批）


class RAGEngine:
    def __init__(self, embed_client: EmbeddingClient, top_k: int = 5):
        self.embed_client = embed_client
        self.top_k = top_k
        self.dimensions = embed_client.dimensions
        self.index: faiss.IndexFlatIP | None = None
        self.chunks: list[dict] = []

    async def _embed_batched(self, texts: list[str], on_progress=None) -> np.ndarray:
        """分批调用 embedding API，L2 归一化后返回。

        Args:
            on_progress: 可选回调 (done, total) -> None，每批完成后调用。
        """
        all_vecs = []
        total = len(texts)
        for i in range(0, total, _EMBED_BATCH):
            batch = texts[i: i + _EMBED_BATCH]
            vecs = await self.embed_client.embed(batch)
            all_vecs.extend(vecs)
            if on_progress:
                on_progress(min(i + len(batch), total), total)

        arr = np.array(all_vecs, dtype=np.float32)
        faiss.normalize_L2(arr)     # 归一化后内积 == 余弦相似度
        return arr

    async def build_index(self, chunks: list[dict], on_progress=None) -> None:
        """从文本块列表构建 FAISS 内积索引。"""
        self.chunks = chunks
        texts = [c["text"] for c in chunks]
        logger.info("构建向量索引，共 %d 个文本块（批大小 %d）…", len(texts), _EMBED_BATCH)
        vecs = await self._embed_batched(texts, on_progress)
        self.index = faiss.IndexFlatIP(self.dimensions)
        self.index.add(vecs)
        logger.info("索引构建完成")

    async def search(self, query: str, top_k: int | None = None) -> list[dict]:
        """检索与 query 最相关的 top_k 个文本块。"""
        if self.index is None:
            raise RuntimeError("索引尚未构建，请先调用 build_index()")
        k = top_k or self.top_k
        q_arr = await self._embed_batched([query])
        scores, indices = self.index.search(q_arr, k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            chunk = dict(self.chunks[idx])
            chunk["score"] = float(score)
            results.append(chunk)
        return results

    def save(self, vectors_dir: str, cache_key: str) -> None:
        """将 FAISS 索引和 chunks 序列化到磁盘。"""
        path = Path(vectors_dir) / f"{cache_key}.pkl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "index": faiss.serialize_index(self.index),
                "chunks": self.chunks,
            }, f)
        logger.info("索引已保存: %s", path)

    def load(self, vectors_dir: str, cache_key: str) -> bool:
        """从磁盘加载索引，返回 True 表示成功，False 表示缓存不存在。"""
        path = Path(vectors_dir) / f"{cache_key}.pkl"
        if not path.exists():
            return False
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.index = faiss.deserialize_index(data["index"])
        self.chunks = data["chunks"]
        logger.info("索引从缓存加载: %s（%d 块）", path, len(self.chunks))
        return True

    async def search_for_keys(self, keys: list[str]) -> dict[str, list[dict]]:
        """对每个招标要素分别检索，返回 {key: [相关文本块, ...]} 映射。"""
        result = {}
        for key in keys:
            hits = await self.search(key)
            result[key] = hits
            pages = sorted({h["page_num"] for h in hits})
            logger.info("「%s」→ 在第 %s 页找到相关内容", key, pages)
        return result


class ParentChildRAGEngine:
    """
    父子分块检索引擎（Dify 风格）。

    原理：用子块（细粒度）做向量匹配以精确定位，
    召回时返回对应的父块（粗粒度）以保留完整上下文。
    """

    def __init__(self, embed_client: EmbeddingClient, top_k: int = 5):
        self.embed_client = embed_client
        self.top_k = top_k
        self.dimensions = embed_client.dimensions
        self.child_index: faiss.IndexFlatIP | None = None
        self.parent_chunks: list[dict] = []
        self.child_chunks: list[dict] = []

    async def _embed_batched(self, texts: list[str], on_progress=None) -> np.ndarray:
        """分批调用 embedding API，L2 归一化后返回。"""
        all_vecs = []
        total = len(texts)
        for i in range(0, total, _EMBED_BATCH):
            batch = texts[i: i + _EMBED_BATCH]
            vecs = await self.embed_client.embed(batch)
            all_vecs.extend(vecs)
            if on_progress:
                on_progress(min(i + len(batch), total), total)
        arr = np.array(all_vecs, dtype=np.float32)
        faiss.normalize_L2(arr)
        return arr

    async def build_index(
        self, parent_chunks: list[dict], child_chunks: list[dict],
        on_progress=None,
    ) -> None:
        """对子块构建 FAISS 索引，同时存储父块映射。"""
        self.parent_chunks = parent_chunks
        self.child_chunks = child_chunks
        texts = [c["text"] for c in child_chunks]
        logger.info(
            "构建父子向量索引：%d 个父块，%d 个子块（批大小 %d）…",
            len(parent_chunks), len(texts), _EMBED_BATCH,
        )
        vecs = await self._embed_batched(texts, on_progress)
        self.child_index = faiss.IndexFlatIP(self.dimensions)
        self.child_index.add(vecs)
        logger.info("父子索引构建完成")

    async def search(self, query: str, top_k: int | None = None) -> list[dict]:
        """
        检索与 query 最相关的子块，返回对应的父块（去重）。

        同一父块可能被多个子块命中，只返回一次（取最高子块得分）。
        """
        if self.child_index is None:
            raise RuntimeError("索引尚未构建，请先调用 build_index()")
        k = top_k or self.top_k
        q_arr = await self._embed_batched([query])
        # 多检索一些子块，因为去重后数量会减少
        fetch_k = min(k * 3, self.child_index.ntotal)
        scores, indices = self.child_index.search(q_arr, fetch_k)

        # 按 parent_id 去重，保留最高得分的子块对应的父块
        seen_parents: dict[int, float] = {}
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            child = self.child_chunks[idx]
            pid = child["parent_id"]
            if pid not in seen_parents or score > seen_parents[pid]:
                seen_parents[pid] = float(score)

        # 按得分排序，取 top_k 个父块
        sorted_parents = sorted(seen_parents.items(), key=lambda x: x[1], reverse=True)
        results = []
        for pid, score in sorted_parents[:k]:
            parent = dict(self.parent_chunks[pid])
            parent["score"] = score
            results.append(parent)

        return results

    def save(self, vectors_dir: str, cache_key: str) -> None:
        """将 FAISS 子块索引和父子块数据序列化到磁盘。"""
        path = Path(vectors_dir) / f"{cache_key}_pc.pkl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "child_index": faiss.serialize_index(self.child_index),
                "parent_chunks": self.parent_chunks,
                "child_chunks": self.child_chunks,
            }, f)
        logger.info("父子索引已保存: %s", path)

    def load(self, vectors_dir: str, cache_key: str) -> bool:
        """从磁盘加载索引，返回 True 表示成功。"""
        path = Path(vectors_dir) / f"{cache_key}_pc.pkl"
        if not path.exists():
            return False
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.child_index = faiss.deserialize_index(data["child_index"])
        self.parent_chunks = data["parent_chunks"]
        self.child_chunks = data["child_chunks"]
        logger.info(
            "父子索引从缓存加载: %s（%d 父块，%d 子块）",
            path, len(self.parent_chunks), len(self.child_chunks),
        )
        return True

    async def search_for_keys(self, keys: list[str]) -> dict[str, list[dict]]:
        """对每个招标要素分别检索，返回 {key: [父块, ...]} 映射。"""
        result = {}
        for key in keys:
            hits = await self.search(key)
            result[key] = hits
            pages = sorted({h["page_num"] for h in hits})
            logger.info("「%s」→ 父子检索在第 %s 页找到相关内容", key, pages)
        return result
