"""基于 FAISS 的 RAG 检索模块。"""
import pickle
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from openai import AsyncOpenAI

from utils.logger import get_logger

logger = get_logger(__name__)


class RAGIndex:
    def __init__(self, embed_client: AsyncOpenAI, embed_model: str, dimensions: int, top_k: int = 5):
        self.embed_client = embed_client
        self.embed_model = embed_model
        self.dimensions = dimensions
        self.top_k = top_k
        self.index: faiss.IndexFlatIP | None = None
        self.chunks: list[dict] = []

    async def _embed(self, texts: list[str]) -> np.ndarray:
        resp = await self.embed_client.embeddings.create(
            model=self.embed_model,
            input=texts,
        )
        vecs = [item.embedding for item in resp.data]
        arr = np.array(vecs, dtype=np.float32)
        faiss.normalize_L2(arr)
        return arr

    async def build(self, chunks: list[dict]) -> None:
        """从文本块列表构建 FAISS 索引。"""
        texts = [c["text"] for c in chunks]
        logger.info("开始构建向量索引，共 %d 个文本块…", len(texts))
        vecs = await self._embed(texts)
        self.index = faiss.IndexFlatIP(self.dimensions)
        self.index.add(vecs)
        self.chunks = chunks
        logger.info("索引构建完成")

    async def search(self, query: str) -> list[dict]:
        """检索与 query 最相关的 top_k 个文本块。"""
        if self.index is None:
            raise RuntimeError("索引尚未构建，请先调用 build()")
        q_vec = await self._embed([query])
        scores, indices = self.index.search(q_vec, self.top_k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            chunk = dict(self.chunks[idx])
            chunk["score"] = float(score)
            results.append(chunk)
        return results

    def save(self, vectors_dir: str, file_id: str) -> None:
        out = Path(vectors_dir) / f"{file_id}.pkl"
        with open(out, "wb") as f:
            pickle.dump({"index": faiss.serialize_index(self.index), "chunks": self.chunks}, f)

    def load(self, vectors_dir: str, file_id: str) -> None:
        src = Path(vectors_dir) / f"{file_id}.pkl"
        with open(src, "rb") as f:
            data = pickle.load(f)
        self.index = faiss.deserialize_index(data["index"])
        self.chunks = data["chunks"]
