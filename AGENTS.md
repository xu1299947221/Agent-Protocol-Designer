# APD Agent Instructions

后续任何 AI/Codex/Claude 在修改本项目之前，必须先阅读：

- `docs/agent_harness_architecture_gaps.md`
- `docs/agent_architecture_guide.md`
- `README.md`

本项目当前长期方向：

```text
Agent 协议设计器 → Agent Harness Designer / Generator → Agent Runtime 平台 → AgentOS 雏形
```

当前最重要的待补方向是：

```text
Agent 架构完整性检查器
Memory Policy / 记忆策略
State Model / 状态模型
Artifact Model / 产物模型
Tool Registry / 工具注册
Permission Policy / 权限策略
Error Recovery / 失败恢复
Eval Cases / 评测用例
Knowledge/RAG Policy / 知识检索策略
Versioning/Rollback / 版本回滚
Cost/Latency Budget / 成本耗时预算
```

交互和文档要求：

- 默认使用中文。
- 英文术语可以保留，但必须用中文解释，例如 `记忆策略（Memory Policy）`。
- WebUI 引导要保持“一次只问一个问题”，不要一次性丢给用户大量问题。
- 不要把 APD 简化成普通 Agent Builder；它的核心价值是帮助用户设计可控、可观察、可校验、可演进的 Agent 架构。
