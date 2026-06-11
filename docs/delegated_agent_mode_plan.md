# 独立部署型 Delegated Agent 模式计划

> 目标：为 APD 新增一种快速创建 Agent 的模式。该模式生成的 Agent 工程内置 open_claude 执行器，可以脱离 APD 独立部署、独立运行、独立保存任务记录和产物。

> 完整产品与生产化蓝图见：`docs/delegated_agent_mode_full_architecture.md`。开发实施细节见：`docs/delegated_agent_mode_development_plan.md`。

> 如果三份文档出现冲突：最终架构与范围以 `delegated_agent_mode_full_architecture.md` 为准；第一阶段代码实施以 `delegated_agent_mode_development_plan.md` 为准；本文只作为方向总览。

## 1. 背景

APD 当前主线是：

```text
需求澄清 → 协议设计 → Agent 工程生成 → Agent IDE 协作开发 → Runtime Inspector 调试
```

这条路线适合沉淀长期稳定的业务 Agent，但要达到 Claude Code/open_claude 级别的执行能力，需要补齐很多运行时工程能力，例如工具调用、权限、上下文、记忆、Transcript、任务恢复、文件操作和命令执行。

open_claude 已经具备成熟的工程执行能力。因此 APD 可以新增一种模式：不是从零生成全部执行能力，而是生成一个服务端 Agent 工程，把复杂执行委托给内置 open_claude。

该模式不是替代原生 Agent，而是 APD 的快速落地能力。

## 2. 核心定位

该模式暂定名：

```text
Delegated Agent
中文：委托执行型 Agent
完整定位：独立部署型 Delegated Agent
```

它的本质是：

```text
业务 Agent 服务
+ 内置 open_claude 执行器
+ 任务队列/任务状态
+ 工作区隔离
+ Trace/Artifact 存储
+ API/Web 调试页面
```

核心原则：

```text
APD 只负责生成工程。
生成后的 Agent 必须可以脱离 APD 独立部署。
生成工程默认内置 open_claude 代码。
运行数据必须保存在生成工程自己的 data 目录。
配置必须通过 .env 管理。
```

## 3. 和现有 APD 模式的关系

未来 APD 可支持三类 Agent 产物：

| 模式 | 中文名 | 适合场景 | 产物 |
|---|---|---|---|
| `native_agent` | 原生业务 Agent | 工具明确、流程稳定、需要长期生产化 | 独立业务 Agent Runtime |
| `workflow_agent` | 流程编排 Agent | 多节点、多人工确认、多产物版本 | Workflow Runtime |
| `delegated_agent` | 委托执行型 Agent | 文件处理、项目分析、代码改造、复杂任务执行 | 内置 open_claude 的 Agent 服务 |

Delegated Agent 适合快速验证复杂场景，也可以作为 Native Agent 的前置探索模式：

```text
先用 Delegated Agent 快速跑业务
→ 沉淀 Trace、产物、失败样本
→ APD 总结稳定流程和操作协议
→ 再生成 Native Agent
```

## 4. 目标用户与使用场景

### 4.1 APD 平台用户

APD 平台本身是内部 Agent 开发工具，主要给开发者、产品、架构人员使用。

### 4.2 Delegated Agent 产物用户

Delegated Agent 是 APD 生成出来的独立 Agent 服务。它的最终用户不等同于 APD 平台用户，可以是业务人员、内部系统或其他服务。

因此设计时必须考虑：

- 独立部署
- 多任务请求
- 任务状态查询
- 运行日志查看
- 产物下载
- 配置与密钥管理
- 后续升级到队列/Worker/Docker 沙箱

## 5. 整体架构

```text
用户 / 业务系统
   ↓
Delegated Agent API / Web UI
   ↓
Task Controller
   ↓
Task Pack Builder
   ↓
Permission / Policy Gate
   ↓
Workspace Manager
   ↓
Runner Worker
   ↓
内置 open_claude / Codex Runner
   ↓
Trace / Artifact Collector
   ↓
Result Parser
   ↓
用户可读结果 + 结构化结果 + 产物
```

APD 生成的 Agent 不直接把用户原话丢给 open_claude，而是先包装成受控任务包，然后让 open_claude 在隔离工作区执行。

## 6. 生成工程结构

第一版建议生成如下结构：

```text
my-delegated-agent/
  README.md
  .env.example
  docker-compose.yml

  backend/
    requirements.txt
    app/
      main.py
      config.py
      schemas.py
      agent_definition.py

      api/
        jobs.py
        chat.py
        artifacts.py
        health.py

      runtime/
        task_pack_builder.py
        workspace_manager.py
        openclaude_runner.py
        runner_worker.py
        permission_gate.py
        result_parser.py
        trace_store.py
        artifact_store.py

      templates/
        task_pack_template.md

  frontend/
    index.html

  runner/
    open_claude/
      Openclaude-openclaude/
        dist/
          cli.js
        src/
        bin/
        package.json
        README.md
        ...

  data/
    jobs/
    artifacts/
    workspaces/
```

其中：

```text
runner/open_claude/Openclaude-openclaude
```

由 APD 从本机 open_claude 模板目录复制进入生成工程。

默认模板源：

```text
/home/data/rag/open_claude/Openclaude-openclaude
```

## 7. open_claude 内置策略

Delegated Agent 默认采用 bundled 模式：

```text
生成工程内置 open_claude 代码。
运行时默认调用工程内的 dist/cli.js。
部署目标机器不需要预先安装 open_claude。
```

支持三种 runner 来源：

| 模式 | 说明 | 使用阶段 |
|---|---|---|
| `bundled` | 复制 open_claude 到生成工程，默认推荐 | 独立部署 |
| `external` | 使用服务器已有 open_claude 路径 | APD 本机开发 |
| `docker_image` | open_claude 预装进 Runner 镜像 | 后续生产部署 |

第一版重点实现 `bundled`。

## 8. 运行流程

```text
1. 用户提交任务
2. Agent 服务创建 job_id
3. 创建 data/jobs/{job_id}/ 目录
4. 准备 input/workspace/artifacts/trace
5. 生成 task_pack.md
6. 启动内置 open_claude
7. open_claude 在 job workspace 中执行任务
8. 后端持续写 stdout.log 和 events.jsonl
9. open_claude 输出 result.json 和 report.md
10. Result Parser 解析结果
11. Job 状态变为 completed/failed/timeout
12. 用户查看结果、日志、产物
```

## 9. Job 目录结构

每个任务独立目录：

```text
data/jobs/{job_id}/
  input/
  workspace/
  artifacts/
    result.json
    report.md
  trace/
    task_pack.md
    stdout.log
    stderr.log
    events.jsonl
  job.json
```

`job.json` 保存：

```json
{
  "job_id": "job_xxx",
  "status": "running",
  "created_at": "...",
  "updated_at": "...",
  "user_request": "...",
  "runner": "open_claude",
  "workspace_dir": "data/jobs/job_xxx/workspace",
  "artifacts_dir": "data/jobs/job_xxx/artifacts",
  "result": null,
  "error": null
}
```

## 10. API 设计

第一版至少提供：

```text
GET  /health
POST /api/jobs
GET  /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/events
GET  /api/jobs/{job_id}/artifacts
GET  /api/jobs/{job_id}/artifacts/{name}
POST /api/jobs/{job_id}/cancel
```

可选对话入口：

```text
POST /api/chat
```

但 `/api/chat` 底层也应该创建 job，不应该长时间同步阻塞。

## 11. Job 状态机

```text
created
queued
preparing_workspace
building_task_pack
running
parsing_result
completed
failed
cancelled
timeout
need_human_approval
```

第一版可以先使用本地线程/进程执行，不引入 Redis 队列。

后续版本再升级为：

```text
API Server + Queue + Worker
```

## 12. Task Pack 格式

Task Pack 是 Delegated Agent 的控制核心。

示例：

```markdown
# 任务目标
你是受控执行器，请根据用户请求完成任务。

# 用户原始请求
{{ user_request }}

# Agent 目标
{{ agent_goal }}

# 工作目录
{{ workspace_dir }}

# 允许操作
- 读取工作目录下文件
- 在 artifacts 目录输出结果
- 运行必要的只读诊断命令

# 禁止操作
- 禁止访问工作目录之外的路径
- 禁止删除源文件
- 禁止修改 .env 或密钥文件
- 禁止执行 sudo、ssh、scp、rm -rf 等高风险命令

# 必须输出
请最终生成：
1. artifacts/result.json
2. artifacts/report.md

# result.json 格式
{
  "status": "completed|failed|need_human_approval",
  "summary": "...",
  "artifacts": [],
  "changed_files": [],
  "risks": [],
  "next_actions": []
}

# 验收标准
- 必须说明做了什么
- 必须列出产物路径
- 必须列出风险和下一步建议
- 不允许只给泛泛而谈的回答
```

## 13. 结果契约

open_claude 最终必须输出：

```text
artifacts/result.json
artifacts/report.md
```

`result.json` 示例：

```json
{
  "status": "completed",
  "summary": "任务完成摘要",
  "artifacts": [
    {
      "name": "report.md",
      "path": "artifacts/report.md",
      "type": "markdown"
    }
  ],
  "changed_files": [],
  "risks": [],
  "next_actions": []
}
```

如果没有成功生成 `result.json`，Result Parser 应降级：

```text
读取 stdout.log → 提取最后总结 → 标记 partial_result
```

## 14. 配置设计

`.env.example`：

```env
AGENT_NAME=My Delegated Agent
DATA_DIR=./data

OPEN_CLAUDE_ROOT=./runner/open_claude/Openclaude-openclaude
OPEN_CLAUDE_CLI=dist/cli.js

OPENAI_BASE_URL=http://your-gateway/v1
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-5.5

MAX_CONCURRENT_JOBS=1
JOB_TIMEOUT_SECONDS=1800
NETWORK_ENABLED=false
```

`runner_config.yaml` 可选：

```yaml
runner:
  type: open_claude
  bundled: true
  root: ./runner/open_claude/Openclaude-openclaude
  cli_entry: dist/cli.js
  command:
    - node
    - --enable-source-maps
    - ./runner/open_claude/Openclaude-openclaude/dist/cli.js
```

## 15. 权限与隔离策略

第一版的最低要求：

```text
每个 job 独立 workspace。
open_claude 工作目录限定在 job workspace。
输入文件默认只读复制。
产物统一写入 artifacts。
任务超时自动杀进程。
```

第一版主要靠：

- 工作目录隔离
- Task Pack 禁止事项
- 超时控制
- 输出产物契约

第二版需要增强：

- Docker Sandbox
- 命令 allow/deny list
- 文件写入范围检查
- 网络开关
- 高风险操作人工确认
- 审计事件

## 16. 部署方式

### 16.1 本机运行

```bash
cd my-delegated-agent/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

前提：

```text
Node.js 已安装。
runner/open_claude/Openclaude-openclaude/dist/cli.js 存在。
.env 已配置模型网关。
```

### 16.2 Docker Compose

```bash
docker compose up -d
```

Docker 镜像应包含：

```text
Python
Node.js
backend
runner/open_claude/Openclaude-openclaude
```

第一版可以先生成 docker-compose 和 Dockerfile 草案，后续再强化 sandbox。

## 17. APD 页面入口设计

APD 后续新增 Agent 生成模式选择：

```text
生成模式：
- 原生业务 Agent
- Workflow Agent
- 独立部署型 Delegated Agent
```

选择 Delegated Agent 后，APD 引导收集：

```text
1. Agent 名称
2. Agent 目标
3. 用户会提交什么输入
4. 允许读取哪些输入
5. 允许写入哪些产物
6. 禁止哪些操作
7. 最终必须输出什么
8. 部署方式：本机 / Docker
9. 是否内置 open_claude：默认是
```

## 18. V1 开发范围

V1 目标：生成一个可以独立启动、可以提交任务、可以调用内置 open_claude、可以查看结果的最小闭环。

必须完成：

```text
1. APD 增加 delegated_agent 生成模式入口
2. 生成 FastAPI 后端工程
3. 复制 open_claude 到 runner/open_claude
4. 生成 .env.example、README、docker-compose.yml
5. 实现 POST /api/jobs
6. 实现 GET /api/jobs/{job_id}
7. 实现 GET /api/jobs/{job_id}/events
8. 后台启动 open_claude
9. 保存 stdout.log、task_pack.md、job.json
10. 解析 artifacts/result.json
11. 生成简单 frontend/index.html
12. zip 下载
```

V1 暂不做：

```text
复杂 RBAC
Redis/Celery
PostgreSQL
K8s
复杂审批流
真实命令拦截
复杂前端工程
```

## 19. V2/V3 演进

### V2：服务端化增强

```text
Redis Queue
独立 Worker
并发控制
Job 重试
任务取消
SSE 实时事件
Artifact 下载页
权限策略增强
Docker Sandbox
```

### V3：生产化增强

```text
PostgreSQL
对象存储
RBAC
审计日志
多租户
K8s Job Runner
Runner 镜像版本管理
Agent 版本升级
Trace 回放
从 Delegated Trace 反推 Native Agent 协议
```

## 20. 风险与限制

| 风险 | 说明 | 应对 |
|---|---|---|
| 包体积变大 | 内置 open_claude 会让 zip 变大 | 接受；后续支持 docker image/external |
| 执行不可控 | open_claude 有命令和文件能力 | workspace 隔离、超时、Docker Sandbox |
| 输出不稳定 | LLM 可能不生成 result.json | Result Parser 降级解析 stdout |
| 并发有限 | open_claude 是重任务执行器 | V1 限制并发，V2 引入队列 |
| 安全边界弱 | V1 主要靠目录隔离和提示约束 | V2 必须补沙箱和权限拦截 |
| 部署依赖 Node | open_claude 需要 Node.js | Docker 镜像内置 Node |

## 21. 第一阶段实施顺序

建议按以下顺序推进：

| 顺序 | 任务 | 产出 |
|---|---|---|
| 1 | 固定 Delegated Agent 生成规范 | 本文档 |
| 2 | 增加工程模板目录 | backend/frontend/runner/data 模板 |
| 3 | 实现 open_claude 复制器 | 从模板源复制到 runner/open_claude |
| 4 | 实现 V1 FastAPI Runtime | jobs/events/artifacts API |
| 5 | 实现 openclaude_runner | 启动 dist/cli.js，收集 stdout |
| 6 | 实现 task_pack_builder | 根据 Agent 定义生成 task_pack.md |
| 7 | 实现 result_parser | 解析 result.json/report.md |
| 8 | 生成 README 和部署说明 | 本机和 Docker 两种方式 |
| 9 | 接入 APD WebUI 导出入口 | 选择 Delegated Agent 并下载 zip |
| 10 | 用一个真实场景验证 | 项目分析 Agent 或文档处理 Agent |

## 22. 当前结论

Delegated Agent 模式值得作为 APD 下一阶段重点方向之一。

它的价值是：

```text
不等待 APD 从零重造 Claude Code Runtime，
先把 open_claude 包装成可控、可部署、可观察的业务 Agent 执行器。
```

最终 APD 将形成双运行时路线：

```text
Native Agent Runtime：长期稳定业务系统。
Delegated Agent Runtime：快速落地、复杂执行、能力放大。
```
