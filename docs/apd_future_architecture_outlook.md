# APD 未来架构展望

> 本文记录 APD 从 Agent Engineering Platform 继续演进时必须纳入视野的长期能力。它不是当前阶段必须全部实现的开发清单，而是防止平台方向只停留在“生成和调试 Agent”的架构提醒。

## 1. 当前定位

APD 当前定位已经从早期的 Agent Protocol Designer 升级为：

```text
Agent Engineering Platform
```

也就是：

```text
面向定制 Agent 的协议设计、工程生成、真实调试和增量迁移平台。
```

当前核心链路是：

```text
需求对话
  ↓
APD 协议草案 JSON
  ↓
两条落地路线
  ├── Delegated Agent：APD 协议 + Delegated Runtime + open_claude Engine
  └── Native Runtime：APD 协议 + 自研脚手架 + 工程开发台持续补 Runtime
  ↓
真实调试 / AI 修复 / 协议增量迁移 / 版本回滚 / 独立部署
```

但如果 APD 要长期成为真正可用的 Agent 工程平台，还需要继续补齐治理、发布、评测、隔离和长期维护能力。

## 2. 未来必须纳入视野的 15 类能力

### 2.1 Agent 身份与租户边界

需要回答：

```text
谁创建的 Agent？
谁能使用这个 Agent？
谁能改协议？
谁能发布版本？
不同部门的数据能不能互相看到？
```

需要能力：

```text
Agent Registry
User / Role / Tenant
Permission Policy
Publish / Draft / Archived 状态
```

### 2.2 Agent 生命周期

一个 Agent 不应只有“生成完成”这个状态。

建议生命周期：

```text
草稿
开发中
测试中
已发布
已下线
已归档
```

不同状态允许的操作不同。

例如：

```text
已发布 Agent 不能直接修改生产配置。
协议更新要先进入测试版本。
失败后必须能回滚到上一版。
```

### 2.3 环境隔离

真实部署需要区分：

```text
dev
test
staging
prod
```

同一个 Agent 在不同环境可能使用不同：

```text
LLM key
知识库
工具地址
权限
数据目录
并发限制
```

环境隔离不提前设计，后期会导致配置、权限、数据和调试结果混乱。

### 2.4 工具治理

Tool Registry 不只是工具清单，还要覆盖治理能力。

需要考虑：

```text
工具健康检查
工具版本兼容
工具调用限流
工具超时
工具幂等
工具副作用声明
工具回滚能力
工具 mock / dry-run
```

尤其是副作用工具，例如：

```text
发邮件
提交审批
写数据库
导出正式文件
调用外部系统
```

不能只靠 prompt 约束 Agent 使用这些工具。

### 2.5 数据与隐私治理

Agent 会读取文件、知识库和业务数据。

必须设计：

```text
哪些字段是敏感信息？
Trace 里能不能保存原文？
LLM 调用时能不能带客户数据？
文件保存多久？
用户能不能删除？
日志如何脱敏？
```

这部分关系到平台能否进入真实企业环境。

### 2.6 评测体系

Eval Cases 不应只是简单样例。

需要分层：

```text
协议评测：意图是否识别正确
工具评测：工具参数是否生成正确
结果评测：产物是否合格
回归评测：新版本有没有破坏旧能力
安全评测：高风险操作是否被拦截
```

没有评测体系，Agent 会越改越玄学。

### 2.7 发布与回滚

需要区分多类版本：

```text
工作区版本
协议版本
工具版本
Runtime 版本
Engine 版本
生产发布版本
```

发布时应记录完整组合：

```text
Agent v3 = protocol v8 + toolset v4 + runtime v2 + engine v1.6
```

否则生产问题很难追溯。

### 2.8 成本与性能

Agent 平台会产生长任务和多轮工具调用。

需要设计：

```text
单任务最多跑多久？
最多调用几次 LLM？
最多花多少钱？
超时怎么办？
并发上限多少？
大文件怎么处理？
```

这些应进入 Runtime Policy 或 Cost / Latency Budget。

### 2.9 人工确认机制

Human-in-the-loop 不应只是一个提示词概念。

需要明确：

```text
什么时候必须暂停？
谁来确认？
确认后怎么继续？
拒绝后怎么回滚？
确认记录保存在哪里？
```

典型高风险操作：

```text
删除文件
提交正式投标文件
发送邮件
写业务系统
导出正式盖章文件
```

### 2.10 Memory / Knowledge / Artifact 边界

三者必须区分：

```text
Memory：Agent 记住的偏好、状态、历史决策
Knowledge：可检索知识库、资料、文档
Artifact：Agent 生成出来的交付物
```

如果三者混在一起，后续会导致上下文污染、知识污染和产物管理混乱。

### 2.11 失败恢复策略

Agent 一定会失败，失败策略不能只靠“让 AI 再想想”。

应提前定义：

```text
重试
降级
追问
跳过
回滚
转人工
生成缺口清单
```

失败恢复策略应进入协议和 Runtime。

### 2.12 可观测性标准

APD 已经开始做白盒化，但需要标准化事件协议。

至少记录：

```text
每轮 LLM 输入输出
每次工具调用
每个 Job 状态
每个 Artifact 写入
每次权限拦截
每次人工确认
每个失败原因
```

统一事件协议是调试、审计、评测和回放的基础。

### 2.13 Agent 安全边界

如果底层 Engine 能执行命令、读写文件、联网调用，就必须有程序级安全边界。

需要明确：

```text
允许读哪些目录？
允许写哪些目录？
允许执行哪些命令？
能不能联网？
能不能访问密钥？
能不能调用系统命令？
```

Prompt 约束不够，必须有 Sandbox / Permission Policy。

### 2.14 多 Runtime 兼容

APD 不应只绑定一个 Runtime。

未来可支持：

```text
open_claude Engine
LangGraph Runtime
OpenAI Agents SDK
自研 Runtime
普通 HTTP Agent
```

APD 协议应保持上层通用，通过 Adapter 接不同 Runtime。

### 2.15 Agent 模板库 / Marketplace

如果 APD 开源，长期价值不只在平台代码，也在模板沉淀。

可以沉淀：

```text
投标 Agent 模板
写作 Agent 模板
知识图谱 Agent 模板
客服 Agent 模板
文档解析 Agent 模板
```

每个模板应包含：

```text
协议模板
工具模板
评测样本
产物模型
调试样本
最佳实践
```

## 3. 建议进入路线图的重点

优先进入设计和后续任务的 10 个方向：

```text
1. Agent Lifecycle
2. Environment Policy
3. Tool Governance
4. Output Contract
5. Eval System
6. Release / Rollback Model
7. Security Sandbox Policy
8. Runtime Adapter Layer
9. Protocol Diff / Migration
10. Template Library
```

## 4. 当前阶段的判断

APD 当前已经开始解决：

```text
怎么设计 Agent
怎么生成 Agent 工程
怎么真实调试 Agent
怎么让 AI 修复 Agent
怎么通过协议 diff 增量演进 Agent
```

下一层要继续解决：

```text
怎么治理 Agent
怎么发布 Agent
怎么评测 Agent
怎么隔离 Agent
怎么长期维护 Agent
```

一句话：

```text
APD 不应止步于“生成和调试 Agent”，还要逐步补齐“治理、发布、评测、隔离和长期维护 Agent”的平台能力。
```
