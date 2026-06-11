# Delegated Agent 使用说明

本文面向 APD 用户，说明如何从 APD 生成一个可独立部署的 Delegated Agent 工程，并完成 fake runner、真实 open_claude、日志和产物验证。

## 1. 适用场景

Delegated Agent 适合把复杂任务委托给内置 open_claude 执行，例如：

```text
文件处理
项目分析
代码改造
文档生成
招投标分析
知识库整理
数据清洗
自动测试/修复
```

它不是 APD 内部开发台，也不是普通聊天机器人。生成后的 zip 是一个独立 Agent 服务，解压后不依赖 APD 进程即可运行。

## 2. 在 APD 里生成 zip

入口：

```text
APD Web UI → 右侧“导出产物” → 生成 Delegated Agent zip
```

点击后填写：

| 字段 | 含义 |
|---|---|
| 项目目录名 | 生成 zip 内的根目录名 |
| Agent 名称 | 生成服务页面和配置中的 Agent 名称 |
| Agent 目标 | 写入 Task Pack，告诉 open_claude 这个 Agent 要完成什么 |
| 默认任务说明 | 每个 Job 的默认交付要求 |
| open_claude 模板路径 | 默认 `/home/data/rag/open_claude/Openclaude-openclaude` |

APD 调用接口：

```text
POST /api/delegated-agent/{session_id}.zip
```

响应是：

```text
application/zip
```

## 3. zip 内部结构

生成工程大致结构：

```text
my-delegated-agent/
  README.md
  .env.example
  Dockerfile
  docker-compose.yml
  manifest.json

  backend/
    requirements.txt
    app/
      main.py
      config.py
      schemas.py
      agent_definition.py
      agent_definition.json
      api/
        health.py
        jobs.py
        artifacts.py
      runtime/
        workspace_manager.py
        task_pack_builder.py
        trace_store.py
        artifact_store.py
        result_parser.py
        openclaude_runner.py
        runner_worker.py
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
    open_claude_manifest.json

  data/
    .gitkeep
```

说明：

- `backend/app/main.py` 是 FastAPI 入口。
- `frontend/index.html` 是生成 Agent 自带的任务控制台。
- `runner/open_claude/Openclaude-openclaude` 是打包进去的 open_claude 源码和 dist。
- `runner/open_claude_manifest.json` 记录复制来源、排除项和文件数量。
- `data/jobs/` 是运行后自动创建的任务数据目录。

## 4. fake runner 本地验证

fake runner 是第一条必跑验收路径。它不调用真实 open_claude，不需要模型 Key，也不需要 Node 成功执行。

```bash
unzip your-agent-delegated-agent.zip
cd your-agent/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开：

```text
http://127.0.0.1:8000
```

提交一条任务，例如：

```text
请生成一份测试报告。
```

成功后应看到 Job 状态为：

```text
completed
```

并生成：

```text
data/jobs/<job_id>/job.json
data/jobs/<job_id>/trace/task_pack.md
data/jobs/<job_id>/trace/events.jsonl
data/jobs/<job_id>/trace/stdout.log
data/jobs/<job_id>/artifacts/report.md
data/jobs/<job_id>/artifacts/result.json
```

## 5. API 验证

健康检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8000/api/config
```

提交任务：

```bash
curl -X POST http://127.0.0.1:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"message":"请生成一份测试报告"}'
```

查看任务：

```bash
curl http://127.0.0.1:8000/api/jobs
curl http://127.0.0.1:8000/api/jobs/<job_id>
curl http://127.0.0.1:8000/api/jobs/<job_id>/events
curl http://127.0.0.1:8000/api/jobs/<job_id>/artifacts
curl http://127.0.0.1:8000/api/jobs/<job_id>/logs/stdout
curl http://127.0.0.1:8000/api/jobs/<job_id>/logs/stderr
```

下载产物：

```bash
curl -O http://127.0.0.1:8000/api/jobs/<job_id>/artifacts/report.md
```

## 6. 接真实 open_claude

编辑生成项目根目录 `.env`：

```env
OPEN_CLAUDE_FAKE=0
OPENAI_BASE_URL=http://your-gateway/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=your-model
JOB_TIMEOUT_SECONDS=600
MAX_CONCURRENT_JOBS=1
```

Runner 会自动把 OpenAI 风格配置映射给 open_claude：

```text
OPENAI_BASE_URL
OPENAI_API_KEY
OPENAI_MODEL
ANTHROPIC_BASE_URL
ANTHROPIC_AUTH_TOKEN
ANTHROPIC_API_KEY
ANTHROPIC_MODEL
```

建议第一条真实任务使用最小验收：

```text
请在 artifacts/report.md 写一段“hello delegated agent”，并生成 artifacts/result.json。
```

真实 Runner 的关键路径：

```text
cwd = data/jobs/<job_id>
--add-dir = data/jobs/<job_id>
```

这样 open_claude 可以同时访问：

```text
input/
workspace/
artifacts/
trace/
```

## 7. Docker Compose

生成工程包含 Dockerfile 和 docker-compose.yml：

```bash
cp .env.example .env
docker compose up --build
```

默认仍然是：

```env
OPEN_CLAUDE_FAKE=1
```

如果要在 Docker 中跑真实 open_claude，需要确认：

```text
Node.js 可用
runner/open_claude/Openclaude-openclaude/dist/cli.js 存在
模型网关可访问
OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL 已配置
```

V1 默认不复制 `node_modules`。如果 `dist/cli.js` 不是独立 bundle，需要在 Dockerfile 中打开 npm install 相关步骤。

## 8. 鉴权

生成服务支持简单 API Token。

`.env` 中：

```env
AGENT_API_TOKEN=
```

规则：

```text
为空：不启用鉴权，仅适合本地/可信内网测试。
非空：所有 /api/* 请求必须带 Authorization: Bearer <token>。
```

Web 控制台会提供 Token 输入框，Token 保存在浏览器 localStorage。

## 9. 安全边界

V1 不是生产安全沙箱。

必须明确：

```text
真实 open_claude 具备文件读写和命令执行能力。
V1 主要靠 Job 目录隔离、Task Pack 约束、超时和日志审计。
不要公网裸露。
不要把真实 API Key 写进代码、README、job.json、events 或 stdout。
生产使用前必须升级 Docker Sandbox、权限拦截、队列、审计和 RBAC。
```

## 10. 常见问题

### 10.1 生成 zip 失败：open_claude source not found

检查 APD 生成时填写的路径：

```text
/home/data/rag/open_claude/Openclaude-openclaude
```

该目录必须存在。

### 10.2 生成 zip 失败：dist/cli.js not found

检查：

```text
runner/open_claude/Openclaude-openclaude/dist/cli.js
```

V1 要求 open_claude 源路径中已经存在 `dist/cli.js`。

### 10.3 fake runner 能跑，真实 runner 失败

优先看：

```text
GET /api/jobs/<job_id>/logs/stdout
GET /api/jobs/<job_id>/logs/stderr
GET /api/config
```

常见原因：

```text
Node.js 不可用
模型网关不可访问
Key 错误
模型名错误
open_claude 首次运行等待交互确认
dist/cli.js 依赖 node_modules，但 node_modules 未安装
```

### 10.4 open_claude 等待安全确认

V1 不做 tty/pty 交互代理。处理方式：

```text
先在服务器上手工运行一次内置 CLI 完成初始化或信任目录确认。
再通过 Delegated Agent 服务提交真实任务。
```

### 10.5 为什么 V1 只支持 message

第一阶段目标是先证明：

```text
APD 生成 zip
zip 独立启动
Job 创建
Task Pack 生成
fake runner 跑通
open_claude runner 可执行
日志和产物可见
```

文件上传、zip 解压、目录型输入属于 V1.1/V2。

## 11. 当前实现状态

当前仓库已完成第一阶段代码实现：

```text
APD WebUI 下载入口：已完成
POST /api/delegated-agent/{session_id}.zip：已完成
生成器：已完成
fake runner：已完成并通过测试
真实 open_claude runner：代码路径已完成
日志 API：已完成
产物 API：已完成
Web 任务控制台：已完成
Dockerfile/docker-compose.yml：已生成草案
```

当前测试覆盖：

```text
生成 zip 结构测试
open_claude 复制过滤测试
生成工程 Python 编译测试
fake runner 端到端测试
APD 导出 API 冒烟测试
```

真实 open_claude 的生产可用性仍取决于目标部署环境的 Node.js、模型网关、Key、模型名和 open_claude 首次确认状态。
