# APD 工程平台开发路线图与任务进度（Engineering Platform Development Roadmap）

> 本文是 APD（Agent Protocol Designer，现定位 Agent Engineering Platform）的**可执行、可追踪开发计划**。
>
> 来源依据：
> - `docs/agent_engineering_workbench_architecture.md`（当前阶段：Agent Engineering Workbench 工程开发台）
> - `docs/apd_future_architecture_outlook.md`（未来阶段：Agent Engineering Platform 工程平台）
>
> 本文的所有任务均基于对当前代码库（`webui_server.py`、`protocol_designer/*`、`apd_cli.py`、`tests/*`）的实际调研结果，**可直接作为后续开发 TODO 使用**。
>
> 文档维护约定：
> - 任务状态字段统一使用：`pending`（待办）/ `in_progress`（进行中）/ `done`（已完成）/ `blocked`（被阻塞）。默认全部为 `pending`。
> - 完成一个任务后，更新对应任务的「完成状态」字段，并同步更新「任务进度表」中的状态列。
> - 英文术语一律保留并附中文解释，例如 `导出前检查（Export Preflight Check）`。

---

## 0. 阅读前必读：两个阶段绝不能混在一起

APD 的演进分两个**互不混淆**的阶段。本文严格区分，任何任务都明确标注所属阶段。

```text
阶段一（当前）：Agent Engineering Workbench / Agent 工程开发台
  → 公司内部 Agent 工程开发工具
  → 目标：生成、调试、修复、导出「可单独部署的 Agent 工程包」
  → 使用者：内部开发者 / 架构设计者 / 工程人员
  → 交付方式：导出工程包 → 单独部署 → 给业务用户使用具体场景

阶段二（未来）：Agent Engineering Platform / Agent 工程平台
  → Agent 创建、发布、治理、运营平台
  → 目标：多 Agent 的创建、发布、使用、治理、隔离、长期维护
  → 使用者：管理员 + 开发者 + 业务用户
  → 交付方式：平台内统一发布和管理
```

一句话区分：

```text
当前阶段：APD 帮公司内部开发者「造」单个 Agent 工程包，造完导出去单独部署。
未来阶段：APD「管」多个 Agent 的创建、发布、使用和治理。
```

**当前阶段最重要的不是平台治理，而是让导出的 Agent 工程包真正可部署、可验收、可排查、可交付给业务用户。**

---

## 1. 阶段划分总览

### 1.1 当前阶段（Phase A：Agent Engineering Workbench）

| 项 | 说明 |
|---|---|
| 阶段代号 | `Phase A` |
| 阶段名称 | Agent Engineering Workbench（Agent 工程开发台） |
| 阶段定位 | 公司内部 Agent 工程开发工具 |
| 核心目标 | 生成、调试、修复、导出「可单独部署的 Agent 工程包」 |
| 主线链路 | 协议设计 → 工程生成 → 真实调试/工程开发 → AI 修复 → 增量迁移 → 保存版本 → 导出工程包 → 单独部署 |
| 落地路线 | ① Delegated Agent（委托型，借 open_claude 执行能力快速落地）<br>② Native Runtime（自研型，生成自研工程骨架并持续开发） |
| 成功标准 | 拿到工程包的人可以按 README 启动、配置、提交测试任务、下载产物、排查失败 |

### 1.2 未来阶段（Phase B：Agent Engineering Platform）

| 项 | 说明 |
|---|---|
| 阶段代号 | `Phase B` |
| 阶段名称 | Agent Engineering Platform（Agent 工程平台） |
| 阶段定位 | Agent 创建、发布、治理、运营平台 |
| 核心目标 | 多 Agent 的身份、租户、生命周期、环境隔离、发布回滚、评测、治理、长期维护 |
| 主要能力 | Agent Lifecycle / Environment Policy / Tool Governance / Output Contract / Eval System / Release-Rollback / Security Sandbox / Runtime Adapter / Protocol Diff-Migration / Template Library |
| 与当前关系 | Phase B 不是 Phase A 的反方向，而是 Phase A 标准化之后的自然延伸（工程包标准化 → Runtime/Engine 标准化 → 发布治理平台） |

### 1.3 演进路径

```text
Agent Engineering Workbench（Phase A，当前）
  ↓ 把单个 Agent 工程包做成可交付
Agent 工程包标准化
  ↓
Agent Runtime / Engine 标准化
  ↓
Agent 发布与治理平台（Phase B，未来）
```

### 1.4 术语约定

为避免全文术语混用，统一定义如下（后文出现的近义词均指同一概念）：

| 术语 | 含义 |
|---|---|
| **导出工程包**（简称「工程包」/「包」；旧称「生成包」「导出包」均同义） | APD 导出的、可单独部署的 Agent 工程，含代码骨架、配置模板、启动脚本、产物目录结构、一键验收脚本 |
| **APD 主服务** | APD 设计器自身的后端服务（`webui_server.py` 等），用于设计/调试协议，**区别于**它导出的工程包 |
| **两条落地路线** | **Delegated**（委托 open_claude 执行）/ **Native**（自研工程骨架）；同一能力可能一条已有、另一条待补 |
| **运行时就绪度（ReadinessLevel）** | 服务运行时能执行哪类任务的等级（startup_ok 等），**区别于**协议设计阶段的「设计就绪度（design_needed）」 |

---

## 2. 当前阶段「不做什么」（Out of Scope for Phase A）

为防止当前阶段范围蔓延（scope creep），以下能力**明确推迟到 Phase B，当前阶段不做**：

1. **不做完整多租户 SaaS**：当前不实现 Tenant（租户）隔离、租户级计费、租户级配额。当前权限只到「内部工具级权限」。
2. **不做 Agent Marketplace（Agent 模板市场）**：不做模板上架、评分、分发、商业化。模板沉淀作为 Phase B 预研。
3. **不做复杂 K8s 发布系统**：当前部署只支持「本机 Python 启动 / Docker / docker-compose」，不做 Kubernetes、不做复杂发布流水线（CI/CD pipeline）。但工程包结构要为后续留空间。
4. **不把 APD 平台直接作为所有业务用户入口**：业务用户访问的是「单独部署的 Agent 工程包」，不是 APD 平台本身。APD 平台只面向内部开发者。
5. **不为每个业务需求修改 open_claude 核心**：open_claude 作为执行引擎保持通用，业务差异通过 APD 协议 + Task Pack（任务包）表达，不下沉到引擎核心。<br>　注：里程碑 M6 会产出 open_claude「HTTP 服务化」的**接口设计草案**，这属于面向未来 Platform 的**基础设施预研（写文档）**，与「为某个业务需求改 Engine 核心」是两回事；Phase A 始终遵循「引擎保持通用、业务差异进协议」原则，不实际改动 Engine 核心代码。
6. **不做统一运营门户 / 多 Agent 商业化管理**：当前是「轻量管理，重点在工程包」，不做强运营治理。
7. **不做完整的数据与隐私合规体系**：当前只做「密钥不入代码/日志/包」的基础安全，不做完整的 PII（个人身份信息）分级、数据留存合规审计。完整体系属 Phase B。
8. **不做强制人工确认工作流引擎（Human-in-the-loop Engine）**：当前只做副作用工具的 dry-run / mock 保护，完整的人工确认审批流属 Phase B。

> 边界原则：当前阶段任何任务，如果发现自己在「为多租户/发布市场/K8s/运营门户做基础设施」，应停下来确认是否越界到 Phase B。

---

## 3. 当前阶段代码现状基线（Phase A Baseline）

> 以下是基于实际代码调研得到的现状（**基线扫描日期：2026-06-13**），作为所有 Phase A 任务的起点。任务的「需要修改的文件」均基于此基线。
>
> **重要：现状须按「两条落地路线 + APD 主服务」分别看待**，不能笼统说「生成包缺某文件」——很多能力 Delegated 路线已有、Native 路线尚缺。开工前请以实际代码为准复核（基线可能随开发推进过时）。

### 3.1 工程包导出与生成

- **共有**：`protocol_designer/generator.py`（Native Runtime 工程骨架生成）、`protocol_designer/delegated_generator.py`（Delegated Agent 工程生成）、`protocol_designer/core.py` 的 `build_exports()`（协议导出）。
- **Delegated 路线已有**：生成包**模板**会写出 `README.md`、`.env.example`、`Dockerfile`（模板逻辑在 `delegated_generator.py:140`）、`docker-compose.yml`（`:141`）、`manifest.json`（`:84`）、生成包内 `/health` `/ready` `/api/config` 接口、生成包内 `cancel_job`（模板字符串，`delegated_generator.py` 的 `605`/`1455` 附近）。注意：这些是「生成出来的工程包」具备的能力，不是 APD 主服务自身的能力。
- **Native 路线已有**：生成包**模板**会写出 `README.md`（`generator.py:138`）、`.env.example`（`generator.py:139`）、`manifest.json`；`generator.py` 内另有 `optional_smoke_case` 评测用例建议（在 `build_eval_case_suggestion` 中，约 `generator.py:259`，属「评测用例建议」而非一键 smoke 脚本，二者不要混淆）。
- **两条路线共同缺口**：`runtime_config.yaml`（配置分层）、标准化的 `tool_registry.json`（含治理字段）、`scripts/smoke_test.py`（结构化一键验收，区别于上面的评测用例建议）、`UPGRADE.md` / `CHANGELOG.md`；`manifest.json` 版本标识字段不完整（缺 `protocol_version` / `route` / `generated_at` 等的统一约定）；`result.json` 的 schema 字段不全；**导出前检查（Export Preflight）完全缺失**。
- **Native 路线特有缺口**：尚未生成 `Dockerfile` / `docker-compose.yml`（已 grep 确认 `generator.py` 中无相关生成逻辑；Delegated 模板已有，Native 需补齐，见 A-PKG-05 / A-NAT-01）。

### 3.2 WebUI 接口 / Job 治理（`webui_server.py`）

- **已有**：`/api/status`（仅 LLM 配置）、`/api/runtime/jobs`、`/api/runtime/job/{job_id}`、`/api/runtime/job/{job_id}/continue`、Delegated playground 的 job 快照与 `llm-diagnose`、`build_delegated_diagnosis()`、`build_delegated_repair_task()`。
- **生成包内已有**：Delegated 生成包**模板**内含 `cancel_job`（模板字符串，`delegated_generator.py` 的 `605`/`1455` 附近）与 `/health` `/ready`。这是生成出来的包的能力，不等于 APD 主服务有取消能力。
- **APD 主服务缺口**：APD 主服务（`webui_server.py`）**自身没有** `/health` `/ready` `/api/tools/health` `/api/config/check`（经 grep 确认这些路由不存在）；主服务 runtime Job **没有取消接口、没有超时 killer（后台超时终止）**；Job 状态机不一致——`workflow_runtime.py:10` 的 `TERMINAL_STATUSES = {"completed", "skipped", "failed", "blocked", "awaiting_human"}`，**既缺 `cancelled` 也缺 `timeout`**（而 `delegated_generator.py` 生成包侧已在用 `timeout` 状态），即「状态机定义」与「实际使用」不匹配；错误响应是扁平的 `{error: str}`，**没有「用户可读错误 / 开发者错误 / 原始日志 / 修复建议」分层**。
- **说明**：Delegated 生成包虽有 `cancel_job`，但 APD 主服务侧对 runtime Job 的「取消 + 超时终止 + 统一状态机」仍缺，A-JOB-* 主要补的是**主服务侧**能力。
- **两条路线生成包的接口不对称（已 grep 确认）**：Delegated 生成包模板（`delegated_generator.py`）含 `/health` `/ready` `/api/config` `/api/jobs/*` 等较完整接口；而 **Native 生成包（`generator.py:1179+`）只有 `/health` `/tools` `/agent/run` `/workflow/run` 等，缺 `/ready` `/api/config/check` `/api/tools/health` 与统一 Job 接口**。这一缺口由 **A-NAT-01** 在 Native 模板内补齐。时序影响：A-SMK-01（P0、第 2 批）早于 A-NAT-01（P2、第 4 批），故 **Native 包在 A-NAT-01 完成前跑 smoke 会对这些缺失接口大量标 `SKIP`，属预期行为**（靠 A-SMK-01 的软依赖降级规则），不视为 FAIL。

### 3.3 工具治理（`tool_runtime.py` / `core.py`）

- **已有**：`tool_runtime.py` 的 `dry_run_tool()`、`build_tool_test_cases()`、`run_tool_tests()`、`save_tool_run()`、`list_tool_runs()`；`/api/tools/test`、`/api/tools/runs`；registry 已有 `name/description/input_schema/output_schema/risk/side_effects/failure_policy/operation_tool_bindings/mcp_servers`。
- **缺口**：registry **没有 `mock_enabled` / `dry_run_enabled` / `real_endpoint` / `healthcheck_endpoint`**；没有真实/mock/dry-run 三模式动态切换；没有 `/api/tools/health`；生成包的 `dry_run_tool` 是硬编码 mock，无法切真实实现；没有工具增量扩展的便捷机制（须改代码）。

### 3.4 协议版本 / Diff / 迁移（`core.py` / `v2.py` / `apd_cli.py`）

- **已有**：`merge_protocol()`、`dedupe_rules()`、`normalize_rule_text()`、`strip_meta_prefix()`、turn 快照（`ensure_snapshot` / `protocol_after_turn` / `append_revert_turn`）、`apd diff`（turn 级 diff）、`_summarize_changes()`（仅 `*_added` 计数器）。
- **缺口**：**没有全局协议版本号 `protocol_version`**；diff 不是结构化变更清单（缺 added/changed/removed、tool_registry_diff、permission_policy_diff、state_model_diff）；**`migration_task_pack`（迁移任务包）完全缺失**；工作区不记录关联的协议版本；`MERGE_SEMANTICS_BUGS.md` 记录的 P0 bug（空 schema 覆盖、引号去重）未修复。

### 3.5 Runtime / Engine（`developer_runner.py` / `delegated_playground.py` / `delegated_generator.py`）

- **已有**：Delegated Agent 已绑定 open_claude CLI 为执行引擎（Task Pack 经 stdin 传入，抓 stdout 的 stream-json）；`delegated_playground.py` 管理 CLI 进程/Job/日志；Native Runtime 工程骨架由 `generator.py` 生成。
- **缺口**：open_claude 仍是 **CLI（命令行程序），不是 HTTP Engine（服务化引擎）**；workspace 隔离不足；任务取消可靠性低；stdout/stderr 与状态判断不统一；缺多 Runtime Adapter（适配层）。这部分大部分属 M6/Phase B 预研，当前阶段只做「为 Engine 化做准备」的轻量工作。

---

## 4. 当前阶段任务清单（Phase A Tasks）

> 任务 ID 规约：`A-<能力域>-<序号>`。能力域代号见下表。每个任务包含完整字段，可直接执行。

| 能力域代号 | 含义 |
|---|---|
| `PKG` | 导出工程包交付标准（Package Delivery Standard） |
| `OUT` | 输出产物标准（Output Contract） |
| `SMK` | 本地一键验收（Smoke / Acceptance） |
| `SEC` | 配置与密钥安全（Config & Secret Safety） |
| `TOOL` | 工具 Mock / dry-run / 健康检查（Tool Governance） |
| `UI` | 业务用户最小 UI（Business User UI） |
| `JOB` | Job 状态 / 取消 / 超时（Job Lifecycle） |
| `CHK` | 导出前检查（Export Preflight） |
| `UPG` | 工程包升级说明（Upgrade Notes） |
| `MIG` | 协议 diff / migration task pack（Protocol Diff & Migration） |
| `REG` | Tool Registry 增量扩展（Tool Registry Extension） |
| `DEL` | Delegated Agent 工程质量（Delegated Quality） |
| `NAT` | Native Runtime 工程骨架质量（Native Scaffold Quality） |

---

### 4.1 P0 — 导出工程包交付标准（PKG）

#### A-PKG-01 生成 README 模板（工程包说明文档）

- **任务 ID**：A-PKG-01
- **任务名称**：为导出工程包生成标准 README 模板
- **阶段**：Phase A（Workbench）
- **优先级**：P0
- **背景说明**：当前生成包已有简单 README（Delegated `delegated_generator.py:138`、Native `generator.py:138`），但内容不统一、缺少「启动→配置→提交任务→下载产物→排查失败」的完整闭环说明，也缺源文档第 5 节明确要求的「日志目录说明 / 产物目录说明」。拿到工程包的人需要靠 README 独立跑通。
- **目标**：生成的每个工程包都带一份结构化 README，覆盖：项目简介、Agent 用途、环境要求、配置步骤（.env）、启动方式（python/docker/compose）、健康检查、提交任务示例、产物下载、**日志目录说明、产物目录说明（源文档第 5 节必需项）**、失败排查、版本信息、升级指引链接。README 须**按路线区分清理职责**：Native 路线说明 `JOB_RETENTION_DAYS` 等留存参数（A-NAT-02）；Delegated 路线说明「Job/产物清理由 open_claude runner 管理」的边界。
- **需要修改的文件 / 模块**：
  - `protocol_designer/delegated_generator.py`（Delegated 路线的文件写入逻辑）
  - `protocol_designer/generator.py`（Native 路线的 scaffold 生成）
- **新增文件建议**：
  - `protocol_designer/templates/readme_template.py`（README 模板渲染器，按协议字段填充）
- **详细实现步骤**：
  1. 设计 README 模板章节结构（见目标），用占位变量（agent_name / goal / route / protocol_version / tools / artifacts 路径）。
  2. 在 `readme_template.py` 中实现 `render_readme(protocol, route, version_info) -> str`。
  3. Delegated 与 Native 两条生成路线都调用该渲染器，替换原有简单 README。
  4. **按路线渲染「日志/产物清理」章节**：Native 路线 README 引用 `JOB_RETENTION_DAYS` / `ARTIFACT_RETENTION_DAYS` 等参数（见 A-NAT-02），并指向 `CONFIG.md`（A-PKG-07）；Delegated 路线 README 说明「Job/产物清理由 open_claude runner 管理」的边界。
  5. README 中加入指向 `UPGRADE.md`、`CONFIG.md`、`scripts/smoke_test.py` 的引用。
- **验收标准**：导出任意 Agent 工程包，README 包含全部章节；按 README 步骤可在干净环境完成「启动→配置→提交任务→看到产物」。
- **测试方式**：`tests/test_scaffold_generator.py` 与 `tests/test_delegated_agent_generator.py` 增加断言：生成包含 README，且 README 含关键章节关键词（启动/配置/产物/排查）。
- **风险点**：模板与协议字段耦合，协议缺字段时渲染要降级处理（用占位符 + 风险提示），不能报错中断导出。**与 A-PKG-06 无循环依赖**：README 的「版本信息」节只写 `generated_at` 等已知值并**指向** `artifacts/manifest.json`（不内联 manifest 内容），manifest 的完整字段由 A-PKG-06 负责，两者无相互阻塞。
- **依赖任务**：无（可立即开始）。
- **完成状态**：`pending`

#### A-PKG-02 生成 .env.example（环境变量占位模板）

- **任务 ID**：A-PKG-02
- **任务名称**：生成标准 `.env.example`，只放占位不放真实密钥
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：`delegated_generator.py` 已有部分 `.env.example`，但字段不全，缺少 Job 留存、产物留存、工具模式、超时等运行参数。
- **目标**：生成的 `.env.example` 覆盖 LLM 配置、工具配置、运行参数、留存策略，且**只放占位值**，真实 key 由部署环境提供。
- **需要修改的文件 / 模块**：
  - `protocol_designer/delegated_generator.py` 的 `_env_example()`
  - `protocol_designer/generator.py`（Native 路线补 `.env.example`）
- **新增文件建议**：
  - `protocol_designer/templates/env_example_template.py`（统一占位字段清单）
- **详细实现步骤**：
  1. 汇总字段：`LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_TIMEOUT` / `LLM_MAX_TOKENS` / `MOCK_TOOLS_ENABLED` / `DRY_RUN_MODE` / `MAX_RETRIES` / `JOB_TIMEOUT_SECONDS` / `JOB_RETENTION_DAYS` / `ARTIFACT_RETENTION_DAYS` / `MAX_JOB_LOG_SIZE_MB` / `MAX_ARTIFACT_STORAGE_MB` / `MAX_CONCURRENT_JOBS`（占位，对应源文档 2.8 并发上限；当前阶段不强制执行，仅预留基础设施位）/ `LOG_LEVEL`。
  2. 每个字段配中文注释说明用途。
  3. 真实值字段（API_KEY 等）一律写 `your-xxx-here` 占位。
  4. 两条路线统一引用该模板。
- **验收标准**：`.env.example` 含上述全部字段且无任何真实密钥；与 A-SEC-01 的密钥扫描联动通过。
- **测试方式**：生成测试断言 `.env.example` 存在、含关键字段、不含疑似真实密钥（正则扫描 `sk-` 等前缀应只出现在占位形式）。
- **风险点**：字段过多吓到用户，需用注释分组（必填 / 可选）。
- **依赖任务**：无。
- **完成状态**：`pending`

#### A-PKG-03 生成 runtime_config.yaml（运行时分层配置）

- **任务 ID**：A-PKG-03
- **任务名称**：生成 `runtime_config.yaml`，实现配置分层
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：当前配置散落，没有独立的运行时配置文件。架构文档第 6 节要求区分「Agent 协议配置 / LLM 配置 / 工具接口配置 / 文件存储配置 / 权限配置 / 运行参数 / 部署参数」，并建议文件：`agent_definition.json` / `protocol.json` / `tool_registry.json` / `runtime_config.yaml` / `.env.example` / `.env`。
- **目标**：生成 `runtime_config.yaml`，把非密钥类运行配置集中、可版本化；密钥仍走 `.env`。同时明确源文档第 6 节几个配置文件的**职责分工**（避免用户混淆）：
  - `agent_definition.json`：Agent 身份与契约（名称、goal、默认任务、输入/输出约定、route），人读为主。
  - `protocol.json`：完整 APD 协议（objects/operations/validators/state_model/permission 等），可版本化（关联 A-MIG-01 的 protocol_version）。
  - `tool_registry.json`：工具注册与治理字段（A-PKG-04 / A-REG-01）。
  - `runtime_config.yaml`：运行时分层配置（本任务），非密钥。
  - `.env` / `.env.example`：仅密钥与环境相关变量（A-PKG-02）。
- **需要修改的文件 / 模块**：
  - `protocol_designer/delegated_generator.py`
  - `protocol_designer/generator.py`
  - `protocol_designer/core.py` 的 `build_exports()`（把 runtime_config 纳入导出）
- **新增文件建议**：
  - `protocol_designer/config_generator.py`（生成 runtime_config.yaml / 校验分层）
- **详细实现步骤**：
  1. 定义 yaml 结构：`llm:` / `tools:` / `storage:` / `runtime:`（超时、并发、重试）/ `deploy:`。
  2. 实现 `render_runtime_config(protocol, route) -> str`。
  3. 区分「可版本化配置（进 yaml）」与「密钥（进 .env）」，禁止把 key 写进 yaml。
  4. 两条路线 + `build_exports()` 都纳入。
- **验收标准**：导出包含 `runtime_config.yaml`，结构分层清晰，无密钥；与 README 配置说明一致。
- **测试方式**：生成测试断言文件存在、yaml 可解析、含 4 个顶层段、不含密钥字段值。
- **风险点**：yaml 与 `.env` 职责边界需在 README 写清，避免用户两处都改。
- **依赖任务**：A-PKG-02（与 .env 划清边界）。
- **完成状态**：`pending`

#### A-PKG-04 生成标准 tool_registry.json（工具注册导出）

- **任务 ID**：A-PKG-04
- **任务名称**：导出包内生成标准 `tool_registry.json`
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：协议导出已含 tool_registry，但生成包内缺独立、标准化、可被运行时读取的 `tool_registry.json`，且字段不含 mock/health 等治理字段（见 A-REG / A-TOOL）。
- **目标**：生成包内含 `tool_registry.json`，字段完整（含 A-REG-01 扩展字段），运行时可直接加载决定工具模式。
- **需要修改的文件 / 模块**：
  - `protocol_designer/core.py`（registry 结构）
  - `protocol_designer/delegated_generator.py` / `generator.py`
- **新增文件建议**：无（复用 `config_generator.py`）。
- **详细实现步骤**：
  1. 以 A-REG-01 扩展后的 registry 结构为准，序列化为 `tool_registry.json`。
  2. 放到生成包约定路径（如 `backend/tool_registry.json`），README 与 runtime_config 引用该路径。
  3. 生成包运行时启动时读取该文件初始化工具。
- **验收标准**：导出包含 `tool_registry.json`，可被生成包运行时加载，字段含 mock/health 治理字段。
- **测试方式**：生成测试断言文件存在、JSON 可解析、每个工具含治理字段。
- **风险点**：与 A-REG-01 强耦合，需先定字段 schema。**执行约束：A-REG-01 虽标 P1，但因 A-PKG-04（P0）依赖它，须在第 1 批与 P0 任务一同提前完成（见第 8 节执行顺序），否则 M1 闭环被阻塞。若 A-REG-01 未就绪，可先用现有 registry 字段生成基础 `tool_registry.json` 并标注「治理字段待补」，避免阻塞导出。**
- **依赖任务**：A-REG-01（硬依赖；A-REG-01 已提前到第 1 批）。
- **完成状态**：`pending`

#### A-PKG-05 生成 Dockerfile + docker-compose.yml + 启动脚本

- **任务 ID**：A-PKG-05
- **任务名称**：补齐容器化部署文件与启动脚本
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：架构文档第 5、13 节要求最低支持「本机 Python / Docker / docker-compose」三种启动方式。**现状（2026-06-13）：Delegated 路线已生成 Dockerfile（`delegated_generator.py:140`）与 docker-compose.yml（`:141`），本任务主要补齐 Native 路线，并统一两条路线的启动脚本与一致性。**
- **目标**：两条路线生成包都含 `Dockerfile`、`docker-compose.yml`、启动脚本（`scripts/start.sh` 或 `make` 目标），三种方式都能起服务；统一两路线的容器化模板风格。
- **需要修改的文件 / 模块**：
  - `protocol_designer/generator.py`（Native 路线补 Dockerfile / docker-compose.yml，当前缺）
  - `protocol_designer/delegated_generator.py`（复用/抽出统一模板，对齐 Native）
  - 可参考现有 `docker/` 目录的写法
- **新增文件建议**：
  - `protocol_designer/templates/docker_template.py`
- **详细实现步骤**：
  1. Dockerfile：基于 python slim，装依赖、暴露端口、入口跑服务。
  2. docker-compose.yml：服务 + 端口 + `.env` 挂载 + 数据卷（logs / artifacts）。
  3. 启动脚本：本机 python 启动封装 + 健康检查等待。
  4. README 的「启动方式」章节同步三种命令。
- **验收标准**：导出包含三类启动文件；`docker-compose up` 能起服务并通过 `/health`。
- **测试方式**：生成测试断言文件存在 + 语法层校验（compose 可解析）；条件允许时在 CI 跑一次 compose up 冒烟（可选）。
- **风险点**：依赖锁定（requirements 版本）要 pin，避免镜像构建漂移。
- **依赖任务**：A-PKG-01。
- **完成状态**：`pending`

#### A-PKG-06 生成 artifacts/manifest.json（产物清单 + 版本标识）

- **任务 ID**：A-PKG-06
- **任务名称**：生成产物清单与工程版本标识
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：架构文档第 15 节要求工程包可追溯来源（来自哪个协议 / 哪条路线 / 是否含某次修复）；未来阶段文档 2.7 节进一步要求记录**完整版本组合**（如 `Agent v3 = protocol v8 + toolset v4 + runtime v2 + engine v1.6`）。当前 `manifest.json` 版本标识不完整。当前阶段先落地 protocol/agent/apd 版本，toolset/runtime/engine 版本字段先建占位以便未来扩展。
- **目标**：每个工程包生成 `artifacts/manifest.json`，含 `agent_version / generated_by / apd_version / protocol_version / generated_at / route`，并**预留 `toolset_version / runtime_version / engine_version` 字段**（当前可填占位/已知值，为 Phase B 发布版本组合追踪留接口）。
- **需要修改的文件 / 模块**：
  - `protocol_designer/delegated_generator.py`（增强 manifest 生成）
  - `protocol_designer/generator.py`（Native 路线同步）
  - `protocol_designer/core.py`（提供版本字段来源）
- **新增文件建议**：无。
- **详细实现步骤**：
  1. 汇总字段来源：`protocol_version`（依赖 A-MIG-01）、`route`、`apd_version`（从项目读取）、`generated_at`（生成时传入，避免脚本内取时间）。
  2. 预留版本组合字段：`toolset_version`（可从 tool_registry 版本派生）、`runtime_version`、`engine_version`（open_claude 版本，从 manifest 读取）；当前阶段未建立完整版本号时填 `unversioned` 占位。
  3. 写入 `artifacts/manifest.json`。
  4. README 「版本信息」章节引用。
- **验收标准**：导出包含完整 manifest，核心字段齐全可追溯，版本组合字段已建占位。
- **测试方式**：生成测试断言 manifest 含全部核心字段且非空（除可选/占位项），版本组合字段存在。
- **风险点**：`protocol_version` 依赖 A-MIG-01，未完成前先用占位 `unversioned` 并标注；toolset/runtime/engine 版本的完整追踪属 Phase B（B-RELEASE-01），当前只建占位不做完整实现。
- **依赖任务**：A-MIG-01（协议版本号），软依赖。
- **完成状态**：`pending`

---

#### A-PKG-07 生成 CONFIG.md（分层配置详解文档）

- **任务 ID**：A-PKG-07
- **任务名称**：生成独立的 `CONFIG.md` 配置详解文档
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：架构文档第 5 节把「配置说明 / LLM 配置说明 / 工具接口配置说明 / 日志目录说明 / 产物目录说明」列为工程包必须包含项。当前这些说明散落在 README/.env.example/runtime_config 注释里，没有一份统一、完整的配置参考。
- **目标**：生成独立 `CONFIG.md`，集中讲清每个配置项（来源文件、含义、默认值、是否必填、是否密钥、改了影响什么），并索引 `.env` / `runtime_config.yaml` / `tool_registry.json` 三处配置的分工。
- **需要修改的文件 / 模块**：
  - `protocol_designer/delegated_generator.py` / `generator.py`
- **新增文件建议**：
  - `protocol_designer/templates/config_doc_template.py`
- **详细实现步骤**：
  1. 从 `.env.example`（A-PKG-02）、`runtime_config.yaml`（A-PKG-03）、`tool_registry.json`（A-PKG-04）三处配置项汇总成表。
  2. 每项标注：所在文件、含义、默认值、必填/可选、是否密钥、变更影响。
  3. 单列「日志目录说明」「产物目录说明」两节（源文档第 5 节必需项）。
  4. README 引用 CONFIG.md。
- **验收标准**：导出含 `CONFIG.md`，覆盖三处配置全部项与日志/产物目录说明。
- **测试方式**：生成测试断言 `CONFIG.md` 存在且含关键章节与配置项条目。
- **风险点**：配置项增减需与 CONFIG.md 同步，建议从配置定义自动生成而非手写。
- **依赖任务**：A-PKG-02、A-PKG-03、A-PKG-04。
- **完成状态**：`pending`

---

### 4.2 P0 — 输出产物标准（OUT）

#### A-OUT-01 定义并落地 result.json 标准 schema

- **任务 ID**：A-OUT-01
- **任务名称**：定义 `artifacts/result.json` 标准 schema 并在生成包落地
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：架构文档第 11 节要求统一输出结构，`result.json` 须含 `final_answer / artifacts / structured_result / diagnostics / trace / next_actions`。当前生成包的 result schema 定义不完整。
- **目标**：定义统一 `result.json` schema，生成包运行后稳定产出该结构；APD 真实调试台也按该结构解析。
- **需要修改的文件 / 模块**：
  - `protocol_designer/delegated_generator.py`（生成包写 result 的逻辑）
  - `protocol_designer/generator.py`
  - `webui_server.py`（调试台解析 result 的逻辑）
- **新增文件建议**：
  - `protocol_designer/result_schema.py`（schema 定义 + 校验 `validate_result(obj)`）
- **详细实现步骤**：
  1. 在 `result_schema.py` 定义字段与类型：`final_answer:str` / `artifacts:list` / `structured_result:object` / `diagnostics:object` / `trace:object` / `next_actions:list`，并定义 `schema_version` 常量。
  2. 提供 `build_result(...)` 构造器和 `validate_result(...)` 校验器。
  3. **生成包落地方式（明确选定方案 c）**：生成包**不**反向导入 APD 的 `protocol_designer.result_schema`（避免运行期依赖 APD）。改为：生成时把 `build_result/validate_result` 的等价逻辑**渲染进生成包自带的轻量模块**（如生成包内 `result_builder.py`），并在文件头注释标注 `schema_version`。即「APD 侧 schema 是权威定义，生成包内是按该版本渲染出来的自包含副本」。
  4. 生成包运行结束时用其自带构造器写 `artifacts/result.json`，并把 `schema_version` 写进 `manifest.json`。
  5. **同时渲染 `artifacts/report.md`**（源文档第 11 节列为产物）：实现 `render_report_md(result) -> str`，把 `final_answer` / 关键 `structured_result` / `next_actions` 渲染成人可读 Markdown 报告（诊断/trace 等开发者信息按 A-OUT-02 的视图分层决定是否纳入）。该渲染器同样渲染进生成包自带模块。
  6. 调试台 / smoke 脚本按 schema 读取；APD 调试台用权威 `result_schema.py` 校验。
- **验收标准**：生成包跑一次任务，产出符合 schema 的 `result.json`（含 `schema_version`）**与 `artifacts/report.md`**；缺字段时校验报清晰错误；生成包不依赖 APD 包即可独立产出与自校验。
- **测试方式**：新增 `tests/test_result_schema.py`：合法/非法样例校验；生成测试断言运行产物含 `result.json` + `report.md` 且 `schema_version` 与 manifest 一致。
- **风险点**：APD 权威 schema 与生成包内副本可能漂移，须由「生成时渲染」保证同源，并用 `schema_version` 做兼容判断；旧生成包不兼容时按 manifest 的 `schema_version` 识别。
- **依赖任务**：无。
- **完成状态**：`pending`

#### A-OUT-02 区分业务用户视图与开发者视图的产物呈现

- **任务 ID**：A-OUT-02
- **任务名称**：产物按「业务用户视图」与「开发者视图」分层呈现
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：第 11、17 节要求业务用户看到「文本回复 / 文件下载 / 风险提示 / 下一步建议」，开发者看到「trace / 日志 / 工具调用 / 诊断」。
- **目标**：result.json 与最小 UI 中，两类视图清晰分离，业务用户看不到 trace/stack。
- **需要修改的文件 / 模块**：
  - `protocol_designer/result_schema.py`（区分 user-facing 与 dev-facing 字段）
  - `protocol_designer/delegated_generator.py`（生成包前端模板）
- **新增文件建议**：无。
- **详细实现步骤**：
  1. schema 中明确：`final_answer` / `artifacts` / `next_actions` / 风险提示为业务视图；`trace` / `diagnostics` 为开发者视图。
  2. 最小 UI（A-UI-01）默认只渲染业务视图；开发者视图（trace/诊断）**默认隐藏，业务用户点击页面「诊断」/「高级」按钮后展开**——纯 UI 展开/收起，**无权限门禁**（Phase A 不做角色权限，权限属 Phase B 的 B-TENANT-01）。
- **验收标准**：业务用户视图默认无 trace/stack；点击「诊断」可展开看到开发者信息。
- **测试方式**：UI 层断言 + result 字段归类断言。
- **风险点**：与 A-UI-01、A-OUT-01 耦合，需统一字段命名。
- **依赖任务**：A-OUT-01。
- **完成状态**：`pending`

#### A-OUT-03 产物目录约定与产物下载

- **任务 ID**：A-OUT-03
- **任务名称**：统一 `artifacts/` 目录约定与下载能力
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：第 11 节列出 `artifacts/result.json / manifest.json / report.md / *.docx / *.xlsx / *.pdf`。需统一目录与下载入口。
- **目标**：生成包统一把产物写入 `artifacts/`，并提供下载接口与 UI 入口。
- **需要修改的文件 / 模块**：
  - `protocol_designer/delegated_generator.py`（生成包后端产物接口 + 前端下载）
  - `protocol_designer/generator.py`
- **新增文件建议**：无。
- **详细实现步骤**：
  1. 约定产物根目录 `artifacts/`，按 job 分子目录。
  2. 生成包后端提供 `GET /api/artifacts/{job_id}` 列产物 + 下载。
  3. 最小 UI 显示产物列表与下载按钮。
- **验收标准**：跑任务后 `artifacts/` 有 result+manifest，UI 可下载。
- **测试方式**：生成测试 + smoke 脚本校验产物存在可读。
- **风险点**：路径穿越安全（下载接口须校验路径在 artifacts 内）。
- **依赖任务**：A-OUT-01。
- **完成状态**：`pending`

---

### 4.3 P0 — 本地一键验收（SMK）

#### A-SMK-01 生成 scripts/smoke_test.py（工程包一键验收脚本）

- **任务 ID**：A-SMK-01
- **任务名称**：生成包内置 `scripts/smoke_test.py` 一键验收
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：第 7 节要求导出前后都能一键验收（`make smoke` 或 `python scripts/smoke_test.py`），验收服务启动、LLM、工具健康、提交任务、产物生成、下载。
- **目标**：每个工程包带 `scripts/smoke_test.py`，输出「通过项 / 失败项 / 失败原因 / 建议修复动作」。
- **需要修改的文件 / 模块**：
  - `protocol_designer/delegated_generator.py` / `generator.py`
- **新增文件建议**：
  - `protocol_designer/smoke_test_generator.py`（渲染 smoke_test.py 模板）
- **详细实现步骤**：
  1. 脚本检查项：服务可启动 → `/health` → `/ready` → `/api/config/check` → `/api/tools/health` → 提交一次测试任务 → 拿到 `final_answer` → 生成 `result.json` + `manifest.json` → 产物可下载。各检查项的就绪等级映射见 A-JOB-01 的映射表。
  2. 每项输出 PASS / FAIL / SKIP + 原因 + 修复建议。
  3. **退出码规则**：最小可验收集（服务可启动 + `/health` ok + 提交任务拿到 `final_answer` + 生成 `result.json`）任一 FAIL → 退出码 `1`；最小集全 PASS、其余项即使有 SKIP → 退出码 `0`；SKIP 不影响退出码（仅在报告中提示「因前置未就绪跳过」）。
  4. **生成时机 vs 运行时机**：smoke 模板**总是生成全部检查项代码**（不因导出时前置任务未完成而省略代码）；是否执行在**运行时**判断——检查项探测到对应接口不存在（404 / 未实现）时标 `SKIP`。即「代码恒在、运行时按接口可用性降级」，便于后续接口补齐后无需重新导出即生效。
  5. 支持 `--mock` 模式（无真实工具/LLM 时跑链路）。
- **验收标准**：生成包跑 `python scripts/smoke_test.py` 输出结构化报告（PASS/FAIL/SKIP）；退出码遵守上述规则（最小集失败为 1，否则为 0）。
- **测试方式**：生成测试断言脚本存在且含全部检查项代码；mock 模式下对样例工程跑通并验证退出码；构造「接口缺失」场景验证对应项 SKIP 且不影响退出码。
- **风险点**：依赖 A-TOOL/A-JOB/A-CHK 的接口。**软依赖降级规则：前置接口未就绪时对应检查项标 `SKIP`（运行时判定，非导出时省略），不算 FAIL。最小可验收集（即使其他都 SKIP 也必须能跑）= 服务可启动 + `/health` + 提交一次任务拿到 `final_answer` + 生成 `result.json`。**
- **依赖任务**：A-OUT-01（result 契约，硬）、A-JOB-01（readiness 枚举与映射表，硬）；A-TOOL-02（/api/tools/health）、A-CHK-01（/api/config/check）为软依赖，未就绪则对应检查运行时 SKIP。
- **完成状态**：`pending`

#### A-SMK-02 提供 Makefile（make smoke 等统一入口）

- **任务 ID**：A-SMK-02
- **任务名称**：生成包提供 `Makefile`，封装 smoke/start/test
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 7 节示例 `make smoke`。统一入口降低使用者心智负担。
- **目标**：生成 `Makefile`，含 `make start` / `make smoke` / `make test` / `make docker`。
- **需要修改的文件 / 模块**：`delegated_generator.py` / `generator.py`。
- **新增文件建议**：`protocol_designer/templates/makefile_template.py`。
- **详细实现步骤**：渲染 Makefile，目标分别封装对应命令；README 同步。
- **验收标准**：导出含 Makefile，`make smoke` 等价于运行 smoke 脚本。
- **测试方式**：生成测试断言 Makefile 存在含目标。
- **风险点**：Windows 无 make，README 注明同时提供 python 直跑方式。
- **依赖任务**：A-SMK-01。
- **完成状态**：`pending`

#### A-SMK-03 APD 内置「导出后自检」（在 APD 侧跑 smoke）

- **任务 ID**：A-SMK-03
- **任务名称**：APD 导出流程后可选触发工程包自检
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 7 节要求「导出前和导出后」都能验收。APD 侧应能对刚导出的包跑一次 smoke 给开发者反馈。
- **目标**：APD 导出后提供「运行自检」入口，调用工程包 smoke 脚本并回显结果。
- **需要修改的文件 / 模块**：`webui_server.py`（导出相关接口）、`protocol_designer/developer_runner.py`。
- **新增文件建议**：无。
- **详细实现步骤**：
  1. 导出后在临时目录解包，mock 模式跑 `smoke_test.py`。
  2. 回显结构化报告到 APD UI。
- **验收标准**：导出后点「自检」能看到 smoke 报告。
- **测试方式**：集成测试：导出样例 → 自检 → 报告含各检查项。
- **风险点**：自检耗时，需异步 + 超时（复用 A-JOB 能力）。
- **依赖任务**：A-SMK-01、A-JOB-02。
- **完成状态**：`pending`

---

### 4.4 P0 — 配置与密钥安全（SEC）

#### A-SEC-01 导出前密钥扫描（禁止 key 进代码/日志/zip）

- **任务 ID**：A-SEC-01
- **任务名称**：导出前对工程包做密钥泄露扫描
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：第 9 节要求「不把 key 写进代码 / 不打进日志 / 不打包进 zip / .env.example 只放占位」。当前无任何扫描。
- **目标**：导出前扫描工程包内容，发现疑似真实密钥（API Key / Token / Cookie / DB 密码 / 内部凭证）即拦截或强警告。
- **需要修改的文件 / 模块**：
  - `webui_server.py`（导出 zip 接口前置扫描）
  - `protocol_designer/core.py`（build_exports 前置钩子）
- **新增文件建议**：
  - `protocol_designer/secret_scanner.py`（密钥模式扫描 `scan_secrets(files) -> findings`）
- **详细实现步骤**：
  1. 定义密钥正则集（`sk-` / `Bearer ` / `AKIA` / `password=` / 高熵串等）。
  2. 扫描待打包文件树（排除 `.env.example` 的占位）。
  3. 命中真实密钥：阻断导出并报告位置；命中占位：放行。
  4. 同时检查 `.env` 不被打包进 zip。
- **验收标准**：含真实密钥的包被拦截并定位；纯占位包正常导出。
- **测试方式**：新增 `tests/test_secret_scanner.py`：正/负样例；导出集成测试构造含密钥文件验证拦截。
- **风险点**：误报（高熵串），需可由开发者确认放行（带审计记录），但默认从严。
- **依赖任务**：无。
- **完成状态**：`pending`

#### A-SEC-02 日志 / Trace 脱敏（避免输出敏感值）

- **任务 ID**：A-SEC-02
- **任务名称**：生成包日志与 Trace 自动脱敏
- **阶段**：Phase A
- **优先级**：P0
- **背景说明**：第 9 节要求日志/Trace/错误信息不输出 API Key/Token/Cookie/DB 密码/用户敏感文件原文。
- **目标**：生成包内置脱敏中间件，写日志/trace 前对敏感字段打码。
- **需要修改的文件 / 模块**：
  - `protocol_designer/delegated_generator.py`（生成包日志模块模板）
  - `protocol_designer/observability.py`（APD 侧 trace 记录）
- **新增文件建议**：
  - `protocol_designer/templates/redaction_template.py`（生成包用脱敏工具）
- **详细实现步骤**：
  1. 定义脱敏字段名集合（key/token/password/cookie/secret）与值模式。
  2. 生成包日志/trace 写入前过滤。
  3. APD 侧 `observability.py` 记录 trace 时同样脱敏。
- **验收标准**：构造含密钥的调用，日志/trace 中只见打码值。
- **测试方式**：新增 `tests/test_redaction.py`；observability 测试断言脱敏生效。
- **风险点**：脱敏过度影响调试，需保留「字段存在但已打码」的可读形式。
- **依赖任务**：无。
- **完成状态**：`pending`

#### A-SEC-03 密钥来源校验（启动时检查必需密钥存在）

- **任务 ID**：A-SEC-03
- **任务名称**：生成包启动时校验必需密钥已由环境提供
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 9 节「真实 key 由部署环境提供」。需在启动/就绪检查时验证必需密钥存在，缺失给清晰提示而非裸崩。**关键澄清：本任务只做「单个工程包启动时，校验该包运行所需的环境变量是否齐备」，不涉及多租户权限隔离、租户级密钥分级或「哪个密钥对哪个用户可见」——那些属 Phase B（B-TENANT-01 / B-ENV-01），不在当前阶段。**
- **目标**：生成包 `/ready` 与启动流程校验必需环境变量；缺失时报「缺哪个 key + 怎么配」。
- **需要修改的文件 / 模块**：`protocol_designer/delegated_generator.py`（/ready 模板）、`config_generator.py`。
- **新增文件建议**：无。
- **详细实现步骤**：从 runtime_config 标记的必需密钥清单，在 `/ready` 检查 `os.environ`，缺失列入未就绪原因。
- **验收标准**：缺密钥时 `/ready` 返回未就绪并指明缺失项；smoke 脚本据此报错。同一 Agent 的多个部署实例使用**同一套必需密钥清单**（不因部署环境/租户而差异化；多租户/环境级密钥隔离属 Phase B 的 B-TENANT-01 / B-ENV-01）。
- **测试方式**：生成包 `/ready` 在缺/全环境下的行为断言。
- **风险点**：必需 vs 可选密钥的划分需准确，避免误报。
- **依赖任务**：A-PKG-03（必需密钥清单来源，硬依赖）；A-SMK-01（仅为在 smoke 脚本中验收该校验，属顺序建议，非硬阻塞）。
- **完成状态**：`pending`

---

### 4.5 P1 — 工具 Mock / dry-run / 健康检查（TOOL）

#### A-TOOL-01 工具三模式切换（real / mock / dry-run）

- **任务 ID**：A-TOOL-01
- **任务名称**：实现工具真实 / mock / dry-run 三模式动态切换
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 8 节要求支持真实工具模式、mock 工具模式、dry-run 模式，用于无真实 API 时调试链路、无副作用验证参数、部署前验证工具配置。当前 `dry_run_tool` 是硬编码 mock，不能切真实实现。
- **目标**：生成包运行时按配置（`MOCK_TOOLS_ENABLED` / `DRY_RUN_MODE` / 工具级 `mock_enabled`）选择执行模式。
- **需要修改的文件 / 模块**：
  - `protocol_designer/tool_runtime.py`（增 `get_tool_mode()` / 增强 `dry_run_tool()`）
  - `protocol_designer/delegated_generator.py`（生成包 tools 模板支持切换）
- **新增文件建议**：
  - `protocol_designer/tool_mock_manager.py`（mock 返回值加载 / 真实-mock 切换）
- **详细实现步骤**：
  1. 工具配置增 `mock_response_template`（依赖 A-REG-01 字段）。
  2. 运行时解析全局 + 工具级模式，决定调真实端点 / 返回 mock / 仅校验参数（dry-run）。
  3. 生成包 tools 模板支持三模式，README 说明如何切换。
- **验收标准**：同一工具可在三模式间切换；mock/dry-run 不触发真实副作用。
- **测试方式**：扩展 `tests/test_tool_runtime.py` + 新增 `tests/test_tool_mock_manager.py`，覆盖三模式。
- **风险点**：副作用工具误走真实模式风险高，默认对 `side_effects=true` 的工具偏向 dry-run/mock，须显式开真实。
- **依赖任务**：A-REG-01。
- **完成状态**：`pending`

#### A-TOOL-02 工具健康检查接口（/api/tools/health）

- **任务 ID**：A-TOOL-02
- **任务名称**：实现 `/api/tools/health` 工具健康检查
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 16 节要求导出工程能查看 LLM 是否通、工具 API 是否通、目录是否可写、Engine 是否可用、模型是否配置。当前 APD 主服务与生成包都缺 `/api/tools/health`。
- **目标**：APD 主服务与生成包都提供 `/api/tools/health`，逐工具检查 `healthcheck_endpoint` 可达性并汇总。两处共用同一响应 schema（工具状态结构与 `ReadinessLevel.real_tool_runnable` 来源于 `health_check.py` 统一定义），避免主服务与生成包实现漂移。
- **需要修改的文件 / 模块**：
  - `webui_server.py`（APD 主服务新增接口）
  - `protocol_designer/delegated_generator.py`（生成包接口模板）
- **新增文件建议**：
  - `protocol_designer/tool_health_check.py`（`check_tool_health(tool, config)`）
  - `tests/test_tool_health_check.py`
- **详细实现步骤**：
  1. 实现 `check_tool_health`：对有 `healthcheck_endpoint` 的工具做探活；无端点的标 `unknown`。
  2. `/api/tools/health` 返回每工具状态 + 汇总（all_ok / degraded / down）。
  3. smoke 脚本（A-SMK-01）调用该接口。
- **验收标准**：接口返回逐工具健康状态；故障工具被标记。
- **测试方式**：`test_tool_health_check.py` 用 mock 端点覆盖通/不通/无端点。
- **风险点**：健康检查本身超时拖慢启动，需设短超时 + 并发探活。
- **依赖任务**：A-REG-01（`healthcheck_endpoint` 字段）、A-JOB-01（复用其 `ReadinessLevel` 枚举中的 `real_tool_runnable` 等级，须用同一定义）。
- **完成状态**：`pending`

#### A-TOOL-03 配置完整性检查接口（/api/config/check）

- **任务 ID**：A-TOOL-03
- **任务名称**：实现 `/api/config/check` 部署配置完整性检查
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 16 节要求导出前验证 LLM 配置、工具配置、文件目录可写、模型已配置。
- **目标**：APD 主服务与生成包提供 `/api/config/check`，校验 LLM 凭证、工具配置、存储目录可写、必需密钥存在。
- **需要修改的文件 / 模块**：`webui_server.py`、`protocol_designer/delegated_generator.py`。
- **新增文件建议**：复用 A-JOB-01 创建的 `protocol_designer/health_check.py`（集中 `ReadinessLevel` 枚举 + 健康/就绪/配置检查），本任务在其中补 `check_config(...)`，不另起新文件。
- **详细实现步骤**：检查项逐项执行并返回 `{ok, checks:[{name,status,detail,suggestion}]}`；与 `/ready` 复用底层，对应 `ReadinessLevel` 的 `mock_runnable` / `llm_available` 等级（见 A-JOB-01 映射表）。
- **验收标准**：配置缺失项被准确报告并给修复建议。
- **测试方式**：在缺/全配置下断言接口结果。
- **风险点**：与 A-JOB-01 的 `/ready` 边界：`/ready` 给探针 bool，`/api/config/check` 给明细。
- **依赖任务**：A-SEC-03（必需密钥清单）、A-JOB-01（`health_check.py` 与 `ReadinessLevel` 枚举的创建者，须复用同一定义）。
- **完成状态**：`pending`

---

### 4.6 P1 — Tool Registry 增量扩展（REG）

#### A-REG-01 扩展 Tool Registry 数据模型（治理字段）

- **任务 ID**：A-REG-01
- **任务名称**：为 Tool Registry 增加治理字段
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：当前 registry 缺 `mock_enabled / dry_run_enabled / real_endpoint / healthcheck_endpoint`，以及版本、超时、限流等。是 A-TOOL-01/02 与 A-PKG-04 的前置。
- **目标**：扩展 registry schema，新增治理字段，向后兼容旧协议。
- **需要修改的文件 / 模块**：
  - `protocol_designer/core.py`（`EMPTY_TOOL_REGISTRY` / `_tool()` / `default_tool_registry`）
- **新增文件建议**：
  - `protocol_designer/tool_registry_schema.py`（完整字段定义 + 校验 + 默认值填充）
- **详细实现步骤**：
  1. 在 `tool_registry_schema.py` 中给出**正式数据模型**（每字段含类型与默认值），此表即**「治理字段默认值映射表」**，是协议规范化与 diff 的唯一权威来源（A-MIG-01 判定实质变更、A-MIG-02 规范化都引用此表，不另维护副本）：`mock_enabled: bool=False` / `dry_run_enabled: bool=True` / `real_endpoint: str|None=None` / `healthcheck_endpoint: str|None=None` / `mock_response_template: dict|None=None` / `version: str="v1"` / `timeout: int=30` / `rate_limit: int|None=None`。该 schema 是 A-PKG-04 序列化 `tool_registry.json` 的唯一依据。
  2. `_tool()` 增参数 + 默认值；`default_tool_registry` 工具补默认治理字段。
  3. 旧协议加载时缺字段自动补默认（向后兼容）。**提供 `normalize_for_diff(registry)` 函数**：移除「值等于上表默认值且原协议未显式声明」的字段，供 A-MIG-02 在 diff 前调用，避免「自动补默认」被误判为「显式新增」产生虚假变更。
  4. 提供 `validate_tool_registry(registry)` 校验器供导出前检查（A-CHK-01）与 A-PKG-04 复用。
- **验收标准**：新旧协议都能加载；新字段可序列化进 `tool_registry.json`；`normalize_for_diff` 对「仅补了默认值」的两份 registry 归一化后相等。
- **测试方式**：扩展 `tests/test_tool_registry.py`：字段默认值、向后兼容、序列化往返、`normalize_for_diff` 消除虚假变更。
- **风险点**：schema 变更影响合并/diff，需与 A-MIG 协调把 tool_registry 纳入 diff；上表默认值是 A-MIG-01/02 的共享基准，修改默认值须同步检查这两个任务。
- **依赖任务**：无（其他 TOOL/PKG 任务的前置）。
- **完成状态**：`pending`

#### A-REG-02 工具增量扩展机制（协议内加自定义工具）

- **任务 ID**：A-REG-02
- **任务名称**：支持在协议层增量添加自定义工具而不改代码
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：当前加新工具须改 `default_tool_registry` 或代码。应支持通过协议/配置增量扩展。
- **目标**：开发者可在 APD 内向当前 Agent 的 registry 增/改工具，导出包据此生成工具桩与配置。
- **需要修改的文件 / 模块**：`webui_server.py`（工具编辑接口）、`protocol_designer/core.py`、`tool_runtime.py`。
- **新增文件建议**：无（复用 `tool_registry_schema.py`）。
- **详细实现步骤**：
  1. 提供「添加/编辑工具」接口（名称、schema、风险、副作用、端点、模式）。
  2. 写回协议 registry，触发本轮变更记录（与 A-MIG-02 联动）。
  3. 导出时生成对应工具桩与 registry.json 条目。
- **验收标准**：不改源码即可新增一个工具并导出可用桩。
- **测试方式**：集成测试：加工具 → 校验 registry → 导出含该工具桩。
- **风险点**：自定义工具 schema 非法需校验拦截。
- **依赖任务**：A-REG-01。
- **完成状态**：`pending`

---

### 4.7 P1 — 业务用户最小 UI（UI）

#### A-UI-01 生成包最小业务 Web 页面

- **任务 ID**：A-UI-01
- **任务名称**：生成包内置业务用户最小 UI
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 12 节要求导出 Agent 不能只给 API，至少有简单 Web 页面：输入任务、查看回复、查看状态、下载产物、查看错误、取消任务。业务用户不应看到 APD 内部调试台。当前生成包前端过于简化。
- **目标**：生成包带最小业务 UI，覆盖六项能力，且只暴露业务视图。
- **需要修改的文件 / 模块**：`protocol_designer/delegated_generator.py`（`_frontend_html()`）、`generator.py`。
- **新增文件建议**：`protocol_designer/templates/business_ui_template.py`。
- **详细实现步骤**：
  1. 单页：任务输入框 + 提交 → 轮询 Job 状态 → 展示 `final_answer` + 风险提示 + next_actions → 产物下载列表 → 错误友好提示 → 取消按钮。
  2. 默认只渲染业务视图（A-OUT-02）；开发者视图（trace/诊断）通过「诊断」按钮展开，同页不默认显示（与 A-OUT-02 的切换机制一致，纯 UI 收展、无权限门禁）。
- **验收标准**：业务用户能在页面完成「提交→看回复→下载→取消」闭环，无 trace/stack 暴露。
- **测试方式**：生成测试断言 UI 文件含关键元素；条件允许做一次浏览器冒烟。
- **风险点**：UI 与 Job/产物接口契约耦合，需先定接口。
- **依赖任务**：A-JOB-01、A-OUT-03、A-OUT-02。
- **完成状态**：`pending`

#### A-UI-02 业务用户可读错误展示

- **任务 ID**：A-UI-02
- **任务名称**：UI 层错误分层展示（用户可读 + 下一步建议）
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 17 节要求前端展示「用户可读错误 / 下一步建议 / 是否可重试 / 是否需联系管理员」，开发者排查走 `trace/events.jsonl / stdout.log / stderr.log / result.json diagnostics`。
- **目标**：最小 UI 把错误渲染为用户可读形式 + 行动建议；不暴露 stack。
- **需要修改的文件 / 模块**：`protocol_designer/delegated_generator.py`（前端 + 后端错误结构）。
- **新增文件建议**：无（依赖 A-JOB-03 的结构化错误）。
- **详细实现步骤**：UI 消费结构化错误对象（`user_readable_error / suggested_action / is_retryable / contact_admin`）并渲染。
- **验收标准**：触发失败时 UI 显示友好错误 + 建议，无技术堆栈。
- **测试方式**：构造失败 Job，断言 UI 错误区不含 stack 关键词。
- **风险点**：依赖后端错误结构化先就绪。
- **依赖任务**：A-JOB-03、A-UI-01。
- **完成状态**：`pending`

---

### 4.8 P1 — Job 状态 / 取消 / 超时（JOB）

#### A-JOB-01 统一 Job 状态机 + 健康/就绪接口

- **任务 ID**：A-JOB-01
- **任务名称**：统一 Job 状态机并补齐 APD 主服务 `/health` `/ready`
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 14、16 节要求 Job 支持 `queued/running/completed/failed/timeout/cancelled`；第 16 节还要求健康检查结果区分**多个就绪等级**（可启动 / 可运行 fake-mock / 可运行真实工具 / 可调用 LLM / 可生成产物）。现状：`workflow_runtime.py:10` 的 `TERMINAL_STATUSES = {"completed","skipped","failed","blocked","awaiting_human"}`，**缺 `cancelled` 与 `timeout`**，而生成包侧（`delegated_generator.py`）已在用 `timeout`，定义与使用不一致；APD 主服务无 `/health` `/ready`。**注意：本任务定义的 readiness_level 是「运行时就绪度」，与代码中已有的协议「设计就绪度」（如 `design_needed`）是两个不同概念，不要复用同一枚举。**
- **目标**：统一 Job 状态枚举（含 `cancelled`/`timeout`）；APD 主服务暴露 `/health` `/ready`；`/ready` 返回**结构化就绪等级枚举**。
- **需要修改的文件 / 模块**：
  - `protocol_designer/workflow_runtime.py`（`TERMINAL_STATUSES` 增 `cancelled` 与 `timeout`，并保留现有 `skipped`/`blocked`/`awaiting_human` 兼容，不破坏既有状态判断）
  - `webui_server.py`（新增 `/health` `/ready`）
- **新增文件建议**：`protocol_designer/health_check.py`（**本任务创建**，集中定义运行时 `ReadinessLevel` 枚举 + 健康/就绪检查函数；A-TOOL-02/03 复用此文件。`ReadinessLevel` 类须加 docstring 注明「运行时就绪等级，评估服务可执行哪类任务，区别于协议设计阶段的『设计就绪度 design_needed』」防止后续误用）。
- **详细实现步骤**：
  1. 定义统一 Job 状态枚举常量：`queued / running / completed / failed / timeout / cancelled`，两处引用同一来源；`TERMINAL_STATUSES` 须包含 `completed/failed/timeout/cancelled`（外加既有 `skipped/blocked/awaiting_human`）。校验状态迁移合法性（不能从 completed 回 running）。
  2. 在 `health_check.py` 定义**运行时就绪等级枚举 `ReadinessLevel`**，对齐源文档第 16 节：`startup_ok`（可启动）/ `mock_runnable`（可运行 fake/mock）/ `real_tool_runnable`（可运行真实工具）/ `llm_available`（可调用 LLM）/ `artifact_ok`（可生成产物）。供 `/ready`、`/api/config/check`（A-TOOL-03）、`/api/tools/health`（A-TOOL-02）、smoke 脚本（A-SMK-01）共用同一定义。
  3. `/health` 返回 `{status:ok}`；`/ready`（**无副作用探针**）返回**前 4 个等级**：`{ready:bool, levels:{startup_ok:bool, mock_runnable:bool, real_tool_runnable:bool, llm_available:bool}, blocking_reasons:[...]}`。`artifact_ok` **不在 `/ready` 中**（它需实际提交任务、有副作用），由 A-SMK-01 的 smoke 脚本通过真实提交一次任务来评估。
  4. 给出**就绪等级 ↔ 检查来源 ↔ smoke 检查项的映射表**（供 A-TOOL-02/03、A-SMK-01 直接引用）：

     | readiness_level | 判定来源 | 对应接口 | A-SMK-01 检查项 |
     |---|---|---|---|
     | `startup_ok` | 服务进程起来、`/health` 返回 ok | `/health` | 服务可启动（最小集） |
     | `mock_runnable` | mock 配置可加载、目录可写 | `/api/config/check` | mock 模式跑链路 |
     | `real_tool_runnable` | 工具 `healthcheck_endpoint` 可达 | `/api/tools/health` | 真实工具健康 |
     | `llm_available` | LLM 必需密钥存在且可连通 | `/api/config/check` | LLM 可调用 |
     | `artifact_ok` | `artifacts/` 可写、能生成 `result.json` | smoke 实际提交任务（**不在 `/ready`**，因有副作用） | 产物生成（最小集） |

     > 表说明：前 4 行（`startup_ok` / `mock_runnable` / `real_tool_runnable` / `llm_available`）是 `/ready` 无副作用探针返回的等级；**最后一行 `artifact_ok` 不由任何接口返回**，因其需实际提交任务（有副作用），仅由 A-SMK-01 的 smoke 脚本评估。
- **验收标准**：`TERMINAL_STATUSES` 含 `cancelled`/`timeout` 且不破坏既有状态；`/health` `/ready` 可用；`/ready` 返回**前 4 个**就绪等级布尔位与阻塞原因（`artifact_ok` 由 smoke 评估，不在 `/ready`）；映射表与 A-TOOL-02/03、A-SMK-01 一致。
- **测试方式**：扩展 `tests/test_runtime_job_storage.py`（断言新终态）；新增主服务健康接口测试，断言就绪等级枚举字段齐全。
- **风险点**：状态迁移合法性需校验；`ReadinessLevel`（运行时就绪）勿与协议「设计就绪度」枚举混用；就绪等级枚举须与 A-TOOL-02/03、A-SMK-01 共用同一定义。
- **依赖任务**：无。**反向关系（被依赖）**：本任务定义的 `ReadinessLevel` 枚举与上表映射，是 A-TOOL-02 / A-TOOL-03 / A-SMK-01 的**共用前置**，故须排在它们之前（见第 8 节第 1 批）。
- **完成状态**：`pending`

#### A-JOB-02 Job 取消与超时终止

- **任务 ID**：A-JOB-02
- **任务名称**：实现 Job 取消接口与超时自动终止
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 14 节要求查询状态、取消任务、超时终止、失败重试提示。当前无取消接口、无超时 killer。
- **目标**：提供取消接口 + 后台超时监控，长任务超 `JOB_TIMEOUT_SECONDS` 自动置 `timeout`，用户可主动 `cancel`。
- **需要修改的文件 / 模块**：
  - `webui_server.py`（`POST /api/runtime/job/{id}/cancel`、超时监控）
  - `protocol_designer/delegated_playground.py`（可靠终止子进程）
- **新增文件建议**：
  - `protocol_designer/job_lifecycle.py`（`cancel_job` / `timeout_job` / `monitor_jobs`）
  - `protocol_designer/timeout_config.py`（超时配置）
- **详细实现步骤**：
  1. 取消接口：标记 cancel 意图 → 终止底层进程/任务 → 状态置 `cancelled`。
  2. 后台监控：周期检查运行中 Job，超时置 `timeout` 并终止。
  3. Delegated 路线确保 CLI 子进程被可靠 kill（进程组）。
- **验收标准**：可取消运行中 Job；超时任务被自动终止并标记。
- **测试方式**：新增 `tests/test_job_lifecycle.py`：模拟长任务取消/超时。
- **风险点**：子进程僵尸/孤儿，需进程组管理；取消后产物/日志一致性。
- **依赖任务**：A-JOB-01。
- **完成状态**：`pending`

#### A-JOB-03 结构化错误分层（用户/开发者/原始/建议）

- **任务 ID**：A-JOB-03
- **任务名称**：API 错误响应结构化分层
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：第 17 节要求错误分层。当前 API 错误是扁平 `{error:str}`，无分层、无重试标志、无建议。
- **目标**：统一错误响应：`{user_readable_error, developer_context, raw_log_ref, suggested_action, confidence, related_files, is_retryable}`。
- **需要修改的文件 / 模块**：
  - `webui_server.py`（重构 JSONResponse 错误返回）
  - `protocol_designer/workflow_runtime.py`（增 `runtime_result_to_diagnosis()`，主 runtime 也产诊断）
- **新增文件建议**：
  - `protocol_designer/error_handler.py`（结构化错误格式器）
- **详细实现步骤**：
  1. 定义错误分类（LLM 不可用 / 工具失败 / 校验失败 / 超时 / 取消）。
  2. `error_handler` 把异常映射为结构化错误。
  3. 主 runtime 复用 delegated 的诊断能力生成 developer_context。
- **验收标准**：API 错误返回结构化对象；UI 可消费（A-UI-02）。
- **测试方式**：构造各类异常断言映射正确；快照测试错误结构。
- **风险点**：大范围改动 error 返回，需逐接口回归。
- **依赖任务**：A-JOB-01。
- **完成状态**：`pending`

---

### 4.9 P0 前置 — 协议合并缺陷修复（MIG / 前置）

> 本小节是 P0 级 bug 修复，虽属 MIG 能力域，但优先级高于下面 4.10 的 P1 diff/migration 任务，须最先做。
> 前置依据：`MERGE_SEMANTICS_BUGS.md` 记录的 P0 合并缺陷必须先修，否则后续协议 diff / 迁移都基于错误的合并结果。

#### A-MIG-00 修复协议合并 P0 缺陷（空 schema 覆盖 / 引号去重）

- **任务 ID**：A-MIG-00
- **任务名称**：修复 `merge_protocol` / `normalize_rule_text` 的 P0 缺陷
- **阶段**：Phase A
- **优先级**：P0（全文档执行顺序第 1 项）
- **背景说明**：`MERGE_SEMANTICS_BUGS.md` 记录：① LLM 返回空 `input_schema/output_schema` 时会覆盖旧非空值；② `normalize_rule_text` 引号处理不完整导致重复规则未去重。**已实测确认两处现状（2026-06-13）**：`core.py:444` 的 `is_empty_structural_value` 仅 `value in ({}, [])`（防御不足，其他假值如 `""`/`None` 由上层零散守卫）；`core.py:382` 的 `normalize_rule_text` 把弯引号**转成直引号但不剥离**，导致 `例如"改点"` 与 `例如改点` 规范化后仍不相等、去重失败。
- **目标**：合并时对协议本体字段做空值守卫；规则去重正确处理引号。
- **需要修改的文件 / 模块**：`protocol_designer/core.py`（`merge_protocol`、`normalize_rule_text:382`、`is_empty_structural_value:444`）。
- **新增文件建议**：无。
- **详细实现步骤**：
  1. `merge_protocol`：若新值为空且旧值非空，保留旧值（schema 字段守卫）。
  2. `normalize_rule_text`：把 `replacements` 中的引号项（`“”‘’` 及书名号 `《》「」`）从「转直引号」改为**剥离为空串**（`text.replace(q, "")`），使含引号与无引号的同义规则归一化后相等。
  3. `is_empty_structural_value`：扩展为检查所有假值（`{}`/`[]`/`""`/`None`/纯空白），不只 `{}`/`[]`。
  4. 记录 `deduplicated` 条数返回给变更摘要。
- **验收标准**：空 schema 不再覆盖；含引号差异的同义规则被正确去重（`normalize_rule_text('例如"改点"') == normalize_rule_text('例如改点')`）。
- **测试方式**：扩展 `tests/test_merge_semantics.py`：`test_empty_schema_protection` / `test_rule_dedupe_with_quotes`（含上面的弯引号反例）/ `test_deduplicated_counting`。
- **风险点**：去重过度合并不同义规则，需保守相似度阈值。
- **依赖任务**：无。
- **完成状态**：`pending`

### 4.10 P1 — 协议 diff / migration task pack（MIG）

> 以下任务依赖 A-MIG-00 先完成（合并缺陷修复），再做版本号、diff、迁移任务包。

#### A-MIG-01 引入全局协议版本号（protocol_version）

- **任务 ID**：A-MIG-01
- **任务名称**：为协议引入全局版本号并与工作区关联
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：当前只有 turn 快照与工作区版本，无全局 `protocol_version`，无法判断工程是否落后于协议。是 A-PKG-06、A-MIG-02 的前置。
- **目标**：协议有递增版本号；工作区记录 `created_from_protocol_version` / `last_synced_protocol_version` / `gap`。
- **需要修改的文件 / 模块**：
  - `protocol_designer/dev_studio.py`（工作区 metadata）
  - `protocol_designer/agent_registry.py`（版本字段）
  - `protocol_designer/v2.py`（快照关联版本）
- **新增文件建议**：
  - `protocol_designer/protocol_versioning.py`（`assign/get/list_protocol_versions`）
- **详细实现步骤**：
  1. 定义 `ProtocolVersion`，在协议**实质变更**时分配新号。**「实质变更」明确定义**：协议体（`operations` / `objects` / `rules` / `tool_registry` / `permission_policy` / `state_model` 中任一处）发生新增 / 删除 / 修改即为实质变更，分配新版本号；仅 turn 快照、工作区元数据、UI 状态等非协议体变更**不**分配新号（避免版本爆炸）。**关于 tool_registry 的边界澄清（引用 A-REG-01 的「治理字段默认值映射表」）**：判定前先用 A-REG-01 的 `normalize_for_diff()` 规范化——「加载时自动补的默认治理字段」（值等于映射表默认值且原协议未显式声明，如默认的 `mock_enabled=False`）**不算**实质变更；只有作者在协议体里**显式新增/删除/修改**了工具或其字段（含显式设定治理字段值、改 `input_schema`/`output_schema`/`side_effects`、增删工具）才算。即判定基于「规范化后的协议体显式内容」，与 A-MIG-02 的 diff 同源，不另维护字段清单。
  2. 工作区保存当前关联版本与差距。
  3. registry 版本记录关联 `protocol_version`。
- **验收标准**：协议体变更产生新版本号、非协议体变更不产生；工作区能显示「落后 N 版」。
- **测试方式**：新增 `tests/test_protocol_versioning.py`：递增、关联、隔离、**非协议体变更不升版**。
- **风险点**：「实质变更」判定须与 A-MIG-02 的 diff 范围一致（同样覆盖那 6 类协议体片段），避免「升了版但 diff 为空」或「diff 有变化却没升版」。
- **依赖任务**：A-MIG-00。
- **完成状态**：`pending`

#### A-MIG-02 结构化协议 diff

- **任务 ID**：A-MIG-02
- **任务名称**：生成结构化协议 diff（含工具/权限/状态模型）
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：当前 `apd diff` 仅 turn 级计数器，`_summarize_changes` 只有 `*_added`。缺完整变更清单与 tool_registry/permission/state_model 的 diff。
- **目标**：实现 `compute_protocol_diff(v_old, v_new)` 输出 added/changed/removed，覆盖 operations/objects/rules/tool_registry/permission_policy/state_model，并可渲染 Markdown。
- **需要修改的文件 / 模块**：
  - `apd_cli.py`（`protocol_diff_lines` 支持完整格式 + `--format`）
  - `webui_server.py`（`_summarize_changes` 增 deduplicated/changed/removed）
  - 复用 `protocol_designer/tool_registry_schema.py` 的 `normalize_for_diff()`（A-REG-01 提供，diff 前规范化 tool_registry，见步骤 2）
- **新增文件建议**：
  - `protocol_designer/protocol_diff.py`（`ProtocolDiff` / `compute_protocol_diff` / `diff_impact_analysis` / `format_diff_to_markdown`）
  - `tests/test_protocol_diff.py`
- **详细实现步骤**：
  1. 定义 `ProtocolDiff` 数据结构（各片段 added/changed/removed 列表）。
  2. 实现按版本号或快照对比两份协议。**对比前先规范化（normalize）两份协议**：对 tool_registry 调用 A-REG-01 提供的 `normalize_for_diff()`（基于其「治理字段默认值映射表」移除自动补的默认值），避免把「补默认」误判为「真的新增字段」产生虚假变更。其他协议片段同理移除加载时补的默认。
  3. 影响分析：推断受影响的工程文件/模块。
  4. CLI 与 WebUI 接入。
- **验收标准**：任意两版协议产出完整结构化 diff + Markdown 报告；对「仅默认值补全」的两份协议 diff 为空（无虚假变更）。
- **测试方式**：`test_protocol_diff.py` 覆盖各片段增删改检测，外加「默认值补全不产生 diff」用例。
- **风险点**：协议片段多，diff 算法需覆盖全字段，避免漏 diff；规范化策略须与 A-REG-01 的向后兼容补默认逻辑一致，否则产生虚假变更。
- **依赖任务**：A-MIG-01；**与 A-REG-01 协调**协议规范化策略（diff 前移除加载时补的默认值）。
- **完成状态**：`pending`

#### A-MIG-03 migration task pack（迁移任务包）生成

- **任务 ID**：A-MIG-03
- **任务名称**：基于 diff 生成迁移任务包与回滚计划
- **阶段**：Phase A
- **优先级**：P1
- **背景说明**：架构文档定义了 migration task pack（协议 diff + 影响分析 + must_update_files + rollback_plan），但代码完全缺失。这是「协议 v1→v2 后增量迁移已有工程」的闭环关键。
- **目标**：实现 `generate_migration_task_pack(diff, workspace)` 产出可交给工程 AI 执行的任务包 + 回滚计划。
- **需要修改的文件 / 模块**：`apd_cli.py` / `webui_server.py`（入口）。
- **新增文件建议**：
  - `protocol_designer/migration_task_pack.py`（`MigrationTaskPack` / `generate_*` / `summarize_affected_files` / `generate_rollback_plan` / `format_*_to_markdown`）
  - `tests/test_migration_task_pack.py`
  - `docs/migration_task_pack_spec.md`
- **详细实现步骤**：
  1. 消费 A-MIG-02 的 diff，推断 must_update_files。
  2. 生成结构化任务（每项含目标文件、改动说明、验收）。
  3. 生成 rollback_plan（回滚步骤清单）。
  4. 输出 Markdown + 可交付给 open_claude/Codex 的 prompt 模板。
- **验收标准**：协议变更后能产出含受影响文件与回滚步骤的任务包。
- **测试方式**：`test_migration_task_pack.py`：生成完整性、文件推断、回滚可读性。
- **风险点**：受影响文件推断不准会误导迁移，需结合工程结构启发式 + 人工确认。
- **依赖任务**：A-MIG-02。
- **完成状态**：`pending`

---

### 4.11 P2 — 导出前检查（CHK）

#### A-CHK-01 导出前完整性检查（Export Preflight）

- **任务 ID**：A-CHK-01
- **任务名称**：实现导出前协议/产物/工具/评测/配置完整性检查
- **阶段**：Phase A
- **优先级**：P2
- **背景说明**：第 18 节要求导出前提醒：协议是否完整、有无 Artifact Model、Tool Registry、Eval Case、是否配置 LLM/工具、是否缺部署说明。不完整也可导出但须给风险提示。
- **目标**：导出前生成检查报告：`{可导出, 可调试, 可部署, 缺失项, 风险项, 建议补齐项}`。
- **需要修改的文件 / 模块**：
  - `webui_server.py`（`GET /api/export-check/{session_id}`，导出接口前置调用）
  - `protocol_designer/core.py`（`build_architecture_check` 增风险/建议项）
- **新增文件建议**：无（可复用 architecture check）。
- **详细实现步骤**：
  1. 汇总检查项：协议完整度、Artifact Model、Tool Registry、Eval Case、LLM 配置、工具配置、部署说明。
  2. 返回三个就绪等级（可调试/可部署）+ 缺失/风险/建议。
  3. 导出接口调用并在 UI 展示，不阻断但强提示。
- **验收标准**：导出前能看到完整性报告与风险提示。
- **测试方式**：扩展 `tests/test_architecture_check.py`：缺各项时报告正确。
- **风险点**：检查项与协议结构耦合，新增协议字段需同步检查项。
- **依赖任务**：A-REG-01（Tool Registry 完整性判断）。
- **完成状态**：`pending`

---

### 4.12 P2 — 工程包升级说明（UPG）

#### A-UPG-01 生成 UPGRADE.md / CHANGELOG.md / migration_notes.md

- **任务 ID**：A-UPG-01
- **任务名称**：导出包生成升级与变更说明文档
- **阶段**：Phase A
- **优先级**：P2
- **背景说明**：第 19 节要求即使不自动升级，也要说明：旧数据在哪、配置怎么迁移、产物目录是否兼容、能否回滚、哪些配置不能覆盖。
- **目标**：生成 `UPGRADE.md` / `CHANGELOG.md` / `migration_notes.md`，回答上述问题，并接入 A-MIG-03 的迁移任务包摘要。
- **需要修改的文件 / 模块**：`protocol_designer/delegated_generator.py` / `generator.py`。
- **新增文件建议**：`protocol_designer/templates/upgrade_template.py`。
- **详细实现步骤**：
  1. UPGRADE.md：升级步骤、不可覆盖配置、回滚指引。
  2. CHANGELOG.md：基于 protocol_version 与 diff 摘要生成。
  3. migration_notes.md：引用 migration task pack 关键点。
- **验收标准**：导出含三份文档，内容针对当前 Agent 版本与协议版本。
- **测试方式**：生成测试断言文件存在且含关键章节。
- **风险点**：CHANGELOG 依赖 A-MIG-01/02，未就绪时降级为「首次发布」说明。
- **依赖任务**：A-MIG-01、A-MIG-03（软依赖）。
- **完成状态**：`pending`

---

### 4.13 P2 — Delegated Agent 工程质量（DEL）

#### A-DEL-01 Delegated 工程包可部署性增强

- **任务 ID**：A-DEL-01
- **任务名称**：提升 Delegated Agent 导出工程的可部署/可排查质量
- **阶段**：Phase A
- **优先级**：P2
- **背景说明**：Delegated 路线借 open_claude CLI 执行（Task Pack 经 stdin，stdout 抓 stream-json）。当前生成包健康检查/产物/错误分层偏弱，子进程管理可靠性不足。
- **目标**：Delegated 生成包达到与本路线相关的全部交付标准（健康检查、result 契约、产物下载、错误分层、可靠取消）。
- **需要修改的文件 / 模块**：`protocol_designer/delegated_generator.py`、`protocol_designer/delegated_playground.py`。
- **新增文件建议**：无（落地前述 PKG/OUT/JOB/UI 标准）。
- **详细实现步骤**：把 A-PKG/A-OUT/A-JOB/A-UI 的标准在 Delegated 生成模板内逐项落地；强化 stdout/stderr 与状态判断的统一解析；进程组可靠 kill。
- **验收标准**：Delegated 导出包通过 smoke_test 全部检查项。
- **测试方式**：扩展 `tests/test_delegated_agent_generator.py`：生成包结构 + mock smoke 通过。
- **风险点**：stream-json 解析对 open_claude 输出格式敏感，需容错。
- **依赖任务**：A-PKG-*、A-OUT-*、A-JOB-*、A-UI-*。
- **完成状态**：`pending`

#### A-DEL-02 Task Pack（任务包）构建标准化

- **任务 ID**：A-DEL-02
- **任务名称**：抽出统一的 Task Pack 构建器
- **阶段**：Phase A
- **优先级**：P2
- **背景说明**：APD 与 open_claude 之间的契约是 Task Pack（探明需求/业务 Goal/默认任务/输入文件/期望产物格式）。当前构建逻辑分散。
- **目标**：抽出 `task_pack_builder.py` 统一构建 Task Pack，含输入校验与产物格式声明，为 M6 Engine 化打基础。
- **需要修改的文件 / 模块**：`protocol_designer/delegated_generator.py` / `delegated_playground.py`。
- **新增文件建议**：`protocol_designer/task_pack_builder.py`。
- **详细实现步骤**：定义 Task Pack 结构与构建器，集中输入校验、产物格式声明、批次/隔离信息。
- **验收标准**：Delegated 路线统一走该构建器；Task Pack 结构稳定可测。
- **测试方式**：新增 `tests/test_task_pack_builder.py`。
- **风险点**：与 open_claude 现有输入约定兼容。
- **依赖任务**：无。
- **里程碑归属**：A-DEL-02 同时服务 **M5（Delegated 工程质量，作为 A-DEL-01 的支撑）** 与 **M6（Engine 化准备，作为唯一的 Phase A 实现部分）**。执行批次保持第 4 批（与 A-DEL-01 同批），M6 的其余产出是 Phase B 设计文档（见 M6「边界澄清」）。若希望 M5 更早闭环，可将 A-DEL-02 提前到第 3 批末，但不强制。
- **完成状态**：`pending`

---

### 4.14 P2 — Native Runtime 工程骨架质量（NAT）

#### A-NAT-01 Native 工程骨架达到交付标准

- **任务 ID**：A-NAT-01
- **任务名称**：提升 Native Runtime 自研骨架的交付质量
- **阶段**：Phase A
- **优先级**：P2
- **背景说明**：Native 路线由 `generator.py` 生成自研工程骨架，当前缺 Dockerfile/compose/smoke/runtime_config/健康检查等（与 Delegated 共性缺口），且**接口不对称**：`generator.py:1179+` 只生成 `/health` `/tools` `/agent/run` `/workflow/run`，缺 `/ready` `/api/config/check` `/api/tools/health` 与统一 Job 接口（见 3.2 节）。
- **目标**：Native 生成包同样满足 PKG/OUT/SMK/SEC/JOB/UI 标准，可单独部署、可验收、可排查。
- **需要修改的文件 / 模块**：`protocol_designer/generator.py`、生成的 `backend/app/*` 模板。
- **新增文件建议**：无（复用 templates）。
- **详细实现步骤**：把各 P0/P1 标准在 Native 模板内落地；补齐生成 `/health`（与 Delegated 对齐响应结构）`/ready` `/api/tools/health` `/api/config/check`、result 契约、产物下载、最小 UI。
- **验收标准**：Native 导出包通过 smoke_test 全部检查项（不再因接口缺失而 SKIP）。
- **测试方式**：扩展 `tests/test_scaffold_generator.py`：结构 + mock smoke 通过。
- **风险点**：Native 骨架技术栈选型需固定（避免每次生成漂移）。**时序提示**：本任务 P2、第 4 批，晚于 A-SMK-01（P0、第 2 批）；在本任务完成前，Native 包跑 smoke 会对上述缺失接口标 `SKIP`（预期行为，见 A-SMK-01 降级规则），本任务完成后这些 SKIP 应转为 PASS。
- **依赖任务**：A-PKG-*、A-OUT-*、A-JOB-*、A-UI-*、A-TOOL-*。
- **完成状态**：`pending`

#### A-NAT-02 Native 骨架运行参数与留存策略落地

- **任务 ID**：A-NAT-02
- **任务名称**：Native 骨架落地日志/产物留存与磁盘限制
- **阶段**：Phase A
- **优先级**：P2
- **背景说明**：第 10 节要求工程包说明日志/Trace/产物位置、清理旧 Job、限制磁盘，支持 `JOB_RETENTION_DAYS` / `ARTIFACT_RETENTION_DAYS` / `MAX_JOB_LOG_SIZE_MB` / `MAX_ARTIFACT_STORAGE_MB`。
- **目标**：Native 骨架实现留存配置与清理逻辑，README 说明。
- **需要修改的文件 / 模块**：`protocol_designer/generator.py`。
- **新增文件建议**：无。
- **详细实现步骤**：读取留存配置 → 周期清理超期 Job/产物 → 超磁盘上限告警/拒绝。
- **验收标准**：配置生效，超期数据被清理，磁盘有上限保护。
- **测试方式**：生成包单测留存清理逻辑。
- **风险点**：误删未下载产物，需保留期 + 下载状态判断。
- **依赖任务**：A-NAT-01、A-PKG-02。
- **完成状态**：`pending`

---

## 5. 未来阶段任务清单（Phase B Tasks，预研为主）

> Phase B 任务在当前阶段**只做预研与接口预留（pre-research & interface reservation）**，不做完整实现。任务 ID 规约：`B-<能力域>-<序号>`。
>
> 当前阶段对 Phase B 的唯一硬要求：**不要做出会阻挡 Phase B 的架构决策**（例如把工程包结构写死到无法纳入平台管理）。

| ID | 能力域 | 预研目标（当前只产出设计文档/接口草案，不实现） |
|---|---|---|
| B-LIFE-01 | Agent Lifecycle（生命周期） | 设计 草稿/开发中/测试中/已发布/已下线/已归档 状态机及各态允许操作 |
| B-TENANT-01 | 身份与租户（Identity & Tenant） | 设计 User/Role/Tenant 与 Permission Policy，明确与当前「工具级权限」的演进关系 |
| B-ENV-01 | 环境隔离（Environment Policy） | 设计 dev/test/staging/prod 的 LLM key/知识库/工具地址/数据目录隔离方案 |
| B-RELEASE-01 | 发布与回滚（Release/Rollback Model） | 设计版本组合记录（Agent = protocol + toolset + runtime + engine）与回滚 |
| B-EVAL-01 | 评测体系（Eval System） | 设计分层评测：协议/工具/结果/回归/安全；扩展现有 `eval_*` 模块 |
| B-SEC-01 | 安全沙箱（Security Sandbox Policy） | 设计目录读写白名单、命令执行白名单、联网/密钥访问边界（程序级，非 prompt） |
| B-ADAPT-01 | Runtime Adapter（多运行时适配层） | 设计 APD 协议 → open_claude / LangGraph / OpenAI Agents SDK / HTTP Agent 的 Adapter 接口 |
| B-DATA-01 | 数据与隐私治理（Data & Privacy） | 设计敏感字段分级、Trace 原文留存策略、数据留存与删除 |
| B-OBS-01 | 可观测性标准（Observability Standard） | 设计统一事件协议（LLM I/O、工具调用、Job 状态、Artifact 写入、权限拦截、人工确认、失败原因） |
| B-HITL-01 | 人工确认机制（Human-in-the-loop） | 设计高风险操作暂停/确认/继续/拒绝回滚/记录 |
| B-MKT-01 | 模板库（Template Library） | 设计投标/写作/客服等 Agent 模板结构（协议+工具+评测+产物+调试样本+最佳实践） |
| B-COST-01 | 成本与性能（Cost/Latency Budget） | 设计单任务时长/LLM 次数/费用/并发上限/大文件处理预算 |
| B-MEM-01 | 记忆/知识/产物边界（Memory/Knowledge/Artifact Boundary） | 设计 Memory（Agent 记住的偏好/状态/历史决策）、Knowledge（可检索知识库）、Artifact（生成交付物）三者的清晰边界，避免上下文/知识污染（源文档 2.10） |
| B-RECOV-01 | 失败恢复策略（Error Recovery Policy） | 设计 重试/降级/追问/跳过/回滚/转人工/生成缺口清单 的失败恢复策略，进入协议与 Runtime（源文档 2.11） |

> 上述每项预研任务的产物是一份 `docs/` 设计文档草案 + 接口草案，**不进入当前实现排期**，但可在 M7 集中产出。

---

## 6. 任务进度表（Task Progress Table）

> 状态默认全部 `pending`。完成任务后同步更新本表「状态」列。

### 6.1 Phase A（当前阶段）任务进度表

> 「依赖」列约定：标注为**硬依赖**=前置不完成则本任务无法启动；标注 **(软)** =前置不完成时本任务可降级运行（对应检查项 SKIP 或用占位）；**(枚举)** =复用前置定义的数据结构，需同源。

| ID | 阶段 | 优先级 | 任务 | 依赖 | 状态 | 验收摘要 |
|---|---|---|---|---|---|---|
| A-PKG-01 | A | P0 | 生成 README 模板 | — | pending | 工程包含完整 README，可独立跑通 |
| A-PKG-02 | A | P0 | 生成 .env.example | — | pending | 含全字段且只放占位 |
| A-PKG-03 | A | P0 | 生成 runtime_config.yaml | A-PKG-02 | pending | 配置分层、无密钥 |
| A-PKG-04 | A | P0 | 生成 tool_registry.json | A-REG-01（硬，可用现有字段降级） | pending | 运行时可加载，含治理字段 |
| A-PKG-05 | A | P0 | Dockerfile + compose + 启动脚本 | A-PKG-01 | pending | 三种启动方式可用 |
| A-PKG-06 | A | P0 | artifacts/manifest.json + 版本标识 | A-MIG-01(软) | pending | 版本可追溯，预留版本组合字段 |
| A-PKG-07 | A | P1 | 生成 CONFIG.md 配置详解 | A-PKG-02,A-PKG-03,A-PKG-04（→A-REG-01） | pending | 覆盖三处配置 + 日志/产物目录说明 |
| A-OUT-01 | A | P0 | result.json 标准 schema | — | pending | 产物符合 schema |
| A-OUT-02 | A | P0 | 业务/开发者视图分层 | A-OUT-01 | pending | 业务视图无 trace |
| A-OUT-03 | A | P0 | artifacts 目录约定 + 下载 | A-OUT-01 | pending | 产物可下载，路径安全 |
| A-SMK-01 | A | P0 | scripts/smoke_test.py | A-OUT-01,A-JOB-01(枚举),A-TOOL-02(软),A-CHK-01(软) | pending | 一键验收输出结构化报告 |
| A-SMK-02 | A | P1 | Makefile（make smoke） | A-SMK-01 | pending | make 入口可用 |
| A-SMK-03 | A | P1 | APD 内置导出后自检 | A-SMK-01,A-JOB-02 | pending | 导出后可看 smoke 报告 |
| A-SEC-01 | A | P0 | 导出前密钥扫描 | — | pending | 含密钥包被拦截定位 |
| A-SEC-02 | A | P0 | 日志/Trace 脱敏 | — | pending | 敏感值打码 |
| A-SEC-03 | A | P1 | 启动时必需密钥校验 | A-PKG-03,A-SMK-01 | pending | 缺密钥时清晰提示 |
| A-TOOL-01 | A | P1 | 工具三模式切换 | A-REG-01 | pending | real/mock/dry-run 可切 |
| A-TOOL-02 | A | P1 | /api/tools/health | A-REG-01,A-JOB-01(readiness 枚举) | pending | 逐工具健康状态 |
| A-TOOL-03 | A | P1 | /api/config/check | A-SEC-03,A-JOB-01(readiness 枚举) | pending | 配置缺失项报告 |
| A-REG-01 | A | P1 | Tool Registry 治理字段 | —（⚠ 提前到第 1 批，解锁 A-PKG-04/TOOL/CHK） | pending | 新旧协议兼容 |
| A-REG-02 | A | P1 | 工具增量扩展 | A-REG-01 | pending | 不改码加工具 |
| A-UI-01 | A | P1 | 业务最小 UI | A-JOB-01,A-OUT-03,A-OUT-02 | pending | 提交/回复/下载/取消闭环 |
| A-UI-02 | A | P1 | 业务可读错误展示 | A-JOB-03,A-UI-01 | pending | UI 无 stack |
| A-JOB-01 | A | P1 | 统一状态机 + /health /ready | — | pending | 状态一致、探针可用 |
| A-JOB-02 | A | P1 | Job 取消 + 超时终止 | A-JOB-01 | pending | 可取消、超时自动终止 |
| A-JOB-03 | A | P1 | 结构化错误分层 | A-JOB-01 | pending | API 错误结构化 |
| A-MIG-00 | A | P0 | 修复合并 P0 缺陷 | — | pending | 空 schema 不覆盖、引号去重 |
| A-MIG-01 | A | P1 | 全局 protocol_version | A-MIG-00 | pending | 工作区显示落后版本 |
| A-MIG-02 | A | P1 | 结构化协议 diff | A-MIG-01 | pending | 完整 diff + Markdown |
| A-MIG-03 | A | P1 | migration task pack | A-MIG-02 | pending | 含受影响文件 + 回滚 |
| A-CHK-01 | A | P2 | 导出前完整性检查 | A-REG-01 | pending | 三就绪等级 + 风险项 |
| A-UPG-01 | A | P2 | UPGRADE/CHANGELOG/migration_notes | A-MIG-01,A-MIG-03(软) | pending | 含升级与回滚说明 |
| A-DEL-01 | A | P2 | Delegated 工程可部署性 | A-PKG/OUT/JOB/UI-* | pending | Delegated 包通过 smoke |
| A-DEL-02 | A | P2 | Task Pack 构建标准化 | — | pending | 统一构建器可测 |
| A-NAT-01 | A | P2 | Native 骨架达交付标准 | A-PKG/OUT/JOB/UI/TOOL-* | pending | Native 包通过 smoke |
| A-NAT-02 | A | P2 | Native 留存与磁盘限制 | A-NAT-01,A-PKG-02 | pending | 留存清理 + 磁盘上限 |

### 6.2 Phase B（未来阶段）预研进度表

| ID | 阶段 | 优先级 | 任务 | 依赖 | 状态 | 验收摘要 |
|---|---|---|---|---|---|---|
| B-LIFE-01 | B | P3 | Agent 生命周期状态机设计 | — | pending | 产出设计文档 |
| B-TENANT-01 | B | P3 | 身份与租户设计 | — | pending | 产出设计文档 |
| B-ENV-01 | B | P3 | 环境隔离设计 | — | pending | 产出设计文档 |
| B-RELEASE-01 | B | P3 | 发布回滚模型设计 | B-LIFE-01 | pending | 产出设计文档 |
| B-EVAL-01 | B | P3 | 分层评测体系设计 | — | pending | 产出设计文档 |
| B-SEC-01 | B | P3 | 安全沙箱策略设计 | — | pending | 产出设计文档 |
| B-ADAPT-01 | B | P3 | Runtime Adapter 设计 | — | pending | 产出接口草案 |
| B-DATA-01 | B | P3 | 数据隐私治理设计 | — | pending | 产出设计文档 |
| B-OBS-01 | B | P3 | 统一事件协议设计 | — | pending | 产出事件协议草案 |
| B-HITL-01 | B | P3 | 人工确认机制设计 | — | pending | 产出设计文档 |
| B-MKT-01 | B | P3 | 模板库设计 | — | pending | 产出模板结构草案 |
| B-COST-01 | B | P3 | 成本/性能预算设计 | — | pending | 产出设计文档 |
| B-MEM-01 | B | P3 | 记忆/知识/产物边界设计 | — | pending | 产出三者边界设计文档 |
| B-RECOV-01 | B | P3 | 失败恢复策略设计 | — | pending | 产出失败恢复策略文档 |

---

## 7. 里程碑（Milestones）

> 每个里程碑对应一组任务的完成。完成判定 = 该里程碑所含任务全部 `done` 且对应测试通过。
>
> **注意：里程碑「包含任务」按能力域组织列出，其列出顺序不代表执行顺序。** 实际开发的执行批次与跨任务依赖以第 8 节为准（例如 A-SMK-01 虽在 M2 列表靠前，实际在第 2 批执行；A-REG-02 虽在 M4 列表首位，实际在第 3 批执行）。

### M1：Workbench 工程包交付标准成型

- **目标**：导出的工程包结构完整、可独立跑通、密钥安全。
- **包含任务**：A-PKG-01 / A-PKG-02 / A-PKG-03 / A-PKG-04 / A-PKG-05 / A-PKG-06 / A-PKG-07 / A-SEC-01 / A-SEC-02 / A-REG-01（A-PKG-04 的前置）。
- **完成判定**（可测步骤）：导出任意 Agent 工程包后，①结构含 README/.env.example/runtime_config.yaml/tool_registry.json/Dockerfile/compose/启动脚本/manifest.json（核心交付物）；②故意在包内放一个真实密钥样例，导出前密钥扫描（A-SEC-01）能拦截并定位；③跑一次任务后检查日志/trace，确认敏感值已打码（A-SEC-02）；④`docker-compose up` 能起服务并通过 `/health`。
- **判定说明**：①中的 `CONFIG.md`（A-PKG-07，P1）为增强交付物——若其依赖链（A-PKG-02/03/04→A-REG-01）尚未完全就绪，M1 核心判定（①核心交付物 + ②③④）仍可先行达成，`CONFIG.md` 可随后补齐，不阻塞 M1 主体闭环。
- **状态**：`pending`

### M2：真实调试与输出契约稳定

- **目标**：输出产物标准化，本地一键验收闭环，Job 可控。
- **包含任务**：A-OUT-01 / A-OUT-02 / A-OUT-03 / A-SMK-01 / A-JOB-01 / A-JOB-02 / A-JOB-03 / A-UI-01 / A-UI-02。
- **完成判定**：工程包 `python scripts/smoke_test.py` 全绿；业务 UI 完成提交→回复→下载→取消闭环；错误结构化分层。
- **状态**：`pending`

### M3：协议增量迁移闭环

- **目标**：协议可版本化、可 diff、可生成迁移任务包。
- **包含任务**：A-MIG-00 / A-MIG-01 / A-MIG-02 / A-MIG-03 / A-UPG-01。
- **完成判定**：协议 v1→v2 能产出结构化 diff + migration task pack + UPGRADE/CHANGELOG，合并 P0 缺陷已修。
- **状态**：`pending`

### M4：工具治理和健康检查

- **目标**：工具三模式可切换、健康可检、可增量扩展、导出前可检查。
- **包含任务**：A-REG-02 / A-TOOL-01 / A-TOOL-02 / A-TOOL-03 / A-CHK-01 / A-SEC-03。
- **前置说明**：A-REG-01 与 A-PKG-04 是本里程碑工具治理能力的前置基础，但因同时是 M1 工程包闭环的前置，已归入 M1 提前完成，故不计入本里程碑「包含任务」（避免重复实施）。
- **完成判定**：工具可在 real/mock/dry-run 切换，`/api/tools/health` 与 `/api/config/check` 可用，导出前检查报告就绪。
- **状态**：`pending`

### M5：Native Runtime 工程骨架增强

- **目标**：Native 与 Delegated 两条路线生成包都达到全部交付标准。
- **包含任务**：A-DEL-01 / A-DEL-02 / A-NAT-01 / A-NAT-02 / A-SMK-02 / A-SMK-03。
- **完成判定**：两条路线导出包均通过 smoke 全检查；留存与磁盘限制生效。
- **状态**：`pending`

### M6：open_claude Engine API 改造准备（仅设计/预研，不改 Engine 核心）

- **目标**：为 open_claude 从 CLI 到 HTTP Engine 的服务化做**接口设计与隔离方案**，**当前阶段只产出设计草案，不实际改造 Engine 核心**（遵守第 2 节第 5 条边界）。
- **边界澄清**：M6 分两类工作，须明确区分——
  - **Phase A 实现部分**：仅 A-DEL-02（Task Pack 构建标准化），这是在 APD 侧抽接口，**不碰 open_claude 核心代码**。
  - **Phase B 预研部分（只写文档）**：`/api/jobs`、`/api/jobs/{id}/events`、`/api/jobs/{id}/cancel` 的 Engine HTTP API 接口草案、workspace 隔离设计、可靠取消设计——这些产出物是设计文档，**不在当前阶段实现，也不修改 Engine 核心**。
- **包含任务（Phase A 实现）**：A-DEL-02（Task Pack 构建标准化，唯一的 Phase A 实现任务）。
- **附加产出（非任务、Phase B 预研输入）**：Engine HTTP API 接口草案、workspace 隔离设计文档——这些是设计文档而非 Phase A 任务，作为 Phase B 预研的输入参考，可与 M7 一并产出，不在当前阶段实现。
- **完成判定**：A-DEL-02 完成；产出 Engine HTTP API 接口草案 + workspace 隔离设计文档；Delegated 现状对接点清晰（明确不要求 Engine 已实现，也未改 Engine 核心）。
- **状态**：`pending`

### M7：未来 Platform 能力预研

- **目标**：集中产出 Phase B 各能力的设计文档与接口草案，确保当前架构不阻挡未来平台化。
- **包含任务**：B-LIFE-01 / B-TENANT-01 / B-ENV-01 / B-RELEASE-01 / B-EVAL-01 / B-SEC-01 / B-ADAPT-01 / B-DATA-01 / B-OBS-01 / B-HITL-01 / B-MKT-01 / B-COST-01 / B-MEM-01 / B-RECOV-01。
- **完成判定**：每项预研产出对应 `docs/` 设计草案；形成 Phase B 正式排期输入。
- **状态**：`pending`

---

## 8. 推荐执行顺序（Recommended Execution Order）

> 原则：先把「能交付一个可部署工程包」的最短闭环打通（P0），再补治理与迁移（P1），最后做质量收口与预研（P2/P3）。

> **关键路径说明（Critical Path）**：第 1 批分「前置链」与「后续可并行项」两段。**前置链（必须先按序完成这 4 项，它们是后续任务的地基）**：
> - **A-MIG-00**（修协议合并 bug）：所有协议 diff/迁移的正确性前提。
> - **A-OUT-01**（result.json 契约）：业务 UI（A-UI-*）、一键验收（A-SMK-01）、产物下载（A-OUT-03）都依赖它。
> - **A-REG-01**（Tool Registry 治理字段，名义 P1 但须提前）：A-PKG-04（P0）、A-TOOL-01/02、A-CHK-01 都依赖它；不先做会阻塞 M1 工程包闭环。
> - **A-JOB-01**（状态机 + `health_check.py` + `ReadinessLevel` 枚举）：A-TOOL-02/03、A-SMK-01 复用其枚举，必须在这三项之前完成。
>
> 这 4 项之外的第 1 批任务（A-PKG 系列、A-OUT-02/03、A-SEC-01/02、A-PKG-06/07）多数彼此独立，**可在前置链就绪后并行推进**，编号 5–8 表示「大致批序」而非严格串行。

### 第 1 批（P0 + 关键前置，最高优先，先做）—— 对应 M1 + M2 起步

**前置链（须先于第 5–8 项完成；这 4 项彼此无硬依赖，除 A-MIG-00 应最先外，其余可并行）：**

1. **A-MIG-00** 修复协议合并 P0 缺陷（**最先做**，避免后续基于错误合并；是唯一的严格串行起点）
2. **A-OUT-01** result.json + report.md 契约（产物契约是后续 UI/smoke 的地基）
3. **A-REG-01** Tool Registry 治理字段（A-PKG-04 / A-TOOL-* 的前置，虽 P1 但须提前）
4. **A-JOB-01** 统一状态机 + /health /ready + `ReadinessLevel` 枚举（被 A-TOOL-02/03、A-SMK-01 共用，须在其前）

**后续（前置链就绪后可大多并行）：**

5. **A-PKG-01 / A-PKG-02 / A-PKG-03 / A-PKG-04 / A-PKG-05** 工程包交付标准文件
6. **A-OUT-02 / A-OUT-03** 产物视图分层与下载
7. **A-SEC-01 / A-SEC-02** 密钥扫描与日志脱敏
8. **A-PKG-06** manifest 版本标识 + **A-PKG-07** CONFIG.md（依赖 PKG-02/03/04）

> 完成后达成 **M1（工程包交付标准成型）**。

### 第 2 批（P0 收尾 + P1 起步）—— 对应 M2 + M4

9. **A-JOB-02 / A-JOB-03** Job 取消超时 + 结构化错误
10. **A-TOOL-01 / A-TOOL-02 / A-TOOL-03** 工具三模式 + 健康检查 + 配置检查
11. **A-CHK-01** 导出前检查
12. **A-SMK-01** 一键验收脚本（依赖 TOOL/CHK/OUT 就绪）
13. **A-UI-01 / A-UI-02** 业务最小 UI + 可读错误
14. **A-SEC-03** 启动密钥校验

> 完成后达成 **M2（输出契约稳定）** 与 **M4（工具治理）**。

### 第 3 批（P1）—— 对应 M3

15. **A-MIG-01** 全局协议版本号
16. **A-MIG-02** 结构化协议 diff
17. **A-MIG-03** migration task pack
18. **A-REG-02** 工具增量扩展
19. **A-SMK-02 / A-SMK-03** Makefile + 导出后自检

> 完成后达成 **M3（协议增量迁移闭环）**。

### 第 4 批（P2）—— 对应 M5 + M6

20. **A-DEL-01 / A-DEL-02** Delegated 质量 + Task Pack 标准化
21. **A-NAT-01 / A-NAT-02** Native 骨架质量 + 留存
22. **A-UPG-01** 升级文档
23. **M6 预研**：open_claude Engine API 接口草案 + workspace 隔离设计

> 完成后达成 **M5（骨架增强）** 与 **M6（Engine 改造准备）**。

### 第 5 批（P3）—— 对应 M7

24. **Phase B 全部预研任务（B-*）**：集中产出设计文档，作为平台化正式排期输入。

> 完成后达成 **M7（未来平台预研）**。

---

## 9. 当前阶段优先级速查（来自架构文档第 20 节，已对齐本文任务）

| 优先级 | 能力 | 对应任务 |
|---|---|---|
| P0 | 导出工程包交付标准 | A-PKG-01~06 |
| P0 | 输出产物标准 | A-OUT-01~03 |
| P0 | 本地一键验收 | A-SMK-01 |
| P0 | 配置和密钥安全 | A-SEC-01、A-SEC-02 |
| P0 | 协议合并缺陷修复 | A-MIG-00 |
| P1 | 工具 Mock / dry-run | A-TOOL-01 |
| P1 | 工具健康检查 | A-TOOL-02、A-TOOL-03 |
| P1 | 业务用户最小 UI | A-UI-01、A-UI-02 |
| P1 | Job 状态 / 取消 / 超时 | A-JOB-01~03 |
| P1 | 协议 diff / migration | A-MIG-01~03 |
| P1 | Tool Registry 扩展 | A-REG-01、A-REG-02 |
| P2 | 导出前检查 | A-CHK-01 |
| P2 | 工程包升级说明 | A-UPG-01 |
| P2 | Delegated / Native 工程质量 | A-DEL-01~02、A-NAT-01~02 |
| P3 | 未来平台能力预研 | B-* |

---

## 10. 文档维护与下一步

- 本文是**活文档（living document）**：每完成一个任务，更新该任务「完成状态」与第 6 节进度表状态列。
- 新增能力先回到协议层与本路线图补任务，再开发（遵循 `AGENTS.md` 的协议优先原则）。
- **基线时效**：第 3 节基线扫描日期为 2026-06-13。开工某任务前若距该日期已超约 2 周，或期间有人改过 `generator.py` / `delegated_generator.py` / `webui_server.py` / `workflow_runtime.py` / `core.py`，应重新 grep 核对该任务「需要修改的文件」中的行号与函数仍然成立，再动手。每完成一个里程碑，重新跑一遍第 3.1–3.5 节的代码扫描、更新基线日期，确认对应能力域缺口已填闭（如 M1 完成后扫 A-PKG-* 文件是否已生成）。
- 修改本项目前请同时阅读：`docs/agent_engineering_workbench_architecture.md`、`docs/apd_future_architecture_outlook.md`、`docs/agent_architecture_guide.md`、`AGENTS.md`。
- 边界守则：任何任务若发现自己在为「多租户 / 发布市场 / K8s / 运营门户」做基础设施，立即停下确认是否越界到 Phase B。Phase A 实现**不得**为 Phase B 预埋会锁死设计的接口桩或命名；但允许用注释标注未来扩展点（如 tool_registry 的字段扩展位）。

> 一句话收尾：**当前阶段把单个 Agent 工程包做成可部署、可验收、可排查、可交付；未来阶段再把多个工程包纳入平台统一发布与治理。**
