# open_claude 从 CLI 改造成服务化 Agent Engine 的方案

> 目标：把 `/home/data/rag/open_claude/Openclaude-openclaude` 从单人 CLI 工具，逐步改造成可被每个业务 Agent 内嵌使用的服务化执行引擎。它不是 APD 平台本身，而是每个业务 Agent 的底层 Runtime Engine。

## 1. 背景

当前 APD 已经形成两条平级落地路线：

```text
路线 A：真实调试台 / Delegated Agent
- 产物：open_claude 包装型业务 Agent
- 执行能力主要来自 open_claude

路线 B：工程开发台 / 自研 Agent 工程
- 产物：自研 Planner / Validator / Executor / Tools / Runtime
- 执行能力需要自己实现
```

当前体验表明，路线 A 效果更强，原因是 open_claude 本身已经具备成熟工程 Agent 能力。

因此下一阶段不是重新造一个 open_claude，而是把 open_claude 抽象成可复用的服务化 Agent Engine。

## 2. 关键定义

### 2.1 APD 协议

```text
APD 协议 = 业务边界规范 + 操作规约 + 验收标准
```

它定义业务世界：目标、对象、状态、意图、操作、风险、权限、产物、评测。

### 2.2 open_claude Engine

```text
open_claude Engine = 单任务执行核心 + 工具系统 + LLM 推理 + 多步循环 + 事件输出
```

它负责把任务真正执行出来。

### 2.3 Delegated Agent Runtime

```text
Delegated Agent Runtime = API / Job / Task Pack / Workspace / Artifact / Trace / Runner Adapter
```

它负责把业务 Agent 服务化，并调用 open_claude Engine 执行任务。

## 3. 为什么不能只继续用 CLI

CLI 模式天然适合：

```text
一个用户
一个终端
一个工作目录
一个任务
```

但业务 Agent 服务需要：

```text
多个用户
多个会话
多个 Job
多个工作区
多个并发任务
可取消
可清理
可观测
可审计
```

如果继续共用一个 CLI，会出现：

```text
上下文串线
文件串改
stdout/stderr 混杂
任务互相阻塞
无法取消指定任务
无法隔离用户数据
无法稳定归档产物
```

所以必须服务化。

## 4. 总体目标架构

```text
业务 Agent 服务
  ├── API 层
  ├── Auth / Session
  ├── APD 协议配置
  ├── Job Manager
  ├── Worker Pool
  ├── Workspace Manager
  ├── Event Stream
  ├── Artifact Store
  └── open_claude Engine
        ├── Core Engine
        ├── LLM Adapter
        ├── Tool Registry
        ├── Permission Policy
        ├── Command Runner
        ├── File Tools
        ├── Trace Writer
        └── Result Contract
```

## 5. open_claude 源码建议分层

建议不要直接把原 CLI 改乱，而是在 open_claude 内部分层：

```text
open_claude/
  core/
    engine.ts
    task_pack.ts
    events.ts
    result.ts
    trace.ts
    tool_registry.ts
    permission.ts
    workspace.ts

  server/
    api.ts
    job_manager.ts
    worker_pool.ts
    session_store.ts
    artifact_store.ts
    sse.ts

  cli/
    cli.ts
```

### 5.1 core 层

负责单个任务如何执行。

能力：

```text
接收 Task Pack
组装上下文
调用 LLM
选择工具
执行工具
记录事件
输出 Result
写入 Artifact
```

### 5.2 server 层

负责多个用户、多个 Job 的服务化管理。

能力：

```text
创建 Job
排队
并发控制
取消 Job
查询状态
SSE 事件流
Job workspace 隔离
Artifact 下载
日志查询
```

### 5.3 cli 层

保留原 CLI 能力。

CLI 不再直接包含全部逻辑，而是调用 core engine。

```text
CLI 输入 → Task Pack → core engine → CLI 渲染输出
```

这样 CLI 和服务端可以共用同一个执行核心。

## 6. Job 模型

### 6.1 Job 状态

```text
queued
running
waiting_user
completed
failed
timeout
cancelled
```

### 6.2 Job 目录

```text
data/jobs/{job_id}/
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
    report.md
    manifest.json
  job.json
```

### 6.3 Job 隔离原则

```text
1 个 Job = 1 个独立 workspace
不同用户 Job 不共享 cwd
open_claude 只能访问授权 workspace
任务结束后归档 trace/artifacts
超时或取消必须 kill worker
```

## 7. APD Task Pack 协议

open_claude Engine 应原生支持 APD Task Pack。

建议输入：

```json
{
  "task_id": "job_xxx",
  "agent": {
    "name": "投标文件生成 Agent",
    "goal": "根据招标文件生成可编辑投标文件"
  },
  "user_input": "请根据这个招标文件生成技术标大纲",
  "business_protocol": {
    "objects": [],
    "operations": [],
    "validators": [],
    "artifacts": [],
    "permission_policy": {},
    "eval_cases": []
  },
  "context": {
    "files": [],
    "knowledge": [],
    "memory": []
  },
  "constraints": {
    "allowed_dirs": [],
    "forbidden_commands": [],
    "max_runtime_seconds": 900,
    "max_output_files": 50
  },
  "expected_outputs": {
    "result_json": "artifacts/result.json",
    "report_md": "artifacts/report.md",
    "artifact_manifest": "artifacts/manifest.json"
  }
}
```

## 8. 事件协议

open_claude Engine 需要结构化事件输出，而不是只靠终端文本。

推荐 JSONL 事件：

```json
{"type":"job_started","job_id":"job_xxx","time":"..."}
{"type":"planning_started","message":"正在分析用户目标"}
{"type":"tool_call_started","tool":"read_file","input":{"path":"..."}}
{"type":"tool_call_finished","tool":"read_file","ok":true}
{"type":"artifact_written","path":"artifacts/report.md"}
{"type":"job_completed","result_path":"artifacts/result.json"}
```

前端只展示简短状态，详细事件放入 Trace / Inspector。

## 9. 权限模型

open_claude Engine 必须支持权限策略。

最低要求：

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

权限判断不能只靠 prompt，必须有程序校验层。

## 10. 并发模型

### 10.1 小规模内网版本

适合当前 5 到 10 人内部使用：

```text
全局并发上限：1 到 3
每个 Job 一个子进程
每个 Job 一个 workspace
任务结束后保留 artifacts，清理临时进程
```

### 10.2 服务化版本

```text
Job Queue
Worker Pool
Per-Agent concurrency limit
Per-User concurrency limit
Timeout Killer
Workspace GC
Artifact Retention Policy
```

### 10.3 不推荐

```text
多个用户共用一个 open_claude 进程
多个 Job 共用一个 cwd
stdout/stderr 混到同一个日志
没有任务超时
没有取消机制
```

## 11. HTTP API 草案

```text
POST   /api/jobs
GET    /api/jobs/{job_id}
GET    /api/jobs/{job_id}/events
GET    /api/jobs/{job_id}/events/stream
POST   /api/jobs/{job_id}/cancel
POST   /api/jobs/{job_id}/retry
GET    /api/jobs/{job_id}/artifacts
GET    /api/jobs/{job_id}/artifacts/{name}
GET    /api/health
GET    /api/ready
```

创建 Job：

```json
{
  "task_pack": {},
  "session_id": "user_session",
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

## 12. 与 APD 的集成方式

### 12.1 当前方式

```text
APD 真实调试台
→ 生成 Delegated Agent 工程
→ FastAPI Runner 启动 open_claude CLI
→ 收 stdout/stderr/artifacts
```

### 12.2 目标方式

```text
APD 真实调试台
→ 生成 Delegated Agent 工程
→ 调用 open_claude Engine HTTP API
→ 订阅 SSE events
→ 展示白盒过程
→ 读取 artifacts/result.json
```

### 12.3 好处

```text
不再依赖 ttyd / 终端文本解析
多用户并发更清晰
任务取消更可靠
事件更结构化
权限更可控
产物契约更稳定
```

## 13. 改造阶段

### 阶段 0：调研 open_claude 当前源码

目标：先不改代码，画出当前结构。

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

不改变架构，只增强 CLI：

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

### 阶段 2：抽 core engine

把 CLI 内部主循环抽成可调用模块。

验收：

```text
CLI 调 core engine
测试脚本也能调 core engine
行为与原 CLI 保持一致
```

### 阶段 3：最小 HTTP Server

增加服务端模式：

```text
open_claude-engine serve --host 127.0.0.1 --port 7001
```

支持：

```text
POST /api/jobs
GET /api/jobs/{id}
GET /api/jobs/{id}/events/stream
```

### 阶段 4：Job 隔离与并发

增加：

```text
Worker Pool
Job Workspace
Timeout
Cancel
Queue
```

### 阶段 5：APD 真实调试台切换到 Engine API

APD 支持两种 runner：

```text
open_claude_cli
open_claude_engine_api
```

先并存，稳定后默认使用 Engine API。

## 14. 风险

| 风险 | 说明 | 处理 |
|---|---|---|
| 破坏原 CLI | 直接改主入口容易破坏现有能力 | 先抽 core，CLI 保持兼容 |
| 并发串线 | 多用户共享 cwd 或进程 | 每 Job 独立 workspace |
| 权限失控 | prompt 约束不可靠 | 程序级 permission policy |
| 输出不稳定 | 只解析终端文本 | JSON events + result contract |
| 任务卡死 | open_claude 长时间无输出 | timeout + heartbeat + cancel |
| 产物丢失 | 输出散落在 workspace | artifact manifest |

## 15. 第一阶段 DoD

第一阶段只做最小可验证，不追求完整产品化。

必须满足：

```text
1. 原 open_claude CLI 仍能正常使用。
2. 支持读取 APD Task Pack。
3. 支持写 trace/events.jsonl。
4. 支持写 artifacts/result.json。
5. 支持一个 HTTP Server 创建 Job。
6. 每个 Job 有独立 workspace。
7. 支持任务状态查询。
8. 支持取消任务。
9. APD 可以接入这个 HTTP API 做一次真实调试。
```

## 16. 当前建议

下一步不要直接重构 open_claude。

正确顺序：

```text
1. 先调研 open_claude 当前源码结构。
2. 产出 current architecture 文档。
3. 找出最小侵入点。
4. 先实现 --apd-task-pack / --json-events / --result-file。
5. 再抽 core engine。
6. 最后做 server/job manager。
```

原因：

```text
open_claude 当前强能力来自复杂 CLI 工程，直接大改风险高。
先把输出协议和 Task Pack 打通，收益最大，风险最小。
```
