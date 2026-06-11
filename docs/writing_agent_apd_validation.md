# 写作 Agent 对 APD 的验证报告

> 验证对象：`/home/data/rag/ragyuyan/rag_agent`
>
> 验证目标：检查 APD 当前产出的协议、架构建议、预览运行和脚手架，是否能指导真实写作 Agent 项目继续演进。

---

## 1. 一句话结论

APD 的方向是成立的。

真实写作 Agent 已经证明：复杂写作场景不适合继续走完全自由的 `agent_loop`，更适合走：

```text
Context Pack → Intent Planner → Intent Frame → Intent Binding → OpCall → Clarification / Validator / Permission → Executor → State Writer → Observation / Trace
```

当前真实项目已经落地了其中一大半：

```text
WritingPlanner → OpCall → ClarificationGate → ValidatorRegistry → Executor / handlers → SSE Observation
```

因此 APD 不是空想设计器，而是可以解释真实项目为什么变稳定，也能指出下一步该补哪里。

---

## 2. 真实项目已经验证 APD 哪些判断

| APD 判断 | 真实项目证据 | 验证结论 |
|---|---|---|
| 写作 Agent 不应完全自由 ReAct | `op_runtime/planner.py` 将用户消息路由为 `OpCall` | 成立 |
| operation 必须有限、受控 | `handlers/` 下有明确 handler，例如 `draft_section_content`、`rewrite_section_content`、`delete_sections` | 成立 |
| Planner 不能直接执行 | `WritingPlanner` 只产出 `OpCall`，执行由 `executor.py` 和 handlers 处理 | 成立 |
| 不确定时必须追问 | `ClarificationGate` 以 `ClarifyDecision` 决定放行或追问 | 成立 |
| 程序校验是安全边界 | `validator_registry.py`、`validators.py` 在执行前处理确定性校验 | 成立 |
| 高风险动作要确认 | `confirmation_gate.py`、pending confirmation 链路处理确认 | 成立 |
| 执行过程必须可观察 | `SSEEmitter` 和 `chat.py` 产出 thinking/progress/artifact/clarification 事件 | 成立 |

---

## 3. 当前真实写作 Agent 的 APD 对齐度

| APD 架构层 | 真实项目状态 | 对齐度 |
|---|---|---|
| Interaction / 交互入口 | `src/api/routes/chat.py` | 已对齐 |
| Entry Router / 入口路由 | R5 写作链路判断 | 已对齐 |
| State / Memory | PG、Redis、Pending、TemplateSnapshot | 已对齐但分散 |
| Context Pack / 上下文包 | 当前是 `_r5_state` 临时结构 | 半对齐 |
| Intent Planner / 意图规划器 | `WritingPlanner` 规则优先，部分 LLM | 已对齐 |
| Intent Frame / 意图框架 | 尚未显式建模 | 缺口 |
| Intent Binding / 意图绑定 | 隐含在 planner parse / route 中 | 缺口 |
| OpCall / 操作调用 | `OpCall(operation, params, confirmation_required, clarification_question)` | 已对齐但偏薄 |
| Clarification / Confirmation | `ClarificationGate`、`confirmation_gate.py` | 已对齐 |
| Validator / Guard | `validator_registry.py`、`validators.py` | 已对齐 |
| Executor / Handler | `executor.py`、`handlers/` | 已对齐 |
| Tool / LLM | `writing_engine.py`、模板渲染、章节生成 | 已对齐 |
| State Writer | 分散在 handlers | 半对齐 |
| Observation / Trace | SSE 事件已有，OpCall 解释 trace 偏薄 | 半对齐 |
| Eval Cases / 评测 | 已有部分 tests，但 intent/binding 回归还应加强 | 半对齐 |

---

## 4. APD 对真实项目给出的最小演进建议

不要推倒重来。当前效果已经不错，应按失败样本增量演进。

优先顺序：

1. **补 OpCall Trace**
   - 给 `OpCall` 增加 `intent_frame`、`confidence`、`evidence`、`matched_rule`、`rejected_routes`。
   - 价值：线上误路由时能知道“为什么这样理解”。

2. **补 Intent Frame**
   - 让 Planner 输出 `intent_type / action / target_type / scope / risk / missing_info / evidence`。
   - 价值：把“LLM 的理解”和“最终操作调用”分开。

3. **拆 Intent Binding**
   - 从 planner 中拆出 `bind_intent_to_opcall(intent_frame, context_pack)`。
   - 价值：operation 继续增加时，不让路由逻辑变成黑盒 if/else。

4. **独立 Context Builder**
   - 把 `chat.py` 中临时组装 `_r5_state` 的逻辑收敛为 `ContextBuilder.build(...)`。
   - 价值：控制 Planner 每轮到底看什么，减少长文档干扰。

5. **补 Intent / Binding Eval Cases**
   - 把真实误判话术沉淀成 `intent_eval_cases`、`binding_eval_cases`。
   - 价值：每次改 prompt、规则、operation 后能防退化。

6. **统一 State Writer / Trace Writer**
   - handler 仍可保留，但写库、版本、SSE、trace 逐步统一。
   - 价值：减少状态写回不一致和版本快照遗漏。

---

## 5. APD 功能是否能指导这个项目开发

可以，但应该这样使用：

```text
1. 在 APD 中描述写作 Agent 现状
2. 让 APD 产出 protocol.json / architecture_check.md / development_advice.md
3. 用“预览运行”测试正常、信息不足、高风险、边界话术
4. 把误路由保存为 eval_cases.json
5. 生成 Agent Harness Demo，对照真实 rag_agent 模块逐步迁移
6. 不直接替换真实项目，而是先补 trace、intent_frame、binding、context_builder
```

APD 最有价值的不是直接生成最终代码，而是：

- 帮开发者判断当前架构缺哪一层；
- 帮开发者决定下一步先改哪里；
- 把自然语言需求转成可控协议；
- 把失败样本转成回归测试；
- 把真实项目从“效果不错但难解释”推进到“效果不错且可观察、可验证、可维护”。

---

## 6. 第 16 项验证结论

第 16 项通过。

真实写作 Agent 证明 APD 的核心路线是对的：

```text
自由 AgentLoop → 受控 OpCall Runtime → Harness 化 → 评测驱动演进
```

下一步不应该继续抽象讨论，而应该进入第 17 项：用招投标 Agent 这种更复杂的多文档、多素材、多流程场景验证 APD 的 Dynamic Workflow、Artifact、RAG/Graph、人工确认和导出能力。
