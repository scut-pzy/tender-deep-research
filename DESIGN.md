# Tender Deep Research — 系统设计文档

> 面向新成员的系统理解指南，重点说明架构、流程和关键设计决策。

---

## 一、系统定位

从投标/招标 PDF 文件中精准提取关键要素（或做合规核查），通过 **RAG + Policy LLM + Critic VLM** 的多轮迭代交叉验证，解决纯 LLM 提取的幻觉和精度问题，并暴露 OpenAI 兼容 API，可对接 LobeChat / Open WebUI 等任意前端。

---

## 二、目录结构

```
tender-deep-research/
├── main.py                  # FastAPI 服务入口，所有 HTTP 路由
├── config.yaml              # 全局配置（模型、RAG、流程参数）
├── .env                     # API Key（不提交 git）
├── requirements.txt         # Python 依赖（注意 faiss-cpu 需单独安装）
│
├── core/
│   ├── orchestrator.py      # 主控调度器，串联全部流程
│   ├── llm_client.py        # LLM/VLM/Embedding API 调用封装
│   ├── doc_processor.py     # PDF 解析 + 页面 300DPI 渲染
│   ├── rag.py               # FAISS 向量检索引擎（flat + parent-child 两种模式）
│   ├── policy.py            # Policy Agent：文本提取与迭代重写
│   └── critic.py            # Critic Agent：VLM 视觉核验
│
├── models/
│   └── schemas.py           # 全部 Pydantic 数据模型（OpenAI 兼容 + 内部类型）
│
├── prompts/
│   ├── extract.py           # 批量首次提取 prompt
│   ├── extract_single.py    # 单字段提取 prompt（含上轮反馈）
│   ├── rewrite.py           # 迭代重写 prompt
│   ├── refine_query.py      # RAG 检索词重构 prompt
│   ├── checklist.py         # 审查清单提取 prompt
│   ├── compliance_judge.py  # 合规判定 prompt
│   ├── critic_vision.py     # Critic VLM 核验 prompt
│   └── chat_qa.py           # 自由问答 prompt
│
├── utils/
│   ├── config_loader.py     # config.yaml 加载 + ${VAR} 环境变量展开
│   ├── file_handler.py      # 文件上传/下载（MD5 去重缓存）+ Word→PDF 转换
│   └── logger.py            # 统一日志格式
│
├── web/                     # 前端静态文件（单页应用）
│   ├── index.html
│   ├── css/
│   └── js/                  # api.js / app.js / chat.js / stream-parser.js 等
│
└── cache/                   # 运行时缓存（已 gitignore）
    ├── uploads/             # 上传的 PDF 文件（以内容 MD5 命名）
    ├── pages/               # PDF 页面 PNG 图片（按 PDF MD5 分目录）
    └── vectors/             # FAISS 向量索引（.pkl，按参数组合哈希命名）
```

---

## 三、系统架构概览

```
                        ┌─────────────────────────────────────────┐
                        │              main.py (FastAPI)           │
                        │  /v1/chat/completions  (提取 / 对话)     │
                        │  /v1/compliance/*      (合规核查)        │
                        │  /v1/files/*           (文件管理)        │
                        └──────────────────┬──────────────────────┘
                                           │
                              ┌────────────▼────────────┐
                              │      Orchestrator        │
                              │  (core/orchestrator.py)  │
                              └──┬──────┬──────┬────────┘
                                 │      │      │
                  ┌──────────────▼─┐  ┌─▼───┐ ┌▼────────────┐
                  │  doc_processor │  │ RAG │ │ Policy/Critic│
                  │  (PDF解析渲染) │  │引擎 │ │   Agent      │
                  └────────────────┘  └─────┘ └─────────────┘
                                         │            │
                              EmbeddingClient    LLMClient / VLMClient
                              (文本向量化)       (llm_client.py)
```

---

## 四、核心流程：信息提取（最重要）

**触发**：`POST /v1/chat/completions`，`mode="extract"`

### 4.1 三步主流程

```
Step 1  doc_processor.process_pdf()
        ├─ PyMuPDF 提取每页文本（供 RAG 使用）
        └─ 每页渲染 300 DPI PNG → base64（供 Critic VLM 使用）
           缓存路径：cache/pages/<PDF_MD5>/page_N.png

Step 2  RAG 索引构建（两种模式，由 config.yaml 的 rag.mode 控制）
        ├─ flat：滑动窗口分块 → Embedding → FAISS IndexFlatIP
        └─ parent_child：先切大块（父）再切小块（子），只向量化子块，检索返回父块
           缓存路径：cache/vectors/<hash>.pkl / <hash>_pc.pkl
           第二次处理同一 PDF 直接加载缓存，跳过 Embedding

Step 3  逐字段三轮迭代提取（run_stream()）
        对每个要素：
          → 轮1: RAG 检索 → Policy 提取 → Critic 核验 source_page（最快路径）
                 ✅ 核验通过 → 收敛
          → 轮2: 扩展 Critic 核验到 RAG 涉及的全部页面
                 ✅ 扩展页命中 → 回传轮1反馈让 Policy 修正值 → 核验新值
          → 轮3: LLM 生成新 RAG 检索词 → 重新检索 → 重新提取 → 核验
          全部3轮失败 → 标记 verified=false，继续下一字段
```

### 4.2 三轮迭代设计意图

| 轮次 | 策略 | 解决的问题 |
|------|------|-----------|
| 轮1 | 只核验 source_page | Policy 提取了正确的值和页码，直接通过（大多数字段） |
| 轮2 | 扩展到全部 RAG 相关页 | source_page 准确但值有笔误，或真实值在相邻页 |
| 轮3 | 重新生成检索词 | 语义检索偏差：原始检索词未命中关键段落 |

**轮2 的关键设计**：若轮2在扩展页发现"值存在于文档"（Critic 通过），但轮1已确认"Policy 的值是错的"，则回传轮1反馈（而非轮2的泛化反馈）给 Policy 重写——因为问题是提取值错误，而非检索偏差。

### 4.3 数据流简图

```
keys + pdf_path
    │
    ├─ RAG.search(key) ──────────► hits: [{text, page_num, score}, ...]
    │                                         │
    ├─ PolicyAgent.extract_single_stream() ◄──┘
    │      ├─ LLM 流式生成
    │      └─ yield ("thinking", ...) | ("result", ExtractionItem)
    │                                         │
    └─ CriticAgent.verify_single() ◄──────────┘
           ├─ VLM 看页面 PNG + 提取值
           └─ return CriticFeedback(verified, actual_value, comment)
```

---

## 五、核心流程：合规核查

**分两阶段，需分别调用两个接口**：

### 阶段一：生成审查清单
`POST /v1/compliance/checklist`（招标书 file_id）

```
RAG 检索预设主题（资质条件、保证金、技术要求等10个话题）
    └─ Policy LLM 从检索结果中提取硬性要求
    └─ 返回 ChecklistItem 列表（key、requirement、category、source_page）
```

### 阶段二：逐项合规核查
`POST /v1/compliance/check`（投标书 file_id + checklist）

```
对每个 ChecklistItem：
    1. 从投标书 RAG 检索相关内容
    2. Policy 提取投标书中的对应响应（复用信息提取三轮逻辑）
    3. Critic VLM 核验提取值
    4. Policy LLM 比对招标要求 vs 投标响应，判定 pass/fail/warn
    └─ 返回 ComplianceItem 列表（含 verdict 和 reason）
```

**可选：单字段重新判定**
`POST /v1/compliance/reevaluate`：用户补充说明后，仅对一个字段重新调用合规判定 LLM，无需重跑全流程。

---

## 六、核心流程：自由问答

`POST /v1/chat/completions`，`mode="chat"`

```
question + file_id
    │
    ├─ doc_processor（利用已缓存的页面）
    ├─ RAG.search(question)
    └─ build_chat_qa_prompt(question, rag_context, context_data)
           └─ LLM 流式生成回答
```

`context_data` 可传入之前的提取结果（dict）或合规报告（list），LLM 会结合已知分析回答。当传入合规报告时，LLM 可在回答末尾输出 `{"updates": [...]}` JSON，前端解析后自动更新合规报告中的字段。

---

## 七、关键组件说明

### 7.1 Policy LLM（`core/policy.py`）
- 模型：Qwen-plus（默认），支持任何 OpenAI 兼容 API
- 职责：文本理解和结构化提取
- 三个工作模式：
  - `extract()`：批量首次提取所有字段
  - `extract_single_stream()`：单字段流式提取（支持 DeepSeek-R1 等推理模型的 thinking token）
  - `rewrite()`：根据 Critic 反馈批量重写失败字段

### 7.2 Critic VLM（`core/critic.py`）
- 模型：Qwen-VL-Max（默认）
- 职责：视觉核验，将提取值与 PDF 页面截图对比
- 关键设计：
  - 每次 VLM 调用传入整页 PNG（300 DPI，约 2MB），而非裁剪区域
  - 同一页的多个字段合并为一次调用（节省 VLM quota）
  - 默认重试10次、间隔10s（VLM API 比 LLM 不稳定，需高容错）
  - 核验标准：允许摘要/简写，但数字/金额/日期必须精确匹配

### 7.3 RAG 引擎（`core/rag.py`）
两种模式，由 `config.yaml → rag.mode` 控制：

| 模式 | 分块策略 | 优点 | 缺点 |
|------|----------|------|------|
| `flat` | 滑动窗口，每块 512 字符 | 简单、速度快 | 上下文窗口小，可能截断关键信息 |
| `parent_child` | 子块（256字）检索，返回父块（1024字） | 检索精度高，上下文完整 | 索引体积更大，构建略慢 |

技术实现：向量用 L2 归一化后做内积（等价于余弦相似度），使用 FAISS `IndexFlatIP`。

### 7.4 核心数据结构（`models/schemas.py`）

```python
ExtractionItem       # Policy 提取结果
  key, value, source_page, source_text, confidence, verified

CriticFeedback       # Critic 核验结果
  key, verified, actual_value, comment

ExtractionResult     # run()/run_stream() 最终返回
  items: list[ExtractionItem], total_iterations, converged

ChecklistItem        # 招标书审查清单中的一条
  key, requirement, category, source_page, source_text

ComplianceItem       # 合规核查中的一条结果
  key, requirement, response, verdict (pass/fail/warn), reason
```

---

## 八、缓存机制

系统有三层本地缓存，均以 MD5 哈希作为 key：

| 缓存类型 | 路径 | Key 构成 | 失效策略 |
|---------|------|----------|---------|
| 页面 PNG | `cache/pages/<PDF_MD5>/page_N.png` | PDF 内容 MD5 | 手动删除目录 |
| 向量索引 | `cache/vectors/<hash>.pkl` | PDF_MD5 + 模型 + 分块参数 | 手动删除 |
| 下载文件 | `cache/uploads/<URL_MD5>.pdf` | URL MD5 | 手动删除 |
| 上传文件 | `cache/uploads/<content_MD5>.pdf` | 文件内容 MD5 | DELETE API 或手动 |

更改 `config.yaml` 中的 `rag.mode`、`chunk_size` 等参数后，向量索引会自动重建（因为哈希变了）；**页面 PNG 不受影响**（只和 PDF 内容相关）。

---

## 九、API 接口速查

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/chat/completions` | 信息提取（extract）或自由问答（chat） |
| POST | `/v1/files` | 上传 PDF/Word，返回 file_id |
| GET  | `/v1/files/list` | 列出已上传文件（可按 type 过滤） |
| DELETE | `/v1/files/{id}` | 删除文件 |
| POST | `/v1/compliance/checklist` | 从招标书生成审查清单 |
| POST | `/v1/compliance/check` | 对投标书做合规核查 |
| POST | `/v1/compliance/reevaluate` | 单字段人工补充后重新判定 |
| GET  | `/v1/history` | 历史记录列表 |
| GET  | `/v1/history/{id}` | 单条历史记录详情 |
| GET  | `/v1/models` | 模型列表（OpenAI 兼容占位） |
| GET  | `/health` | 健康检查 |

---

## 十、配置参数参考

```yaml
# config.yaml 关键参数说明

rag:
  mode: "parent_child"      # "flat" 或 "parent_child"，推荐 parent_child
  top_k: 5                  # 每个字段检索返回的块数，值越大覆盖越广但 LLM 输入越长

pipeline:
  max_iterations: 3         # 每字段最多迭代轮数，减小可加速但降低准确率
  field_top_k: 5            # 与 rag.top_k 独立，run_stream 单字段检索用这个值

policy_llm:
  model: "qwen-plus"        # 可替换为任何 OpenAI 兼容模型（如 gpt-4o、deepseek-chat）
  temperature: 0.3          # 提取任务推荐低温，减少随机性

critic_vlm:
  model: "qwen-vl-max"      # 必须支持图片输入的模型
  temperature: 0.1          # 核验任务要求确定性，建议 0.1

embedding:
  model: "text-embedding-v4"
  dimensions: 1024          # 必须与模型实际输出维度一致
```

---

## 十一、SSE 流式输出格式

所有流式接口均使用 OpenAI SSE 格式（`text/event-stream`），每帧：
```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"delta":{"content":"文本片段"}}]}

data: [DONE]
```

前端的 `stream-parser.js` 负责解析这些文本帧，将 Markdown 中的结构（标题、表情符号状态标记、JSON 代码块）转换为结构化事件，分发给侧边栏和聊天面板渲染。
