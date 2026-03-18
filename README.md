# tender-deep-research

招标文件深度分析系统。通过 **RAG + Policy LLM + Critic VLM** 的迭代交叉验证，从投标 PDF 中精准提取关键要素，并暴露标准 OpenAI API，可直接对接 LobeChat / Open WebUI 等前端。

## 架构

```
用户消息（要素清单 + PDF URL）
  │
  ├─ doc_processor:  PDF → 文本块 + 300DPI 页面图片(base64)
  ├─ rag:            文本块 → DeepSeek Embedding → FAISS 语义检索
  │
  └─ 迭代循环（最多3轮）
       ├─ Policy(Qwen-plus):   RAG文本 → 要素提取 / 重写
       └─ Critic(Qwen-VL):    页面图片 → 视觉核验
           └─ 全部通过 → 输出 Markdown 表格
```

## 目录结构

```
tender-deep-research/
├── main.py              # FastAPI 入口，OpenAI 兼容 API
├── config.yaml          # 全局配置（模型/RAG/流程参数）
├── requirements.txt     # Python 依赖
├── core/
│   ├── orchestrator.py  # 主控调度器
│   ├── doc_processor.py # PDF 解析 + 页面渲染
│   ├── rag.py           # RAGEngine（批量 Embedding + FAISS）
│   ├── policy.py        # PolicyAgent（提取/重写）
│   ├── critic.py        # CriticAgent（视觉核验）
│   └── llm_client.py    # httpx 封装（LLM / VLM / Embedding）
├── models/
│   └── schemas.py       # OpenAI 兼容数据模型 + 内部类型
├── prompts/
│   ├── extract.py       # Policy 提取提示词
│   ├── rewrite.py       # Policy 重写提示词
│   └── critic_vision.py # Critic 视觉核验提示词
├── utils/
│   ├── file_handler.py  # 文件上传/下载（MD5 去重缓存）
│   └── logger.py        # 统一日志
└── cache/               # 运行时缓存（已 gitignore）
    ├── uploads/         # PDF 原文件
    ├── pages/           # 页面 PNG 图片
    └── vectors/         # 预留向量缓存
```

## 快速开始

```bash
pip install -r requirements.txt
```

编辑 `config.yaml`，填入实际 API Key：

```yaml
policy_llm:
  api_key: "sk-your-dashscope-key"   # Qwen-plus

critic_vlm:
  api_key: "sk-your-dashscope-key"   # Qwen-VL-Max

embedding:
  api_key: "sk-your-deepseek-key"    # DeepSeek Embedding
```

启动服务：

```bash
python main.py
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/chat/completions` | 核心分析接口（OpenAI 兼容，支持 stream） |
| POST | `/v1/files` | 上传 PDF 文件 |
| GET  | `/v1/models` | 返回模型列表 |
| GET  | `/health` | 健康检查 |

### 对话示例

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tender-research",
    "stream": true,
    "messages": [{
      "role": "user",
      "content": "请分析 https://example.com/tender.pdf，提取：\n1. 项目名称\n2. 投标总价\n3. 工期\n4. 资质要求"
    }]
  }'
```

### 流式输出示例

```
🔍 开始分析投标文件...

📄 **Step 1/5: 文档预处理**
  ✅ 共识别 **58** 页

🧮 **Step 2/5: 构建检索索引**
  ✅ 索引构建完成：**142** 个文本块

🔎 **Step 3/5: RAG 语义检索**
  - 「项目名称」→ 在第 [1, 2] 页找到相关内容
  - 「投标总价」→ 在第 [15, 16] 页找到相关内容
  ✅ 检索完成

📝 **Step 4/5: Policy LLM 提取 (第1轮)**
  - 「项目名称」= **XX市政道路工程**  (第1页, 置信度:98%)
  - 「投标总价」= **1280万**  (第15页, 置信度:90%)

👁️ **Step 5/5: Critic VLM 视觉核验 (第1轮)**
  ✅ 「项目名称」: 确认正确
  ❌ 「投标总价」: 图片显示为 **1380万**，非提取值

🔄 有 **1** 个要素未通过，进入下一轮...

...（第2轮后全部通过）

## 📋 最终提取结果

| 要素 | 提取值 | 来源页 | 核验 |
|------|--------|--------|------|
| 项目名称 | XX市政道路工程 | 第1页 | ✅ |
| 投标总价 | 1380万 | 第15页 | ✅ |
```

## 配置说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `pipeline.max_iterations` | 3 | 最大迭代轮次 |
| `pipeline.confidence_threshold` | 0.8 | 置信度阈值（预留，当前由 Critic 决定收敛） |
| `rag.chunk_size` | 512 | 文本块大小（字符） |
| `rag.chunk_overlap` | 64 | 块间重叠字符数 |
| `rag.top_k` | 5 | 每个要素检索返回的块数 |
| `files.max_size_mb` | 100 | 上传文件大小限制 |
