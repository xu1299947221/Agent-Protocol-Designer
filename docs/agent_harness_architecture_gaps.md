# APD Agent Harness 架构补全清单

> 这份文档用于防止后续开发遗忘本次讨论结论。后续继续开发 APD 前，应先阅读本文件。

## 1. 本次核心结论

APD 不应只停留在“协议设计器”，后续应升级为：

```text
Agent 架构设计器 → Agent Harness 生成器 → Agent Runtime 平台 → AgentOS 雏形
```

当前最准确的定位不是完整 AgentOS，而是：

```text
Agent Harness Designer / Generator
```

也就是帮助开发者从一个模糊业务需求，逐步得到一个可运行、可观察、可校验、可演进的 Agent 工程骨架。

## 2. 为什么之前漏掉“记忆”

之前 APD 主要关注的是 Agent 的可控执行链路：

```text
Context Pack → Intent Planner → OpCall → Validator → Executor → Observation → Trace
```

这条链路解决的是：

```text
如何防止 Agent 乱跑、乱调工具、乱改状态
```

但“记忆”属于另一条关键能力：

```text
Agent 如何跨轮、跨任务、跨项目持续理解用户、任务和环境
```

之前 APD 有会话历史、上下文包、任务状态、Trace，但没有把记忆明确提升为一等架构层，所以需要补：

```text
Memory Policy / 记忆策略
```

关键认知：

```text
上下文 = 本轮交给 LLM 看的材料
记忆 = 可以跨轮、跨任务、长期存在，并被规则管理的数据资产
```

运行时应是：

```text
Memory Retrieval → Context Pack → Intent Planner → OpCall → Validator → Executor → Observation → Memory Write → Trace
```

## 3. APD 后续必须新增的 Memory Policy

APD 设计 Agent 时，应判断这个 Agent 是否需要记忆，以及需要哪种记忆。

常见记忆类型：

```text
1. 会话记忆：本次对话前面确认过什么
2. 任务状态记忆：任务做到哪一步，哪些步骤已完成
3. 用户偏好记忆：用户偏好的语言、风格、格式、交互方式
4. 项目记忆：项目规则、模板、知识库、历史决策
5. 长期经验记忆：失败 case、常见误判、评测结果
```

Memory Policy 至少应回答：

```text
这个 Agent 要记什么？
什么时候读取？
什么时候写入？
谁能修改？
谁能删除？
写入是否需要用户确认？
记忆如何进入 Context Pack？
错误记忆如何纠正？
```

原则：

```text
LLM 可以建议写入记忆，程序负责校验和落库，用户拥有查看、修改、删除权。
```

## 4. 通用 Agent 架构层清单

不是每个 Agent 都必须具备全部模块，但 APD 应该能判断每个场景是否需要。

### 4.1 核心必备层

```text
1. Context Pack / 上下文包
2. Intent Planner / 意图规划器
3. Intent Frame / 意图框架
4. Intent Binding / 意图绑定
5. OpCall / 操作调用
6. Validator / 校验器
7. Executor / 执行器
8. Observation / 观察结果
9. Trace / 过程追踪
```

这些层负责让 Agent 的单轮或多轮执行可控、可查、可复现。

### 4.2 场景增强层

```text
10. Memory Policy / 记忆策略
11. State Model / 状态模型
12. Artifact Model / 产物模型
13. Tool Registry / 工具注册表
14. Permission Policy / 权限策略
15. Human-in-the-loop / 人工确认点
16. Error Recovery / 失败恢复
17. Eval Cases / 评测用例
18. Knowledge/RAG Policy / 知识检索策略
19. Versioning/Rollback / 版本与回滚
20. Cost/Latency Budget / 成本与耗时预算
```

这些层决定 Agent 是否能进入真实业务系统。

### 4.3 平台治理层

```text
21. Agent Registry / Agent 注册表
22. Runtime Scheduler / 运行调度器
23. Event Bus / 事件总线
24. Sandbox / 沙箱隔离
25. Observability / 可观测性
26. Data Governance / 数据治理
27. Feedback Learning / 反馈学习
28. Multi-Agent Coordination / 多 Agent 协作
```

这些层决定 APD 是否能从 Harness 生成器继续演进成 Agent Runtime 或 AgentOS。

## 5. APD 近期开发优先级

建议优先补这 10 个缺口：

```text
P0. Agent 架构完整性检查器
P1. Memory Policy / 记忆策略
P2. State Model / 状态模型
P3. Artifact Model / 产物模型
P4. Tool Registry / 工具注册
P5. Permission Policy / 权限策略
P6. Error Recovery / 失败恢复
P7. Eval Cases / 评测用例
P8. Knowledge/RAG Policy / 知识检索策略
P9. Versioning/Rollback / 版本回滚
P10. Cost/Latency Budget / 成本耗时预算
```

其中 P0 是入口能力：

```text
每次设计 Agent，APD 自动检查当前架构缺了哪些通用层。
```

示例输出：

```text
当前架构完整度：65%

已覆盖：
- Intent Planner
- OpCall
- Validator
- Executor

建议补齐：
- Memory Policy
- State Model
- Artifact Model
- Error Recovery
- Eval Cases
```

## 6. APD 对话引导应如何升级

后续 APD 不应该只追问业务操作，还应在合适阶段自动进入架构完整性检查。

建议新增引导问题：

```text
这个 Agent 是否需要跨轮记住信息？
这个 Agent 是否有明确任务状态？
这个 Agent 会生成哪些可编辑/可导出的产物？
这个 Agent 依赖哪些底层工具或外部 API？
哪些操作需要权限或人工确认？
失败后应该重试、追问、回滚还是转人工？
如何评测这个 Agent 是否理解正确？
是否需要知识库/RAG/图谱？
是否需要版本记录和撤回？
是否有成本或响应时间限制？
```

注意：这些问题不能一次性全问，应继续保持 APD 当前原则：

```text
一次只问一个问题，根据用户回答决定是否进入下一步。
```

## 7. 对写作 Agent 与招投标 Agent 的影响

### 写作 Agent

需要重点考虑：

```text
会话记忆
用户风格偏好
当前文档结构状态
段落/章节版本
编辑操作 Trace
撤回与恢复
```

### 招投标 Agent

需要重点考虑：

```text
招标文件解析状态
评分点和废标条款
投标目录生成状态
素材匹配结果
证据来源
人工确认点
最终 DOCX 产物版本
```

## 8. 后续开发时给 AI 的提示词

如果后续让 Codex、Claude Code 或其他 AI 继续开发 APD，可以直接让它读本文件，并使用下面这段话：

```text
请先阅读 docs/agent_harness_architecture_gaps.md。
APD 当前要从 Agent 协议设计器升级为 Agent Harness Designer / Generator。
不要只补一个功能点，要围绕“Agent 架构完整性检查器”推进：
1. 在协议结构里补 memory_policy、state_model、artifact_model、tool_registry、permission_policy、error_recovery、eval_cases 等能力；
2. 在 WebUI 对话引导中保持一次只问一个问题；
3. 在预览运行中展示这些架构层如何影响 Context Pack、OpCall、Validator、Executor 和 Trace；
4. 在导出和脚手架中体现这些架构层；
5. 默认中文交互，英文术语只作为括号补充。
```

## 9. 一句话记住

```text
APD 以后不是只问“这个 Agent 能做哪些操作”，还要检查“这个 Agent 作为一个可运行系统缺哪些架构层”。
```

---

## 10. 外部项目参考：LobeHub

参考项目：`https://github.com/lobehub/lobehub/blob/canary/README.zh-CN.md`

### 10.1 借鉴原则

LobeHub 对 APD 有参考价值，但 APD 不能偏离主线。

```text
LobeHub 更像 Agent 使用、组织和运营平台。
APD 应继续定位为 Agent Harness 架构设计与生成平台。
```

所以 APD 只借鉴产品设计和架构概念，不直接照搬功能边界，也不直接复用代码。

判断一个外部功能是否值得吸收，要看它是否满足：

```text
1. 是否帮助用户更好地设计 Agent？
2. 是否让 Agent 更可控、更可观察、更可校验？
3. 是否能沉淀为 APD 的协议字段、检查项或生成项？
4. 是否不会把 APD 变成普通聊天客户端或 Agent 使用平台？
```

### 10.2 可以吸收的概念

| LobeHub 概念 | APD 中的转化 | 说明 |
|---|---|---|
| Agent 配置 | Agent Profile / 智能体画像 | 定义角色、目标用户、交互风格、模型策略、技能摘要。 |
| Memory | Memory Policy / 记忆策略 | 设计记忆类型、读写规则、置信度、可编辑/可删除策略。 |
| Skills / Integrations / MCP | Tool Registry / 工具注册表 | 区分业务操作和底层工具，声明输入输出、风险、副作用和失败策略。 |
| Knowledge Base | Knowledge Policy / 知识检索策略 | 定义知识库、检索规则、引用策略和冲突处理。 |
| Pages | Artifact Model / 产物模型 | 定义文档、报告、代码、图谱等产物，以及编辑、版本、导出规则。 |
| Agent Groups | Collaboration Workflow / 协作工作流 | 转成顺序、并行、迭代、辩论、多 Agent 协作模板。 |
| Schedule | Runtime Trigger / 运行触发器 | 转成手动、定时、事件触发和运行预算设计。 |
| Project / Workspace | Workspace Policy / 项目空间策略 | 定义项目边界、数据边界、协作规则和会话保留策略。 |

### 10.3 暂时不要做的方向

这些功能不是不好，而是会让 APD 偏离“Agent 架构设计与生成”的主线：

```text
通用聊天客户端
IM 网关
Agent 商店
插件市场运营
完整知识库产品
完整在线文档产品
完整多租户协作 SaaS
复杂模型供应商管理平台
```

APD 的边界应保持为：

```text
如果它只是让用户更好“使用 Agent”，先不做。
如果它能帮助开发者更好“设计和生成可靠 Agent”，才做。
```

### 10.4 对当前开发顺序的影响

LobeHub 的启发应落到 APD 当前任务表中：

```text
1. Agent Profile
2. Memory Policy
3. Tool Registry
4. Knowledge Policy
5. Artifact Model
6. Collaboration Workflow
7. Runtime Trigger
8. Workspace Policy
```

这些能力必须服务于同一个目标：

```text
让 APD 更会帮用户设计、检查、生成一个可靠 Agent Harness。
```
