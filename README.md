# AutoGen Schedule System

面向施工项目进度计划生成与动态调整的多 Agent 协同系统。系统使用 AutoGen AgentChat 编排资料解析、参数提取、WBS 分解、资源配置、约束校核、动态事件响应和方案仲裁，并使用 Excel 公共黑板保存正式数据结果。

本仓库已按公开发布整理：真实项目资料、运行结果、日志、黑板表、归档和本地密钥默认不提交。

## 核心能力

- 从 `data/input_docs/` 读取项目资料，支持 `.txt`、`.md`、`.csv`、`.xlsx`、`.xlsm`、`.docx`、`.doc`、`.pdf`、`.dxf`、`.dwg`。
- 自动生成项目参数、参数检查清单、WBS、资源需求、初始进度计划、CPM 分析、网络关系、里程碑检查、约束检查、动态事件和调整方案。
- 使用 Excel 公共黑板作为正式数据交换层，表结构集中定义在 `src/blackboard/sheet_schema.py`。
- 真实案例流程必须连接可用大模型，不会在缺少模型、密钥或资料时回退到固定模板结果。
- 所有运行产物写入 `outputs/`，默认被 Git 忽略。

## 系统架构

```text
source documents
  -> document readers and deterministic extractors
  -> AutoGen SelectorGroupChat
  -> agent draft tables in memory
  -> local validation and repair loop
  -> Excel public blackboard
  -> schedule workbooks, reports, visualizations
```

真实案例主流程由 `src/main_real_case_workflow.py` 启动。它先读取资料并构造 source context，然后创建 AutoGen `SelectorGroupChat` 团队。专业 Agent 不直接写最终 Excel，而是通过工具把草稿写入内存中的 draft tables。只有最终 JSON 通过本地 schema、前置关系、CPM、资源引用和证据字段校验后，系统才把结果统一写入 Excel 黑板和输出目录。

## 通信机制

系统有两层通信机制：

| 层级 | 用途 | 关键模块 |
|---|---|---|
| AgentChat 协作层 | 真实案例端到端生产流程，由 AutoGen `SelectorGroupChat` 选择下一个发言/执行工具的 Agent | `src/agentchat_runtime/workflow.py` |
| 轻量消息路由层 | 本地 demo、通信日志和状态检查，支持 direct、broadcast、arbitration、log_only | `src/communication/router.py` |

### AgentChat 协作层

- 每个 Agent 有明确工具权限，只能写自己负责的草稿表。
- `write_blackboard_table` 在中间阶段只更新内存草稿，返回 `excel_written=false`。
- `constraint_checker_agent` 负责检查草稿问题，但不写业务成果表。
- `coordinator_agent` 负责读取所有草稿表、触发校验、组织修复，并输出 `FINAL_SCHEDULE_READY` JSON。
- 本地运行时会解析最终 JSON；如果校验失败，会要求相关 Agent 修复，或允许 `coordinator_agent` 在限制次数内修复草稿。
- 通过校验后，`write_agentchat_output` 才把正式数据写入 Excel 黑板并导出成果。

### 轻量消息路由层

轻量路由层用于 demo 和通信可追溯，不替代真实 AgentChat 生产流程。

- `direct`：点对点请求，记录请求和响应。
- `broadcast`：按事件主题向订阅 Agent 广播。
- `arbitration`：请求总控 Agent 做协调/仲裁。
- `log_only`：只记录状态，不触发 Agent 响应。

消息统一使用 `AgentMessage` 结构，字段包括 `message_id`、`sender`、`receiver`、`mode`、`event_type`、`priority`、`payload`、`status`、`related_sheet`、`related_id` 和 `timestamp`。所有路由消息会写入黑板中的 `agent_message_log`，也可以导出为 `outputs/demo/communication_log.xlsx`。

事件订阅关系在 `config/events.yaml` 中配置，例如：

- `source.documents.parsed` 通知总控、资料解析、WBS 和资源 Agent。
- `wbs.generated` 通知资源、约束和总控 Agent。
- `resource.conflict.detected` 通知资源、约束和方案仲裁 Agent。
- `dynamic.event.received` 通知动态响应、资源和约束 Agent。
- `adjustment.plan.proposed` 通知方案仲裁、约束和总控 Agent。

## Agent 职责

| Agent | 主要职责 | 主要读写对象 |
|---|---|---|
| `coordinator_agent` | 总控调度、收敛团队草稿、处理冲突、执行最终校验并发布最终 JSON | 读取全部草稿；必要时修复目标草稿；最终写入正式结果 |
| `data_parser_agent` | 读取资料上下文，抽取项目参数，维护参数检查清单 | 写 `parameter_checklist`、`project_parameters` |
| `wbs_planner_agent` | 基于资料和项目参数生成 WBS 工序、工期、前置关系和证据说明 | 写 `wbs_tasks_final` |
| `resource_allocator_agent` | 基于 WBS 生成劳动力、机械、材料需求，识别资源容量冲突 | 写 `resource_plan_final` |
| `constraint_checker_agent` | 检查 schema、前置关系、CPM 可行性、资源引用、证据字段和阻塞问题 | 只读草稿；反馈校验问题 |
| `dynamic_responder_agent` | 从资料、供应风险、季节条件、场地限制或进度风险中提取动态事件 | 写 `event_log` |
| `plan_arbiter_agent` | 基于事件和约束证据生成、评分、排序并选择调整方案 | 写 `adjustment_plan` |

这些职责边界在 `src/agentchat_runtime/workflow.py` 中通过系统提示、工具分配和写表权限共同约束。

## 公共黑板

公共黑板是一个 Excel 工作簿。默认 demo 黑板路径是：

```text
data/blackboard/demo_blackboard.xlsx
```

真实案例流程使用：

```text
data/blackboard/real_case_blackboard.xlsx
```

核心工作表包括：

- `parameter_checklist`
- `project_parameters`
- `wbs_tasks_final`
- `resource_plan_final`
- `schedule_initial`
- `cpm_analysis`
- `network_edges`
- `milestone_check`
- `constraint_check`
- `event_log`
- `adjustment_plan`
- `agent_message_log`
- `debug_records`
- `quality_gates`

如果要新增或修改字段，先改 `src/blackboard/sheet_schema.py`，再同步读写逻辑、校验逻辑和测试。

## 目录结构

```text
config/                 # 路径、模型、Agent、事件订阅配置
data/
  blackboard/           # 本地 Excel 黑板，提交时只保留 .gitkeep
  input_docs/           # 用户放置项目资料，提交时只保留 .gitkeep
  templates/            # 可提交的参数检查清单模板
docs/workflow/          # 接口契约、运行手册和 Agent I/O 示例
outputs/                # 本地运行输出，提交时只保留 .gitkeep
src/                    # 源码、Agent、工具、测试
```

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

## 环境变量

复制模板后填写自己的密钥：

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

| 变量 | 说明 |
|---|---|
| `MODEL_PROVIDER` | 模型供应商类型，默认 `openai_compatible` |
| `MOONSHOT_API_KEY` | Moonshot/Kimi 密钥；存在时优先使用 |
| `OPENAI_API_KEY` | OpenAI 或 OpenAI-compatible 密钥 |
| `OPENAI_MODEL` | 模型名称，默认 `kimi-k2.6` |
| `OPENAI_BASE_URL` | OpenAI-compatible `/v1` 地址 |
| `MODEL_API_STYLE` | `chat_completions` 或 `responses` |
| `KIMI_DISABLE_THINKING` | Kimi 模型是否关闭 thinking 输出 |
| `OPENAI_TIMEOUT_SECONDS` | 可选，覆盖模型调用超时时间 |
| `OPENAI_MAX_RETRIES` | 可选，覆盖模型调用重试次数 |

不要提交 `.env`、API Key、控制台截图或包含密钥的日志。若密钥曾出现在本地文件中，请在服务商后台轮换。

## 预设配置

`config/model.yaml` 的默认模型配置：

| 配置 | 默认值 |
|---|---|
| `provider` | `openai_compatible` |
| `name` | `kimi-k2.6` |
| `api_style` | `chat_completions` |
| `base_url` | `https://api.moonshot.cn/v1` |
| `timeout_seconds` | `600` |
| `max_retries` | `4` |
| `disable_thinking` | `true` |
| `mock_mode` | `false` |

`config/paths.yaml` 的默认路径：

| 配置 | 默认路径 |
|---|---|
| `blackboard` | `data/blackboard/demo_blackboard.xlsx` |
| `parameter_template` | `data/templates/parameter_checklist_template.xlsx` |
| `source_docs_dir` | `data/input_docs` |
| `outputs_dir` | `outputs/demo` |
| `communication_log` | `outputs/demo/communication_log.xlsx` |
| `runtime_log` | `outputs/demo/runtime.log` |
| `demo_transcripts_dir` | `outputs/demo/demo_transcripts` |
| `docs_dir` | `docs/workflow` |
| `schedule_dir` | `outputs/demo/schedule` |
| `report_assets_dir` | `outputs/demo/report_assets` |

`config/agents.yaml` 控制 Agent 是否启用和角色说明，`config/events.yaml` 控制事件主题和订阅关系。

## 个性化配置

- 更换模型供应商：修改 `.env` 中的 `OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL` 和 `MODEL_API_STYLE`。
- 调整默认模型参数：修改 `config/model.yaml`，或用环境变量覆盖 timeout/retry。
- 使用自己的项目资料：将文件放入 `data/input_docs/`，不要提交真实资料。
- 改变输出位置：修改 `config/paths.yaml`。
- 扩展公共黑板：先修改 `src/blackboard/sheet_schema.py`，再更新读写逻辑和测试。
- 扩展 Agent：在 `src/agents/` 或 `src/agentchat_runtime/workflow.py` 中新增角色、工具权限和校验规则。

## 运行

真实案例端到端流程：

```bash
python src/main_real_case_workflow.py
```

该流程会读取 `data/input_docs/`，使用 `data/blackboard/real_case_blackboard.xlsx`，并将成果写入 `outputs/real_case/`。

Demo 与本地检查：

```bash
python src/main_generate_demo.py
python src/main_event_demo.py
python src/main_initial_schedule.py
python src/visualize_schedule.py
```

这些命令生成的 Excel、Markdown、图片和日志均属于本地运行产物，默认不提交。

## 输出

| 路径 | 内容 |
|---|---|
| `outputs/real_case/schedule/` | 真实案例进度、WBS、资源、CPM、网络关系等 Excel 成果 |
| `outputs/real_case/demo_transcripts/` | 真实案例运行摘要 |
| `outputs/real_case/report_assets/` | 报告素材 |
| `outputs/demo/` | Demo 检查输出 |
| `data/blackboard/` | 本地公共黑板工作簿 |

## 测试

```bash
python -m pytest
```



## License

MIT License. See `LICENSE`.
