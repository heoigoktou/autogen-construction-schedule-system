# 真实案例 Agent 数据流推测

本文根据当前代码推测 `python src/main_real_case_workflow.py` 接入真实案例资料后的数据流向。结论重点：真实案例主链路不是传统消息总线逐个转发大表，而是 AutoGen `SelectorGroupChat` 编排各 Agent，通过工具读真实资料、读写共享的内存草稿表，最后一次性校验并写入 Excel 公共黑板。

## 主流程图

```mermaid
flowchart TD
    A["真实案例资料<br/>data/input_docs/*"] --> B["main_real_case_workflow.py<br/>read_source_documents"]
    B --> C["SourceDocument 列表<br/>text / sections / tables / warning"]
    C --> D["AgentChatWorkflow.run_async"]
    D --> E["reset_agentchat_output_tables<br/>清空历史生成表"]
    E --> F["build_selector_team<br/>SelectorGroupChat"]

    F --> G["工具层<br/>build_agent_tools"]
    C --> G
    G --> G1["read_source_context<br/>拼接原文"]
    G --> G2["read_extracted_parameter_candidates<br/>规则抽取参数候选"]
    G --> G3["read_document_evidence<br/>document_sections / document_tables / extracted_facts"]
    G --> G4["read_blackboard_sheet<br/>读内存草稿表"]
    G --> G5["write_blackboard_table<br/>写内存草稿表"]
    G --> G6["validate_candidate_output<br/>本地校验候选最终 JSON"]
    G --> G7["build_final_payload_from_drafts<br/>从草稿表组装最终 JSON"]

    F --> P["data_parser_agent<br/>资料解析"]
    P -->|写| P1["parameter_checklist<br/>project_parameters"]

    P1 --> W["wbs_planner_agent<br/>工序规划"]
    W -->|读 project_parameters / source context<br/>写| W1["wbs_tasks_final"]

    W1 --> R["resource_allocator_agent<br/>资源配置"]
    R -->|读 WBS / evidence<br/>写| R1["resource_plan_final"]

    R1 --> Y["dynamic_responder_agent<br/>动态响应"]
    Y -->|读 evidence / source context<br/>写| Y1["event_log"]

    Y1 --> Z["plan_arbiter_agent<br/>方案仲裁"]
    Z -->|读 event_log / resources / WBS<br/>写| Z1["adjustment_plan"]

    P1 --> M["内存草稿表<br/>draft_tables"]
    W1 --> M
    R1 --> M
    Y1 --> M
    Z1 --> M

    M --> K["constraint_checker_agent<br/>约束校核"]
    K -->|只读草稿表<br/>指出阻塞问题| CO["coordinator_agent<br/>总控收敛"]
    CO -->|可读全部草稿 / evidence<br/>必要时改写任意目标草稿表| M
    CO -->|FINAL_SCHEDULE_READY + JSON| J["最终 JSON payload"]

    J --> L["parse_agentchat_json<br/>validate_agentchat_payload"]
    L --> N["write_agentchat_output"]
    N --> N1["normalize / evidence enrich<br/>quality gates"]
    N1 --> N2["maybe_expand_segmented_wbs<br/>必要时扩展 WBS"]
    N2 --> N3["build_initial_schedule<br/>CPM / network / milestone"]
    N3 --> N4["resource_load / resource_resolution<br/>constraint_check"]
    N4 --> O["Excel 公共黑板<br/>data/blackboard/real_case_blackboard.xlsx"]

    O --> Q["导出成果<br/>outputs/real_case/schedule/*"]
    O --> T["real_case_workflow.md<br/>运行摘要"]
```

## Agent 间数据方向

```mermaid
flowchart LR
    DP["data_parser_agent"] -->|parameter_checklist<br/>project_parameters| WBS["wbs_planner_agent"]
    WBS -->|wbs_tasks_final| RES["resource_allocator_agent"]
    RES -->|resource_plan_final| DYN["dynamic_responder_agent"]
    DYN -->|event_log| ARB["plan_arbiter_agent"]
    ARB -->|adjustment_plan| CHK["constraint_checker_agent"]
    CHK -->|blocking issues / suggestions| COO["coordinator_agent"]
    COO -->|最终 JSON / 必要修正| OUT["write_agentchat_output -> Excel 黑板"]

    DP -.共享读.-> EV["document evidence / source context"]
    WBS -.共享读.-> EV
    RES -.共享读.-> EV
    DYN -.共享读.-> EV
    ARB -.共享读.-> EV
    CHK -.共享读.-> EV
    COO -.共享读.-> EV
```

## 校验与重试回路

```mermaid
flowchart TD
    A["coordinator_agent 输出 FINAL_SCHEDULE_READY JSON"] --> B["parse_agentchat_json"]
    B --> C["validate_agentchat_payload"]
    C -->|通过| D["write_agentchat_output 写 Excel"]
    C -->|失败| E{"失败类型"}
    E -->|已有最终 JSON 但字段/逻辑不合规| F["build_revision_task<br/>coordinator-only 修正"]
    E -->|缺少 FINAL_SCHEDULE_READY| G["build_missing_final_marker_task<br/>coordinator-only 收敛"]
    E -->|其他无法形成最终 payload| H["build_full_team_retry_task<br/>全团队重跑"]
    F --> I["SelectorGroupChat retry"]
    G --> I
    H --> I
    I --> A
    C -->|达到修正上限仍失败| J["force_write_agentchat_output<br/>best-effort 落盘 + debug_records"]
```

## 关键推断

- 入口数据来自 `data/input_docs/`，由 `read_source_documents` 读成 `SourceDocument`，支持文本、表格、Word、PDF、DXF/DWG 等资料。
- `build_agent_tools` 会先基于全文和结构化证据生成规则抽取结果，作为 `data_parser_agent` 的候选输入；这些不是 Agent 之间直接传消息，而是通过工具暴露给所有 Agent。
- 真实案例中间成果先进入 `draft_tables` 内存草稿表，`write_blackboard_table` 明确返回 `excel_written=false`；Excel 只在最终 JSON 通过本地校验后统一写入。
- `candidate_func` 按草稿表是否就绪决定下一位 Agent：参数 -> WBS -> 资源 -> 事件 -> 仲裁 -> 约束校核 / 总控交替。
- `constraint_checker_agent` 没有写表权限，只负责读草稿、指出阻塞问题；`coordinator_agent` 拥有所有目标草稿表写权限，并负责最终 `FINAL_SCHEDULE_READY` JSON。
- `write_agentchat_output` 是最终落盘枢纽：除六张核心表外，还补充证据表、审计表、质量门禁、初始进度、CPM、网络关系、里程碑、资源负载、资源冲突消解和约束校核结果。
- `config/events.yaml` 定义的是事件订阅式通信视角，更多用于 demo 和通信日志理解；真实案例主流程的实际路由由 `SelectorGroupChat` 和 `_build_candidate_func` 控制。

## 代码依据

- `src/main_real_case_workflow.py`：真实案例入口、资料读取、工作流运行和成果导出。
- `src/agentchat_runtime/workflow.py`：Agent 团队构建、工具权限、候选路由、重试逻辑、内存草稿表。
- `src/agentchat_runtime/output_writer.py`：最终 JSON 解析、校验、衍生排程计算和 Excel 落盘。
- `src/tools/document_tools.py`：真实资料读取和 evidence rows 构造。
- `src/blackboard/excel_store.py`、`src/blackboard/sheet_schema.py`：Excel 公共黑板读写与表结构契约。
