# Runtime Inspector v2 开发方案

> 目标：把当前“工程运行检查器”升级成“真实 Agent 体验调试台”。用户像最终用户一样和 Agent 对话；开发者能看到每轮问题定位；一键把精准修复任务交给 open_claude / Codex。

## 1. 背景与定位

当前 APD 已经具备：

- Agent 协议设计：从需求拆出能力、操作、校验、workflow。
- Agent 工程生成：生成 FastAPI + Demo 前端 + planner / executor / tools / workflow。
- Agent IDE：通过 ttyd 接入 open_claude / Codex 做工程修改。
- Runtime Inspector：可以发送测试话术，查看 Agent Run / Workflow / Trace / Raw Data。
- 一键修复：把诊断结果打包给 CLI 修复。
- 一键重启：CLI 改完代码后重启当前 Agent 调试服务。

但当前 Runtime Inspector 仍偏工程视角，主要问题：

1. 用户侧真实对话体验不够突出。
2. 多轮上下文、memory、artifact、workflow 状态还没有形成一致会话。
3. 每轮回复和诊断关系不够强，用户不知道“这句话为什么这样回”。
4. 诊断还偏静态规则，缺少对真实运行数据的综合判断。
5. 修复任务还不够精准，需要绑定完整上下文、问题层级和验收话术。

Runtime Inspector v2 的定位是：

```text
真实用户对话框
+ 场景参数和输入素材
+ 每轮运行观测
+ 精准问题归因
+ 一键 AI 修复
+ 重启后继续验证
```

参考 Dify 的 Debug / Preview / Logs / Tracing / Variable Inspector 思路，但 APD 的差异点是：

```text
Dify 更偏低代码应用调试；
APD 更偏 Agent 架构设计 + 生成工程 + AI 工程修复闭环。
```

## 2. 产品目标

### 2.1 用户目标

面向两类用户：

1. **Agent 架构小白但会开发的工程师**
   - 不想自己判断 planner / executor / tools / workflow 哪里错。
   - 希望通过真实对话发现问题，再让 AI 修代码。

2. **产品经理 / 业务负责人**
   - 不关心 trace JSON。
   - 只想像最终用户一样试 Agent 效果。
   - 看到效果不对时，能用自然语言反馈问题。

### 2.2 系统目标

Runtime Inspector v2 要做到：

- 真实运行当前工作区 Agent，而不是另写一套假逻辑。
- 支持连续多轮会话，上下文连贯。
- 支持业务参数、文件、模板、素材等测试输入。
- 每轮回复都有诊断卡片。
- 能判断问题大概率在哪一层：
  - Context / Memory
  - Intent Planner
  - OpCall
  - Validator
  - Tools
  - Workflow
  - Executor
  - Artifact
  - UI / 交互层
- 一键生成修复任务并发送给 open_claude / Codex。
- 修复后支持重启当前 Agent 并继续同一测试会话验证。

## 3. V2 总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│ Runtime Inspector v2                                         │
├──────────────────────────────────────────────────────────────┤
│ 左侧：测试场景面板                                           │
│ - 用户身份 / 业务场景 / 参数                                  │
│ - 文件 / 模板 / 素材                                          │
│ - 会话 memory / artifact 状态                                 │
│ - 重启 / 清空 / 保存测试用例                                  │
├──────────────────────────────────────────────────────────────┤
│ 中间：真实 Agent 对话框                                      │
│ - 用户像真实产品一样聊天                                      │
│ - Agent 回复直接展示                                          │
│ - 每轮绑定诊断摘要                                            │
├──────────────────────────────────────────────────────────────┤
│ 右侧：开发者诊断面板                                         │
│ - 本轮 Context Pack                                           │
│ - Intent Frame                                                │
│ - OpCall                                                      │
│ - Validator Results                                           │
│ - Tool Calls / Tool Results                                   │
│ - Workflow Nodes                                              │
│ - Memory / Artifact Diff                                      │
│ - Raw Data                                                    │
├──────────────────────────────────────────────────────────────┤
│ 底层：APD Debug Runtime                                      │
│ - debug_session                                               │
│ - conversation_history                                        │
│ - runtime_trace                                               │
│ - diagnosis_engine                                            │
│ - repair_task_builder                                         │
└──────────────────────────────────────────────────────────────┘
```

## 4. 核心概念

### 4.1 Runtime Inspector 不是另一个 Agent

Runtime Inspector 不应该自己写一套 Agent 逻辑。

它应该调用当前工作区真实 Agent 工程：

```text
当前工作区 backend/app/main.py
→ /agent/run
→ /workflow/run
→ /store/snapshot
→ /tools
→ /debug/guide
→ /debug/recommendations
```

如果用户改了 `planner.py`、`executor.py`、`tools.py`、`workflow.py`，Runtime Inspector 重启后应该马上跑新代码。

### 4.2 用户侧对话和工程诊断分离

中间对话框只展示用户可理解内容：

```text
用户：帮我根据周报模板生成一段内容
Agent：已根据模板生成以下内容……
```

诊断放在旁边或每轮卡片里：

```text
本轮判断：回复质量问题
建议修改：backend/app/executor.py
原因：Agent 命中了正确操作，但最终回复没有使用模板字段。
```

### 4.3 每轮调试必须有完整上下文

每一轮调试请求都应该带上：

```json
{
  "message": "用户本轮输入",
  "context": {
    "session_id": "debug-session-id",
    "conversation_history": [],
    "memory": [],
    "files": [],
    "template": {},
    "artifacts": [],
    "runtime_params": {},
    "debug_mode": true
  }
}
```

### 4.4 诊断不是只看 JSON

诊断引擎需要综合判断：

```text
用户输入
+ 历史上下文
+ Agent 回复
+ op_call
+ validator_results
+ tool_results
+ workflow_run
+ store_snapshot
+ artifacts
+ logs
```

输出应该是人能看懂的结论：

```text
问题大概率在 executor.py。
原因：planner 已经正确识别为 rewrite_section，validator 通过，tool 也返回了模板字段，但最终回复没有引用这些字段。
```

## 5. 功能模块拆分

### 5.1 调试会话管理 Debug Session

目标：保证连续多轮上下文。

需要支持：

- 创建调试会话。
- 保存每轮 user / assistant 消息。
- 保存每轮运行结果。
- 保存 memory / artifact 快照。
- 重启 Agent 后保留调试历史。
- 用户可手动清空调试会话。

后端建议结构：

```python
DebugSession = {
    "workspace_id": "ws-xxx",
    "session_id": "debug-xxx",
    "turns": [],
    "memory": [],
    "artifacts": [],
    "last_run": {},
    "created_at": "...",
    "updated_at": "..."
}
```

涉及文件：

- `protocol_designer/dev_studio.py`
- `webui_server.py`

### 5.2 真实 Agent 对话框

目标：中间区域变成最终用户体验。

能力：

- 输入文本。
- 展示 Assistant 回复。
- 保留多轮对话。
- 每轮可展开诊断。
- 支持重新发送某一轮。
- 支持把本轮保存为测试用例。

第一版不急着做复杂 UI，但必须把“对话框”作为主视觉。

涉及文件：

- `webui_server.py`

### 5.3 测试场景面板

目标：让用户在真实场景里测 Agent。

第一版字段：

- 用户身份：普通用户 / 管理员 / 审核人 / 自定义。
- 业务场景：自由文本。
- 会话变量：JSON。
- 文件/模板：先用模拟列表或 JSON，后续接真实上传。
- 开关：保留上下文 / 清空上下文 / 重启后继续。

后续扩展：

- 文件上传。
- 模板选择。
- 知识库选择。
- 工具 mock 数据。
- LLM 参数。

涉及文件：

- `webui_server.py`
- 后续可能新增 `protocol_designer/debug_session.py`

### 5.4 诊断引擎 Diagnosis Engine

目标：每轮自动判断问题在哪。

输入：

```text
message
conversation_history
agent_run
workflow_run
store_snapshot
tools
logs
```

输出：

```json
{
  "summary": "本轮问题大概率在 executor.py",
  "level": "executor",
  "confidence": 0.82,
  "reasons": [],
  "suggested_files": [],
  "next_test_messages": [],
  "repair_prompt": "..."
}
```

诊断层级：

| 层级 | 判断依据 | 建议文件 |
|---|---|---|
| Context / Memory | 历史上下文缺失、memory 没带上 | `store.py` / `harness.py` / `runtime.py` |
| Intent Planner | op_call 错、confidence 低、误解用户意图 | `planner.py` |
| OpCall | 参数缺失、操作绑定不稳定 | `planner.py` / `models.py` |
| Validator | validator failed 或该拦没拦 | `validators.py` |
| Tools | 工具没调用、工具结果不对 | `tools.py` |
| Workflow | 节点没跑、节点顺序错、状态不连贯 | `workflow.py` |
| Executor | 操作对了但回复/产物错 | `executor.py` |
| Artifact | 文档/文件/版本产出不对 | `executor.py` / `store.py` |
| UI | 后端结果对但页面展示错 | 前端文件 |

涉及文件：

- 第一版可先写在 `webui_server.py` JS 中。
- 第二版迁到 `protocol_designer/debug_diagnosis.py`。

### 5.5 一键 AI 修复 Repair Task Builder

目标：修复任务必须精准。

修复任务包包含：

```text
【用户本轮话术】
【历史上下文】
【Agent 当前回复】
【运行诊断】
【op_call】
【validator_results】
【tool_results】
【workflow 节点】
【memory/artifact 快照】
【建议修改文件】
【禁止事项】
【验收测试话术】
```

发送目标：

- 当前 ttyd/open_claude session。
- 后续支持 Codex runner。

涉及文件：

- `webui_server.py`
- `protocol_designer/interactive_cli.py`

### 5.6 生成工程调试支持

目标：生成工程天然支持 Runtime Inspector。

生成工程应包含：

- `/agent/run`
- `/workflow/run`
- `/store/snapshot`
- `/tools`
- `/debug/guide`
- `/debug/recommendations`
- trace_events
- turns
- memory
- artifacts
- runtime_summary

涉及文件：

- `protocol_designer/generator.py`

### 5.7 测试用例与回放

目标：把“这轮不好用”保存成可重复验证。

第一版：

- 保存本轮测试话术。
- 保存期望回复描述。
- 保存诊断结果。

后续：

- 批量 replay。
- 对比修复前后。
- 评分。

涉及文件：

- `protocol_designer/dev_studio.py`
- `webui_server.py`

## 6. UI 方案

### 6.1 页面结构

```text
Runtime Inspector v2

┌───────────────┬─────────────────────────────┬─────────────────────┐
│ 测试场景       │ 真实 Agent 对话              │ 诊断与修复            │
│               │                             │                     │
│ 用户身份       │ 用户：...                    │ 本轮结论              │
│ 业务场景       │ Agent：...                   │ 问题层级              │
│ 变量/文件      │ [诊断摘要] [让AI修]           │ 建议文件              │
│ 模板/素材      │                             │ Trace/Raw            │
│ 重启/清空      │ 输入框                       │ 修复任务              │
└───────────────┴─────────────────────────────┴─────────────────────┘
```

### 6.2 默认展示原则

默认给业务用户看：

- Agent 回复。
- ���轮是否符合预期。
- 问题在哪一层。
- 下一步点什么。

高级信息折叠：

- Raw JSON。
- Trace 全量。
- Workflow 节点详情。
- Tool Result 原始值。

### 6.3 每轮消息卡片

```text
用户：帮我按周报模板生成本周总结
Agent：已生成……

诊断摘要：
- 命中操作：render_document
- 工具：template_reader 已调用
- 问题：回复没有使用模板章节
- 建议：改 executor.py
[让 AI 修这个问题] [保存测试用例] [展开详情]
```

## 7. 后端 API 设计

### 7.1 发送一轮真实调试

```http
POST /api/dev-studio/workspace/{workspace_id}/debug/chat
```

请求：

```json
{
  "session_id": "debug-session-id",
  "message": "用户输入",
  "scenario": {},
  "runtime_params": {},
  "attachments": [],
  "keep_context": true
}
```

响应：

```json
{
  "session": {},
  "turn": {},
  "assistant_message": "...",
  "diagnosis": {},
  "agent_run": {},
  "workflow_run": {},
  "store_snapshot": {},
  "repair_task": "..."
}
```

### 7.2 重启当前 Agent

已有：

```http
POST /api/dev-studio/workspace/{workspace_id}/debug/restart
```

需要保证：

- 重启进程。
- 保留 debug session history。
- 下一轮继续带 history。

### 7.3 清空调试会话

新增：

```http
POST /api/dev-studio/workspace/{workspace_id}/debug/clear
```

用于：

- 清空 conversation_history。
- 清空 Inspector 侧临时 memory。
- 不删除用户工程。

### 7.4 保存测试用例

新增：

```http
POST /api/dev-studio/workspace/{workspace_id}/debug/testcase
```

用于后续 replay。

## 8. 开发阶段计划

### 阶段 1：调试页产品形态重构

目标：先把页面从“状态检查器”改成“真实对话调试台”。

任务：

1. Runtime Inspector 独立页改为三块：测试场景 / 真实对话 / 诊断修复。
2. 中间真实对话成为主区域。
3. 每轮回复下显示诊断摘要。
4. 右侧显示本轮详情。
5. 顶部保留：重启当前 Agent、清空会话、停止会话。

验收：

- 用户可以像真实产品一样连续聊天。
- 不看 Raw JSON 也能知道本轮 Agent 效果。
- 每轮都有“让 AI 修这个问题”。

### 阶段 2：后端 Debug Session 上下文打通

目标：解决上下文不连贯。

任务：

1. 后端维护 `debug_histories`。
2. 每轮请求自动带上 `conversation_history`。
3. 重启当前 Agent 后保留历史。
4. 增加清空会话能力。
5. 响应返回 `debug_context`。

验收：

- 第一轮说“我叫张三”。
- 第二轮问“我叫什么”。
- 后端请求里能看到上一轮历史。
- Agent 生成工程的 context_pack 能看到 recent_turns / conversation_history。

### 阶段 3：生成工程支持真实调试上下文

目标：让新生成工程天然支持多轮调试。

任务：

1. `STORE.read_context` 合并 memory、turns、artifacts。
2. `runtime.py` 调用 `STORE.read_context` 后再 build context_pack。
3. `context_pack` 显示 conversation_history / recent_turns。
4. `/debug/recommendations` 根据上下文完整度给建议。

验收：

- `/store/snapshot` 能看到 turns / memory / runtime_summary。
- `/agent/run` 的 context_pack 能看到 recent_turns。
- Runtime Inspector 能显示历史轮数。

### 阶段 4：诊断引擎升级

目标：问题定位更精准。

任务：

1. 抽出诊断规则。
2. 根据 op_call / validator / tools / workflow / memory / artifact 综合判断。
3. 输出问题层级、置信度、建议文件、验收话术。
4. 诊断结果绑定每轮消息。

验收：

- op_call 错时提示 planner。
- validator failed 时提示 validators。
- 工具没调用时提示 tools。
- 回复不符合预期时提示 executor。
- workflow 节点缺失时提示 workflow。

### 阶段 5：一键修复任务升级

目标：让 AI 修复更准。

任务：

1. 修复任务加入完整 conversation_history。
2. 加入本轮诊断和建议文件。
3. 加入验收话术。
4. 加入禁止事项。
5. 发送到 ttyd/open_claude。

验收：

- 点击“让 AI 修这个问题”后，open_claude 能收到完整任务包。
- 任务包不是泛泛日志，而是明确指出“为什么、改哪里、怎么验收”。

### 阶段 6：测试用例和回放

目标：让调试变成可重复验证。

任务：

1. 每轮可保存为测试用例。
2. 保存输入、期望、诊断、实际回复。
3. 支持一键 replay。
4. 修复前后对比。

验收：

- 能保存失败样本。
- 修复后能重新跑同一条样本。
- 页面能看出是否改善。

## 9. 推荐实施顺序

虽然目标是“全做”，但建议按以下顺序推进：

```text
1. 调试页产品形态重构
2. 后端 Debug Session 上下文打通
3. 生成工程上下文支持
4. 诊断引擎升级
5. 一键修复任务升级
6. 测试用例和回放
```

原因：

- 先改页面形态，用户马上能感知方向对了。
- 再补上下文，否则真实对话体验不成立。
- 再补诊断和修复，才能实现闭环。
- 最后做测试用例，保证长期质量。

## 10. 文件改造清单

### 必改

| 文件 | 作用 |
|---|---|
| `webui_server.py` | Runtime Inspector v2 页面、接口、修复任务发送 |
| `protocol_designer/dev_studio.py` | Debug session、上下文历史、重启保留、清空会话 |
| `protocol_designer/generator.py` | 生成工程支持 memory / turns / debug guide / recommendations |

### 建议新增

| 文件 | 作用 |
|---|---|
| `protocol_designer/debug_session.py` | 后端调试会话模型，后续从 dev_studio.py 拆出 |
| `protocol_designer/debug_diagnosis.py` | 诊断规则引擎，后续从 JS/页面逻辑拆出 |
| `docs/runtime_inspector_v2_development_plan.md` | 当前开发方案 |

## 11. 风险与约束

### 11.1 真实 Agent 能力依赖生成工程

Runtime Inspector 只能真实运行当前工作区已有能力。

如果生成工程只是 Demo executor，用户看到的也是 Demo 效果。

解决：

- Runtime Inspector 明确标记当前能力是 Demo 还是真实接入。
- 通过 open_claude/Codex 逐步把 Demo executor 替换成真实业务逻辑。

### 11.2 LLM Agent 与规则 Demo 的边界

当前生成工程默认 planner 是规则式 Demo，不是强 LLM Planner。

解决：

- 页面提示“当前 planner 是 Demo，可让 AI 接入真实 LLM”。
- 诊断发现 planner 能力不足时，建议修改 `planner.py`。

### 11.3 UI 复杂度

如果三栏信息太多，会再次变乱。

解决：

- 默认只展示真实对话和诊断摘要。
- 高级 Trace / Raw Data 折叠。
- 右侧只看当前选中的一轮。

## 12. 第一版完成定义

Runtime Inspector v2 第一版完成时，应满足：

1. 页面主体验是用户侧真实对话。
2. 支持连续多轮上下文。
3. 每轮回复都有诊断摘要。
4. 能展开查看 trace / workflow / raw data。
5. 能一键生成修复任务并发给 ttyd/open_claude。
6. CLI 改完后能点“重启当前 Agent”继续验证。
7. 新生成工程支持 `/debug/guide` 和 `/debug/recommendations`。
8. 用户不懂 Agent 架构，也能知道：这轮问题大概在哪里、下一步应该点什么。

## 13. 下一步立即开发任务

下一步从阶段 1 开始：

```text
重构 Runtime Inspector 独立页
→ 三栏布局
→ 中间真实对话为主
→ 每轮诊断卡片
→ 右侧当前轮详情
```

完成后再进入阶段 2：

```text
后端 Debug Session 上下文打通
→ conversation_history
→ memory / turns
→ 重启保留上下文
→ 清空会话
```
