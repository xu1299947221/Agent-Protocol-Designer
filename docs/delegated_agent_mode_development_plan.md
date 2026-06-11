# 独立部署型 Delegated Agent 开发实施方案

> 本文是 `docs/delegated_agent_mode_plan.md` 的开发落地版。目标是把“内置 open_claude、可独立部署的委托执行型 Agent”细化到 APD 代码改造、生成工程模板、API、Runner、验收用例和实施顺序。

> 完整产品与生产化蓝图见：`docs/delegated_agent_mode_full_architecture.md`。

> 如果本文与完整架构文档冲突：长期架构以完整架构文档为准；第一阶段编码任务和验收细节以本文为准；若冲突影响 V1 范围，必须先更新完整架构的 ADR/冻结清单。

## 1. 开发目标

V1 目标：APD 可以生成一个独立部署的 Delegated Agent 工程 zip。

该 zip 解压后应满足：

```text
1. 不依赖 APD 进程。
2. 默认内置 open_claude 代码。
3. 可通过 Python 本地启动。
4. 可通过 Docker Compose 启动。
5. 可提交一个任务。
6. 后端能启动内置 open_claude 执行任务。
7. 能保存 job.json、task_pack.md、stdout.log。
8. 能解析 result.json/report.md。
9. 能通过 Web 页面查看任务状态、日志和产物。
```

V1 不追求完整生产级安全，但必须为 V2 的队列、Worker、Docker Sandbox 留好结构。

## 2. APD 侧改造范围

### 2.1 新增生成模式

在 APD 中新增生成模式：

```text
delegated_agent
```

可见文案：

```text
独立部署型 Delegated Agent
```

该模式不是替换现有脚手架，而是在“导出产物 / Agent IDE / 生成工程”中新增一种下载产物。

### 2.2 建议新增文件

```text
protocol_designer/delegated_generator.py
protocol_designer/delegated_templates/
  backend/
  frontend/
  docker/
  README.md.j2
  env.example.j2
```

职责：

| 文件/目录 | 职责 |
|---|---|
| `delegated_generator.py` | 生成 Delegated Agent 工程目录和 zip |
| `delegated_templates/backend` | FastAPI Runtime 模板 |
| `delegated_templates/frontend` | 简单 Web 调试页模板 |
| `delegated_templates/docker` | Dockerfile / docker-compose 模板 |
| `README.md.j2` | 生成项目说明 |
| `env.example.j2` | 生成环境变量说明 |

### 2.3 建议改动现有文件

| 文件 | 改动 |
|---|---|
| `webui_server.py` | 新增导出 API、WebUI 按钮、参数表单 |
| `protocol_designer/generator.py` | 可选：复用 safe_project_name 等工具 |
| `tests/` | 新增 delegated generator 和 zip 结构测试 |
| `docs/delegated_agent_mode_plan.md` | 保持总设计，不频繁改 |

## 3. APD WebUI 入口设计

### 3.1 最小入口

在“导出产物”区域新增按钮：

```text
生成独立 Delegated Agent zip
```

点击后弹出简单配置区：

```text
项目名
Agent 名称
Agent 目标
默认任务说明
是否内置 open_claude：默认开启
open_claude 模板路径：/home/data/rag/open_claude/Openclaude-openclaude
部署方式：本地 + Docker Compose
```

### 3.2 后续入口

在 Agent IDE 中新增引导：

```text
如果你的 Agent 需要复杂文件/项目/代码执行，可以选择 Delegated Agent 模式。
```

V1 不做复杂多步骤引导，只提供下载入口。

## 4. APD 导出 API

新增接口：

```text
POST /api/delegated-agent/{session_id}.zip
```

请求：

```json
{
  "project_name": "my-delegated-agent",
  "agent_name": "项目分析 Agent",
  "agent_goal": "帮助用户分析项目并生成结构化报告",
  "default_task": "请分析输入内容并生成 report.md 和 result.json",
  "bundle_open_claude": true,
  "open_claude_source": "/home/data/rag/open_claude/Openclaude-openclaude",
  "deployment": "local_and_docker"
}
```

响应：

```text
application/zip
```

错误响应：

```json
{
  "error": "open_claude source not found"
}
```

## 5. 生成器设计

### 5.1 函数签名

建议实现：

```python
def generate_delegated_agent_project(
    *,
    protocol: dict[str, Any],
    project_name: str,
    agent_name: str,
    agent_goal: str,
    default_task: str = "",
    open_claude_source: Path | None = None,
    bundle_open_claude: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    ...
```

返回：

```text
zip bytes + manifest
```

### 5.2 生成步骤

```text
1. 规范化 project_name。
2. 创建临时目录。
3. 写入 backend 模板。
4. 写入 frontend 模板。
5. 写入 README.md、.env.example、docker-compose.yml。
6. 写入 agent_definition.json。
7. 写入 task_pack_template.md。
8. 如果 bundle_open_claude=true，复制 open_claude 到 runner/open_claude。
9. 过滤不必要目录。
10. 打包 zip。
11. 返回 manifest。
```

### 5.3 open_claude 复制过滤规则

复制源：

```text
/home/data/rag/open_claude/Openclaude-openclaude
```

建议排除：

```text
.git
node_modules
.cache
.tmp
*.log
.DS_Store
```

建议保留：

```text
dist/cli.js
src/
bin/
package.json
README.md
tsconfig.json
build.ts
```

V1 默认不复制 `node_modules`，依赖 Dockerfile/npm install 或已有 dist 运行。

如果担心 dist 运行缺依赖，V1 README 需要明确：

```text
如果 dist/cli.js 运行缺依赖，请在 runner/open_claude/Openclaude-openclaude 执行 npm install。
```

## 6. 生成工程目录结构

V1 生成结构：

```text
my-delegated-agent/
  README.md
  .env.example
  docker-compose.yml

  backend/
    requirements.txt
    app/
      __init__.py
      main.py
      config.py
      schemas.py
      agent_definition.py

      api/
        __init__.py
        jobs.py
        artifacts.py
        health.py

      runtime/
        __init__.py
        task_pack_builder.py
        workspace_manager.py
        openclaude_runner.py
        runner_worker.py
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
        dist/cli.js
        src/
        bin/
        package.json
        README.md

  data/
    .gitkeep
```

## 7. 生成工程后端模块设计

### 7.1 `app/main.py`

职责：

```text
创建 FastAPI app
挂载 API router
挂载 frontend/index.html
暴露 /health
```

### 7.2 `app/config.py`

读取环境变量：

```text
AGENT_NAME
DATA_DIR
OPEN_CLAUDE_ROOT
OPEN_CLAUDE_CLI
OPENAI_BASE_URL
OPENAI_API_KEY
OPENAI_MODEL
MAX_CONCURRENT_JOBS
JOB_TIMEOUT_SECONDS
```

### 7.3 `app/schemas.py`

定义：

```text
CreateJobRequest
JobSummary
JobDetail
JobEvent
ArtifactInfo
```

### 7.4 `app/agent_definition.py`

加载生成时写入的 Agent 定义：

```json
{
  "agent_type": "delegated_agent",
  "agent_name": "...",
  "agent_goal": "...",
  "result_contract": {
    "required_files": ["result.json", "report.md"]
  }
}
```

### 7.5 `runtime/workspace_manager.py`

职责：

```text
创建 data/jobs/{job_id}
创建 input/workspace/artifacts/trace
写入初始 job.json
处理上传文件或输入文本
```

V1 可以先只支持 text input。

### 7.6 `runtime/task_pack_builder.py`

职责：

```text
读取 task_pack_template.md
填充 user_request、agent_goal、workspace_dir、artifacts_dir
写入 trace/task_pack.md
```

### 7.7 `runtime/openclaude_runner.py`

职责：

```text
构造 node dist/cli.js 命令
设置 OPENAI/ANTHROPIC 环境变量
启动 subprocess
流式读取 stdout/stderr
写入 trace/stdout.log 和 trace/stderr.log
超时 kill
返回 exit_code
```

启动命令：

```bash
node --enable-source-maps runner/open_claude/Openclaude-openclaude/dist/cli.js \
  --dangerously-skip-permissions \
  --add-dir /absolute/data/jobs/{job_id} \
  "任务包内容"
```

### 7.8 `runtime/runner_worker.py`

职责：

```text
Job 状态流转总控
prepare workspace
build task pack
run open_claude
parse result
mark completed/failed/timeout
```

V1 使用后台线程：

```python
threading.Thread(target=run_job, daemon=True).start()
```

V2 再换成队列/Worker。

### 7.9 `runtime/result_parser.py`

优先解析：

```text
data/jobs/{job_id}/artifacts/result.json
```

如果不存在：

```text
读取 stdout.log 最后 8000 字符
生成 partial result
```

输出统一写回：

```text
job.json.result
```

### 7.10 `runtime/trace_store.py`

职责：

```text
append events.jsonl
更新 job.json status
记录 runner_started / runner_output / runner_finished / parser_result
```

### 7.11 `runtime/artifact_store.py`

职责：

```text
列出 artifacts 目录
下载指定 artifact
防止路径穿越
```

## 8. API 细节

### 8.1 `POST /api/jobs`

请求：

```json
{
  "message": "请分析这个项目"
}
```

V1 先只支持 `message`。

响应：

```json
{
  "job_id": "job_20260611_xxxxxx",
  "status": "queued"
}
```

### 8.2 `GET /api/jobs`

响应：

```json
{
  "jobs": [
    {
      "job_id": "...",
      "status": "completed",
      "summary": "...",
      "created_at": "...",
      "updated_at": "..."
    }
  ]
}
```

### 8.3 `GET /api/jobs/{job_id}`

响应：

```json
{
  "job_id": "...",
  "status": "completed",
  "result": {},
  "artifacts": [],
  "error": null
}
```

### 8.4 `GET /api/jobs/{job_id}/events`

V1 简化为一次性返回 events：

```json
{
  "events": []
}
```

V2 升级为 SSE。

### 8.5 `GET /api/jobs/{job_id}/artifacts`

列出产物：

```json
{
  "artifacts": [
    {"name": "report.md", "path": "report.md", "size": 1234}
  ]
}
```

### 8.6 `GET /api/jobs/{job_id}/artifacts/{name}`

下载产物文件。

### 8.7 `POST /api/jobs/{job_id}/cancel`

V1 可先标记 `cancel_requested`，如果进程还在则 kill。

## 9. Job 状态流转实现

V1 状态：

```text
queued
preparing_workspace
building_task_pack
running
parsing_result
completed
failed
timeout
cancelled
```

流转：

```text
queued
→ preparing_workspace
→ building_task_pack
→ running
→ parsing_result
→ completed
```

失败：

```text
任意阶段 → failed
running 超时 → timeout
用户取消 → cancelled
```

## 10. Task Pack 模板变量

模板变量：

```text
{{ agent_name }}
{{ agent_goal }}
{{ user_request }}
{{ workspace_dir }}
{{ artifacts_dir }}
{{ job_id }}
{{ required_files }}
```

V1 不引入模板引擎也可以，使用 `.replace()` 即可。

## 11. 前端调试页

V1 `frontend/index.html` 做成单页：

```text
顶部：Agent 名称和说明
左侧：任务输入框 + 提交按钮
中间：任务列表
右侧/下方：当前任务详情、日志、产物下载
```

页面调用：

```text
POST /api/jobs
GET /api/jobs
GET /api/jobs/{job_id}
GET /api/jobs/{job_id}/events
GET /api/jobs/{job_id}/artifacts
```

V1 可以轮询，不做 WebSocket。

## 12. Docker 设计

### 12.1 Dockerfile

基础镜像需要：

```text
Python 3.11+
Node.js 18+
```

启动命令：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 12.2 docker-compose.yml

V1 单服务：

```yaml
services:
  delegated-agent:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
```

V2 再拆：

```text
api
worker
redis
sandbox-runner
```

## 13. 测试计划

### 13.1 APD 生成器测试

新增：

```text
tests/test_delegated_agent_generator.py
```

检查：

```text
生成 zip 成功
zip 包含 backend/app/main.py
zip 包含 frontend/index.html
zip 包含 .env.example
zip 包含 runner/open_claude/Openclaude-openclaude/dist/cli.js 或给出明确错误
zip 不包含 node_modules
agent_definition.json 正确
```

### 13.2 生成工程 Runtime 测试

V1 可以生成一个 fake runner 模式：

```text
OPEN_CLAUDE_FAKE=1
```

fake runner 不启动真实 open_claude，而是写入：

```text
artifacts/result.json
artifacts/report.md
```

用于验证 API、Job 状态、Result Parser。

### 13.3 手工验收

```text
1. APD 页面点击生成 zip
2. 解压 zip
3. 配置 .env
4. 启动 backend
5. 打开 Web 页面
6. 提交任务
7. 查看 job 状态 running/completed
8. 查看 stdout.log
9. 下载 report.md
```

## 14. 验收标准

V1 完成标准：

完整 Definition of Done 见 `docs/delegated_agent_mode_full_architecture.md` 的“Definition of Done”章节；本节只列第一阶段开发验收项。

```text
1. APD 能生成 delegated-agent.zip。
2. zip 内置 open_claude 代码。
3. zip 解压后无需 APD 即可启动。
4. 本机启动方式可用。
5. Docker Compose 文件存在且说明清楚。
6. 提交任务后能创建 job 目录。
7. 能生成 task_pack.md。
8. 能启动 open_claude 或 fake runner。
9. 能保存 stdout.log/events.jsonl/job.json。
10. 能展示任务状态和产物列表。
11. 能下载 report.md。
```

## 15. 开发任务拆分

### 阶段 A：生成器骨架

| 任务 | 文件 | 结果 |
|---|---|---|
| 新增生成器模块 | `protocol_designer/delegated_generator.py` | 可生成 zip |
| 新增模板目录 | `protocol_designer/delegated_templates/` | 有 backend/frontend/docker 模板 |
| 新增单测 | `tests/test_delegated_agent_generator.py` | 校验 zip 结构 |

### 阶段 B：生成工程 Runtime

| 任务 | 文件 | 结果 |
|---|---|---|
| FastAPI 主程序 | `backend/app/main.py` | 可启动服务 |
| Job API | `backend/app/api/jobs.py` | 可创建/查询任务 |
| Workspace Manager | `backend/app/runtime/workspace_manager.py` | 可创建 job 目录 |
| Task Pack Builder | `backend/app/runtime/task_pack_builder.py` | 可生成任务包 |
| Trace Store | `backend/app/runtime/trace_store.py` | 可写 job.json/events |
| Result Parser | `backend/app/runtime/result_parser.py` | 可解析结果 |

### 阶段 C：open_claude Runner

| 任务 | 文件 | 结果 |
|---|---|---|
| Runner 实现 | `backend/app/runtime/openclaude_runner.py` | 可启动 dist/cli.js |
| 超时控制 | `openclaude_runner.py` | 超时 kill |
| 环境变量传递 | `openclaude_runner.py` | 支持 base_url/key/model |
| fake runner | `openclaude_runner.py` | 测试不依赖真实 LLM |

### 阶段 D：前端与下载入口

| 任务 | 文件 | 结果 |
|---|---|---|
| 简单前端 | `frontend/index.html` | 可提交任务、看状态 |
| APD API | `webui_server.py` | 可下载 zip |
| APD 按钮 | `webui_server.py` | 可见入口 |
| README | `README.md.j2` | 启动说明完整 |

### 阶段 E：真实场景验证

| 任务 | 结果 |
|---|---|
| 用 fake runner 验证 | 无 LLM 可跑通 |
| 用真实 open_claude 验证 | 能执行简单分析任务 |
| 记录问题清单 | 进入 V2 |

## 16. 实施顺序建议

建议严格按顺序推进：

```text
1. 先做 generator + fake runner。
2. 确认 zip 可启动。
3. 再接真实 open_claude。
4. 再接 APD WebUI 下载入口。
5. 最后补 Docker 和 README。
```

不要一开始就做复杂队列、权限系统和沙箱。

## 17. 与 open_claude 源码的关系

V1 直接复制：

```text
/home/data/rag/open_claude/Openclaude-openclaude
```

到生成工程：

```text
runner/open_claude/Openclaude-openclaude
```

注意：

```text
APD 不修改 open_claude 源码。
生成工程内的 open_claude 是一份固定版本副本。
后续 Agent 如果需要定制，可以改自己的 runner 副本。
```

## 18. 后续需要用户确认的产品点

开发前最好再确认：

```text
1. V1 生成 zip 是否必须包含完整 src，还是只要 dist + package.json？
2. V1 是否默认启用 fake runner 用于自检？
3. 生成工程前端是否只要简单 HTML，还是需要接入现有前端框架？
4. Docker 是否 V1 必须跑通，还是先生成草案？
5. open_claude 模型配置是否默认复用 APD 当前 LLM 配置？
```

当前建议默认答案：

```text
1. 包含完整 src + dist，不含 node_modules。
2. 启用 fake runner，便于无 key 验证。
3. 简单 HTML。
4. V1 生成 Docker 草案，尽量跑通。
5. 默认写入 .env.example，用户部署时配置。
```

## 19. 关键风险控制

```text
1. 不在 APD 当前主流程里硬改太多，先作为独立导出能力。
2. 不直接承诺生产级安全，V1 明确是轻量独立部署。
3. 不复制 node_modules，避免包体过大。
4. 真实 open_claude 执行失败时，必须能看到 stdout/stderr。
5. 每一步都要有 fake runner 兜底，避免 LLM/key/网络阻塞开发。
```

## 20. 开发完成后的文档更新

完成 V1 后，需要更新：

```text
docs/delegated_agent_mode_plan.md
README.md
WebUI 使用说明
可能新增 docs/delegated_agent_usage.md
```

并在 APD 页面上加入简短说明：

```text
Delegated Agent 适合复杂文件/项目执行任务。它会把 open_claude 打包进生成工程，生成后可以独立部署。
```

## 21. 文档审查补充与修订决定

本节是对前面方案的二次审查结果。以下内容应作为 V1 开发时的强约束，避免实现时出现路径、Runner、部署和验收不一致。

### 21.1 Job 根目录必须作为 Runner 可见根

前文同时出现了：

```text
workspace/
artifacts/
trace/
```

旧方案中 Runner 启动命令只把 `workspace` 加入 `--add-dir`，不要按这个实现：

```bash
--add-dir /absolute/job/workspace
```

这样 open_claude 可能无法稳定写入 `artifacts/result.json` 和 `artifacts/report.md`。

V1 修订为：

```text
open_claude 的 cwd = data/jobs/{job_id}
open_claude 的 --add-dir = data/jobs/{job_id}
```

Job 目录保持：

```text
data/jobs/{job_id}/
  input/
  workspace/
  artifacts/
  trace/
  job.json
```

Task Pack 中明确要求：

```text
读取 input/ 和 workspace/
最终只把交付产物写入 artifacts/
不要改 trace/
不要改 job.json
```

对应启动命令修订为：

```bash
node --enable-source-maps runner/open_claude/Openclaude-openclaude/dist/cli.js \
  --dangerously-skip-permissions \
  --add-dir /absolute/data/jobs/{job_id} \
  "任务包内容"
```

### 21.2 V1 必须保留 fake runner，且默认可自检

真实 open_claude 依赖模型网关、Key、Node 环境和 CLI 状态。为了保证生成工程“下载后立刻能验证服务链路”，V1 必须内置 fake runner。

配置：

```env
OPEN_CLAUDE_FAKE=1
```

fake runner 行为：

```text
1. 不启动 open_claude。
2. 写入 trace/stdout.log。
3. 写入 artifacts/result.json。
4. 写入 artifacts/report.md。
5. Job 状态进入 completed。
```

README 的第一条验收路径必须使用 fake runner，第二条才是真实 open_claude。

### 21.3 Runner 需要处理 CLI 交互和卡住问题

open_claude 可能出现以下情况：

```text
首次信任目录确认
安全提示
长时间无输出
模型网关失败
进程不退出
输出大量 ANSI 控制符
```

V1 Runner 必须实现：

```text
timeout_seconds 超时 kill
stdout/stderr 双日志
exit_code 记录
last_output_at 记录
最大日志截断策略
失败时把最后日志写入 job.json.error
```

如果 open_claude 出现需要交互的提示，V1 不做复杂交互代理，先按失败处理，并在错误中提示：

```text
open_claude 可能等待交互确认，请先在本机手动运行一次 runner/open_claude/Openclaude-openclaude/dist/cli.js 完成初始化，或检查 Runner 参数。
```

V2 再考虑 tty/pty、自动确认策略或 Web 终端模式。

### 21.4 环境变量要同时兼容 Anthropic/OpenAI 风格

Runner 启动 open_claude 时需要注入：

```text
ANTHROPIC_BASE_URL
ANTHROPIC_AUTH_TOKEN
ANTHROPIC_API_KEY
ANTHROPIC_MODEL
OPENAI_BASE_URL
OPENAI_API_KEY
OPENAI_MODEL
```

并且要先清理宿主机中可能污染执行的旧环境变量，再写入 `.env` 中的配置。

建议规则：

```text
OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL 是用户主要填写项。
Runner 自动映射到 ANTHROPIC_*。
```

### 21.5 bundled open_claude 复制策略需要可审计

V1 默认复制完整源码和 dist，但不复制 `node_modules`。

必须生成：

```text
runner/open_claude_manifest.json
```

内容包括：

```json
{
  "source_path": "/home/data/rag/open_claude/Openclaude-openclaude",
  "bundled": true,
  "copied_at": "...",
  "include": ["dist", "src", "bin", "package.json", "README.md"],
  "exclude": [".git", "node_modules", ".cache", "*.log"]
}
```

如果 `dist/cli.js` 不存在，生成器应失败并给出明确错误，不要生成半成品。

### 21.6 API 需要补充日志与健康检查

V1 API 除前文接口外，建议补充：

```text
GET /api/config
GET /api/jobs/{job_id}/logs/stdout
GET /api/jobs/{job_id}/logs/stderr
```

用途：

```text
/api/config：前端展示 Agent 名称、fake runner 状态、模型是否配置。
/logs/stdout：方便排查真实 open_claude 输出。
/logs/stderr：方便排查 Node/CLI/网关错误。
```

### 21.7 前端第一版不要做复杂布局

V1 前端只做“任务控制台”，不要做 APD 级复杂 IDE。

页面分区：

```text
1. 顶部：Agent 名称、运行模式 fake/real、健康状态。
2. 左侧/上方：任务输入框。
3. 中间：任务列表。
4. 右侧/下方：当前任务详情。
5. 日志区：stdout/stderr tabs。
6. 产物区：result.json/report.md 下载。
```

交互方式：

```text
轮询 GET /api/jobs/{job_id}
轮询 GET /api/jobs/{job_id}/events
不做 WebSocket/SSE
```

### 21.8 安全边界必须在 README 中写清楚

V1 不是生产安全沙箱。README 必须明确：

```text
V1 是轻量独立部署版。
真实 open_claude 具备文件和命令执行能力。
请只在可信环境运行。
请不要直接暴露公网。
生产使用前必须升级 Docker Sandbox、权限拦截和认证。
```

### 21.9 V1 测试验收顺序修订

验收必须按以下顺序：

```text
1. 生成 zip 结构测试。
2. 解压后 py_compile 后端代码。
3. fake runner 启动服务并跑通任务。
4. 检查 job.json/events/stdout/result/report。
5. 检查前端能展示任务和下载产物。
6. 再配置真实模型网关，测试真实 open_claude。
7. 最后测试 Docker Compose。
```

不要把真实 open_claude 作为第一验收路径，否则会被模型网关、Key、首次确认和 Node 环境卡住。

### 21.10 当前文档已足够进入 V1 开发

审查后结论：

```text
总设计文档负责方向。
开发实施文档负责落地。
本补充节补齐了路径可见性、fake runner、CLI 卡住、环境变量、复制审计、日志 API、安全声明和验收顺序。
```

因此，下一步可以开始 V1 开发，建议先做：

```text
protocol_designer/delegated_generator.py
protocol_designer/delegated_templates/
tests/test_delegated_agent_generator.py
```

## 22. 二次审查补充：实现细节风险清单

本节是第二轮审查后新增的实现细节约束。它主要解决“文档看起来完整，但开发时仍会卡住”的问题。

### 22.1 zip 可能很大，APD 导出不能完全照搬现有 Demo zip

现有 Demo zip 生成逻辑可以把整个 zip 放进内存返回。但 Delegated Agent 会内置 open_claude，包体可能明显变大。

V1 实现建议：

```text
生成 zip 时使用临时文件。
优先返回 FileResponse。
不要长期把完整 zip bytes 保存在内存中。
生成完成后由临时目录生命周期清理。
```

如果为了开发速度先用内存 zip，必须在代码中保留 TODO，并在文档说明：

```text
内置 open_claude 后包体较大，生产应改为临时文件/流式下载。
```

### 22.2 APD 导出接口已统一为 POST zip 下载

现有脚手架下载是：

```text
GET /api/scaffold/{session_id}.zip?name=...
```

Delegated Agent 因参数更多，V1 统一使用：

```text
POST /api/delegated-agent/{session_id}.zip
```

原因：

```text
参数较多，用 POST body 更清晰。
响应仍然是 application/zip。
```

前端使用 `fetch(..., {method:'POST'})` 获取 blob 下载。

### 22.3 生成工程 V1 运行模式必须声明为单进程

V1 使用后台线程执行 Job，因此必须限制部署方式：

```text
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

不建议：

```text
--reload
--workers > 1
gunicorn 多 worker
```

原因：

```text
后台线程、进程句柄、文件状态在多 worker 下会不一致。
```

README 必须明确：

```text
V1 是单进程任务执行器。
如需多 worker/多实例，请升级到 V2 Queue + Worker 架构。
```

### 22.4 并发控制需要真实实现，不只是配置项

`.env` 中有：

```text
MAX_CONCURRENT_JOBS=1
```

V1 需要实现一个进程内并发限制：

```text
threading.Semaphore(MAX_CONCURRENT_JOBS)
```

行为：

```text
超过并发限制的任务状态保持 queued。
后台调度器按创建时间取下一个 queued job。
```

如果 V1 为了简化只做“提交即运行”，则必须限制：

```text
MAX_CONCURRENT_JOBS 固定为 1。
如果已有 running job，新任务直接返回 429 或 queued 但不启动。
```

推荐 V1 做最小 queued 调度，避免用户一连点几次导致多个 open_claude 同时跑。

### 22.5 文件写入必须原子化，避免 job.json 损坏

V1 使用文件存储，不上数据库。因此 `job.json` 写入必须避免半写入。

建议实现：

```text
写入 job.json.tmp
fsync/close 后 rename 到 job.json
```

events.jsonl 可以 append，但每行必须是完整 JSON。

如果有多个线程可能写同一个 Job，需要加：

```text
threading.Lock
```

或按 Job 级别串行写入。

### 22.6 生成的独立 Agent 至少需要简单鉴权开关

前文已经提醒不要公网裸露，但作为独立部署服务，V1 最好内置简单 API Token。

`.env.example` 增加：

```env
AGENT_API_TOKEN=
```

规则：

```text
为空：不启用鉴权，仅适合本地/内网测试。
非空：所有 /api/* 请求必须带 Authorization: Bearer <token>。
```

前端页面如果检测到需要 token，可以让用户输入并保存在浏览器 localStorage。

这不是完整 RBAC，但能避免用户误以为该服务可以直接公网暴露。

### 22.7 输入文件能力需要分阶段明确

前文 V1 先只支持 `message`，但 Delegated Agent 的核心价值之一是文件/项目处理。因此需要明确分阶段：

V1：

```text
只支持 text message。
任务可以要求 open_claude 在 job workspace 内生成报告。
```

V1.1：

```text
支持 multipart 上传文件。
上传文件保存到 data/jobs/{job_id}/input/。
Task Pack 自动列出 input 文件清单。
```

V2：

```text
支持上传 zip 并解压到 workspace。
支持目录型输入。
支持文件大小和扩展名限制。
```

如果 V1 开发时有余力，可以直接实现单文件上传，但不能阻塞 V1 主闭环。

### 22.8 open_claude 运行依赖要做启动前检查

生成工程启动时或执行 Job 前，需要检查：

```text
Node.js 是否存在。
dist/cli.js 是否存在。
OPENAI_BASE_URL/OPENAI_API_KEY/OPENAI_MODEL 是否配置。
```

fake runner 模式下不要求 Node 和 Key。

`GET /api/config` 应返回：

```json
{
  "fake_runner": true,
  "node_available": true,
  "open_claude_cli_exists": true,
  "model_configured": false
}
```

前端展示为“运行前检查”，避免用户提交后才发现环境不完整。

### 22.9 bundled open_claude 默认不复制 node_modules，但 Docker 要处理依赖

V1 默认不复制 `node_modules`，因此 Dockerfile 需要二选一：

方案 A：

```text
仅运行 dist/cli.js，不安装依赖。
```

前提：

```text
dist/cli.js 是可独立运行 bundle。
```

方案 B：

```text
构建镜像时在 runner/open_claude/Openclaude-openclaude 内执行 npm/pnpm install。
```

V1 推荐：

```text
优先方案 A。
Dockerfile 中保留注释，说明如果 dist 缺依赖再打开安装步骤。
```

验收时必须实际跑一次：

```bash
node runner/open_claude/Openclaude-openclaude/dist/cli.js --help
```

或等价检查。

### 22.10 真实 open_claude 验收应使用最小任务

真实 Runner 第一条验收任务不要直接做复杂业务。

推荐最小任务：

```text
请在 artifacts/report.md 写一段“hello delegated agent”，并生成 artifacts/result.json。
```

这样可以优先验证：

```text
open_claude 能启动
模型网关可用
路径可写
结果契约可解析
```

再验证复杂项目分析任务。

### 22.11 APD 页面说明要避免用户误解

页面文案必须明确 Delegated Agent 和 Agent IDE 的关系：

```text
Agent IDE：用于在 APD 内协作开发 Agent。
Delegated Agent：导出一个可独立部署的 Agent 服务，内部打包 open_claude。
```

不要让用户误以为点击该按钮是在 APD 当前页面直接运行 open_claude。

### 22.12 二审结论

加入本节后，开发方案已经覆盖：

```text
大 zip 下载
接口风格
单进程限制
并发控制
原子文件写入
简单鉴权
输入文件分阶段
运行前检查
Docker 依赖策略
真实 Runner 最小验收
页面认知边界
```

因此，文档现在可以交给另一个开发者或 open_claude/Codex 按阶段 A 开始实现。
