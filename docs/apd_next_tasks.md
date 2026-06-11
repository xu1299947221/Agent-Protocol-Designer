# APD 后续任务表

> 当前目标：把 APD 从协议设计器升级成 Agent Harness 架构设计与生成平台。

| 顺序 | 目标任务 | 核心产出 | 状态 |
|---|---|---|---|
| 1 | 写入 LobeHub 借鉴结论 | `docs/agent_harness_architecture_gaps.md` 新增“外部项目参考：LobeHub” | 已完成 |
| 2 | 扩展 Protocol Schema | 新增 `agent_profile`、`memory_policy`、`state_model`、`artifact_model` 等字段 | 已完成 |
| 3 | 实现架构完整性检查器 | `architecture_check`、`architecture_check.md`、`architecture_check.json` | 已完成雏形 |
| 4 | 接入对话引导 | 根据架构缺口一次只问一个问题 | 已完成雏形 |
| 5 | 补 Memory Policy | 记忆类型、读写规则、删除规则、确认机制 | 已完成雏形 |
| 6 | 升级预览运行 | 展示记忆读取、上下文、校验、执行、记忆写入建议 | 已完成雏形 |
| 7 | 补 State Model | 任务状态、状态流转、状态校验 | 已完成雏形 |
| 8 | 补 Artifact Model | 文档、报告、DOCX、图谱等产物模型 | 已完成雏形 |
| 9 | 补 Tool Registry | 区分业务操作和底层工具 | 已完成雏形 |
| 10 | 补 Permission Policy | 自动执行、人工确认、禁止操作、权限规则 | 已完成雏形 |
| 11 | 补 Error Recovery | 重试、追问、回滚、转人工策略 | 已完成雏形 |
| 12 | 补 Eval Cases | 意图、绑定、校验、记忆、工作流测试用例 | 已完成雏形 |
| 13 | 升级脚手架生成器 | 可运行 Agent Harness Demo | 已完成雏形 |
| 14 | 重构 WebUI 布局 | 页面分区更清晰 | 已完成雏形 |
| 15 | 增加下一步开发建议 | APD 告诉用户后续该先开发什么 | 已完成雏形 |
| 16 | 用写作 Agent 验证 | 对齐真实写作 Agent 架构 | 已完成雏形 |
| 17 | 用招投标 Agent 验证 | 验证复杂 Workflow、文档产物、知识库、人工确认 | 已完成雏形 |
| 18 | Agent Runtime 雏形 | 生成的 Harness Demo 可在 APD 内运行 | 已完成雏形 |
| 19 | 再评估 AgentOS | Runtime 成熟后再考虑平台化 | 已完成雏形 |
| 20 | Runtime 状态持久化 | 保存 Runtime Job、节点状态、人工确认点、产物和 Trace | 已完成雏形 |
| 21 | Runtime 节点实现绑定 | 将 workflow node 绑定到 Tool、Validator、LLM Prompt 或 RAG 调用 | 已完成雏形 |
| 22 | Artifact 版本管理 | 让 Runtime 产物支持版本、快照、确认和回滚 | 已完成雏形 |
| 23 | Eval 回放 | 将失败样本和评测用例在 Runtime 中重新运行并比较结果 | 已完成雏形 |
| 24 | Agent Registry 雏形 | 保存多个 Agent/Workflow，支持选择、复制和版本管理 | 已完成雏形 |
| 25 | Tool 执行与测试雏形 | 让 Registry/Runtime 中的 Tool 支持测试、执行记录和失败策略 | 已完成雏形 |
| 26 | Runtime 观测面板 | 汇总 Runtime Job、Tool Run、Eval Replay、失败率和 Trace 指标 | 已完成雏形 |
| 27 | 权限审计与数据治理 | 为 Runtime、Tool、Memory、Artifact 增加审计事件和数据治理策略 | 已完成雏形 |
| 28 | Multi-Agent 协作雏形 | 设计多 Agent 角色、消息、交接、冲突处理和协同 Trace | 已完成雏形 |

## 下一步

当前 1–28 项基础架构补齐任务已完成雏形。下一阶段建议重新开一张任务表，把 APD 从 `Agent Harness Designer / Generator` 继续推进到“可运行 Agent 项目生成与运行平台”。

优先候选方向：

```text
1. Multi-Agent 角色绑定到真实 Runtime 节点
2. Agent Registry 支持按角色复用子 Agent
3. 生成脚手架时输出多 Agent 协作代码骨架
4. 协作 Trace 持久化与观测指标
5. 将冲突处理接入人工确认、产物版本和权限审计
```

第 5 项已完成雏形：`memory_policy` 会自动生成，可导出 `memory_policy.md/json`，预览运行会展示 `memory_retrieval` 和 `memory_write_proposal`。

第 6 项已完成雏形：预览运行新增 `preview_timeline` 和 `diagnostics`，WebUI 会用“小白时间线”展示记忆读取、上下文、意图、操作、校验、模拟执行、记忆写入和 Trace。

第 7 项已完成雏形：`state_model` 会自动生成，可导出 `state_model.md/json`，预览运行会展示 `state_context` 和“检查状态”时间线步骤。

第 8 项已完成雏形：`artifact_model` 会自动生成，可导出 `artifact_model.md/json`，预览运行会展示 `artifact_context`、`artifact_effect` 和“检查产物”时间线步骤。

第 9 项已完成雏形：`tool_registry` 会自动生成，可导出 `tool_registry.md/json`，预览运行会展示 `tool_context`、`tool_plan` 和“规划工具”时间线步骤。

第 10 项已完成雏形：`permission_policy` 会自动生成，可导出 `permission_policy.md/json`，预览运行会展示 `permission_context`、`permission_check` 和“权限判断”时间线步骤。

第 11 项已完成雏形：`error_recovery` 会自动生成，可导出 `error_recovery.md/json`，预览运行会展示 `recovery_context`、`recovery_plan` 和“失败恢复”时间线步骤。

第 12 项已完成雏形：`eval_policy` 会自动生成，可导出 `eval_policy.md/json`，`eval_cases.json` 会按意图、绑定、校验、记忆、状态、产物、工具、权限、失败恢复和工作流分组；预览运行会展示 `eval_case_suggestion` 和“沉淀评测”时间线步骤。

第 13 项已完成雏形：脚手架 zip 现在生成可运行 Agent Harness Demo，包含 `backend/app/harness.py`、`requirements.txt`、`/harness/spec`、`/agent/run`、上下文包、权限判断、失败恢复、评测沉淀建议和 Trace；生成物可用 `uvicorn app.main:app --reload --port 8000` 启动。

第 14 项已完成雏形：WebUI 右侧工作区改为标签页分区，分为“协议草案 / 导出产物 / 开发入口”；左侧继续专注对话，右侧默认只展示当前步骤需要看的内容，降低按钮和内容堆叠造成的拥挤感。

第 15 项已完成雏形：新增 `development_advice.md/json`，会根据当前协议的 operation、validator、权限、工具、产物、评测、workflow 和架构完整度，判断当前应先补协议、先跑预览、先生成 Demo，还是先实现校验器/执行器；WebUI“开发入口”会直接展示“现在先开发什么”。

第 16 项已完成雏形：已用 `/home/data/rag/ragyuyan/rag_agent` 真实写作 Agent 验证 APD 路线，新增 `docs/writing_agent_apd_validation.md`，结论是 APD 的受控 OpCall Runtime 与 Harness 化路线成立；写作案例页会展示验证结论，建议真实项目优先补 OpCall Trace、Intent Frame、Intent Binding、Context Builder 和 intent/binding eval cases。

第 17 项已完成雏形：已用招投标 C 方案验证 APD 的 Dynamic Workflow、Artifact、Knowledge/RAG、Permission、Error Recovery、Eval 方向。新增 `docs/bid_agent_apd_validation.md`，并在 WebUI 增加“招投标 Agent 验证”入口。

第 18 项已完成雏形：新增 `protocol_designer/workflow_runtime.py`，支持把当前 workflow 归一化为节点级 Runtime Plan，并在 APD 内模拟运行、暂停人工确认、继续确认节点、追踪模拟产物和 Trace；WebUI 新增“Runtime 运行”入口和 `/api/runtime/plan`、`/api/runtime/run`。

第 19 项已完成雏形：新增 `docs/agentos_evaluation.md`，结论是 APD 不应立刻宣称完整 AgentOS，应继续保持 Agent Harness Designer / Generator 定位，并在 Runtime 成熟后逐步演进为轻量 AgentOS；WebUI 新增“AgentOS 评估”入口。下一步建议先做 Runtime 状态持久化、人工确认恢复运行、节点实现绑定、Artifact 版本管理、Eval 回放和 Agent Registry 雏形。

第 20 项已完成雏形：Runtime 运行现在会保存为 `data/runtime_jobs/*.json`，包含 job_id、session_id、节点状态、人工确认点、产物、Trace、上下文和 approvals；WebUI Runtime 抽屉新增历史运行列表、打开历史 Job、确认并继续暂停任务；接口新增 `/api/runtime/jobs`、`/api/runtime/job/{job_id}`、`/api/runtime/job/{job_id}/continue`。

第 21 项已完成雏形：Runtime 节点现在会输出 `implementation_binding` 和 `execution_preview`，可根据 workflow node 类型、operation、tool_registry、validators、knowledge_policy 自动绑定到 Tool、Validator、LLM Prompt、RAG 或 Human Review；WebUI Runtime 时间线和开发者细节会展示每个节点的实现绑定。

第 22 项已完成雏形：Runtime 产物现在包含版本号、previous_version_id、snapshot_id、is_active、confirmation_status、source_trace_id、lineage 和 rollback_available；Runtime 结果新增 `artifact_versions` 索引，WebUI 会展示“产物版本（Artifact Versions）”；接口新增 `/api/runtime/job/{job_id}/artifact/{artifact_id}/rollback` 用于将某个产物版本标记为活动版本。

第 23 项已完成雏形：新增 `protocol_designer/eval_replay.py`，可收集 `eval_policy.case_groups` / `golden_cases`，逐条放入 Runtime 回放，并比较 expected_operation、Trace、状态上下文、产物版本、工具绑定、Validator 绑定等结果；新增 `/api/runtime/eval-replay`，WebUI Runtime 抽屉新增“运行 Eval 回放”按钮。

第 24 项已完成雏形：新增 `protocol_designer/agent_registry.py`，支持将当前 session 的 protocol/workflow 注册为 Agent 条目，保存版本快照、追加新版本、查看列表、加载详情，并复制为新会话；WebUI 新增 Agent Registry 抽屉；接口新增 `/api/registry/agents`、`/api/registry/register`、`/api/registry/agent/{agent_id}`、`/api/registry/agent/{agent_id}/version`、`/api/registry/agent/{agent_id}/clone`。

第 25 项已完成雏形：新增 `protocol_designer/tool_runtime.py`，支持从 Tool Registry 生成 dry-run 测试输入、模拟执行工具、记录输出、风险、副作用、失败策略和 trace；新增 `/api/tools/test` 与 `/api/tools/runs`；WebUI Runtime 抽屉新增“运行 Tool 测试”按钮。当前版本不真实调用外部服务，高风险或有副作用工具只允许 dry-run。

第 26 项已完成雏形：新增 `protocol_designer/observability.py`，可汇总 Runtime Job、Tool Run、Eval Replay、Agent Registry、失败率、等待人工确认、Trace、产物、回滚点等指标；Eval Replay 现在会保存到 `data/eval_replays/*.json`；新增 `/api/observability/summary`；WebUI 新增“Runtime 观测面板”。

第 27 项已完成雏形：新增 `protocol_designer/governance.py`，支持敏感字段检测、审计事件、风险分类、保留策略和责任边界；Runtime Run、Runtime Continue、Artifact Rollback、Eval Replay、Registry Register/Version/Clone、Tool Test 等关键动作会写入 `data/audit_events/*.json`；新增 `/api/governance/summary`、`/api/governance/events`；WebUI 新增“权限审计与数据治理”面板。

第 28 项已完成雏形：新增 `protocol_designer/multi_agent.py`，可根据当前协议和 workflow 推导多 Agent 角色分工、消息协议、任务交接、冲突处理和协同 Trace；新增 `/api/multi-agent/analyze`；WebUI“学习/工具”和“开发入口”新增“Multi-Agent 协作”抽屉。

## Runtime Inspector v2：真实 Agent 体验调试台

详细方案见：`docs/runtime_inspector_v2_development_plan.md`

后续开发按 6 个阶段推进：

1. 调试页产品形态重构：三栏布局，真实 Agent 对话为主。
2. 后端 Debug Session 上下文打通：conversation_history、memory、turns、重启保留上下文。
3. 生成工程支持真实调试上下文：context_pack 接入 recent_turns、memory、artifact。
4. 诊断引擎升级：按 Context / Planner / OpCall / Validator / Tools / Workflow / Executor / Artifact 分层定位。
5. 一键修复任务升级：带完整上下文、诊断、建议文件、验收话术发送给 open_claude / Codex。
6. 测试用例和回放：保存失败样本，修复后 replay 对比。

第一步立即开发任务：重构 Runtime Inspector 独立页，让中间区域成为真实用户对话框，右侧展示当前轮诊断详情，左侧承载测试参数和会话控制。
