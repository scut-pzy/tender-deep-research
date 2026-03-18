# tender-deep-research

招标文件深度分析系统，基于 RAG + 多模态 LLM 的迭代式要素提取与视觉审核框架。

## 架构

```
PDF 上传 → 文档解析(PyMuPDF) → 向量索引(FAISS + DeepSeek Embedding)
    → 要素提取(Qwen3-8B) → 视觉审核(Qwen-VL) → 迭代重写 → 结果输出(SSE)
```

## 目录结构

```
tender-deep-research/
├── main.py              # FastAPI 服务入口
├── config.yaml          # 模型与服务配置
├── requirements.txt     # 依赖列表
├── core/
│   ├── orchestrator.py  # 主流程编排（提取→审核→重写）
│   ├── doc_processor.py # PDF 解析与页面渲染
│   ├── rag.py           # FAISS 向量检索
│   ├── policy.py        # 要素提取与重写（Policy LLM）
│   ├── critic.py        # 视觉审核（Critic VLM）
│   └── llm_client.py    # OpenAI 兼容客户端封装
├── models/
│   └── schemas.py       # Pydantic 数据模型
├── prompts/
│   ├── extract.py       # 要素提取提示词
│   ├── critic_vision.py # 视觉审核提示词
│   └── rewrite.py       # 要素重写提示词
├── utils/
│   ├── file_handler.py  # 文件上传与缓存管理
│   └── logger.py        # 统一日志
└── cache/               # 运行时缓存（.gitignore）
    ├── uploads/
    ├── pages/
    └── vectors/
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 API Key（编辑 config.yaml）
# policy_llm.api_key: DashScope Key（Qwen3-8B）
# critic_vlm.api_key: DashScope Key（Qwen-VL）
# embedding.api_key:  DeepSeek Key

# 启动服务
python main.py
```

服务默认运行在 `http://0.0.0.0:8000`。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/upload` | 上传 PDF，返回 `file_id` |
| GET  | `/analyze/{file_id}` | SSE 流式分析，返回进度与结果 |
| GET  | `/health` | 健康检查 |

### 示例

```bash
# 上传文件
curl -X POST http://localhost:8000/upload -F "file=@招标书.pdf"
# → {"file_id": "xxxx", "total_pages": 42, ...}

# 流式分析
curl -N http://localhost:8000/analyze/xxxx
```

## 配置说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `orchestrator.max_iterations` | 3 | 最大迭代轮次 |
| `orchestrator.confidence_threshold` | 0.8 | 提前停止的置信度阈值 |
| `rag.chunk_size` | 800 | 文本块大小（字符） |
| `rag.top_k` | 5 | 每次检索返回的块数 |
