# 写作 Agent 落地案例

这页记录 `/home/data/rag/ragyuyan/rag_agent` 当前已经落地的写作 Agent 架构，以及未来演进方向。它不是抽象理论，而是把真实项目现状和 APD 理想架构对齐。

---

## 一句话定位

当前写作 Agent 已经从自由 `agent_loop` 演进为：

```text
Hybrid Intent Router + OpCall Runtime + ClarificationGate + Validator/Executor + SSE Observation
```

中文理解：

```text
混合路由式受控写作 Agent
```

它现在已经不是“LLM 自己循环选工具直到结束”，而是：

```text
用户消息 → Planner 生成受控 OpCall → 追问/确认/校验 → Handler 执行 → 状态写回 → SSE 返回结果
```

---

## 当前实际架构

```text
[Interaction]
用户消息 / selected_text / edit_section_id
        ↓
[Entry Router]
chat.py 判断是否进入 R5 写作链路
        ↓
[State & Memory]
PG + Redis + Pending + Template Snapshot
        ↓
[Context Builder]
chat.py 组装 _r5_state
        ↓
[Context Pack]
当前为 _r5_state，尚未独立
        ↓
[Intent Planner]
WritingPlanner: fast rules + LLM router
        ↓
[OpCall Protocol]
operation + params
        ↓
[Clarification Gate]
目标/范围不清则追问
        ↓
[Validator / Guard]
确定性校验
        ↓
[Executor / Handler]
执行 op
        ↓
[Tool / Local LLM]
章节生成、改写、渲染
        ↓
[State Writer]
写库、版本、缓存
        ↓
[Observation]
SSE 返回前端
```

---

## 当前模块分层

| 架构层 | 当前实现 | 状态 |
|---|---|---|
| 交互入口 | `src/api/routes/chat.py` | 已有 |
| R5 入口路由 | `chat.py` 判断 post edit / free topic / chat edit | 已有 |
| 状态与记忆 | PostgreSQL、Redis、PendingConfirmationRepository、TemplateSnapshotRepository | 已有 |
| 上下文构建 | `chat.py` 临时组装 `_r5_state` | 可用但未独立 |
| Context Pack | 当前等同 `_r5_state` | 半成品 |
| Intent Planner | `WritingPlanner`，支持 `rule_only / hybrid / llm_only` | 已有 |
| Intent Frame | 尚未显式建模 | 缺失 |
| Intent Binding | 隐含在 `_parse_intent_response()` | 隐式 |
| OpCall | `OpCall(operation, params, confirmation_required, clarification_question)` | 已有但偏薄 |
| ClarificationGate | `clarification_gate.py` + `checkers/` | 已有 |
| Validator | `validator_registry.py` + `validators.py` | 已有 |
| Executor | `run_op()` + `handlers/` | 已有 |
| Tool / LLM | `writing_engine.py`、template render、section writer | 已有 |
| State Writer | 分散在各 handler 内写库/版本/缓存 | 可用但未收敛 |
| Observation | `SSEEmitter` + SSE events | 已有 |

---

## 当前做得好的地方

- 已经放弃自由 `agent_loop`，改为受控 `OpCall` 运行时。
- `WritingPlanner` 支持 `hybrid`：低歧义快路径走规则，复杂指代/评价型意图交给 LLM。
- `ClarificationGate` 位于 Planner 和 Executor 之间，目标或范围不清时可以阻断执行并追问。
- `Validator` 在执行前校验 session、section、content、confirmation、version 等确定性条件。
- 高风险操作如删除、全文修订、恢复版本、导出有确认/校验机制。
- 执行过程通过 SSE 返回 `writing_thinking / writing_progress / writing_section / writing_artifact / clarification_required` 等观察结果。

---

## 当前主要缺口

这些不是说当前效果差，而是长期演进时会遇到的可维护性和可解释性问题。

### 1. Context Pack 未独立

现在 `chat.py` 直接组装 `_r5_state`，Planner prompt 里也倾向于拼完整文档状态。

风险：

- 文档变长后 prompt 变大。
- 无关上下文干扰 LLM 判断。
- 很难复用或测试“本轮到底给 Planner 看了什么”。

理想做法：

```text
State / Memory → ContextBuilder → ContextPack
```

### 2. Intent Frame 缺失

现在 LLM Router 基本直接输出：

```json
{"operation": "rewrite_section_content", "section_id": "...", "instruction": "...", "reason": "..."}
```

缺少明确的：

```text
intent_type / action / target_type / scope / confidence / evidence / missing_info
```

风险：

- 出现误路由时，不知道 LLM 是怎么理解的。
- `reason` 太弱，不足以支撑调试和评测。

### 3. Intent Binding 还是隐式

当前 `_parse_intent_response()` 同时做了解析、section_id 校正、operation 选择和参数组装。

风险：

- “为什么这个意图绑定到这个 OpCall”不够透明。
- 禁止路由规则难以系统测试。
- 以后 operation 增多时容易变成 if/else 黑盒。

理想做法：

```text
Intent Frame → Routing Rules → Param Mapping → OpCall
```

### 4. OpCall Trace 偏薄

当前 `OpCall` 没有显式携带：

```text
intent_frame
confidence
evidence
matched_rule
rejected_routes
```

风险：

- 用户说“你为什么这样改”时不好解释。
- 线上误判难复盘。

### 5. State Writer 分散

现在各 handler 自己写库、更新版本、emit 事件。

短期没问题，长期会出现：

- 版本快照不一致。
- trace 记录不统一。
- 多 handler 写回逻辑重复。

---

## 未来演进架构

不是现在必须推倒重来，而是在当前效果变复杂后逐步补齐。

```text
[Interaction]
用户消息 / 前端编辑动作
        ↓
[Entry Router]
判断是否进入写作 Agent
        ↓
[State & Memory Store]
PG + Redis + Pending + Template Snapshot
        ↓
[Context Builder]
按本轮意图裁剪状态
        ↓
[Context Pack]
结构化上下文包
        ↓
[Intent Planner]
LLM 充分理解，但不执行
        ↓
[Intent Frame]
用户到底想做什么
        ↓
[Intent Binding]
路由规则 + 参数映射 + 禁止绑定
        ↓
[OpCall]
受控业务操作调用
        ↓
[Clarification / Confirmation]
追问或确认
        ↓
[Validator / Guard]
确定性校验
        ↓
[Executor / Tool Adapter]
执行 handler / 局部 LLM / 渲染器
        ↓
[State Writer]
统一写库、版本、trace
        ↓
[Observation]
SSE / 结果 / 错误 / 下一轮上下文
```

---

## 建议的最小增强路线

当前效果已经不错，不建议马上大重构。建议按失败 case 驱动演进。

### P0：继续体验测试

记录所有误路由 case，例如：

- 局部编辑误判为全文重写。
- 删除一句误判为删除整章。
- 新增一段误判为新增章节。
- 改标题误改正文。

### P1：扩展 OpCall Trace

先不拆模块，只给 `OpCall` 增加：

```python
intent_frame: dict
confidence: float
evidence: list[str]
matched_rule: str | None
rejected_routes: list[str]
```

### P2：补 Intent Eval / Binding Eval

把失败 case 变成测试：

```json
{
  "user_message": "第二章太长，缩短一半",
  "expected_operation": "rewrite_section_content",
  "forbidden_operations": ["apply_document_wide_revision", "delete_sections"]
}
```

### P3：拆 Intent Binding

当误路由多起来时，把 `_parse_intent_response()` 拆成：

```text
parse_intent_frame()
bind_intent_to_opcall()
validate_binding()
```

### P4：拆 ContextBuilder

当文档变长、LLM 变慢或抓错重点时，再把 `_r5_state` 组装独立成：

```text
ContextBuilder.build(user_message, raw_state) -> ContextPack
```

### P5：收敛 StateWriter

当 handler 写回逻辑越来越多时，再统一：

```text
StateWriter.apply(op_result)
VersionWriter.snapshot()
TraceWriter.record()
```

---

## 当前结论

当前架构已经是合理的生产前架构：

```text
Hybrid OpCall Writing Agent
```

如果用户体验已经很好，不要为了“理想架构”马上推倒重来。

更好的策略是：

```text
先上线验证 → 收集失败 case → 做 intent/binding eval → 针对性补 Intent Frame / Binding / ContextBuilder
```

一句话：

```text
当前架构够用，理想架构是后续复杂化后的保险和可维护性升级路径。
```

---

## APD 第 16 项验证结论

本项目已用 `/home/data/rag/ragyuyan/rag_agent` 做了一次 APD 真实场景验证，详细报告见：`docs/writing_agent_apd_validation.md`。

结论：APD 的路线成立。真实写作 Agent 已经从自由 `agent_loop` 演进到受控 `OpCall Runtime`，这验证了 APD 对 Planner、Validator、Executor、Permission、Trace、Eval 的分层判断。

当前不建议推倒重来，建议优先补：

1. `OpCall Trace`
2. `Intent Frame`
3. `Intent Binding`
4. `Context Builder / Context Pack`
5. `Intent / Binding Eval Cases`
6. `State Writer / Trace Writer`

这说明 APD 可以指导真实项目演进：先解释现状，再指出缺口，再生成建议和脚手架，最后用失败样本驱动增量重构。
