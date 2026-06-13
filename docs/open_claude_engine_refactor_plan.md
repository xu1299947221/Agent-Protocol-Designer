# open_claude 从 CLI 改造成服务化 Agent Engine 的方案

> 目标：把 `/home/data/rag/open_claude/Openclaude-openclaude` 从单人 CLI 工具，逐步改造成可被每个业务 Agent 内嵌使用的服务化执行引擎。它不是 APD 平台本身，而是每个业务 Agent 的底层 Runtime Engine。

## 1. 结论先行

当前判断：

```text
APD 负责设计业务 Agent 的协议边界。
open_claude 适合作为业务 Agent 的通用执行引擎。
业务 Agent 服务负责把二者组合成可部署、可多人使用、可观测、可治理的系统。
```

所以后续方向不是把 APD 改成 open_claude，也不是把 open_claude 改成 APD，而是形成三层结构：

```text
APD 协议层
  ↓
业务 Agent 服务层
  ↓
open_claude Engine 执行层
```

## 2. 为什么需要改造

当前 open_claude 以 CLI 形态运行，天然适合：

```text
一个用户
一个终端
一个目录
一个任务
人工盯着输出
```

但业务 Agent 服务需要：

```text
多个用户
多个会话
多个任务
服务端 API 调用
任务排队
任务取消
超时控制
工作区隔离
结构化事件
产物归档
权限审计
```

如果继续直接把 CLI 当服务端 Runtime 用，会出现：

- 多个用户上下文串线
- 多个任务 stdout/stderr 混在一起
- 无法可靠取消某个任务
- 无法准确判断任务状态
- 无法区分“思考过程”和“最终回复”
- 无法稳定收集产物
- 无法做权限和目录隔离
- 无法水平扩展

因此，必须从 CLI 进程模型升级成 Engine 服务模型。

## 3. open_claude 的新定位

open_claude 不应只被看成命令行工具。

更合理的定位是：

```text
open_claude = 通用工程类 Agent 执行引擎
```

它已经具备的关键能力：

- LLM 推理
- 多步 Agent 循环
- 工具调用
- 文件读取
- 文件编辑
- 命令执行
- 观察反馈
- 错误修复
- 工程上下文管理
- 终端交互

类比 Java 开发体系：

```text
APD 协议 ≈ 业务契约 / 领域规约
业务 Agent 服务 ≈ 具体业务应用
open_claude Engine ≈ Spring Boot + 工具系统 + LLM Agent Runtime
```

这个类比的重点是：open_claude 可以作为多个业务 Agent 复用的底层框架。

## 4. 总体架构

目标架构：

```text
用户 / 业务系统
  ↓
业务 Agent API
  ↓
Session / Auth / Permission
  ↓
APD Protocol Loader
  ↓
Task Pack Builder
  ↓
Job Manager
  ↓
Workspace Manager
  ↓
open_claude Engine API
  ↓
Core Engine
  ↓
LLM / Tools / File / Shell / Web / RAG
  ↓
Events / Artifacts / Trace
  ↓
业务 Agent API 返回结果
```

核心原则：

```text
一个用户请求不直接操作 open_claude CLI。
一个用户请求先变成一个可审计 Task Pack。
一个 Task Pack 创建一个 Job。
一个 Job 使用独立 Workspace。
open_claude Engine 执行 Job 并输出结构化事件和产物。
```

## 5. 三层职责边界

### 5.1 APD 协议层

负责业务语义和边界：

- 业务目标
- 对象模型
- 用户意图
- 操作列表
- 状态模型
- 权限策略
- 风险等级
- 人审点
- 产物要求
- 验收标准
- 评测用例

APD 不负责真正执行复杂任务。

### 5.2 业务 Agent 服务层

负责把 APD 协议部署成服务：

- 登录和租户
- 会话管理
- API 入参校验
- Task Pack 组装
- Job 创建
- Job 查询
- 事件转发
- 产物下载
- 版本记录
- 运行诊断
- 用户侧对话体验

这一层是每个具体业务 Agent 的应用层。

### 5.3 open_claude Engine 层

负责复杂任务执行：

- 读取 Task Pack
- 组装执行上下文
- 调用 LLM
- 多步规划与行动
- 调用工具
- 执行命令
- 修改文件
- 观察结果
- 写事件
- 写产物
- 返回最终结果

它不应该知道 APD WebUI 的页面细节。

## 6. 源码分层建议

不要直接把现有 CLI 改乱。建议在 open_claude 内部逐步拆成：

```text
open_claude/
  core/
    engine.ts
    task_pack.ts
    context_builder.ts
    loop.ts
    model_adapter.ts
    tool_registry.ts
    permission.ts
    workspace.ts
    events.ts
    trace.ts
    result.ts

  server/
    api.ts
    job_manager.ts
    worker_pool.ts
    session_store.ts
    workspace_manager.ts
    artifact_store.ts
    sse.ts
    health.ts

  cli/
    cli.ts
    renderer.ts
    interactive.ts
```

### 6.1 core 层

core 层只关心单个任务怎么执行。

输入：

```text
Task Pack + Workspace + Permission Policy + Runtime Config
```

输出：

```text
Event Stream + Result Contract + Artifacts + Trace
```

### 6.2 server 层

server 层负责多 Job 管理。

能力：

- 创建 Job
- Job 排队
- 并发限制
- 超时终止
- 取消任务
- 查询状态
- SSE 事件流
- 工作区隔离
- 产物归档

### 6.3 cli 层

CLI 继续保留。

但 CLI 不再自己持有全部执行逻辑，而是调用 core：

```text
CLI 输入 → Task Pack → core engine → CLI renderer
```

这样可以保证：

```text
原 CLI 体验保留。
服务端 Engine 复用同一套执行核心。
```

## 7. Task Pack 协议

Task Pack 是 APD 与 open_claude Engine 之间的关键契约。

建议结构：

```json
{
  "task_id": "job_xxx",
  "agent": {
    "id": "bid_writer",
    "name": "投标文件生成 Agent",
    "goal": "根据招标文件生成可编辑投标文件"
  },
  "user": {
    "id": "u_001",
    "role": "project_manager"
  },
  "session": {
    "id": "s_001",
    "history_summary": "用户已确认先生成技术标目录"
  },
  "user_input": "请根据招标文件生成技术标大纲",
  "business_protocol": {
    "objects": [],
    "operations": [],
    "state_model": {},
    "permission_policy": {},
    "validators": [],
    "artifacts": [],
    "eval_cases": []
  },
  "context": {
    "files": [],
    "knowledge": [],
    "memory": [],
    "runtime_params": {}
  },
  "constraints": {
    "allowed_dirs": [],
    "readonly_dirs": [],
    "writable_dirs": [],
    "forbidden_paths": [],
    "allowed_commands": [],
    "forbidden_commands": [],
    "network_policy": "restricted",
    "max_runtime_seconds": 900,
    "max_output_files": 50
  },
  "expected_outputs": {
    "final_answer": true,
    "result_json": "artifacts/result.json",
    "report_md": "artifacts/report.md",
    "artifact_manifest": "artifacts/manifest.json"
  }
}
```

## 8. Job 模型

### 8.1 Job 状态

```text
queued        已创建，等待执行
running       正在执行
waiting_user  等待用户确认或补充
completed     成功完成
failed        执行失败
timeout       超时终止
cancelled     用户取消
```

### 8.2 Job 目录结构

```text
data/jobs/{job_id}/
  job.json
  task_pack.json
  task_pack.md
  workspace/
  trace/
    events.jsonl
    llm_calls.jsonl
    tool_calls.jsonl
    stdout.log
    stderr.log
  artifacts/
    result.json
    final_answer.md
    report.md
    manifest.json
```

### 8.3 Job 隔离原则

```text
1 个 Job = 1 个独立 workspace
1 个 Job = 1 条独立事件流
1 个 Job = 1 份 trace
1 个 Job = 1 组 artifacts
```

禁止：

```text
多个 Job 共用一个 cwd
多个用户共用一个 open_claude 进程上下文
多个 Job 输出到同一个 stdout.log 后再硬解析
```

## 9. 事件协议

服务化后不能只依赖终端文本，必须输出结构化事件。

建议 JSONL / SSE 事件：

```json
{"type":"job_started","job_id":"job_xxx","time":"2026-06-13T10:00:00+08:00"}
{"type":"planning_started","summary":"正在分析用户目标和协议约束"}
{"type":"llm_call_started","purpose":"intent_planning","model":"gpt-5.5"}
{"type":"llm_call_finished","purpose":"intent_planning","ok":true,"tokens":1234}
{"type":"tool_call_started","tool":"read_file","input":{"path":"workspace/bid.pdf"}}
{"type":"tool_call_finished","tool":"read_file","ok":true,"duration_ms":120}
{"type":"artifact_written","path":"artifacts/report.md"}
{"type":"job_completed","result_path":"artifacts/result.json"}
```

前端展示策略：

```text
默认只展示人能看懂的简短过程。
需要排查时展开完整 Trace。
```

## 10. Result Contract

每个 Job 完成后必须写稳定结果文件。

```json
{
  "job_id": "job_xxx",
  "status": "completed",
  "final_answer": "已生成技术标大纲...",
  "summary": "完成招标文件解析和技术标目录生成",
  "artifacts": [
    {"name":"report.md","path":"artifacts/report.md","type":"markdown"}
  ],
  "diagnostics": {
    "warnings": [],
    "missing_capabilities": [],
    "next_actions": []
  }
}
```

APD 和业务 Agent 不应从 CLI 屏幕文本里猜最终结果，而应读取 `result.json`。

## 11. 权限模型

权限必须由程序校验，不能只靠 prompt。

最低权限项：

```text
allowed_dirs
readonly_dirs
writable_dirs
forbidden_paths
allowed_commands
forbidden_commands
network_policy
max_file_size
max_command_seconds
requires_human_confirmation
```

关键原则：

```text
LLM 可以建议操作。
Engine Permission 层决定能不能执行。
```

## 12. 并发模型

### 12.1 小规模内网版本

适合当前内部 5 到 10 人使用：

```text
全局并发：1 到 3
每用户并发：1
每 Job 一个子进程或隔离执行上下文
每 Job 独立 workspace
任务完成后保留 artifacts，定期清理临时文件
```

### 12.2 生产版本

```text
Job Queue
Worker Pool
Per-Agent concurrency limit
Per-User concurrency limit
Timeout Killer
Heartbeat
Workspace GC
Artifact Retention Policy
Audit Log
```

### 12.3 需要避免

```text
一个 open_claude 进程同时服务多个用户
多个用户共用同一个上下文窗口
任务无法取消
任务失败没有最终状态
产物散落在工作目录里
```

## 13. HTTP API 草案

```text
GET    /api/health
GET    /api/ready
POST   /api/jobs
GET    /api/jobs/{job_id}
GET    /api/jobs/{job_id}/events
GET    /api/jobs/{job_id}/events/stream
POST   /api/jobs/{job_id}/cancel
POST   /api/jobs/{job_id}/retry
GET    /api/jobs/{job_id}/artifacts
GET    /api/jobs/{job_id}/artifacts/{name}
```

创建 Job：

```json
{
  "task_pack": {},
  "stream": true
}
```

返回：

```json
{
  "job_id": "job_xxx",
  "status": "queued",
  "events_url": "/api/jobs/job_xxx/events/stream"
}
```

## 14. 与 APD 的集成方式

### 14.1 当前方式

```text
APD 真实调试台
→ 生成 Delegated Agent 工程
→ FastAPI Runner 启动 open_claude CLI
→ 捕获 stdout/stderr
→ 尝试整理结果
```

### 14.2 目标方式

```text
APD 真实调试台
→ 生成 Task Pack
→ 调用 open_claude Engine HTTP API
→ 订阅 SSE events
→ 展示白盒过程
→ 读取 artifacts/result.json
→ 提供“让 AI 修这个问题”诊断任务包
```

### 14.3 兼容方式

APD 应同时支持两种 Runner：

```text
open_claude_cli        当前兼容模式
open_claude_engine_api 目标服务模式
```

先并存，不要一次性切换。

## 15. 改造阶段

### 阶段 0：调研当前 open_claude 源码

目标：先不改代码，画出现状。

需要回答：

```text
CLI 入口在哪里
主循环在哪里
工具注册在哪里
文件工具在哪里
命令执行在哪里
权限确认在哪里
终端渲染在哪里
模型调用在哪里
上下文压缩在哪里
错误恢复在哪里
```

产物：

```text
docs/open_claude_current_architecture.md
```

### 阶段 1：APD 友好 CLI 参数

最小侵入增强 CLI：

```text
--apd-task-pack path
--trace-dir path
--result-file path
--json-events
--no-interactive-confirm
```

验收：

```text
open_claude 可以读取 task_pack.json
执行过程中写 events.jsonl
结束后写 result.json
原 CLI 仍可用
```

### 阶段 2：抽 Core Engine

把 CLI 内部主循环抽成可调用模块。

验收：

```text
CLI 调 core engine
测试脚本也能调 core engine
原 CLI 行为保持一致
```

### 阶段 3：最小 HTTP Server

新增服务模式：

```text
open_claude-engine serve --host 127.0.0.1 --port 7001
```

最小支持：

```text
POST /api/jobs
GET /api/jobs/{id}
GET /api/jobs/{id}/events/stream
POST /api/jobs/{id}/cancel
```

### 阶段 4：Job 隔离与并发

补齐：

```text
Job Queue
Worker Pool
Job Workspace
Timeout
Cancel
Heartbeat
Artifact Store
```

### 阶段 5：APD 真实调试台切换到 Engine API

APD 增加 Engine API Runner。

验收：

```text
真实调试台不再依赖 ttyd 文本解析获得结果。
真实调试台可以展示结构化事件。
真实调试台可以读取 result.json。
真实调试台可以取消和重试 Job。
```

## 16. 第一阶段 DoD

第一阶段完成标准：

```text
1. 原 open_claude CLI 仍能正常使用。
2. 支持读取 APD Task Pack。
3. 支持写 trace/events.jsonl。
4. 支持写 artifacts/result.json。
5. 支持最小 HTTP Server 创建 Job。
6. 每个 Job 有独立 workspace。
7. 支持任务状态查询。
8. 支持取消任务。
9. APD 可以通过 HTTP API 完成一次真实调试。
```

## 17. 主要风险与处理

| 风险 | 说明 | 处理 |
|---|---|---|
| 破坏原 CLI | 直接改主入口容易破坏现有能力 | CLI 保持兼容，先加旁路参数 |
| 并发串线 | 多用户共享 cwd 或进程 | 每 Job 独立 workspace |
| 权限失控 | prompt 约束不可靠 | 程序级 Permission Policy |
| 输出不稳定 | 只解析终端文本 | JSON events + result contract |
| 任务卡死 | open_claude 长时间无输出 | timeout + heartbeat + cancel |
| 产物丢失 | 输出散落在 workspace | artifact manifest |
| 改造过大 | 一次性重构风险高 | 先 CLI 参数，再抽 core，再 server |

## 18. 当前建议执行顺序

不要马上大改 open_claude。

正确顺序：

```text
1. 调研 open_claude 当前源码结构。
2. 输出 open_claude_current_architecture.md。
3. 找出最小侵入点。
4. 实现 --apd-task-pack / --json-events / --result-file。
5. 让 APD 真实调试台读取 result.json，而不是猜 stdout。
6. 抽 Core Engine。
7. 实现 Engine HTTP Server。
8. APD 切换到 Engine API Runner。
```

原因：

```text
open_claude 当前强能力来自复杂 CLI 工程。
直接大改风险高。
先把输入协议、事件协议和结果协议打通，收益最大，风险最小。
```

## 19. 每个业务 Agent 如何部署

用户明确区分了两件事：

```text
APD 平台 5 到 10 人内部使用。
APD 生成或治理出来的每个业务 Agent，未来可能给多个业务用户同时使用。
```

因此，不能只按 APD 平台自身的小并发来设计 Engine。

每个业务 Agent 的推荐部署形态：

```text
business-agent-service/
  api/                 # 业务用户访问入口
  protocol/            # APD 导出的业务协议
  task-pack-builder/   # 把用户请求转成 Task Pack
  runtime-adapter/     # 调 open_claude Engine
  storage/             # 会话、Job、Trace、Artifact
  ui/                  # 用户侧对话或业务页面
```

open_claude Engine 可以有两种部署方式：

### 19.1 内嵌式 Engine

```text
每个业务 Agent 服务自带一个 open_claude Engine 进程或服务。
```

优点：

- 隔离清晰
- 一个业务 Agent 故障不影响其他 Agent
- 权限和目录更容易控制

缺点：

- 资源占用更高
- 多个 Agent 需要分别升级 Engine

### 19.2 共享式 Engine 集群

```text
多个业务 Agent 调用同一个 open_claude Engine 服务集群。
```

优点：

- 资源利用率更高
- Engine 统一升级
- 统一审计和运维

缺点：

- 多租户隔离要求更高
- 权限、队列、限流必须更严格
- 一旦 Engine 集群故障，影响面更大

当前建议：

```text
第一阶段先做内嵌式 Engine。
等接口和事件协议稳定后，再考虑共享式 Engine 集群。
```

原因：

```text
内嵌式更容易验证，权限边界更简单，符合当前探索阶段。
```

## 20. 与“直接封装 open_claude CLI”的区别

短期 APD 可以继续封装 open_claude CLI 做真实调试，但这不是最终形态。

区别如下：

| 维度 | CLI 封装 | 服务化 Engine |
|---|---|---|
| 调用方式 | 启动命令行进程 | HTTP / SDK / Queue |
| 输出 | 终端文本为主 | 结构化事件 + result.json |
| 并发 | 弱 | Job Queue + Worker Pool |
| 取消 | kill 进程，粒度粗 | cancel job，状态明确 |
| 隔离 | 依赖 cwd 和命令参数 | Job workspace + Permission Policy |
| 产物 | 需要从目录猜 | Artifact Contract |
| 可观测 | stdout/stderr | Trace / Events / LLM Calls / Tool Calls |
| 多用户 | 容易串线 | 可设计成多租户 |

所以当前路径应该是：

```text
CLI 封装验证效果
  ↓
补 Task Pack / JSON Events / Result Contract
  ↓
抽 Core Engine
  ↓
服务化 Engine
```

而不是一开始就推倒重写。

## 21. 真实调试台修复与 Engine 改造的边界

真实调试台里的 AI 诊断修复不应默认等同于 Engine 改造。

因为真实调试台修复可能属于三类：

```text
1. 当前业务 Agent 的场景定制问题
2. Delegated Runtime 的通用服务外壳问题
3. open_claude Engine 的底层执行能力问题
```

默认处理规则：

```text
场景业务修复 → 只改当前生成的业务 Agent 工程
Runtime 通用修复 → 评审后回灌 Delegated Runtime SDK / 模板
Engine 能力修复 → 单独进入 open_claude Engine 改造计划
```

### 21.1 不应回灌的场景业务修复

例如：

- 某个投标 Agent 需要优先解析评分办法
- 某个写作 Agent 需要固定品牌语气
- 某个报告 Agent 需要特定章节顺序
- 某个知识图谱 Agent 需要特定实体关系 schema

这些属于当前业务 Agent 的协议、提示词、Task Pack 或业务代码调整。

它们不应该进入通用 Runtime 或 Engine。

### 21.2 应回灌 Runtime 的通用问题

例如：

- Job 状态流转错误
- queued/running/completed 显示不准
- result.json 解析不稳定
- artifact manifest 缺失
- cancel/restart 不生效
- stdout/stderr 没有正确转换为事件
- 工作区目录创建或清理有问题

这些属于 Delegated Runtime 服务外壳问题，应回灌到 Runtime SDK 或生成模板。

### 21.3 应进入 Engine 改造的问题

例如：

- open_claude 无法原生读取 Task Pack
- open_claude 无法输出结构化 JSON Events
- open_claude 无法稳定写 result.json
- 多用户或多 Job 容易上下文串线
- 权限控制只能靠 prompt，缺少程序级校验
- 长任务无法 heartbeat/cancel/timeout

这些属于 open_claude Engine 层问题，应进入本文的 Engine API 改造阶段。

### 21.4 后续 APD 页面应增加的判断

真实调试台的“让 AI 修这个问题”应先产出修复归类：

```json
{
  "problem_type": "scenario|runtime|engine",
  "patch_target": "current_agent|runtime_template|open_claude_engine",
  "backport_required": false,
  "engine_refactor_required": false,
  "reason": "说明为什么归到这一类"
}
```

这一步是必要的，因为：

```text
真实调试台修好的代码，不一定适合通用情况。
```

只有经过归类和确认，才能决定是否把修复沉淀成平台能力。

## 22. APD 全局增量模式：协议变更如何同步到已有 Agent 工程

本节虽然记录在 open_claude Engine 改造方案中，但它不是只针对 open_claude 路线。

它是 APD 整体系统必须补齐的全局能力，同时影响两条路线：

```text
路线 A：委托型真实调试 / Delegated Agent
路线 B：自研工程开发 / 自研 Runtime
```

### 22.1 为什么需要增量模式

真实开发中，用户不会一次性把 Agent 需求说完整。

常见情况是：

```text
已经基于协议 v1 做出了一个可用 Agent 工程
  ↓
业务方提出新需求 / 新边界 / 新规则
  ↓
协议需要变成 v2
  ↓
已有工程也要跟着增量改造
```

如果没有增量模式，会出现：

- 协议已经更新，但工程代码还是旧的
- AI 直接改代码，协议没有记录新能力
- 新需求没有生成评测样本
- 重新生成工程会丢掉手工修复
- 后续诊断时不知道代码和协议是否一致

所以 APD 需要从：

```text
生成一次工程
```

升级为：

```text
协议持续演进 + 工程持续迁移
```

### 22.2 当前实现状态

当前 APD 已经具备部分能力：

```text
同一会话可以继续对话更新协议
会话有协议快照 snapshots
工程开发台有 workspace
工程开发台有版本保存和回滚
AI 可以在已有工作区里改代码
真实调试台可以修当前生成的业务 Agent 工程
```

但还没有形成完整闭环。

当前缺口：

```text
协议 v1 → 协议 v2 的结构化 diff
协议变更影响分析
把协议 v2 同步进已有工程
生成工程迁移任务包
自动补充评测用例
提示“当前工程落后于协议”
迁移后自动运行验证
```

所以当前状态是：

```text
可以人工走通增量开发，但还不是正式产品化闭环。
```

### 22.3 正确的增量流程

目标流程应是：

```text
已有 Agent 工程
  ↓
用户提出新业务需求
  ↓
回首页同一会话补充协议
  ↓
协议 v1 → 协议 v2
  ↓
APD 生成 protocol_diff
  ↓
APD 判断影响范围
  ↓
选择已有工作区
  ↓
生成 migration_task_pack
  ↓
工程开发台 / 真实调试台让 AI 修改代码
  ↓
同步协议文件、代码、评测样本
  ↓
运行调试验证
  ↓
保存工程版本
```

关键原则：

```text
业务新需求先改协议，再改工程。
小 bug 和纯实现问题可以直接进开发台。
```

### 22.4 协议 diff 应包含什么

`protocol_diff` 至少应包含：

```json
{
  "from_version": "v1",
  "to_version": "v2",
  "change_type": "add_capability|modify_boundary|add_tool|change_artifact|change_permission|bugfix",
  "added_objects": [],
  "changed_objects": [],
  "added_operations": [],
  "changed_operations": [],
  "removed_operations": [],
  "changed_validators": [],
  "changed_permissions": [],
  "changed_artifacts": [],
  "changed_eval_cases": [],
  "risk_level": "low|medium|high",
  "requires_human_review": true,
  "implementation_notes": []
}
```

它的作用不是展示给用户看 JSON，而是给工程迁移和 AI 修复提供稳定上下文。

### 22.5 工程迁移任务包

当协议变化后，APD 应生成 `migration_task_pack`。

建议结构：

```json
{
  "workspace_id": "ws_xxx",
  "route": "delegated|native",
  "protocol_diff": {},
  "current_project_summary": {},
  "files_likely_affected": [],
  "must_update_files": [],
  "must_not_touch_files": [],
  "test_cases_to_add": [],
  "acceptance_criteria": [],
  "rollback_plan": []
}
```

迁移任务包应明确告诉工程 AI：

```text
这不是重写项目。
这是基于协议 diff 对已有工程做增量改造。
必须保留已有业务能力。
必须同步协议文件和测试样本。
```

### 22.6 对 Delegated 路线的影响

Delegated 路线中，增量改造通常影响：

- `agent_definition.json`
- Task Pack 模板
- 业务约束描述
- 默认任务说明
- result contract
- 前端任务输入说明
- 场景业务代码
- 调试测试样本

如果是通用 Runtime 问题，才进入模板回灌。

如果是 open_claude 执行能力问题，才进入 Engine 改造。

所以 Delegated 路线的增量模式不是：

```text
每次新需求都改 open_claude
```

而是：

```text
优先改当前业务 Agent 的协议、Task Pack 和场景代码。
只有 Engine 层确实缺能力，才改 open_claude。
```

### 22.7 对自研 Runtime 路线的影响

自研路线中，增量改造通常影响：

- `protocol.json`
- Planner
- Intent Binding
- Validator
- Executor
- Tool Registry
- State Model
- Memory Policy
- Artifact Model
- Eval Cases
- Runtime Loop

这条路线的重点是：

```text
协议 diff 驱动自研 Runtime 能力成长。
```

也就是说，自研路线不是每次重新生成新 Agent，而是在已有工作区里持续补能力。

### 22.8 页面需要补的能力

APD 后续页面应增加：

```text
协议版本状态：当前会话协议版本
工程同步状态：当前工作区基于哪个协议版本生成
协议变更提示：协议已更新，工程未同步
生成迁移任务：把协议 diff 转成工程改造任务包
选择目标工作区：对哪个已有 Agent 工程应用变更
迁移后验证：运行测试样本和真实调试
保存版本：把迁移后的工程保存为新版本
```

用户体验应是：

```text
你刚才补充的新需求改变了 Agent 协议。
当前工程基于协议 v1，最新协议是 v2。
是否生成增量改造任务，并发送给工程开发台？
```

### 22.9 第一阶段实现建议

第一阶段不要做复杂自动合并，先做最小闭环：

```text
1. 每次协议变化保存 protocol_version。
2. 工作区 metadata 记录 created_from_protocol_version。
3. 当协议版本比工作区新时，页面提示“工程待同步”。
4. 生成 markdown 版 migration_task_pack。
5. 一键发送给 ttyd / open_claude。
6. 要求 AI 修改当前工作区并同步 protocol.json。
7. 用户调试通过后保存 workspace version。
```

第一阶段先不要求自动改代码，重点是把增量变更链路显式化、可追踪、可复用。
