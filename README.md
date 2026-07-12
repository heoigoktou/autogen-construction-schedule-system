# AutoGen Schedule System

面向施工项目进度计划生成、动态调整和可视化展示的多 Agent 协同系统。

项目现在同时提供两种使用方式：

- **CLI 命令行端**：适合本地批处理、真实案例资料生成、测试和自动化运行。
- **Web 可视化端**：适合通过浏览器上传资料、预处理、运行排程、查看日志、编辑结果、查看进度看板和下载成果。

CLI 和 Web 共用同一套核心能力：资料解析、参数提取、WBS 分解、资源配置、约束校核、CPM 分析、动态事件响应、调整方案生成和 Excel 公共黑板。

## 目录结构

```text
config/                 # 模型、路径、Agent、事件订阅配置
data/
  blackboard/           # 本地 Excel 黑板，提交时只保留 .gitkeep
  input_docs/           # CLI 默认资料输入目录
  templates/            # 参数检查清单模板
  web/                  # Web 任务数据，提交时只保留 .gitkeep
docs/
  web_deployment.md     # Web 部署补充说明
  workflow/             # Agent I/O、接口契约、运行手册
outputs/                # CLI 运行输出，默认不提交
src/
  agents/               # Agent 定义
  agentchat_runtime/    # AutoGen AgentChat 编排
  blackboard/           # Excel 公共黑板读写与 schema
  communication/        # 轻量消息路由 demo
  tools/                # 资料解析、排程、资源、校验、可视化工具
  webapp/               # Web 可视化端
  main_real_case_workflow.py
  main_generate_demo.py
  main_event_demo.py
  main_initial_schedule.py
  visualize_schedule.py
```

## 环境要求

- Python 3.10 或更高版本
- Windows、macOS 或 Linux
- 可用的大模型 API Key，真实案例流程不会在缺少模型或密钥时回退到固定模板
- 如果要处理 DWG 文件，Windows 上可选安装 ODA File Converter

## 安装

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## 配置环境变量

复制环境变量模板：

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

常用模型配置：

| 变量 | 说明 |
|---|---|
| `MODEL_PROVIDER` | 模型供应商类型，默认 `openai_compatible` |
| `MOONSHOT_API_KEY` | Moonshot/Kimi 密钥，存在时优先使用 |
| `OPENAI_API_KEY` | OpenAI 或 OpenAI-compatible 密钥 |
| `OPENAI_MODEL` | 模型名称，默认 `kimi-k2.6` |
| `OPENAI_BASE_URL` | OpenAI-compatible `/v1` 地址 |
| `MODEL_API_STYLE` | `chat_completions` 或 `responses` |
| `KIMI_DISABLE_THINKING` | Kimi 模型是否关闭 thinking 输出 |
| `OPENAI_TIMEOUT_SECONDS` | 模型调用超时时间 |
| `OPENAI_MAX_RETRIES` | 模型调用重试次数 |

不要提交 `.env`、API Key、控制台截图或包含密钥的日志。

## CLI 端使用方法

### 1. 准备资料

默认把项目资料放到：

```text
data/input_docs/
```

CLI 支持读取 `.txt`、`.md`、`.csv`、`.xlsx`、`.xlsm`、`.docx`、`.doc`、`.pdf`、`.dxf`、`.dwg` 等资料。真实项目资料默认不提交到 Git。

### 2. 运行真实案例流程

```bash
python src/main_real_case_workflow.py
```

常用参数：

```bash
python src/main_real_case_workflow.py --input-dir data/input_docs
python src/main_real_case_workflow.py --skip-visualizations
python src/main_real_case_workflow.py --no-archive
python src/main_real_case_workflow.py --install-oda-if-missing
```

参数说明：

| 参数 | 说明 |
|---|---|
| `--input-dir` | 覆盖默认资料目录 |
| `--skip-visualizations` | 跳过运行后的甘特图、CPM、资源图生成 |
| `--no-archive` | 跳过运行前自动归档 |
| `--install-oda-if-missing` | 遇到 DWG 且缺少 ODA Converter 时尝试自动安装 |
| `--dwg-timeout-seconds` | DWG 转换超时时间，默认 120 秒 |

真实案例流程会读取资料、调用 AgentChat 团队、生成草稿、执行校验和修复，并在通过校验后写入 Excel 黑板与输出目录。

### 3. 生成补充排程和可视化

基于已有黑板生成初始进度计划、CPM 和报表素材：

```bash
python src/main_initial_schedule.py
```

从当前黑板重新生成可视化：

```bash
python src/visualize_schedule.py
```

使用内置示例数据生成可视化：

```bash
python src/visualize_schedule.py --demo-data
```

### 4. 本地 demo 和通信检查

这些命令主要用于检查轻量消息路由和本地状态，不替代真实 AgentChat 生产流程：

```bash
python src/main_generate_demo.py
python src/main_event_demo.py
```

### 5. CLI 输出位置

| 路径 | 内容 |
|---|---|
| `data/blackboard/real_case_blackboard.xlsx` | 真实案例公共黑板 |
| `outputs/real_case/schedule/` | WBS、资源、进度、CPM、网络关系等 Excel 成果 |
| `outputs/real_case/report_assets/` | 报告素材 |
| `outputs/real_case/visualizations/` | 甘特图、网络图、资源图等可视化输出 |
| `outputs/demo/` | demo 检查输出 |

## Web 端部署方法

Web 端是一个 FastAPI 单用户内部应用，入口是 `webapp.app:app`。它复用 CLI 的核心排程逻辑，任务数据保存在：

```text
data/web/jobs/
```

### 1. 安装依赖

```bash
python -m pip install -e ".[dev]"
```

### 2. 配置 Web 登录

Web 登录变量必须进入 uvicorn 进程环境。

macOS / Linux:

```bash
export WEB_USERNAME=admin
export WEB_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export WEB_PASSWORD_HASH="$(python -c 'from webapp.auth import make_password_hash; print(make_password_hash("change-me"))')"
export WEB_MAX_UPLOAD_SIZE=100MB
```

Windows PowerShell:

```powershell
$env:WEB_USERNAME = "admin"
$env:WEB_SECRET_KEY = (python -c "import secrets; print(secrets.token_urlsafe(48))")
$env:WEB_PASSWORD_HASH = (python -c "from webapp.auth import make_password_hash; print(make_password_hash('change-me'))")
$env:WEB_MAX_UPLOAD_SIZE = "100MB"
```

同时确保模型相关变量也已配置好，例如 `MOONSHOT_API_KEY` 或 `OPENAI_API_KEY`。

### 3. 本地启动

```bash
uvicorn webapp.app:app --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000/login
```

### 4. 服务器部署

Linux 服务器可使用 systemd。示例：

```ini
[Unit]
Description=Construction Schedule Web
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/autogen-construction-schedule-system
EnvironmentFile=/opt/autogen-construction-schedule-system/.env
ExecStart=/opt/autogen-construction-schedule-system/.venv/bin/uvicorn webapp.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

生产环境建议放在 Nginx/Caddy 等反向代理后面，并启用 HTTPS。该 Web 应用是内部单用户工具，不建议直接暴露到公网。

## Web 端使用方法

1. 打开 `/login`，使用 `WEB_USERNAME` 和对应密码登录。
2. 进入 `/jobs` 查看任务列表。
3. 点击“新建任务”，上传施工资料。Web 当前支持 `.txt`、`.md`、`.csv`、`.xlsx`、`.docx`、`.pdf`。
4. 创建任务后进入任务详情页。
5. 点击“资料预处理”，系统会提取参数、资源、工期、风险事件等候选信息。
6. 如果预处理发现必需参数缺失，在页面中补充后重新预处理。
7. 点击“运行排程”，Web 会在后台调用真实案例流程。
8. 在任务详情页查看运行状态、日志和输出文件。
9. 点击“进度看板”查看甘特图、关键线路、资源负荷和对比数据。
10. 点击“编辑排程”可修改 WBS、资源计划或添加调整事件，再重新计算。
11. 点击“下载全部”导出该任务的主要成果。

Web 任务运行过程中会生成上传文件、黑板、日志、预处理结果和排程成果。这些内容都位于 `data/web/jobs/`，默认不会提交到 Git。

## 测试

运行全部测试：

```bash
python -m pytest
```

运行 Web 服务相关测试：

```bash
python -m pytest src/tests/test_webapp_services.py
```

代码格式与静态检查：

```bash
python -m black src
python -m ruff check src
```

## Git 协作约定

- `main` 只放稳定版本，合并后应同时保留 CLI 和 Web 能力。
- CLI 新功能从 `main` 拉分支，命名为 `feature/cli-xxx`。
- Web 新功能从 `main` 拉分支，命名为 `feature/web-xxx`。
- Bug 修复可用 `fix/xxx`。
- 所有功能分支通过 Pull Request 合并回 `main`，避免直接推送到 `main`。

## License

MIT License. See `LICENSE`.
