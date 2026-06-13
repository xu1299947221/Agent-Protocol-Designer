# 独立部署型 Delegated Agent 完整架构设计

> 本文是 Delegated Agent 模式的完整蓝图。它不只描述第一阶段怎么做，而是把最终产品形态、APD 平台侧交互、生成 Agent 的独立部署形态、多用户、安全、队列、沙箱、数据模型、观测、版本和演进路径整体设计清楚。

> 当前实现状态：第一阶段 V1 已完成。APD 已能生成独立部署型 Delegated Agent zip，生成工程包含 FastAPI、Web 任务控制台、fake runner、真实 open_claude runner、日志/产物 API、README、Dockerfile 和 docker-compose.yml 草案。完整使用说明见 `docs/delegated_agent_usage.md`。

## 1. 设计目标

Delegated Agent 模式的最终目标：

```text
APD 可以生成一种独立部署的 Agent 服务工程。
该工程内置 open_claude/Codex 这类强执行器。
业务用户访问生成后的 Agent 服务，而不是访问 APD。
生成后的 Agent 可以接收任务、委托执行、实时回显、保存产物、审计过程，并逐步演进到生产部署。
```

核心原则：

```text
1. APD 是设计和生成平台，不是生成 Agent 的运行依赖。
2. 生成的 Agent 工程必须可独立部署。
3. 默认内置 open_claude，避免依赖 APD 服务器路径。
4. open_claude 是执行器，不是无约束黑盒。
5. Delegated Agent 必须有任务包、权限、工作区、Trace、Artifact 和结果契约。
6. 先保证闭环，再逐步增强安全、并发、沙箱和生产化。
```

## 2. 三份文档分工

当前 Delegated Agent 相关文档分三层：

| 文档 | 作用 |
|---|---|
| `docs/delegated_agent_mode_plan.md` | 方向和总体计划，回答“为什么做、做成什么” |
| `docs/delegated_agent_mode_development_plan.md` | 开发实施方案，回答“第一阶段怎么编码落地” |
| `docs/delegated_agent_mode_full_architecture.md` | 完整架构蓝图，回答“最终整体系统怎么设计” |

本文是第三层。

## 3. 产品定位

Delegated Agent 不是 APD 内部的 Agent IDE，也不是普通聊天机器人。

它是：

```text
可独立部署的任务型 Agent 服务。
```

它适合：

```text
文件处理
项目分析
代码改造
文档生成
招投标分析
知识库整理
数据清洗
自动测试/修复
复杂多步骤执行任务
```

不适合第一优先级支持：

```text
毫秒级客服问答
高并发低延迟 API
强事务核心业务
无沙箱公网执行任意命令
```

## 4. APD 平台侧完整交互

### 4.1 APD 中的模式选择

APD 未来生成 Agent 时应明确区分：

```text
1. Native Agent：原生业务 Agent
2. Workflow Agent：流程编排 Agent
3. Delegated Agent：委托执行型 Agent
```

选择 Delegated Agent 后，APD 进入专门向导。

### 4.2 Delegated Agent 创建向导

APD 向导应收集：

```text
Agent 名称
Agent 目标
典型用户任务
输入类型：文本/文件/目录/zip/API
输出类型：报告/JSON/文档/代码/文件包
允许读取范围
允许写入范围
禁止操作
是否需要人工确认
open_claude 版本来源
部署目标：本地/Docker/队列版/K8s
鉴权方式
是否生成示例任务
```

### 4.3 APD 输出产物

APD 最终输出：

```text
delegated-agent.zip
```

zip 内包含：

```text
Agent 服务代码
内置 open_claude
配置模板
Docker 部署文件
README
示例任务
测试脚本
架构说明
```

### 4.4 APD 不应该做的事

APD 不应该成为生成 Agent 的运行时依赖：

```text
生成后的 Agent 不需要连接 APD 才能运行。
生成后的 Agent 不依赖 APD 的数据库。
生成后的 Agent 不依赖 APD 的 open_claude 路径。
```

APD 可以作为开发期控制台，但不是生产依赖。

## 5. 生成 Agent 的最终产品形态

生成后的 Agent 应有两种入口：

```text
1. Web 任务控制台
2. HTTP API
```

### 5.1 Web 任务控制台

最终形态：

```text
顶部：Agent 名称、版本、运行模式、健康状态
左侧：新建任务、输入文件、参数配置
中间：任务列表、状态、耗时、发起人
右侧：任务详情、执行过程、日志、产物、诊断
底部：运行环境检查、模型配置、Runner 状态
```

### 5.2 HTTP API

最终 API 分组：

```text
/api/config
/api/jobs
/api/jobs/{job_id}
/api/jobs/{job_id}/events
/api/jobs/{job_id}/logs/stdout
/api/jobs/{job_id}/logs/stderr
/api/jobs/{job_id}/artifacts
/api/jobs/{job_id}/cancel
/api/jobs/{job_id}/retry
/api/jobs/{job_id}/approve
/api/agents/version
/api/health
```

### 5.3 使用体验目标

业务用户体验：

```text
提交任务
→ 看到任务排队/执行中
→ 看到执行过程摘要
→ 看到最终回复
→ 下载产物
→ 如果失败，看到失败原因和下一步建议
```

管理员体验：

```text
配置模型和 Runner
查看任务队列
查看失败任务
查看审计日志
配置并发/超时/权限
升级 Agent 版本
```

## 6. 总体架构

最终架构：

```text
Delegated Agent Service
├── Web UI
├── API Server
├── Auth / Permission Layer
├── Job Manager
├── Task Pack Builder
├── Queue / Scheduler
├── Runner Worker
├── Runner Adapter
│   ├── open_claude
│   └── codex / future runners
├── Workspace Manager
├── Sandbox Manager
├── Trace Store
├── Artifact Store
├── Result Parser
├── Audit Store
└── Observability
```

数据流：

```text
用户请求
  ↓
API 鉴权
  ↓
创建 Job
  ↓
准备 Workspace
  ↓
生成 Task Pack
  ↓
进入 Queue
  ↓
Worker 启动 Sandbox
  ↓
Runner 执行 open_claude
  ↓
采集 stdout/stderr/events/file changes
  ↓
解析 result.json/report.md
  ↓
写入 Job Result / Artifacts / Audit
  ↓
Web/API 返回结果
```

## 7. 运行时分层

### 7.1 Control Plane

职责：

```text
接收请求
鉴权
创建 Job
状态查询
任务取消/重试/审批
配置管理
```

### 7.2 Execution Plane

职责：

```text
启动 Runner
执行任务
采集日志
处理超时
回收进程
```

### 7.3 Data Plane

职责：

```text
保存 job.json
保存 events.jsonl
保存 stdout/stderr
保存 artifacts
保存 result
保存 audit
```

### 7.4 Design Plane

这部分属于 APD：

```text
设计 Agent 定义
生成 Task Pack 模板
生成权限策略
生成部署工程
```

## 8. Job 模型

### 8.1 Job 状态

完整状态：

```text
created
queued
preparing_workspace
building_task_pack
waiting_approval
running
collecting_artifacts
parsing_result
completed
partial_completed
failed
timeout
cancelled
rejected
```

### 8.2 Job 数据模型

```json
{
  "job_id": "job_xxx",
  "agent_id": "agent_xxx",
  "agent_version": "1.0.0",
  "tenant_id": "default",
  "user_id": "user_xxx",
  "status": "running",
  "message": "用户原始任务",
  "input_refs": [],
  "workspace_dir": "data/jobs/job_xxx/workspace",
  "artifacts_dir": "data/jobs/job_xxx/artifacts",
  "trace_dir": "data/jobs/job_xxx/trace",
  "runner": {
    "type": "open_claude",
    "version": "2.1.88",
    "mode": "bundled"
  },
  "resource_limits": {
    "timeout_seconds": 1800,
    "max_output_chars": 200000
  },
  "result": null,
  "error": null,
  "created_at": "...",
  "updated_at": "..."
}
```

### 8.3 Job 事件模型

`events.jsonl` 每行：

```json
{
  "ts": "...",
  "job_id": "...",
  "type": "runner_output",
  "level": "info",
  "message": "...",
  "payload": {}
}
```

事件类型：

```text
job_created
workspace_prepared
task_pack_created
queued
runner_started
runner_output
runner_error
file_created
artifact_collected
result_parsed
approval_required
job_completed
job_failed
job_timeout
job_cancelled
```

## 9. Task Pack 体系

Task Pack 是 Delegated Agent 的核心控制协议。

它必须包含：

```text
任务目标
用户原始请求
Agent 角色
允许操作
禁止操作
输入目录
输出目录
结果契约
验收标准
失败时输出要求
```

Task Pack 不是简单 prompt，而是：

```text
给 open_claude 的执行协议。
```

最终 Task Pack 应支持模板变量：

```text
{{ agent_name }}
{{ agent_goal }}
{{ job_id }}
{{ user_request }}
{{ input_manifest }}
{{ workspace_manifest }}
{{ artifacts_dir }}
{{ result_schema }}
{{ denied_operations }}
{{ acceptance_criteria }}
```

## 10. Runner Adapter 设计

### 10.1 Runner 统一接口

未来不只支持 open_claude，所以要定义 Runner Adapter：

```python
class RunnerAdapter:
    def check(self) -> RunnerHealth: ...
    def run(self, job: Job, task_pack: str, sink: EventSink) -> RunnerResult: ...
    def cancel(self, job_id: str) -> None: ...
```

### 10.2 open_claude Adapter

职责：

```text
定位内置 open_claude
构造 node dist/cli.js 命令
注入模型环境变量
设置 cwd 和 --add-dir
采集 stdout/stderr
处理超时和退出码
```

### 10.3 Codex Adapter

后续可支持：

```text
codex CLI
claude code CLI
其他内部执行器
```

但 V1 聚焦 open_claude。

## 11. open_claude 内置与版本管理

### 11.1 bundled 模式

APD 生成工程时复制 open_claude：

```text
runner/open_claude/Openclaude-openclaude
```

并生成：

```text
runner/open_claude_manifest.json
```

### 11.2 版本记录

manifest 必须记录：

```json
{
  "runner": "open_claude",
  "source_path": "/home/data/rag/open_claude/Openclaude-openclaude",
  "version": "2.1.88",
  "bundled_at": "...",
  "dist_cli": "dist/cli.js",
  "exclude": ["node_modules", ".git"]
}
```

### 11.3 升级策略

生成后的 Agent 可以升级 open_claude：

```text
替换 runner/open_claude/Openclaude-openclaude
更新 manifest
跑 Runner 自检
跑回归任务
```

APD 后续可提供：

```text
重新导出新版本 Agent
对比 Runner 版本
生成升级说明
```

## 12. 权限与安全模型

### 12.1 V1 轻量安全

```text
单机/内网
API Token
Job 独立目录
超时 kill
Task Pack 禁止事项
不建议公网开放
```

### 12.2 V2 沙箱安全

```text
Docker per job
只挂载 job workspace
只挂载 artifacts
限制 CPU/内存/磁盘
可关闭网络
容器结束即销毁
```

### 12.3 V3 企业安全

```text
RBAC
租户隔离
审计日志
敏感文件扫描
命令 allow/deny
网络策略
镜像签名
密钥托管
审批流
```

### 12.4 高风险操作

高风险包括：

```text
删除文件
覆盖用户输入
访问外部网络
执行 shell 写操作
读取密钥
修改系统配置
上传文件到外部服务
```

V1 主要提示约束，V2 起需要技术拦截。

## 13. 多用户与多租户设计

### 13.1 用户模型

```text
anonymous/local
user
admin
service_account
```

### 13.2 租户模型

```text
tenant_id
agent_id
user_id
job_id
```

所有 Job、Artifact、Audit 都应带 `tenant_id`。

### 13.3 V1 简化

V1 可以默认：

```text
tenant_id = default
user_id = local
```

但数据模型预留字段。

### 13.4 V2/V3

```text
多用户登录
API Token per user
租户级并发限制
租户级数据目录
租户级 Runner 配额
```

## 14. 队列与 Worker 架构

### 14.1 V1 本地线程

```text
API Server 内部线程执行任务
适合本地/内网验证
单进程
并发 1-2
```

### 14.2 V2 Redis Queue

```text
API Server
Redis
Worker
```

流程：

```text
POST /api/jobs
→ 写 DB
→ push queue
→ Worker pop job
→ run sandbox
→ update status
```

### 14.3 V3 分布式 Worker

```text
多个 Worker
Runner 镜像池
K8s Job
资源调度
失败重试
```

## 15. 存储设计

### 15.1 V1 文件存储

```text
data/jobs/{job_id}/job.json
data/jobs/{job_id}/trace/events.jsonl
data/jobs/{job_id}/trace/stdout.log
data/jobs/{job_id}/artifacts/
```

### 15.2 V2 数据库 + 文件

```text
PostgreSQL：job metadata、user、tenant、audit
文件系统/对象存储：logs、artifacts、input files
```

### 15.3 V3 对象存储

```text
MinIO/S3
Artifact signed URL
日志归档
冷热数据分层
```

## 16. Artifact 设计

Artifact 类型：

```text
markdown
json
docx
pdf
zip
code_patch
folder
log
```

Artifact 元数据：

```json
{
  "artifact_id": "art_xxx",
  "job_id": "job_xxx",
  "name": "report.md",
  "path": "artifacts/report.md",
  "type": "markdown",
  "size": 1234,
  "created_at": "...",
  "checksum": "..."
}
```

V2 起支持：

```text
Artifact 版本
Artifact 预览
Artifact 审批
Artifact 回滚
```

## 17. Trace 与观测

### 17.1 Trace 目标

用户应能知道：

```text
任务为什么失败
open_claude 做了什么
读写了哪些文件
最终产物在哪里
下一步怎么处理
```

### 17.2 Trace 内容

```text
task_pack.md
stdout.log
stderr.log
events.jsonl
result.json
runner metadata
artifact manifest
```

### 17.3 指标

```text
任务数
成功率
失败率
平均耗时
超时数
Runner 启动失败数
模型配置错误数
Artifact 生成成功率
```

## 18. 结果契约

Delegated Agent 必须要求 Runner 输出：

```text
artifacts/result.json
artifacts/report.md
```

如果失败：

```text
result.json 可以 status=failed
report.md 说明失败原因
```

如果没有 result.json：

```text
Result Parser 从 stdout.log 降级生成 partial_result
```

最终 API 返回必须稳定，不依赖自然语言格式。

## 19. 部署拓扑

### 19.1 本地轻量版

```text
FastAPI + 文件存储 + 内置 open_claude + fake runner
```

### 19.2 Docker 单机版

```text
Docker Compose
agent-api 单容器
./data volume
```

### 19.3 Docker Worker 版

```text
api
worker
redis
postgres
minio
```

### 19.4 K8s 版

```text
api deployment
worker deployment
runner job
postgres
redis
object storage
ingress
auth
```

## 20. 配置体系

配置来源优先级：

```text
环境变量
.env
config.yaml
默认值
```

关键配置：

```text
AGENT_NAME
AGENT_VERSION
AGENT_API_TOKEN
DATA_DIR
OPEN_CLAUDE_ROOT
OPEN_CLAUDE_CLI
OPENAI_BASE_URL
OPENAI_API_KEY
OPENAI_MODEL
OPEN_CLAUDE_FAKE
MAX_CONCURRENT_JOBS
JOB_TIMEOUT_SECONDS
NETWORK_ENABLED
SANDBOX_MODE
```

## 21. 认证与权限

### 21.1 V1

```text
AGENT_API_TOKEN
Bearer Token
本地/内网使用
```

### 21.2 V2

```text
用户登录
管理员角色
任务发起人
任务只读/可取消权限
```

### 21.3 V3

```text
SSO
RBAC
租户隔离
服务账号
API Key 管理
审计策略
```

## 22. Sandbox 设计

### 22.1 Docker Sandbox

每个 Job 一个容器：

```text
mount input read-only
mount workspace read-write
mount artifacts read-write
network disabled by default
memory/cpu limit
job timeout
```

### 22.2 Sandbox 生命周期

```text
create
start
stream logs
collect artifacts
stop
destroy
```

### 22.3 Sandbox 与 open_claude

两种方式：

```text
1. open_claude 在主服务进程里启动，cwd 指向 job dir。
2. open_claude 在 sandbox 容器里启动。
```

生产推荐第二种。

## 23. Delegated → Native 转换

Delegated Agent 的长期价值之一：帮助发现真实业务流程。

转换流程：

```text
收集多个 Job Trace
分析用户请求类型
归纳稳定任务包
抽取常见操作
抽取工具需求
抽取权限规则
抽取结果契约
生成 Native Agent 协议草案
再由 APD 生成 Native Agent
```

APD 后续可以提供：

```text
从 Delegated Trace 生成协议
从失败样本生成评测用例
从产物结构生成 Artifact Model
```

## 24. Agent 版本与升级

生成的 Agent 应包含：

```text
agent_definition.json
agent_version
runner_manifest
schema_version
```

升级场景：

```text
升级 Agent 目标
升级 Task Pack 模板
升级 open_claude
升级 Runtime 代码
升级部署架构
```

每次升级应记录：

```text
version_id
changelog
migration notes
compatibility notes
```

## 25. 失败恢复

失败类型：

```text
模型配置错误
open_claude 启动失败
超时
无 result.json
权限违规
磁盘不足
用户取消
Sandbox 创建失败
```

恢复策略：

```text
retry
retry with longer timeout
rerun fake runner
manual inspect logs
download trace bundle
send failure to APD/open_claude for fix
```

## 26. 密钥与配置安全

Delegated Agent 会持有模型网关地址和 Key，必须把密钥当成一等安全对象。

### 26.1 密钥来源

支持：

```text
.env
环境变量
部署平台 Secret
K8s Secret
后续企业密钥托管
```

禁止：

```text
把真实 API Key 写入 zip 模板
把真实 API Key 写入 README
把真实 API Key 写入 job.json/events/stdout
```

### 26.2 日志脱敏

写入日志前必须脱敏：

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
ANTHROPIC_AUTH_TOKEN
Authorization header
Bearer token
```

脱敏格式：

```text
sk-****last4
```

### 26.3 配置导出

`GET /api/config` 只能返回布尔状态，不返回密钥值：

```json
{
  "model_configured": true,
  "api_key_configured": true,
  "api_key_preview": "sk-****abcd"
}
```

## 27. 路径安全与文件边界

所有文件访问 API 都必须防路径穿越。

### 27.1 路径规则

```text
所有 job 文件必须位于 data/jobs/{job_id}/ 下。
Artifact 下载必须位于 artifacts/ 下。
Trace 下载必须位于 trace/ 下。
禁止使用 .. 跳出目录。
禁止跟随 symlink 跳出目录。
```

### 27.2 文件大小限制

应配置：

```text
MAX_UPLOAD_SIZE_MB
MAX_ARTIFACT_SIZE_MB
MAX_LOG_SIZE_MB
MAX_WORKSPACE_SIZE_MB
```

超过限制时：

```text
拒绝上传
截断日志
停止 Job
标记 quota_exceeded
```

## 28. 配额与限流

Delegated Agent 是重任务系统，必须有配额概念。

### 28.1 V1 配额

```text
MAX_CONCURRENT_JOBS
JOB_TIMEOUT_SECONDS
MAX_OUTPUT_CHARS
MAX_LOG_SIZE_MB
```

### 28.2 V2/V3 配额

```text
tenant_daily_jobs
tenant_concurrent_jobs
user_concurrent_jobs
runner_cpu_limit
runner_memory_limit
runner_disk_limit
monthly_token_budget
```

### 28.3 限流结果

如果超过配额：

```text
HTTP 429
job status = rejected / quota_exceeded
返回明确原因和可重试时间
```

## 29. Result Contract 校验

Result Parser 不应只“读取 result.json”，还要校验结构。

### 29.1 必填字段

```text
status
summary
artifacts
risks
next_actions
```

### 29.2 状态枚举

```text
completed
partial_completed
failed
need_human_approval
```

### 29.3 校验失败处理

如果 result.json 不是合法 JSON 或字段缺失：

```text
job status = partial_completed 或 failed
记录 parser_error
从 stdout.log 生成 fallback summary
在 Web UI 明确提示“结果契约不完整”
```

## 30. 可移植性与离线部署

独立部署的核心是可移植。

### 30.1 生成包不得依赖 APD 绝对路径

生成工程中不能硬编码：

```text
/home/data/api/agent-protocol-designer
/home/data/rag/open_claude
```

可以记录 source path 到 manifest，但运行时必须使用相对路径：

```text
./runner/open_claude/Openclaude-openclaude
./data
```

### 30.2 离线部署

如果目标环境不能联网：

```text
必须使用已有 dist/cli.js
Python 依赖需要提前打包或使用内网源
Node 依赖不能在部署时下载
Docker 镜像需要提前构建
```

V1 README 要明确联网/离线差异。

## 31. 兼容性与迁移

生成的 Agent 自身也需要 schema 版本。

```json
{
  "schema_version": "delegated-agent/v1",
  "runtime_version": "0.1.0",
  "agent_version": "1.0.0"
}
```

后续升级时需要迁移：

```text
job.json schema
events schema
result schema
agent_definition schema
runner_manifest schema
```

迁移策略：

```text
V1 可以不实现自动迁移。
但必须在文件中写入 schema_version。
V2 起提供 migration scripts。
```

## 32. 人工确认模型

V1 可以不实现人工确认，但最终架构需要预留。

### 32.1 需要确认的场景

```text
删除/覆盖文件
外部网络访问
执行高风险命令
上传产物到外部系统
访问敏感路径
超过预算继续执行
```

### 32.2 状态流转

```text
running
→ waiting_approval
→ approved → running
→ rejected → failed/rejected
```

### 32.3 审批记录

```json
{
  "approval_id": "appr_xxx",
  "job_id": "job_xxx",
  "requested_action": "...",
  "risk": "high",
  "decision": "approved|rejected",
  "operator": "user_xxx",
  "decided_at": "..."
}
```

## 33. 监控、告警与健康检查

生产化后不能只看日志文件，需要有健康检查和告警。

### 33.1 健康检查

接口：

```text
GET /health
GET /ready
GET /api/config
```

检查项：

```text
API 进程存活
data 目录可写
Runner 可用
Node.js 可用
open_claude dist/cli.js 存在
模型配置是否完整
队列是否可连接
数据库是否可连接
对象存储是否可连接
```

### 33.2 告警指标

```text
连续失败任务数
Runner 启动失败率
任务超时率
队列堆积长度
磁盘使用率
Artifact 生成失败率
模型网关错误率
API 5xx 错误率
```

### 33.3 告警渠道

V1 可以只写日志，V2/V3 支持：

```text
Webhook
企业微信/钉钉
Prometheus Alertmanager
邮件
```

## 34. 数据保留、清理与备份恢复

Delegated Agent 会产生大量 Job、日志和 Artifact，需要生命周期策略。

### 34.1 数据保留策略

按类型配置保留时间：

```text
job metadata：默认保留 180 天
stdout/stderr：默认保留 30 天
artifacts：默认保留 180 天
failed job trace：默认保留 90 天
临时 workspace：任务完成后可在 7 天后清理
```

### 34.2 清理任务

后台清理器负责：

```text
删除过期 workspace
压缩旧日志
清理孤儿临时目录
清理失败但无引用的 artifacts
保留 result.json/job.json/audit metadata
```

### 34.3 备份恢复

V1 文件存储：

```text
定期备份 data/jobs 和配置文件
```

V2/V3：

```text
PostgreSQL 定期备份
对象存储版本化
runner manifest 备份
agent_definition 备份
```

恢复目标：

```text
能恢复 Agent 配置
能恢复 Job 元数据
能恢复关键 Artifact
不要求恢复正在运行的进程
```

## 35. 依赖、供应链与许可证治理

由于生成工程会内置 open_claude，必须记录依赖来源和版本。

### 35.1 依赖清单

生成工程应包含：

```text
runner/open_claude_manifest.json
backend/requirements.txt
package manifest
Dockerfile base image
```

### 35.2 供应链风险

需要记录：

```text
open_claude 来源路径
生成时间
dist/cli.js 是否存在
是否复制 node_modules
Docker base image
Python/Node 版本
```

### 35.3 许可证说明

生成工程 README 应包含：

```text
本工程内置 open_claude 执行器。
请根据组织内部要求确认 open_claude 代码来源、许可证和分发策略。
如果不希望打包源码，可切换为 external 或 docker_image runner 模式。
```

这里不是阻止使用，而是保证生成工程在内部/外部分发时有明确说明。

## 36. 多环境部署策略

生成工程需要支持不同部署环境。

### 36.1 环境类型

```text
local：本机开发和 fake runner 验证
dev：内部开发环境，真实模型网关
staging：准生产，接近生产配置
prod：生产环境，必须启用认证、沙箱、备份和告警
```

### 36.2 配置差异

```text
local：OPEN_CLAUDE_FAKE=1，可无 API Token
dev：真实 runner，低并发，内网访问
staging：真实 runner，开启 API Token，接近生产资源限制
prod：认证、队列、沙箱、审计、备份、告警全部开启
```

### 36.3 部署保护

如果检测到公网绑定且没有 `AGENT_API_TOKEN`，应在启动时强警告；生产模式下应拒绝启动。

## 37. 测试矩阵

完整测试不只包括生成器测试。

### 37.1 生成器测试

```text
zip 结构
模板变量替换
open_claude 复制过滤
manifest 生成
不复制 node_modules
```

### 37.2 Runtime 测试

```text
fake runner completed
fake runner failed
job timeout
job cancel
result.json 缺失
result.json 非法 JSON
artifact 路径穿越
API Token 开/关
并发限制
```

### 37.3 真实 Runner 测试

```text
node 可用
dist/cli.js 可启动
模型配置错误
最小 hello 任务
stdout/stderr 采集
超时 kill
```

### 37.4 部署测试

```text
本地启动
Docker Compose 启动
data volume 持久化
重启后历史 job 可见
```

## 38. 成本与预算控制

Delegated Agent 执行任务可能消耗大量模型 Token 和机器资源。

### 38.1 成本来源

```text
模型 Token
Runner 执行时间
CPU/内存
磁盘空间
对象存储
日志存储
```

### 38.2 预算控制

```text
每任务最大运行时间
每任务最大输出长度
每用户每日任务数
每租户每月预算
超预算暂停任务
```

### 38.3 成本展示

如果 Runner 能提供 Token 信息，应写入：

```text
job.json.runner_usage
observability metrics
```

如果不能提供，也至少记录：

```text
运行时长
输出字符数
日志大小
```

## 39. Roadmap

### Phase 0：设计完成

```text
总设计文档
开发实施方案
完整架构蓝图
```

### Phase 1：独立闭环

```text
generator
fake runner
FastAPI
Web UI
zip download
内置 open_claude
```

### Phase 2：真实 Runner 稳定

```text
真实 open_claude 验收
日志增强
运行前检查
API Token
并发控制
```

### Phase 3：队列与沙箱

```text
Redis Queue
Worker
Docker Sandbox
资源限制
Artifact 管理
```

### Phase 4：生产部署

```text
PostgreSQL
对象存储
RBAC
审计
K8s
Runner 镜像管理
```

### Phase 5：闭环进化

```text
Delegated Trace → Native Agent 协议
失败样本 → Eval Cases
产物结构 → Artifact Model
任务包 → Workflow
```

## 40. 威胁模型与安全边界

Delegated Agent 底层执行器具备文件读写和命令执行能力，因此需要明确威胁模型。

### 40.1 主要威胁

```text
恶意用户提交 prompt 注入
诱导 Runner 读取密钥
诱导 Runner 删除或覆盖文件
诱导 Runner 访问外部网络
上传恶意压缩包或路径穿越文件
产物中夹带敏感信息
日志泄露 API Key
通过长任务耗尽 CPU/内存/磁盘
并发任务造成状态污染
```

### 40.2 V1 安全边界

```text
只适合可信内网。
默认不公网开放。
依靠 API Token、独立 job 目录、超时、日志脱敏、路径检查降低风险。
不宣称强沙箱。
```

### 40.3 V2/V3 安全边界

```text
Docker/K8s Sandbox
网络策略
资源限制
命令/文件操作拦截
敏感数据扫描
人工确认
审计事件
租户隔离
```

## 41. API 错误码与幂等语义

API 需要稳定错误语义，避免前端和调用方只能解析自然语言。

### 41.1 错误响应格式

```json
{
  "error": {
    "code": "quota_exceeded",
    "message": "当前并发任务已达到上限",
    "retryable": true,
    "details": {}
  }
}
```

### 41.2 常见错误码

```text
invalid_request
unauthorized
forbidden
not_found
quota_exceeded
runner_unavailable
model_not_configured
job_not_cancellable
artifact_not_found
path_forbidden
timeout
internal_error
```

### 41.3 HTTP 状态码

```text
400 invalid_request
401 unauthorized
403 forbidden/path_forbidden
404 not_found/artifact_not_found
409 job_not_cancellable/conflict
413 payload_too_large
429 quota_exceeded
500 internal_error
503 runner_unavailable/model_not_configured
```

### 41.4 幂等性

`POST /api/jobs` 后续应支持：

```text
Idempotency-Key
```

相同用户、相同 key、短时间内重复提交，应返回同一个 job_id，避免用户双击创建多个重任务。

V1 可以先不实现请求头，但前端按钮必须防重复提交；V2 实现真正幂等键。

## 42. 取消、重试与进程生命周期

### 42.1 取消语义

取消任务不是删除任务。

```text
POST /api/jobs/{job_id}/cancel
```

行为：

```text
queued：直接标记 cancelled
running：设置 cancel_requested，向进程发送 SIGTERM
超时未退出：发送 SIGKILL
completed/failed/timeout：返回 job_not_cancellable
```

### 42.2 进程生命周期

Runner Worker 必须记录：

```text
pid
started_at
last_output_at
exit_code
terminated_by
```

并处理：

```text
父进程退出后的孤儿进程
超时进程
僵尸进程回收
服务重启后的 running job 标记为 interrupted
```

### 42.3 重试语义

重试不覆盖原 Job，应创建新 Job：

```text
retry_of = old_job_id
```

原因：

```text
保留原始失败现场
方便对比两次结果
避免覆盖审计链路
```

## 43. 数据分类与敏感信息治理

Delegated Agent 会处理用户文件和生成产物，需要数据分类。

### 43.1 数据分类

```text
public：公开样例、无敏感数据
internal：内部业务数据
confidential：合同、投标、客户资料
secret：密钥、凭据、账号、Token
```

### 43.2 处理规则

```text
secret 不允许进入 prompt、日志、artifact。
confidential 允许进入任务，但需要访问控制和保留策略。
internal 默认仅当前租户可见。
public 可以用于示例和测试。
```

### 43.3 敏感信息扫描

V2 起应对以下内容做扫描：

```text
上传文件
stdout/stderr
result.json
report.md
artifact 文件
```

命中敏感信息时：

```text
脱敏展示
标记 risk
必要时阻断下载或要求管理员确认
```

## 44. 审计与不可抵赖

生产化后需要能回答：谁在什么时候让 Agent 做了什么。

### 44.1 审计事件

```text
job_created
job_cancelled
job_retried
artifact_downloaded
approval_requested
approval_decided
config_changed
runner_upgraded
auth_failed
quota_rejected
```

### 44.2 审计字段

```json
{
  "event_id": "evt_xxx",
  "tenant_id": "tenant_xxx",
  "user_id": "user_xxx",
  "job_id": "job_xxx",
  "action": "job_created",
  "ip": "...",
  "user_agent": "...",
  "created_at": "..."
}
```

### 44.3 不可抵赖增强

V3 可考虑：

```text
审计日志 append-only
对象存储 WORM
日志签名
关键产物 checksum
```

## 45. 灾难恢复边界

需要明确能恢复什么，不能恢复什么。

### 45.1 V1 恢复边界

```text
可恢复：已完成 Job 的 metadata、trace、artifact。
不可恢复：正在运行的 open_claude 进程。
服务重启后 running job 标记为 interrupted。
```

### 45.2 V2/V3 RPO/RTO

建议目标：

```text
RPO：最近一次备份点，默认 24 小时内。
RTO：服务 1 小时内恢复可用。
```

具体取决于部署环境和业务要求。

### 45.3 恢复流程

```text
恢复配置和密钥
恢复数据库
恢复对象存储/artifacts
恢复 runner manifest
启动服务
执行 health/ready 检查
抽样打开历史 job
```

## 46. 非目标与范围控制

完整架构可以很大，但开发必须控制范围。

### 46.1 明确非目标

第一阶段不做：

```text
不做多租户登录系统
不做完整 RBAC
不做 Redis Queue
不做 PostgreSQL
不做对象存储
不做 K8s
不做生产级 Docker Sandbox
不做复杂审批流
不做复杂前端工程
不做 open_claude 源码改造
不做 Native Agent 自动转换
```

### 46.2 第一阶段只证明一件事

```text
APD 能生成一个内置 open_claude 的独立 Agent 工程，
这个工程能脱离 APD 启动，
能提交任务，
能保存日志，
能产出 result/report，
能让用户看到结果。
```

### 46.3 范围扩张规则

任何新增需求如果不服务于上面这个闭环，都进入后续阶段。

判断标准：

```text
没有它，zip 是否还能生成？
没有它，fake runner 是否还能跑通？
没有它，真实 open_claude 是否还能完成 hello 任务？
没有它，用户是否还能看到 result/report？
```

如果答案是“能”，则不进入第一阶段。

## 47. 架构决策记录 ADR

本节记录已经达成的关键架构决策，避免后续反复摇摆。

| 编号 | 决策 | 原因 |
|---|---|---|
| ADR-001 | Delegated Agent 生成物必须可独立部署 | 生成后的 Agent 面向业务用户，不能依赖 APD 运行 |
| ADR-002 | 默认 bundled open_claude | 避免依赖服务器 `/home/data/rag/open_claude` 路径 |
| ADR-003 | V1 使用 fake runner 自检 | 避免 LLM/Key/Node 问题阻塞主闭环 |
| ADR-004 | V1 使用文件存储 | 降低实现复杂度，便于 zip 独立运行 |
| ADR-005 | V1 单进程后台线程 | 先证明闭环，队列/Worker 放到 V2 |
| ADR-006 | Job 根目录作为 Runner 可见根 | 确保 input/workspace/artifacts/trace 路径一致 |
| ADR-007 | 重试创建新 Job，不覆盖旧 Job | 保留失败现场和审计链路 |
| ADR-008 | 不修改 open_claude 源码 | 降低集成风险，先作为执行器使用 |
| ADR-009 | V1 不宣称生产级安全 | 没有沙箱/RBAC/审计闭环前不能公网裸露 |
| ADR-010 | 完整架构保留 V2/V3，但实现按阶段推进 | 避免过度设计阻塞第一阶段 |

## 48. 第一阶段 MVP 冻结清单

第一阶段 MVP 固定为以下内容。

### 48.1 APD 侧必须完成

```text
新增 delegated generator
新增 delegated templates（V1 实际采用生成器内嵌模板，后续可拆目录）
新增 zip 下载接口
新增 WebUI 下载入口
新增生成器测试
```

### 48.2 生成工程必须完成

```text
FastAPI 服务
简单 HTML 页面
POST /api/jobs
GET /api/jobs/{job_id}
GET /api/jobs/{job_id}/events
GET /api/jobs/{job_id}/artifacts
fake runner
open_claude runner
job.json
events.jsonl
stdout.log/stderr.log
artifacts/result.json
artifacts/report.md
.env.example
README
docker-compose.yml 草案
```

### 48.3 第一阶段验收门槛

```text
APD 能下载 zip
zip 包含 open_claude dist/src/package
解压后 fake runner 能跑通
真实 runner 能完成 hello delegated agent 任务
前端能显示任务状态
前端能下载 report.md
README 能让新用户照着启动
```

当前 V1 验收状态：

```text
APD zip 下载：已完成。
zip 包含 open_claude dist/src/package：已完成，且不复制 node_modules/.git。
解压后 fake runner 能跑通：已完成并有自动化测试。
真实 runner：代码路径已完成，真实运行依赖部署环境的模型网关、Key、Node.js 和 open_claude 首次确认状态。
前端显示任务状态和下载 report.md：已完成。
README 和独立使用文档：已完成。
```

## 49. 开工前 Checklist

正式编码前确认：

```text
open_claude 模板路径存在
dist/cli.js 存在
决定是否复制 src + dist，不复制 node_modules
确认 zip 下载接口命名：POST /api/delegated-agent/{session_id}.zip
确认 V1 默认 fake runner 可用
确认 V1 只支持 message 输入
确认 V1 单进程，不支持多 worker
确认 README 明确非生产安全
确认测试先跑 fake runner，再跑真实 runner
```

## 50. Definition of Done

第一阶段完成的定义：

```text
代码实现符合开发实施方案。
生成器单测通过。
生成 zip 结构符合文档。
fake runner 端到端通过。
真实 open_claude 最小任务通过。
文档包含本地启动和 Docker 启动说明。
失败时能看到 stderr/stdout 和明确错误。
不遗留与最终接口/路径冲突的旧说明。
```

当前 DoD 结论：

```text
代码实现、生成器单测、zip 结构、fake runner 端到端、本地启动说明、Docker 启动说明、stdout/stderr 错误可见性均已完成。
真实 open_claude 最小任务的稳定性不完全由 APD 代码决定，还依赖目标部署环境；V1 已提供真实 runner 路径、配置映射、超时、日志和错误提示。
```

## 51. 术语表

| 术语 | 含义 |
|---|---|
| APD | Agent Protocol Designer，负责设计和生成 Agent 工程 |
| Delegated Agent | 委托执行型 Agent，生成后可独立部署，底层委托 open_claude 执行任务 |
| Native Agent | 原生业务 Agent，拥有自己的业务 Runtime、工具、状态和协议 |
| Workflow Agent | 流程编排 Agent，强调节点、人工确认、Artifact 和状态流转 |
| Runner | 实际执行任务的底层执行器，例如 open_claude、Codex |
| Runner Adapter | 封装不同 Runner 的统一适配层 |
| Task Pack | APD/Agent 生成给 Runner 的受控任务包，不是普通 prompt |
| Job | 一次用户任务请求，拥有状态、日志、产物和结果 |
| Artifact | Job 产物，例如 report.md、result.json、docx、zip |
| Trace | Job 执行过程记录，例如 task_pack、events、stdout、stderr |
| Sandbox | 隔离 Runner 的执行环境，例如 Docker per job |
| Result Contract | Runner 必须输出的结构化结果约定 |
| bundled open_claude | open_claude 代码被复制进生成工程内部 |
| fake runner | 不调用真实 open_claude 的测试执行器，用于自检 |

## 52. 当前设计假设

以下是假设条件，不是永久事实。

```text
open_claude 的 dist/cli.js 可作为命令行入口运行。
生成工程可以携带 open_claude 源码和 dist。
第一阶段目标环境至少具备 Python 和 Node.js。
第一阶段优先内部可信环境，不直接公网部署。
第一阶段只支持文本任务输入。
第一阶段使用文件存储，不使用数据库。
第一阶段使用单进程后台线程，不使用多 worker。
第一阶段通过 fake runner 保证无 LLM 也能自检。
```

如果这些假设变化，需要回到架构文档重新评估。

## 53. 已决问题与后续未决问题

第一阶段已经确认：

```text
open_claude 打包完整 src + dist 后 zip 体积可接受，当前真实包约几十 MB。
V1 严格只支持 message 输入。
生成工程默认中文 UI。
API Token 默认空，由用户按需启用。
fake runner 默认开启。
```

后续 V1.1/V2 仍需确认：

```text
dist/cli.js 在无 node_modules 情况下是否稳定可运行？
Dockerfile 是否需要执行 npm/pnpm install？
真实 open_claude 首次运行是否一定会要求交互确认？
是否需要为 open_claude 增加非交互启动参数？
真实 Runner 的最小 hello 任务是否能稳定产出 result.json？
是否进入 V1.1 支持单文件上传，还是直接进入 V2 目录/zip 输入。
```

这些问题不阻塞整体设计，但会影响第一阶段实现细节。

## 54. 责任边界

### 54.1 APD 负责

```text
设计 Delegated Agent 协议
生成独立工程 zip
复制 bundled open_claude
生成 README/.env/docker-compose
生成 Task Pack 模板
生成 Runtime 代码骨架
提供 fake runner
提供下载入口
```

### 54.2 生成后的 Delegated Agent 负责

```text
独立启动服务
接收业务用户任务
创建 Job
运行 Runner
保存 Trace
收集 Artifact
返回结果
提供任务页面和 API
```

### 54.3 open_claude 负责

```text
理解 Task Pack
读取允许目录
执行具体分析/生成/修改任务
输出 result.json/report.md
```

### 54.4 open_claude 不负责

```text
业务服务鉴权
Job 状态管理
Artifact 下载 API
数据保留策略
审计合规
多租户隔离
```

这些由 Delegated Agent Runtime 负责。

## 55. 交接说明

如果把本方向交给 open_claude/Codex 或其他开发者继续实现，应先阅读：

```text
1. docs/delegated_agent_mode_plan.md
2. docs/delegated_agent_mode_full_architecture.md
3. docs/delegated_agent_mode_development_plan.md
```

然后按以下顺序执行：

```text
1. 不要先改 WebUI。
2. 先实现 delegated_generator.py。
3. 先生成 fake runner 可跑通的 zip。
4. 再接真实 open_claude。
5. 再接 APD 页面下载入口。
6. 每一步都跑测试，不要一次性大改。
```

交接时必须说明：

```text
当前第一阶段 V1 已完成。
第一阶段范围已冻结并已落地。
完整架构中的 V2/V3/V4 不进入第一阶段。
```

## 56. 文档治理与冲突解决

随着文档变多，必须规定优先级。

### 56.1 文档优先级

```text
1. delegated_agent_mode_full_architecture.md
   - 负责最终架构、范围边界、ADR、路线图。

2. delegated_agent_mode_development_plan.md
   - 负责第一阶段开发任务、文件清单、接口、测试和验收。

3. delegated_agent_mode_plan.md
   - 负责方向总览和讨论入口。
```

### 56.2 冲突解决规则

如果三份文档出现冲突：

```text
长期产品和架构问题，以完整架构文档为准。
第一阶段具体开发步骤，以开发实施方案为准。
如果开发实施方案要突破 MVP 冻结范围，必须先更新完整架构中的 ADR、MVP 冻结清单和 Definition of Done。
如果总计划和后两份文档冲突，以后两份为准。
```

### 56.3 变更流程

任何影响以下内容的改动，都要同步更新文档：

```text
接口路径
Job 状态机
Runner 启动方式
Job 目录结构
Result Contract
安全边界
MVP 范围
ADR 决策
验收标准
```

建议变更顺序：

```text
先更新完整架构文档
再更新开发实施方案
最后更新总览文档
```

### 56.4 文档状态

当前状态：

```text
总览文档：已完成，可作为方向入口。
开发实施方案：已完成，并已同步 V1 实现状态。
完整架构文档：已完成，可作为最终架构蓝图。
使用说明文档：已完成，见 docs/delegated_agent_usage.md。
代码实现：第一阶段 V1 已完成。
```

## 57. 最终判断

完整设计结论：

```text
Delegated Agent 是 APD 的第二条重要运行时路线。
它不是临时包装 open_claude，而是一种可独立部署的任务型 Agent 工程形态。
```

APD 未来应形成：

```text
Native Agent：稳定、可控、长期生产化。
Workflow Agent：复杂流程、人工确认、产物版本。
Delegated Agent：快速落地、复杂执行、open_claude 能力放大。
```

三者共同构成 APD 的 Agent 生成平台能力。

## 58. 2026-06-13 架构再澄清：APD 协议与 open_claude Engine

本节记录一次关键架构澄清，避免后续继续把“首页协议设计、真实调试台、工程开发台”混成线性流程。

### 58.1 当前准确认知

APD 首页产出的协议不是最终业务代码，也不是完整 Runtime。

更准确的定义是：

```text
APD 协议 = 业务边界规范 + 操作规约 + 验收标准
```

它通常以 JSON / Markdown 形式存在，描述：

```text
业务目标
用户意图
业务对象
状态模型
可执行操作
风险等级
权限边界
工具需求
产物要求
追问条件
拒绝条件
评测样本
Workflow
```

APD 协议本身不直接完成复杂任务。它需要被具体 Runtime 消费。

### 58.2 两条平级落地路线

APD 协议完成后，存在两条平级落地路线。

```text
                  APD 协议
          业务语义 / 边界 / 评测
                    │
        ┌───────────┴───────────┐
        │                       │
路线 A：委托型 Agent        路线 B：自研 Agent 工程
真实调试台                 工程开发台
open_claude 执行引擎        自研 Runtime
```

这两条路线不是主辅关系，也不是先后关系。

区别在于最终产物不同：

```text
真实调试台路线：产出 open_claude 包装型业务 Agent。
工程开发台路线：产出自研业务 Agent 工程。
```

当前阶段，真实调试台路线效果更好，是因为它复用了 open_claude 已经具备的强执行能力。

### 58.3 APD 协议能否通用

APD 协议可以通用，但通用的是业务语义和边界，不是执行实现。

通用部分：

```text
业务目标
对象 / 状态 / 操作
权限 / 风险 / 追问 / 拒绝
工具需求
产物要求
成功标准
评测样本
```

不通用部分：

```text
如何多步执行
如何调用工具
如何维护上下文
如何恢复失败
如何部署
如何隔离权限
如何记录运行事件
```

这些需要不同 Runtime 适配层实现。

### 58.4 open_claude 的定位

open_claude 不应只理解为 LLM，也不只是普通 CLI。

当前更合理的定位是：

```text
open_claude = 通用工程类 Agent 执行引擎
```

它内置了：

```text
LLM 推理
多步循环
文件读取
文件修改
命令执行
观察结果
错误修复
终端交互
工程上下文管理
```

因此它类似 Java 体系里的 Spring Boot 级底层框架，但比 Spring Boot 更“能动”。

更准确类比：

```text
open_claude ≈ Spring Boot + 工作流执行器 + 工具系统 + LLM 大脑
```

APD 与 open_claude 的关系：

```text
APD = 业务协议层 / 场景治理层
open_claude = 通用执行引擎
Delegated Agent Runtime = 把 APD 协议交给 open_claude 执行的包装层
```

### 58.5 open_claude 是否包含协议边界

open_claude 自身有边界，但主要是工程执行边界。

```text
open_claude 边界：能读哪些目录、能改哪些文件、能执行哪些命令、如何交互确认。
APD 边界：某个业务场景允许做什么、禁止做什么、什么时候追问、什么结果合格。
```

二者不是同一层协议。

真实调试台效果强，是因为它叠加了：

```text
APD 业务协议
+ open_claude 工程执行协议
+ LLM 推理能力
+ 文件 / 命令 / 多步执行能力
```

### 58.6 为什么工程开发台短期达不到真实调试台效果

工程开发台生成的是自研 Agent 工程骨架。

它可以包含：

```text
Planner
Validator
Executor
Tools
State
Memory
Trace
Workflow
```

但如果没有实现成熟 Runtime 能力，它不会天然具备 open_claude 的工程执行能力，例如：

```text
自动读项目
自动改文件
自动执行命令
自动排错
自动多步推进
自动根据观察调整计划
```

因此，工程开发台路线不是没价值，而是对应另一种产物：自研、可控、长期维护的业务 Agent 工程。

### 58.7 新战略判断

如果目标是快速产生多个业务场景 Agent，并且希望复用 open_claude 的强执行能力，则 APD 可以定位为：

```text
open_claude 上层的业务 Agent 生成与治理平台
```

短期路线：

```text
APD 协议
→ Task Pack / 约束 / 产物要求 / 验收标准
→ open_claude 执行
→ APD 白盒观测 / 诊断 / 修复 / 版本管理
```

长期路线：

```text
把 open_claude 从单人 CLI Agent 改造成可嵌入、可服务化、多会话、多 Job 的 Agent Engine。
```

这个长期路线不是把 open_claude 改成 APD 平台，而是让每个业务 Agent 都能把 open_claude 作为内部执行引擎，服务多个用户。

### 58.8 对 UI 的影响

首页顶部不应表达成严格线性：

```text
1 设计 Agent → 2 工程开发台 → 3 真实调试台
```

更准确表达应是：

```text
1 设计协议
2A 委托型真实调试
2B 自研工程开发
```

首页协议完成后，应提示用户选择两条平级路线：

```text
如果想先看到强效果，进入真实调试台，走委托型 Agent 路线。
如果想做自研可控工程，进入工程开发台，走自研 Agent Runtime 路线。
```
