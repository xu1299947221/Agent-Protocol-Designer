# APD 当前能力边界

> 本文用于回答一个核心问题：APD 现在到底是什么、已经能帮开发者做什么、还不能做什么、下一阶段应该往哪里走。

## 1. 一句话定位

APD 当前不是完整的 AgentOS，也不是普通的聊天机器人搭建器。

它当前最准确的定位是：

```text
Agent Harness Designer / Generator
```

大白话解释：

```text
APD 帮你在真正写 Agent 代码之前，先把 Agent 的职责、能力、流程、工具、权限、记忆、状态、产物、评测和运行边界设计清楚。
```

它解决的不是“直接替你完成所有业务”，而是解决：

```text
一个 Agent 到底应该怎么拆、怎么控、怎么测、怎么跑、怎么交给工程实现。
```

## 2. 当前已经具备的能力

| 能力层 | 当前状态 | 能做什么 |
|---|---|---|
| 对话式场景拆解 | 已可用 | 用户用自然语言描述业务，APD 一次只问一个问题，逐步收敛 Agent 场景 |
| Agent Protocol | 已可用 | 生成对象、意图、操作、参数、校验器、失败策略等能力协议 |
| 架构完整性检查 | 已有雏形 | 检查 Context、Intent、OpCall、Validator、Executor、Memory、State、Artifact、Tool、Permission 等缺口 |
| Dynamic Workflow | 已有雏形 | 设计多步骤、多节点、人工确认、失败策略和产物流转 |
| Memory Policy | 已有雏形 | 设计 Agent 需要记什么、什么时候读、什么时候写、谁能改删 |
| State Model | 已有雏形 | 设计任务状态、状态流转、状态校验和状态上下文 |
| Artifact Model | 已有雏形 | 设计文档、报告、DOCX、图谱等产物，以及版本、确认、回滚思路 |
| Tool Registry | 已有雏形 | 区分业务操作和底层工具，描述工具风险、副作用、输入输出 |
| Permission Policy | 已有雏形 | 区分自动执行、需要确认、禁止执行和高风险动作 |
| Error Recovery | 已有雏形 | 设计失败后的重试、追问、降级、回滚、转人工 |
| Eval Cases | 已有雏形 | 生成意图识别、操作绑定、校验、权限、记忆、状态、产物、工作流评测用例 |
| Preview Runtime | 已可用 | 在不真实写库、不真实执行工具的情况下，模拟 Context → Intent → OpCall → Validator → Executor → Trace |
| Workflow Runtime | 已有雏形 | 节点级模拟运行 workflow，支持暂停、继续、产物版本和 Trace |
| Agent Registry | 已有雏形 | 保存当前 Agent / Workflow 设计，支持版本和复制会话 |
| Tool 测试 | 已有雏形 | 对 Tool Registry 做 dry-run 测试，记录风险、副作用和失败策略 |
| Observability | 已有雏形 | 汇总 Runtime Job、Tool Run、Eval Replay、失败率、等待确认和 Trace 指标 |
| Governance | 已有雏形 | 记录关键动作审计事件，检测敏感字段，说明数据治理边界 |
| Multi-Agent 协作 | 已有雏形 | 设计多个 Agent 的角色、消息协议、任务交接、冲突处理和协同 Trace |
| 脚手架生成 | 已可用雏形 | 导出可运行 FastAPI Agent Harness Demo zip，作为后续工程开发起点 |

## 3. 当前最适合使用 APD 的场景

APD 适合这些情况：

1. 你有一个业务 Agent 想法，但不知道怎么拆成可控工程。
2. 你担心 LLM 自由发挥，想先限定可执行操作范围。
3. 你的场景有多步骤、多状态、多产物、人工确认或高风险操作。
4. 你需要把 Agent 设计交给 Codex / Claude Code / 人类开发者继续实现。
5. 你想沉淀一套可复用的 Agent 架构协议，而不是每次临时写 Prompt。

典型例子：

```text
写作 Agent
招投标 Agent
知识库问答 + 文档生成 Agent
客服退款 Agent
合同审查 Agent
资料解析 + 报告生成 Agent
```

## 4. 当前不适合 APD 直接完成的事情

| 事情 | 当前边界 |
|---|---|
| 真实业务系统替代 | APD 不能直接替代完整业务系统，只能生成协议、计划、模拟运行和脚手架 |
| 真实多 Agent 并发执行 | 当前 Multi-Agent 是设计与模拟层，还没有真正启动多个 LLM Agent 并行协作 |
| 真实工具调用 | Tool Runtime 当前以 dry-run 为主，不会直接调用高风险外部系统 |
| 真实文档编辑器 | APD 不提供 Word 级编辑器，只能设计 Artifact Model 和导出工程骨架 |
| 真实知识库/RAG 服务 | APD 能设计 Knowledge/RAG Policy，但不直接替代你的知识库系统 |
| 生产级权限系统 | 当前有权限策略和审计雏形，但不是完整企业 IAM / RBAC 系统 |
| 强安全沙箱平台 | Docker 沙箱是实验能力，不等于生产级隔离平台 |
| 自动保证 Agent 效果 | APD 能帮助拆解、约束、评测，但不能保证 LLM 每次理解都完美 |

## 5. 当前 APD 的核心价值边界

APD 最核心的价值可以概括为五句话：

```text
1. 把模糊需求拆成可执行能力协议。
2. 把自由 LLM 行为限制到受控 OpCall。
3. 把复杂任务设计成可观察 workflow。
4. 把工程风险提前暴露成校验、权限、记忆、状态、产物和评测。
5. 把设计结果导出成后续可开发、可运行、可测试的工程骨架。
```

所以 APD 的边界不是“替你做完业务”，而是：

```text
帮你把 Agent 开发从凭感觉写 Prompt，升级为先设计协议、流程、约束、运行和评测。
```

## 6. 与 AgentOS 的关系

APD 现在还不是完整 AgentOS。

但它已经具备 AgentOS 的一部分底座能力：

```text
协议设计
工作流设计
运行模拟
工具注册
权限审计
Agent 注册表
观测面板
多 Agent 协作设计
```

如果继续演进，路径应该是：

```text
APD 当前：Agent Harness Designer / Generator
下一阶段：Agent Project Generator + Runtime Playground
再下一阶段：轻量 Agent Runtime Platform
更远阶段：轻量 AgentOS 雏形
```

## 7. 下一阶段能力缺口

当前最值得补的不是再堆概念，而是让 APD 生成的东西更“能跑、能试、能改”。

建议下一阶段任务表围绕这些方向展开：

| 优先级 | 方向 | 为什么重要 |
|---|---|---|
| P0 | 可运行 Demo Playground | 已推进：WebUI 可一键生成临时 Demo、启动 FastAPI、测试 /agent/run、/workflow/run、/store/snapshot 和 /tools |
| P0 | 脚手架质量升级 | 已推进：生成项目新增 Store、Tool Adapter、Workflow Runtime Demo 和更完整测试，后续继续接真实业务实现 |
| P1 | Runtime 与生成代码打通 | APD 内预览结果要能对应到生成项目里的真实模块 |
| P1 | Multi-Agent 角色绑定 Runtime 节点 | 多 Agent 设计要落到可执行节点、消息和 Trace |
| P1 | Artifact 真实文件流 | 产物版本、回滚、导出要从模拟走向真实文件 |
| P2 | Tool Adapter 模板 | 常见工具接入要有标准模板，而不是每个项目重写 |
| P2 | Eval Replay 工程化 | 评测用例要能直接跑生成项目，形成回归测试 |
| P2 | 沙箱运行统一化 | Docker 沙箱要变成稳定的 Demo 执行环境 |
| P3 | Agent Marketplace / Registry | 让设计好的 Agent 可以复用、组合和版本化 |

## 8. 对用户的使用建议

如果你现在要用 APD 开发一个 Agent，建议按这个顺序：

```text
1. 先描述业务场景
2. 回答 APD 的单步追问
3. 看架构完整性检查
4. 运行 Preview Runtime
5. 如果是复杂流程，进入 Dynamic Workflow
6. 运行 Workflow Runtime
7. 看开发入口里的下一步建议
8. 导出 Demo zip
9. 交给 Codex / Claude Code / 人类开发者继续实现
10. 把真实项目反馈再回填 APD，沉淀 eval cases 和架构改进
```

## 9. 最重要的判断标准

以后判断 APD 有没有偏方向，可以用这三个问题：

```text
1. 它有没有让 Agent 更可控？
2. 它有没有让 Agent 更可观察、可复盘？
3. 它有没有让设计结果更容易变成真实可运行工程？
```

如果答案是“没有”，这个功能就可能偏离 APD 主线。
