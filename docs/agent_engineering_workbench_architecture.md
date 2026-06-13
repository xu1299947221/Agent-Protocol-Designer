# Agent Engineering Workbench 架构文档

> 本文定义 APD 当前阶段的产品形态：公司内部使用的 Agent 工程开发工具。它不同于未来的多租户 Agent 发布治理平台。当前重点是生成、调试、修复、导出可单独部署的 Agent 工程包。

## 1. 当前阶段定位

APD 当前阶段应定位为：

```text
Agent Engineering Workbench
```

也就是：

```text
公司内部 Agent 工程开发工具
```

当前目标：

```text
帮助内部开发者 / 架构设计者 / 工程人员
更快设计、生成、调试、修复、导出一个定制 Agent 工程包。
```

交付方式：

```text
每个开发完成的 Agent
  ↓
导出工程包
  ↓
单独部署
  ↓
给业务用户使用这个 Agent 的具体场景功能
```

当前阶段不要求：

```text
APD 平台直接承载所有业务用户访问
APD 平台统一发布所有 Agent
APD 平台做完整多租户运营治理
```

这些属于未来平台阶段。

## 2. 与未来 Platform 阶段的区别

| 维度 | 当前 Workbench 阶段 | 未来 Platform 阶段 |
|---|---|---|
| APD 角色 | 内部 Agent 工程开发工具 | Agent 创建、发布、治理平台 |
| 使用者 | 内部开发者 / 架构设计者 | 管理员、开发者、业务用户 |
| Agent 交付方式 | 导出工程包，单独部署 | 平台内发布和管理 |
| 业务用户访问 | 访问单独部署的 Agent | 访问 APD 平台发布的 Agent |
| 多 Agent 管理 | 轻量管理，重点在工程包 | 强管理，重点在租户、发布、治理 |
| 权限模型 | 内部工具级权限 | 多租户、角色、发布权限 |
| 当前重点 | 设计、生成、调试、导出 | 创建、发布、运营、治理 |

一句话：

```text
当前阶段：APD 帮公司内部开发者造 Agent 工程包。
未来阶段：APD 管理多个 Agent 的创建、发布、使用和治理。
```

## 3. 当前阶段主流程

```text
1. 首页左侧对话补充业务需求
2. 生成 / 更新 APD 协议草案 JSON
3. 选择落地路线
   ├── Delegated Agent：借 open_claude 执行能力快速落地
   └── Native Runtime：生成自研工程骨架并持续开发
4. 在真实调试台 / 工程开发台调试效果
5. 使用 AI 诊断修复当前 Agent 工程
6. 新需求先回协议层补充，再生成增量迁移任务
7. 保存工程版本
8. 导出 Agent 工程包
9. 单独部署给业务用户使用
```

核心链路：

```text
协议设计
  ↓
工程生成
  ↓
真实调试 / 工程开发
  ↓
AI 修复
  ↓
增量迁移
  ↓
保存版本
  ↓
导出工程包
  ↓
单独部署
```

## 4. 当前阶段最重要的不是平台治理

当前阶段最重要的不是：

```text
平台内 Agent 发布市场
复杂租户权限
统一运营门户
多 Agent 商业化管理
```

当前阶段最重要的是：

```text
让导出的 Agent 工程包真正可部署、可验收、可排查、可交付给业务用户。
```

因此，当前阶段的架构重点应放在导出工程包质量上。

## 5. 导出工程包交付标准

每个导出的 Agent 工程包至少应包含：

```text
README.md
.env.example
启动脚本
Dockerfile
docker-compose.yml
健康检查接口
配置说明
LLM 配置说明
工具接口配置说明
日志目录说明
产物目录说明
验收样例
```

目标是：

```text
拿到工程包的人可以按 README 启动、配置、提交测试任务、下载产物、排查失败。
```

## 6. 工程包配置分层

导出的 Agent 工程不能把配置写死。

需要区分：

```text
Agent 协议配置
LLM 配置
工具接口配置
文件存储配置
权限配置
运行参数
部署参数
```

建议文件：

```text
agent_definition.json
protocol.json
tool_registry.json
runtime_config.yaml
.env.example
.env
```

其中：

```text
.env.example 只放占位。
真实 key 由部署环境提供。
协议和工具配置应可版本化。
```

## 7. 本地一键验收

导出工程前和导出工程后，都应支持一键验收。

建议命令：

```text
make smoke
```

或：

```text
python scripts/smoke_test.py
```

验收内容：

```text
服务能启动
LLM 配置可用
工具健康检查通过
提交测试任务能返回 final_answer
能生成 artifacts/result.json
能生成 artifacts/manifest.json
能下载产物
```

验收结果应输出：

```text
通过项
失败项
失败原因
建议修复动作
```

## 8. 工具接口 Mock / Dry-run

很多业务工具在开发阶段不一定可用。

导出工程应支持：

```text
真实工具模式
mock 工具模式
dry-run 模式
```

用途：

```text
没有真实 API 时也能调试 Agent 链路。
不产生副作用地验证工具参数。
部署前验证工具配置是否完整。
```

工具配置应能声明：

```text
mock_enabled
dry_run_enabled
real_endpoint
healthcheck_endpoint
```

## 9. 环境变量和密钥安全

导出工程必须保证：

```text
不把 key 写进代码
不把 key 打进日志
不把 key 打包进 zip
.env.example 只放占位
真实 key 由部署环境提供
```

日志、Trace、错误信息中应避免输出：

```text
API Key
Token
Cookie
数据库密码
内部服务凭证
用户敏感文件原文
```

## 10. 日志、Trace 和产物清理

单独部署给业务用户后，日志和产物会持续增长。

工程包需要说明：

```text
日志保存在哪里
Trace 保存在哪里
产物保存在哪里
怎么清理旧 Job
怎么限制磁盘
怎么排查失败
```

建议支持：

```text
JOB_RETENTION_DAYS
ARTIFACT_RETENTION_DAYS
MAX_JOB_LOG_SIZE_MB
MAX_ARTIFACT_STORAGE_MB
```

## 11. 输出产物标准

每个 Agent 工程必须统一输出结构。

建议标准：

```text
artifacts/result.json
artifacts/manifest.json
artifacts/report.md
artifacts/*.docx
artifacts/*.xlsx
artifacts/*.pdf
```

其中 `result.json` 应包含：

```text
final_answer
artifacts
structured_result
diagnostics
trace
next_actions
```

业务用户看到的是：

```text
文本回复
文件下载
风险提示
下一步建议
```

开发者看到的是：

```text
Trace
日志
工具调用
诊断信息
```

## 12. 业务用户最小 UI

导出的 Agent 工程不能只提供 API。

至少要有一个简单 Web 页面：

```text
输入任务
查看 Agent 回复
查看执行状态
下载产物
查看错误提示
取消任务
```

业务用户不应该看到 APD 内部复杂调试台。

当前阶段 UI 目标：

```text
能让业务用户完成当前 Agent 的具体场景任务。
```

不是：

```text
让业务用户理解 Agent 架构。
```

## 13. 部署方式

当前阶段建议最低支持：

```text
本机 Python 启动
Docker 启动
docker-compose 启动
```

暂不强制支持：

```text
Kubernetes
多租户 SaaS
复杂发布流水线
```

但工程包结构应为后续支持这些部署方式留出空间。

## 14. Job 状态、取消和超时

业务用户提交任务后，任务可能是长任务。

工程包需要支持状态：

```text
queued
running
completed
failed
timeout
cancelled
```

需要能力：

```text
查询 Job 状态
刷新任务进度
取消任务
超时终止
失败重试提示
```

## 15. Agent 工程版本标识

导出的工程包要能追溯来源。

建议包含：

```json
{
  "agent_version": "v1",
  "generated_by": "APD",
  "apd_version": "...",
  "protocol_version": "v3",
  "generated_at": "...",
  "route": "delegated|native"
}
```

否则后续排查时无法判断：

```text
这个工程包来自哪个协议？
用了哪个模板？
是否包含某次修复？
是否需要升级？
```

## 16. 工具健康检查

导出工程启动后，应能查看：

```text
LLM 是否通
工具 API 是否通
文件目录是否可写
open_claude / Engine 是否可用
模型是否配置
```

建议接口：

```text
GET /health
GET /ready
GET /api/tools/health
GET /api/config/check
```

健康检查结果应区分：

```text
可启动
可运行 fake/mock
可运行真实工具
可调用 LLM
可生成产物
```

## 17. 业务用户可读错误

业务用户不应该看到 stack trace。

错误应分层：

```text
用户可读错误
开发者错误
原始日志
修复建议
```

前端展示：

```text
用户可读错误
下一步建议
是否可以重试
是否需要联系管理员
```

开发者排查：

```text
trace/events.jsonl
stdout.log
stderr.log
result.json diagnostics
```

## 18. 导出前检查

APD 在导出工程前应提醒：

```text
协议是否完整？
是否有 Artifact Model？
是否有 Tool Registry？
是否有 Eval Case？
是否配置 LLM？
是否配置工具？
是否缺少部署说明？
```

不完整也可以导出，但必须给风险提示。

建议输出：

```text
可导出
可调试
可部署
缺失项
风险项
建议补齐项
```

## 19. 工程包升级策略

当前阶段不一定要自动升级已部署 Agent，但至少要有说明。

需要回答：

```text
旧版本数据在哪里？
新版本配置怎么迁移？
产物目录是否兼容？
能否回滚？
哪些配置不能覆盖？
```

建议生成：

```text
UPGRADE.md
CHANGELOG.md
migration_notes.md
```

## 20. 当前阶段优先级

| 优先级 | 能力 |
|---|---|
| P0 | 导出工程包交付标准 |
| P0 | 输出产物标准 |
| P0 | 本地一键验收 |
| P0 | 配置和密钥安全 |
| P1 | 工具 Mock / dry-run |
| P1 | 工具健康检查 |
| P1 | 业务用户最小 UI |
| P1 | Job 状态 / 取消 / 超时 |
| P2 | 导出前检查 |
| P2 | 工程包升级说明 |

## 21. 与未来平台阶段的关系

Workbench 阶段不是未来 Platform 阶段的反方向。

它是更务实的第一阶段：

```text
先把单个 Agent 工程包做成可交付。
再把多个 Agent 工程包纳入平台统一发布和治理。
```

演进路径：

```text
Agent Engineering Workbench
  ↓
Agent 工程包标准化
  ↓
Agent Runtime / Engine 标准化
  ↓
Agent 发布与治理平台
```

一句话：

```text
当前阶段最重要的不是平台发布治理，而是让导出的 Agent 工程包真正可部署、可验收、可排查、可交付给业务用户。
```
