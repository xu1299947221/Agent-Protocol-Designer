# APD - Agent Engineering Platform

APD started as **Agent Protocol Designer**. Its current direction is **Agent Engineering Platform**: a toolchain for turning messy business requirements into runnable, debuggable, evolvable Agent projects.

APD 不是普通 Agent Builder、RAG 平台或 Workflow 拖拽器。它的核心目标是：

```text
把模糊业务需求，持续转化为可运行、可调试、可演进的 Agent 工程。
```

当前 APD 的完整链路是：

```text
需求对话
  ↓
APD 协议草案 JSON
  ↓
两条落地路线
  ├── 委托型真实调试 / Delegated Agent：APD 协议 + Delegated Runtime + open_claude Engine
  └── 自研工程开发 / Native Runtime：APD 协议 + 自研脚手架 + 工程开发台持续补 Runtime
  ↓
真实调试 / AI 修复 / 协议增量迁移 / 版本回滚 / 独立部署
```

## Why

自由 ReAct / Tool Calling 很灵活，但复杂业务里容易失控：模型会误选工具、误填参数、覆盖状态、提前提交或删除内容。

早期 APD 的目标是把：

```text
自然语言业务需求
```

收敛成：

```text
objects + operations + validators + planner schema + executor skeleton
```

也就是让 LLM 负责语义理解，让程序负责确定性校验和状态执行。

现在 APD 的目标进一步升级为：

```text
自然语言业务需求
  → APD 协议
  → Agent 工程脚手架
  → 真实调试
  → AI 诊断修复
  → 协议 diff
  → 工程增量迁移
  → 可部署 Agent 服务
```

## Product Direction

APD 后续方向不是普通 Agent Builder，而是面向定制 Agent 开发的 `Agent Engineering Platform`。

继续开发前请阅读：

- `docs/agent_harness_architecture_gaps.md`
- `docs/agent_architecture_guide.md`
- `docs/apd_next_tasks.md`

当前主线：

```text
1. 协议设计：通过对话生成 APD 协议草案 JSON。
2. 工程生成：基于协议生成 Delegated Agent 或自研 Agent 工程。
3. 真实调试：在 APD 页面里直接和生成 Agent 对话，观察白盒过程。
4. AI 修复：把问题转成工程任务，交给 open_claude / Codex 类工具修复。
5. 增量迁移：协议 v1 → v2 后生成 diff 和 migration task pack，迁移已有工程。
6. Engine 化：把 open_claude 从 CLI 改造成可服务化 Agent Engine。
7. 部署治理：支持多用户、Job、Workspace、Trace、Artifact、权限和版本回滚。
```

核心架构能力包括：记忆策略（Memory Policy）、状态模型（State Model）、产物模型（Artifact Model）、工具注册（Tool Registry）、权限策略（Permission Policy）、失败恢复（Error Recovery）、评测用例（Eval Cases）、架构完整性检查器、协议 diff、工程迁移任务包、真实调试台和 Engine Adapter。

## Features

- 对话式收敛业务 Agent 需求
- 自动生成 APD 协议草案 JSON：`objects` / `user_intents` / `operations` / `validators`
- 生成架构增强层：`workflow` / `memory_policy` / `state_model` / `artifact_model` / `tool_registry` / `permission_policy`
- 架构完整性检查：识别当前 Agent 缺少哪些 Harness 层
- 两条工程落地路线：
  - `Delegated Agent`：生成委托型 Agent 工程，调用 open_claude CLI / 未来 Engine API
  - `Native Runtime`：生成自研 Agent 工程骨架，通过工程开发台持续开发 Runtime
- 真实调试台：用户侧 Agent 对话、白盒过程、LLM 诊断、AI 修复任务
- 工程开发台：工作区、ttyd/open_claude 终端、版本保存、下载工程
- 增量演进设计：协议 diff、工程迁移任务包、已有工作区同步
- 高级导出：`protocol.json` / `workflow.json` / `eval_cases.json` / `planner_prompt.md` / `executor_skeleton.py`
- 支持 OpenAI-compatible API
- 支持 Multi-Agent 协作雏形：角色分工、消息协议、任务交接、冲突处理和协同 Trace
- 原型阶段主要使用文件存储和本地工作区

## Quick Start

```bash
cd /home/data/api/agent-protocol-designer

export APD_API_BASE="http://your-gateway/v1"
export APD_API_KEY="your-key"
export APD_MODEL="deepseek-v3.2"

./run_webui.sh
```

打开：

```text
http://localhost:8510
```

也可以在页面顶部直接填写 API Base / Key / Model。

## Suggested Flow

1. 在首页左侧描述你想做的 Agent 场景。
2. APD 通过一次一个问题的方式收敛业务对象、操作、状态、权限、产物和评测。
3. 首页生成 APD 协议草案 JSON。
4. 协议基本清楚后，选择落地路线：
   - 想快速看到真实效果：进入 `真实调试台 / Delegated Agent`
   - 想长期完全自研可控：进入 `工程开发台 / Native Runtime`
5. 在调试台里和 Agent 对话，观察白盒过程和诊断结果。
6. 使用“让 AI 修这个问题”把调试问题转成工程任务。
7. 新业务需求先回首页补协议，再生成协议 diff 和迁移任务，增量改造已有工程。
8. 调试通过后保存版本、下载工程或独立部署。

## Example Input

```text
我想设计一个客服退款 Agent，能查询订单、申请退款、修改地址、投诉物流。
```

Expected output shape:

```json
{
  "objects": ["User", "Order", "RefundRequest", "Ticket"],
  "operations": [
    "query_order",
    "request_refund",
    "modify_shipping_address",
    "create_logistics_complaint"
  ]
}
```

## Dynamic Workflow Usage

Dynamic Workflow 是 APD 的增量能力，用来描述“多个 Agent / Tool / Validator 如何组成一次可靠任务流程”。APD 会在识别到复杂场景时主动询问是否进入 Dynamic Workflow 设计，不要求用户自己判断。

它适合：

- 多步骤任务
- 流程会随输入变化
- 有多个子任务可以并行
- 需要人工确认、失败回退、覆盖率校验
- 需要把流程沉淀成模板复用

它不强制替代原来的 Agent Protocol。原来的能力协议仍然是底座：

```text
Agent Protocol = 原子能力层
Dynamic Workflow = 能力编排层
```

协议中新增字段：

```json
{
  "workflow": {
    "name": "",
    "goal": "",
    "strategy": "",
    "nodes": [],
    "edges": [],
    "parallel_groups": [],
    "human_review_points": [],
    "failure_strategy": [],
    "artifacts": []
  }
}
```

Web UI 中可以：

- 点击 `Dynamic Workflow 架构说明` 查看架构说明
- 正常对话即可；当系统识别到多步骤/并行/强校验/人工确认场景，会在“下一步问题”里主动询问是否进入工作流设计
- 在右侧“导出产物”里下载 `workflow.json`
- 在右侧“导出产物”里下载 `workflow_plan.md`

## Agent Project Scaffold Generator

APD 可以根据已有 session 生成一个可运行 Agent Harness Demo。这个功能不是“一键完成业务系统”，而是把 `protocol.json`、`workflow.json` 和 Harness 架构层转成可启动、可体验、可继续开发的工程骨架。

```bash
./apd generate <session_id> --out ./generated --name my-agent
```

`--name` 用来指定生成项目目录名；不传时默认从会话标题或项目名推导。

Web UI 也支持生成：在右侧“导出产物”区域点击 `生成可运行 Demo zip`，输入项目目录名后会直接下载可运行 Demo zip 包。

如果目录已存在，可覆盖：

```bash
./apd generate <session_id> --out ./generated --force
```

当前模板：

```text
fastapi-vue
```

生成内容：

```text
generated/<project-name>/
├── README.md
├── backend/
│   ├── protocol.json
│   ├── workflow.json
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py              # FastAPI 入口，含 /agent/run 和 /harness/spec
│   │   ├── harness.py           # 上下文、权限、恢复、评测沉淀等 Harness 层
│   │   ├── models.py            # 请求/响应/业务对象骨架
│   │   ├── planner.py           # 规则版 Demo Planner，可替换为 LLM Planner
│   │   ├── validators.py        # Validator 注册表和 Demo 校验器
│   │   ├── executor.py          # 安全模拟执行器
│   │   ├── store.py             # Demo 内存存储：Memory / State / Artifact / Trace / Job
│   │   ├── tools.py             # Tool Adapter dry-run 模板
│   │   ├── workflow.py          # Workflow Runtime Demo，支持节点级运行
│   │   └── runtime.py           # Context Pack → Planner → OpCall → Validator → Tool → Store → Artifact → Trace
│   └── tests/
│       └── test_protocol_shape.py
├── frontend/
│   ├── AgentPlayground.vue      # 最小体验页面
│   └── api.js
├── docs/
│   ├── workflow_plan.md
│   ├── development_plan.md
│   ├── planner_prompt.md
│   ├── architecture_check.md
│   ├── eval_policy.md
│   └── executor_skeleton.py
└── session.json
```

生成物的定位：

```text
能启动 FastAPI
能看 Harness 结构：/harness/spec
能运行单轮 Agent：POST /agent/run
能运行 Workflow Demo：POST /workflow/run
能查看 Demo 内存：GET /store/snapshot
能查看 Tool Adapter：GET /tools
能体验 Context Pack → Intent Planner → OpCall → Validator → Permission → Tool Dry-run → Store → Artifact Version → Recovery → Eval Suggestion → Trace
真实业务逻辑在 TODO 里继续开发
```

## Delegated Agent Export

APD 现在支持生成独立部署型 Delegated Agent。它不同于普通 Harness Demo：生成 zip 会内置 open_claude，把复杂文件/项目/代码执行任务委托给 open_claude，同时由生成服务负责 Job、Task Pack、Trace、Artifact、日志和 Web 控制台。

适合场景：

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

Web UI 入口：

```text
右侧“导出产物” → 生成 Delegated Agent zip
```

生成接口：

```text
POST /api/delegated-agent/{session_id}.zip
```

生成工程包含：

```text
FastAPI 服务
Web 任务控制台
fake runner
真实 open_claude runner
Job 文件存储
Task Pack 生成
stdout/stderr/events 日志
artifacts/result.json
artifacts/report.md
Dockerfile
docker-compose.yml
```

下载后第一条验收路径默认使用 fake runner，不需要 LLM、Key 或 Node：

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

要接真实 open_claude，编辑生成项目根目录 `.env`：

```env
OPEN_CLAUDE_FAKE=0
OPENAI_BASE_URL=http://your-gateway/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=your-model
```

安全边界：

```text
V1 是轻量独立部署版，只适合本地或可信内网。
真实 open_claude 具备文件读写和命令执行能力。
不要直接暴露公网。
生产使用前必须升级 Docker Sandbox、权限拦截、队列、审计和 RBAC。
```

完整使用说明：

```text
docs/delegated_agent_usage.md
```




## Agent 开发台 v1

APD Web UI 现在新增 `Agent 开发台` 入口，用来把“一次性 Demo”升级成“可持续开发的 Agent 项目”。

入口：

```text
顶部按钮：Agent 开发台
右侧导出产物：打开 Agent 开发台
右侧开发入口：Agent 开发台 v1
```

小白使用顺序：

```text
1. 先通过左侧对话设计一个 Agent，或者打开已有会话
2. 点击 Agent 开发台
3. 点击“创建可持续开发工作区”
4. 点击“运行当前版本”，看当前项目真实启动后的效果
5. 如果结果还行，点击“保存新版本”
6. 如果后续改坏了，在版本历史里点击“回滚到此版本”
7. 点击“下载当前版本”，拿到完整项目 zip
```

几个核心概念：

```text
工作区 = APD 给你生成的一份可持续修改的 Agent 项目
版本 = 每次你觉得效果还行，就拍一张项目快照
运行 = APD 真实启动当前项目，调用 Agent / Workflow 接口看效果
回滚 = 不满意就退回旧版本
下载 = 把当前版本打包拿走继续开发或部署
```

当前 v1 能做：

```text
能创建持久化工作区
能在页面内编辑关键文件
能用“小白快速改效果”直接修改运行回复
能保存后立即运行，看 Agent 返回是否变化
能保存版本快照
能回滚到历史版本
能真实启动当前工作区后端
能调用 /agent/run、/workflow/run、/store/snapshot、/tools
能下载 current 或指定版本 zip
```

边改边看的最小体验：

```text
1. 打开 Agent 开发台
2. 创建或选择一个工作区
3. 在“小白快速改效果”里填一句新的 Agent 回复
4. 点击“边改边看：保存回复并立即运行”
5. 页面会修改 backend/app/executor.py，并马上运行当前工作区
6. 查看运行结果里的 assistant_message 是否变成刚才填写的话
```

代码编辑体验：

```text
1. 在“页面内文件编辑器”选择 backend/app/executor.py
2. 搜索 message
3. 修改回复文案或执行逻辑
4. 点击“保存并立即运行”
5. 如果效果满意，再点击“保存新版本”
```

### AI 工程开发 Developer Runner

`Agent 开发台 v1` 现在的目标不是单个 `open_claude` 按钮，而是面向“会编程、会用 Claude Code / Codex，但不熟 Agent 架构”的开发者提供工程驾驶舱。

正确流程：

```text
Agent 改造需求
→ APD 判断 Agent 类型和涉及架构层
→ APD 生成工程任务包
→ 选择 Runner 执行：open_claude / codex / claude_code / custom_shell
→ 页面展示日志和改动文件
→ 运行当前 Agent 验收
→ 保存版本 / 回滚 / 继续迭代
```

页面入口：

```text
Agent 开发台 → AI 工程开发：架构分析 → 任务包 → Runner 执行 → APD 验收
```

Runner 选项：

```text
open_claude：默认 CLI 目录 /home/data/rag/open_claude/Openclaude-openclaude
codex：使用 codex exec，从 stdin 接收 APD 任务包
claude_code：使用 claude -p，从参数接收 APD 任务包
custom_shell：使用自定义命令模板，可用 {task} {task_file} {project_root}
```

页面可配置：

```text
Agent 改造需求：你希望当前 Agent 增强什么能力
Runner：open_claude / codex / claude_code / custom_shell
Base URL：本次传给 Runner 的模型网关地址
Key / Token：本次传给 Runner，日志会脱敏
模型：本次模型名，可留空
open_claude CLI 目录：仅 open_claude 需要
目标项目目录：留空则使用当前 APD 工作区项目目录
自定义命令模板：仅 custom_shell 或高级用法需要
超时秒数：默认 900 秒
```

建议使用方式：

```text
1. 先创建或选择一个 APD 工作区
2. 在“Agent 改造需求”里描述目标
3. 点击“生成架构方案和任务包”
4. 先看 APD 判断的 Agent 类型、架构层、推荐文件、风险、验收标准
5. 选择 Runner，例如 open_claude
6. 点击“执行选中 Runner”
7. 查看 Runner 日志和改动文件
8. 点击“运行当前 Agent 验收”
9. 满意后点击“保存新版本”
```

注意：这是部门自用的工程 Runner，不建议暴露到公网。Key 不会显示在日志中，但浏览器本地会用 `localStorage` 临时保存页面配置。

当前 v1 已支持通过 Developer Runner 调用外部工程 Agent 改代码。它的定位是先把“架构任务包 → Runner 执行 → APD 验收”的闭环稳定下来：

```text
对话设计需求 → 生成工作区 → APD 生成工程任务包 → Runner 改代码 → 运行验收 → 保存版本 → 回滚 → 下载项目
```

下一步演进方向：

```text
对话提出修改需求 → APD 修改工作区代码 → 自动运行验证 → 页面实时看到效果 → 保存版本
```

优先改的生成项目文件：

```text
backend/app/planner.py   # 用户话术理解与 OpCall 绑定
backend/app/executor.py  # 真实业务动作执行
backend/app/tools.py     # 知识库、文档、检索、导出等工具接入
backend/app/workflow.py  # 复杂任务节点化执行
```

## Demo Playground

APD Web UI 现在支持小白版“一键体验”：在页面内直接运行生成出来的 Agent Demo，不必先下载 zip、不必手动启动服务、不必理解接口。

入口：

```text
顶部按钮：Demo Playground
右侧导出产物：在线运行 Demo
右侧开发入口：Demo Playground
```

小白使用顺序：

```text
1. 打开一个已有会话，或先设计一个 Agent
2. 点击 Demo Playground
3. 点击“一键体验：生成、启动并自动测试”
4. 先看中文解释，不要先看 Raw JSON
5. 如果想深入，再展开“开发者细节”
6. 用完点击“停止 Demo”释放临时进程
```

小白优先看这些中文结果：

```text
当前 Demo 成熟度：例如 30% / 50% / 70%
当前阶段：可运行骨架、半成品 Agent、接近可体验 Agent
已经做到什么：工程骨架、OpCall、权限、Trace、Workflow、Store 等
为什么还没业务感受：真实 LLM、真实 Executor、真实工具、真实文档产物是否还没接
下一步应该开发什么：按优先级列出文件和目标
```

再看这些运行指标：

```text
选中的操作：Demo 是否把你的话术绑定到某个 operation
权限结果：这个操作是允许、需要确认，还是禁止
工具结果：是否经过 Tool Adapter dry-run
产物版本：执行后是否生成了可追踪产物
过程日志：每一步是否能复盘
Workflow 节点：是否跑到了节点级流程
Store Trace：运行结果是否写入 Demo 内存
工具模板：生成项目里是否有工具接入位置
```

开发者再看这些字段：

```text
tool_results
state_snapshot
artifact_versions
trace
node_states
```

当前 Demo Playground 更准确的定位是“Agent 成熟度评估台”：它会在 APD 服务器内部生成临时项目并启动 FastAPI，用来判断当前 Agent 做到了哪一步、为什么还没有业务效果、下一步应该开发哪里；它不等于生产部署。

## Multi-Agent Coordination

APD Web UI 提供 `Multi-Agent 协作` 入口，用于分析当前协议是否需要多个 Agent 角色协作。

入口位置：

```text
顶部 学习/工具 → Multi-Agent 协作
右侧 开发入口 → Multi-Agent 协作
```

当前雏形会展示：

- 角色分工：协调者、研究检索、内容生成、执行者、审校者、人工守门人
- 消息协议：多个 Agent 之间必须传递的结构化消息字段
- 任务交接：每次交接必须说明已完成、剩余事项、产物、风险和 Trace
- 冲突处理：意图冲突、证据冲突、产物冲突、权限冲突的处理规则
- 协同 Trace：模拟多个 Agent 如何一步步协作，方便后续接入真实 Runtime

当前版本仍是设计和模拟层，不会真实启动多个 LLM Agent。真实执行应继续接入 Runtime、权限审计、产物版本和人工确认。

## Agent Preview Runtime

APD Web UI 提供 `预览运行 Agent` 入口，用于在不生成代码、不修改真实状态的情况下，直接验证当前协议的 Agent 行为。

预览链路：

```text
测试话术 → Context Pack → Intent Frame → OpCall → Validator → Mock Executor → Trace
```

使用方式：

1. 打开 APD Web UI。
2. 选择或继续一个会话。
3. 点击顶部 `预览运行 Agent`。
4. 可以手动输入测试话术，也可以点击 `AI 生成当前场景测试示例`，系统会根据当前 session 的业务场景动态生成多条示例。
5. 点击示例里的 `填入并运行`，或手动点击 `运行预览`。
6. 默认先看“小白视图”：用户可见回复、绑定操作、最终动作和下一步怎么看。
7. 需要开发细节时，再点击 `开发者细节`，查看 `context_pack`、`intent_frame`、`op_call`、`validator_results`、`executor_preview` 和 `trace`。

结果含义：

- `context_pack`：本轮交给 LLM 观察的结构化上下文。
- `intent_frame`：LLM 对用户真实意图、目标、范围、风险和证据的理解。
- `op_call`：把自由意图绑定到协议中的有限 operation 和参数。
- `validator_results`：程序侧确定性校验，判断操作是否存在、是否缺信息、是否需要确认。
- `executor_preview`：模拟执行器，只展示如果真实执行会做什么。
- `trace`：完整决策链路，用于排查误解、误绑定和校验缺失。

预览运行只做模拟执行，不会真实写库、导出文件或修改业务状态。LLM 不可用时会进入离线规则降级模式。

## Docker Sandbox Engineering Runner

APD Web UI 提供 `Docker 沙箱` 入口，用于把当前会话产物和目标项目副本放进 Docker 容器里执行工程任务。第一版定位为个人开发工具，不会直接修改真实项目。

运行流程：

```text
当前 session → APD 产物 → 临时 workspace → Docker 容器 → stdout/stderr/diff/result → Web UI 展示
```

使用方式：

1. 打开 APD Web UI。
2. 点击顶部 `Docker 沙箱`。
3. 填写目标项目目录，例如 `/home/data/rag/ragyuyan/rag_agent`；留空则自动生成当前 APD 会话的脚手架并放入 `/workspace/project`。
4. 填写 Docker 镜像，默认 `python:3.12-slim` 可做冒烟测试。
5. 填写容器内执行命令；如果不填，默认执行冒烟测试并写入 `/workspace/APD_RESULT.json`。
6. 点击 `运行 Docker 沙箱`。

安全边界：

- 只挂载临时 workspace：`/tmp/apd-sandbox/jobs/<job_id>/workspace:/workspace`
- 目标项目会先复制到 `/workspace/project`
- 默认 `cap-drop ALL`、`no-new-privileges`、限制 CPU/内存/PID
- 默认仅传入 APD LLM 配置白名单环境变量
- 运行结果保留在 job 目录，真实项目不会被直接修改

如果要接 Codex CLI / Claude Code CLI，请准备包含对应 CLI 的镜像，并在页面里填写镜像名和执行命令。

它适合交给 Claude Code / Codex 继续实现。

## Environment Variables

| Name | Description |
|---|---|
| `APD_API_BASE` | OpenAI-compatible base URL, for example `http://host:4000/v1` |
| `APD_API_KEY` | API key |
| `APD_MODEL` | Chat model name |

Fallback aliases are also supported: `OPENAI_API_BASE`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `STORM_API_BASE`, `STORM_API_KEY`, `STORM_MODEL`.

## Roadmap

- Save sessions to local files
- Export LangGraph / AgentScope templates
- Add protocol quality score
- Add operation clustering from user examples
- Add validator test runner
- Add GitHub-ready example gallery

## CLI Usage

Claude Code 或其他命令行工具可以直接使用 `apd`。

### 单条消息

```bash
./apd run "我想设计一个客服退款智能体"
```

继续某个历史会话：

```bash
./apd run -s <session_id> "主要面向电商售后客服"
```

输出 JSON，方便程序读取：

```bash
./apd run --json "我想设计一个写作智能体"
```

### 交互模式

```bash
./apd chat
./apd chat -s <session_id>
```

交互模式命令：

```text
/protocol          查看当前协议
/history           查看当前对话
/export DIR        导出协议和开发文件
/exit              退出
```

### 历史会话

```bash
./apd list
./apd show <session_id>
./apd delete <session_id>
```

### 导出

```bash
./apd export <session_id> --out ./exports/my-agent
```

导出内容：

- `protocol.json`
- `workflow.json`
- `workflow_plan.md`
- `planner_prompt.md`
- `executor_skeleton.py`
- `development_plan.md`
- `eval_cases.json`
- `session.json`

### 配置模型

CLI 使用和 WebUI 相同的环境变量：

```bash
export APD_API_BASE="http://your-gateway/v1"
export APD_API_KEY="your-key"
export APD_MODEL="gpt-5.5"
export APD_LLM_TIMEOUT="180"
export APD_MAX_TOKENS="1800"
```

也可以每次命令传参：

```bash
./apd --api-base http://your-gateway/v1 --api-key your-key --model gpt-5.5 run "我想做一个写作智能体"
```

### Agent-friendly CLI commands

`apd ask` is the preferred command for Claude Code / external agents. It advances one turn and prints a short, structured text response instead of the full protocol.

```bash
./apd ask --new "我要设计一个写作智能体"
./apd ask -s <session_id> "主要面向企业员工"
echo "大段上下文" | ./apd ask -s <session_id> -
```

Output shape:

```text
[session] <session_id>
[stage] discover
[assistant] 简短回复
[next_question] 每轮最多一个问题
[quality_notes]
- ...
[open_questions]
- ...
[changes] +1 operation, ~2 object
[done] no
```

Useful companion commands:

```bash
./apd next -s <session_id>
./apd diff -s <session_id>
./apd diff -s <session_id> --json
./apd get -s <session_id> operations
./apd get -s <session_id> operations.draft_section.input_schema
./apd show -s <session_id> --history --diff
./apd workflow <session_id>
./apd workflow <session_id> --json
./apd workflow <session_id> --out ./workflow_plan.md
./apd generate <session_id> --out ./generated --name my-agent
```
