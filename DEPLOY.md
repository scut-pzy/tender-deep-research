# Tender Deep Research — 部署文档

## 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Linux（Ubuntu 20.04+ 推荐）或 macOS |
| Python | **3.10+**（代码使用 `X \| Y` 联合类型注解，低于 3.10 会报语法错误）|
| 包管理 | Conda（推荐，faiss-cpu 通过 conda 安装更稳定）|
| 网络 | 可访问 `dashscope.aliyuncs.com`（境外服务器见"常见问题"中的代理配置）|

---

## 一、系统依赖（Linux）

WeasyPrint（Word→PDF 转换）需要以下系统库：

```bash
# Ubuntu / Debian
sudo apt-get update
sudo apt-get install -y \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-noto-cjk          # 中文字体（PDF 中文显示必须）
```

> **CentOS / RHEL**：将 `apt-get` 替换为：
> ```bash
> sudo yum install -y pango gdk-pixbuf2 libffi \
>     google-noto-cjk-fonts
> ```

---

## 二、创建 Conda 环境并安装依赖

```bash
# 1. 创建环境（Python 3.10）
conda create -n tender python=3.10 -y
conda activate tender

# 2. 进入项目目录
cd /path/to/tender-deep-research

# 3. 安装 faiss-cpu（requirements.txt 中已注释，需单独安装）
#    推荐通过 conda-forge 安装，在 Linux / macOS / ARM 平台均兼容：
conda install -c conda-forge faiss-cpu -y
#    如果不使用 conda，也可以直接 pip 安装（部分 ARM 平台可能失败）：
#    pip install faiss-cpu

# 4. 安装其余 Python 依赖
pip install -r requirements.txt
```

> `requirements.txt` 已锁定版本。faiss-cpu 之所以注释掉，是因为 conda-forge 版本比 PyPI 版本在各平台兼容性更好，需要单独用 conda 安装。

---

## 三、配置 API Key

在项目根目录创建 `.env` 文件：

```bash
cp .env.example .env   # 如果有模板
# 或直接创建：
cat > .env << EOF
DASHSCOPE_API_KEY=sk-你的阿里云百炼API密钥
EOF
```

> **获取 Key**：登录 [阿里云百炼控制台](https://bailian.console.aliyun.com/) → API-KEY 管理 → 创建

---

## 四、配置文件（可选）

编辑 `config.yaml` 调整参数（默认配置已可直接使用）：

```yaml
server:
  host: "0.0.0.0"
  port: 7123          # 服务端口

policy_llm:
  model: "qwen-plus"  # 提取/判定模型

critic_vlm:
  model: "qwen-vl-max"  # 视觉核验模型

embedding:
  model: "text-embedding-v4"  # 向量化模型
```

---

## 五、初始化目录

首次部署需要创建缓存和数据目录：

```bash
mkdir -p cache/uploads cache/pages cache/vectors data/history
```

---

## 六、启动服务

### 前台启动（测试用）

```bash
conda activate tender
cd /path/to/tender-deep-research
python main.py
```

或直接用 uvicorn：

```bash
uvicorn main:app --host 0.0.0.0 --port 7123
```

### 后台启动（生产推荐）

```bash
nohup uvicorn main:app --host 0.0.0.0 --port 7123 \
    --workers 1 >> logs/app.log 2>&1 &
echo $! > logs/app.pid
```

> 注意：RAG 状态存在内存中，`--workers` 必须为 **1**，否则多进程间向量索引不共享。

### 用 systemd 管理（推荐生产）

创建 `/etc/systemd/system/tender.service`：

```ini
[Unit]
Description=Tender Deep Research
After=network.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/path/to/tender-deep-research
Environment="PATH=/home/你的用户名/anaconda3/envs/tender/bin"
ExecStart=/home/你的用户名/anaconda3/envs/tender/bin/uvicorn \
    main:app --host 0.0.0.0 --port 7123
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable tender
sudo systemctl start tender
sudo systemctl status tender
```

---

## 七、验证部署

```bash
# 检查服务响应
curl http://localhost:7123/

# 检查文件上传接口
curl http://localhost:7123/v1/files/list

# 浏览器访问
http://服务器IP:7123
```

---

## 八、防火墙开放端口

```bash
# Ubuntu（ufw）
sudo ufw allow 7123/tcp

# CentOS（firewalld）
sudo firewall-cmd --permanent --add-port=7123/tcp
sudo firewall-cmd --reload
```

---

## 九、目录结构说明

```
tender-deep-research/
├── main.py              # FastAPI 入口
├── config.yaml          # 配置文件
├── .env                 # API Key（不提交 git）
├── requirements.txt     # Python 依赖
├── core/                # 核心逻辑（RAG、Policy、Critic）
├── prompts/             # Prompt 模板
├── models/              # Pydantic 数据模型
├── utils/               # 工具函数
├── web/                 # 前端静态文件
├── cache/
│   ├── uploads/         # 上传的 PDF 文件
│   ├── pages/           # PDF 页面图片缓存
│   └── vectors/         # FAISS 向量索引缓存
└── data/
    └── history/         # 历史提取记录
```

---

## 十、常见问题

**Q: 启动报 `环境变量 'DASHSCOPE_API_KEY' 未设置`**  
A: 检查项目根目录是否有 `.env` 文件，且 Key 格式正确（`DASHSCOPE_API_KEY=sk-xxx`）

**Q: Word 转 PDF 报错 `cannot load library 'libgdk_pixbuf'`**  
A: 系统缺少 weasyprint 依赖，执行第一步的系统依赖安装命令

**Q: PDF 中文显示乱码**  
A: 安装中文字体：`sudo apt-get install -y fonts-noto-cjk`

**Q: 向量索引重复构建很慢**  
A: 正常现象，同一 PDF 第二次运行会命中 `cache/vectors/` 缓存，直接跳过 Embedding

**Q: 多用户并发时响应变慢**  
A: 模型 API 为外部调用，瓶颈在网络和 LLM 推理，调大 `field_top_k` 可减少 RAG 轮数

**Q: 境外服务器无法访问 dashscope.aliyuncs.com**  
A: 系统所有 LLM/Embedding 调用均走 `httpx`，在 `.env` 中设置代理即可：
```bash
# 追加到 .env 文件
HTTP_PROXY=http://your-proxy:port
HTTPS_PROXY=http://your-proxy:port
```
下载远程 PDF（`file_url` 参数）同样经过 `httpx`，会自动使用上述代理设置。

**Q: ARM / Apple Silicon（M 系列芯片）上 faiss-cpu 安装失败**  
A: 不要使用 `pip install faiss-cpu`，改用 conda-forge：
```bash
conda install -c conda-forge faiss-cpu -y
```
