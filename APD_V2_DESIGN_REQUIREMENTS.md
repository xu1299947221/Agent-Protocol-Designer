# Agent Protocol Designer v2 设计需求

## 背景

我（Claude Code agent）正在用 APD 推进一份"写作智能体协议"的演进（session id `689bcd3a7df8e784`）。从 R1 推到 R4 的过程中，APD 暴露出一系列结构性问题。本文档把这些问题按层归类，作为 APD v2 的开发依据。

**用户定位**：APD 是"agent 核心架构层"——后续所有 agent 项目都按这种"协议先行"的方式开发。所以 APD 必须达到生产级稳定，不能要求使用者反复手工兜底它的合并/校验缺陷。

**当前状态**：codex 已修复 P0 的 3 项 merge 语义问题（空值守卫 / rules 去重 / 元注解前缀剥离）。本文档是在那之上的 v2 路线图。

---

## 局限分级总览

| 层 | 局限点数 | 严重度 |
|---|---|---|
| 第 1 层：协议演进语义 | 4 | P0/P1 |
| 第 2 层：协议产出物 | 4 | P0 |
| 第 3 层：协议自一致性 | 1 大类 | P0 |
| 第 4 层：协作与多分支 | 3 | P1 |
| 第 5 层：CLI 体验 | 3 | P1/P2 |

---

## 第 1 层：协议演进语义

### 1.1 [P1] `changes` 字段需要从计数器升级为结构化变更记录

**当前实现**

每轮 turn 的 `changes` 是 `{added_operations:[], removed_operations:[], changed_operations:[name1,name2], confirmation_rules_added: int, clarification_rules_added: int}`。

**问题**

- `*_added: int` 没法回答"加了哪几条"
- 没有 `*_removed`、`*_modified`、`*_deduplicated` 维度
- `apd diff` 只能输出 `+ confirmation_rules: 3 条新规则`，看不到具体内容
- 修复 P0 时正是这个字段误差让用户难以察觉"+3 重复"的回归

**修复要求**

```jsonc
{
  "operations": {
    "added":   [{name, snapshot_path}],
    "removed": [{name}],
    "modified":[{name, fields_changed: ["description","validators"]}]
  },
  "objects":   { "added/removed/modified": [...] },
  "confirmation_rules": {
    "added":         ["text 1", "text 2"],
    "removed":       ["text 3"],
    "modified":      [{old: "...", new: "..."}],
    "deduplicated":  ["text X"]
  },
  "clarification_rules": { 同上 },
  "validators": { "added/removed/modified": [...] }
}
```

`apd diff` 输出格式相应升级，能展开到具体条目。

---

### 1.2 [P0] `open_questions` 重复追加，没有去重和 GC

**当前问题（直接证据）**

R4 session 当前的 `last_response.open_questions` 包含：

```
- 新增章节能力已按本轮要求延后到第二版；下一步建议先评审模板能力拒绝规则是否会影响真实业务流程。
- B/C 类问题尚未评审；下一轮建议继续评审条件导出、版本快照、待确认操作过期等边界。
- 下一轮建议继续评审条件导出、版本快照、待确认操作过期等边界，避免高风险确认链路再出现协议本体字段被差异说明污染的问题。
- 第四轮已经补齐取消确认、恢复版本、条件导出和关键校验边界。下一步建议评审"恢复版本"和"取消待确认操作"的交互链路...
- 下一步建议先确认是否按本任务清单拆迭代...
```

第 2 条和第 3 条是同义重复（都是"下一轮评审条件导出、版本快照、待确认操作过期"）；第 1 条提的"新增章节能力延后到第二版"在 R3 已经说过、R4 已无关。**APD 没在每轮 ask 时清理过时 / 重复 open_questions**。

**修复要求**

每轮 ask 处理 LLM 输出后：
1. 把上一轮 `open_questions` 与本轮 LLM 新提出的 open_questions 合并
2. 用归一化文本相似度（去标点、去空白、去前缀"下一步建议"等模板词）做去重，相同的合并
3. 标记每条 open_question 的 `created_turn` 和 `last_seen_turn`；超过 N 轮（默认 3）没被 LLM 重新提及的自动归档到 `closed_open_questions`
4. `apd next -s SID` 命令优先取最新一轮的 next_question，不再取过时的 open_questions

---

### 1.3 [P0] snapshots 不全，无法回滚整轮

**当前问题（直接证据）**

R4 session 已经有 8 个 turn（含手工合并的 R1+R2 + R3 + R3-fix + R4 + dev_plan_request）。但 `session.json.snapshots` 只有 2 条记录。

`turns[i].protocol_before / protocol_after` 字段倒是每轮都存了完整快照，但**没有 CLI 命令暴露这个能力**。我现在如果想把协议退回 R3-fix（撤销 R4 的 7 条新规则），唯一办法是手工编辑 session.json。

**修复要求**

1. **新增 `apd revert -s SID --to-turn N` 命令**：把当前 protocol 退回到 turn N 之后的 protocol_after 快照，同时记录这是一次 revert turn（不删历史，而是 append 一个 revert 操作）
2. **新增 `apd rollback -s SID --last`**：撤销最近一轮的 protocol 变更，等同 `revert --to-turn (last-1)`
3. **`apd show -s SID --history` 输出每个 turn 旁边显示 `[revertable]` 标记**
4. snapshots 数组语义化：每次 ask 都 append 一个 snapshot，包含 `{turn_index, timestamp, protocol, change_summary}`

---

### 1.4 [P0] LLM 失败重试时不带反馈

**当前问题**

R3 第一次 ask 我用 `--max-tokens 8000` 还会偶尔触发 fallback。fallback 路径只把"fallback=True"写进 last_response，没记录 LLM 实际返回的错误体、finish_reason、HTTP code。

P0 修复（codex 已做了一部分）应该已经把 trace 完善了。需要补的是：

**修复要求**

1. LLM 调用失败（HTTP 错误 / finish_reason=length / JSON parse 错误）时，**自动重试时必须携带失败反馈**：在重试 prompt 末尾追加 "上一次失败原因：<具体>"
2. 重试上限 2 次，每次 max_tokens 翻倍
3. 全部失败后 fallback，但 fallback 路径必须保留所有重试历史到 `last_response.trace.attempts: [{attempt:1, max_tokens, error, finish_reason}, ...]`
4. `apd show -s SID` 默认只显示最近一次 trace；加 `--all-attempts` 显示全部尝试

---

## 第 2 层：协议产出物（设计性问题）

### 2.1 [P0] export 4 件套是死模板，无法被 ask 演进

**当前问题（直接证据）**

我让 APD 根据 R4 协议生成"工程级开发任务清单"（按 Phase 0-6 分组、每个 task 含 id/title/scope/preconditions/deliverables/acceptance_criteria/risk/estimated_days）。APD 在 stdout 回复 "已按阶段 0 到阶段 6 分组..."，但导出 R5 后 `development_plan.md` 跟 R4 一字不差，仍是 APD 默认的"按 op 罗列实现建议"模板（160 行）。

**根因**

`apd export` 渲染产物是用 protocol.json 走固定 Jinja 模板生成，不读 LLM 在 ask 中的输出。development_plan / planner_prompt / executor_skeleton 都受同样限制。

**修复要求（按重要度）**

#### A. 把 4 件套从"死模板渲染"升级为"LLM 演进 + 模板回填"

每个产物文件作为一个独立的 `artifact` 存在 protocol 内，同样支持 ask 演进：

```python
protocol = {
  "operations": [...],
  "objects": [...],
  ...,
  "artifacts": {
    "development_plan": {
      "schema_version": "v2",
      "structure": "phase_grouped_tasks",  # 用户可指定 schema
      "content": {
        "phases": [
          {"id":"P0", "name":"...", "tasks":[
            {"id":"T01", "title":"...", "scope":"...", "preconditions":[],
             "deliverables":[], "acceptance_criteria":[], "risk":"low",
             "estimated_days": 1.5}
          ]}
        ]
      }
    },
    "planner_prompt": {
      "structure": "system_prompt_with_rules_injection",
      "content": "..."  # 完整 prompt 字符串
    },
    "executor_skeleton": {
      "structure": "python_class_skeleton",
      "content": {...}  # 结构化代码节点树
    },
    "eval_cases": {...}
  }
}
```

每个 artifact 走相同的 ask 演进通道：用户说"把任务清单按 Phase 0-6 重排"→ APD 让 LLM 输出新的 `artifacts.development_plan.content`，按相同的 P0 合并语义合并。

#### B. 用户可指定 artifact schema

新增命令 `apd artifact -s SID --set development_plan.structure phase_grouped_tasks`，让用户告诉 APD 用哪种 schema 渲染。常用 schema 内置：
- `development_plan`: `op_listing`（默认） / `phase_grouped_tasks` / `gantt_table`
- `planner_prompt`: `monolithic` / `composable_rules`
- `executor_skeleton`: `python_class_skeleton` / `langgraph_node_template` / `agentscope_template`

#### C. export 时按 schema 渲染对应的 artifact

`apd export SID --out DIR` 默认导出每个 artifact 当前的 schema 渲染结果。也支持 `--artifact-schema development_plan=phase_grouped_tasks` 临时覆盖。

---

### 2.2 [P0] planner_prompt.md 没把 confirmation_rules / clarification_rules 注入

**当前问题**

现在 export 出来的 planner_prompt.md（4.5KB）里只列了 operations 名单和简短说明，**完全没提 17 条 confirmation_rules 和 15 条 clarification_rules**。这意味着：开发者拿这个 prompt 喂给 LLM 当 Planner 时，Planner 不知道何时该追问、何时该拒绝、何时该路由到 cancel_pending_confirmation。

**修复要求**

planner_prompt 必须把以下内容渲染进 system prompt：
1. 16 个 operation 的名字 + input_schema + 何时使用（从 description 抽）
2. 17 条 confirmation_rules（标注为"满足以下任一条件时必须先创建确认请求而不是直接执行"）
3. 15 条 clarification_rules（标注为"满足以下任一条件时必须先追问而不是直接路由"）
4. 状态读取约束：列出 Planner 可以读的 State 字段（active_section, outline_status, pending_confirmations, ...）
5. 输出格式约束：JSON schema {"intent": "...", "operation": "name", "params": {...}, "confirmation_required": bool, "clarification_question": str|null}

---

### 2.3 [P0] executor_skeleton.py 不可执行

**当前问题**

export 出来的 `executor_skeleton.py` 17KB，但里面只是 16 个 stub 函数（`def handle_xxx(state, params): pass`），不引用 protocol.json，不定义 State 类型，不调用 validators。开发者把 R4 协议落地时这个 skeleton **几乎不能复用**。

**修复要求**

executor_skeleton.py 必须：
1. 顶部 `import json; protocol = json.load(open("protocol.json"))`，protocol 是单一来源
2. 定义 `State` dataclass，字段从 protocol 的 objects/states 自动派生
3. 每个 `handle_xxx` 函数体：
   ```python
   def handle_set_field_value(state: State, params: dict) -> tuple[State, dict]:
       # 1. 跑 validators（从 protocol.operations[name].validators 自动加载）
       failed = run_validators(protocol, "set_field_value", state, params)
       if failed:
           return state, {"status": "validator_failed", "failed": failed}
       # 2. 检查 confirmation_rules 是否触发
       conf = check_confirmation_rules(protocol, "set_field_value", state, params)
       if conf.required:
           return state, {"status": "confirmation_required", "rule": conf.rule}
       # 3. 实际 op 逻辑（用户填充）
       raise NotImplementedError("Fill in actual operation logic for set_field_value")
   ```
4. 提供 `run_validators` / `check_confirmation_rules` / `check_clarification_rules` 三个通用函数实现，开发者不用每个 op 重写
5. 提供一个 `dispatch(operation_name, state, params)` 入口

---

### 2.4 [P1] 缺少"现有代码适配器" / Roadmap 里"Export LangGraph templates" 未实现

README Roadmap 写了 "Export LangGraph / AgentScope templates"，未实现。我现在的写作系统已经有 17 个 LangGraph 节点 + 13 个 tool，要从这个状态迁到 R4 协议，没有现成的适配器路径。

**修复要求**

1. 实现 `apd export SID --target langgraph --out DIR`：输出 LangGraph 风格的 graph builder，每个 op 是一个 node，有 conditional edge 路由 + 状态共享 + 中断恢复
2. 实现 `apd export SID --target agentscope --out DIR`：AgentScope 风格
3. 提供 `apd analyze --existing-code DIR` 反向分析：扫描现有 LangGraph 代码，列出"哪些节点 / 哪些 tool 大致对应协议里的哪个 op"，给迁移提示

P1 阶段实现 LangGraph 即可，AgentScope/AutoGen 等可作为 P2。

---

## 第 3 层：协议自一致性

### 3.1 [P0] 缺少 `apd validate` 命令做形式化校验

**当前问题（直接证据）**

R4 协议中 `cancel_pending_confirmation.validators` 包含 `pending_confirmation_exists`、`pending_confirmation_not_expired`，但 12 个 objects 里**没有任何 PendingConfirmation/pending_confirmation 对象**。这是悬空引用——开发者按这个协议生成 executor 时会发现 state 里压根没这个字段。

类似潜在问题：
- 91 个 unique validator name 中，是否每个都被某个 op 实际使用？
- confirmation_rules 引用的 op 名（"删除章节、全文重写..."）是否都存在？
- operation.input_schema 引用的字段是否在某个 object.key_fields 里？
- regulation 之间是否互相矛盾（"必须 X" 同时 "必须不 X"）

**修复要求**

新增 `apd validate -s SID` 命令，输出：

```text
✓ All operation references in confirmation_rules exist
✗ FAIL: cancel_pending_confirmation.validators references "pending_confirmation_exists"
        but no PendingConfirmation object defined.
        Suggest: add PendingConfirmation object or remove the validator.
✗ FAIL: 4 validator names defined but never used:
        - leaf_task_matches_section_plan (referenced by no op... wait, false positive: used by draft_section_content)
        ...
✗ WARN: confirmation_rule "高风险确认必须包含..." 与 "导出文档默认不要求确认..." 在 render_document_artifact 上下文有歧义
✓ No object schema field collision

Total: 1 fail, 1 warn, 18 ok
```

校验类目（按重要度）：

#### 3.1.A [P0] 引用完整性
- confirmation_rules 引用的 operation 名都存在
- clarification_rules 引用的 operation 名都存在
- validator 引用的 object 都存在（推断方式：validator 名里出现的 PascalCase 词、或 validator description 里的引用）
- operation.input_schema 引用的对象字段在 object.key_fields 中

#### 3.1.B [P0] 完备性
- 每个 risk=high 的 op 必须 requires_confirmation=true 或在 description 中说明为何 false（render_document_artifact 是合理例外）
- 每个 op 至少 1 个 validator
- 每个 op 都有 input_schema 非空
- 每个 op 都有 failure_policy 非空

#### 3.1.C [P0] 反向使用率
- 每个 validator 名至少被 1 个 op 引用（否则要么是死代码要么是悬空规则）
- 每个 object 至少被 1 个 op 的 input_schema / output 引用，或被某条 rule 提到（否则该 object 是死代码）

#### 3.1.D [P1] 矛盾检测
- 同一 op 上 "默认不要求确认" 和 "必须要求确认" 同时出现 → 报矛盾
- LLM 协助检测：把所有 rules 喂给一个独立的 review_llm，让它指出潜在矛盾

`apd ask` 在每轮 LLM 输出合并前先跑 validate，发现 fail 时拦下并要求 LLM 修正（自动循环 1 次）。

---

## 第 4 层：协作与多分支

### 4.1 [P1] 缺少 `apd fork`

**修复要求**

```bash
apd fork -s SID --new-name "writing-with-graph"
```

行为：复制 session.json 到新 SID（生成新 id），保留所有 turns / snapshots / protocol，作为分支起点。后续在新 SID 上 ask 不影响原 SID。

### 4.2 [P1] 缺少 `apd merge`

**修复要求**

```bash
apd merge --base SID_a --branch SID_b --out SID_c
```

行为：取两个 session 当前的 protocol，按以下规则合并：
- operations / objects：按 name 匹配，相同名取 base，base 没有的从 branch 拉过来；同名定义不同的列出冲突让用户选
- rules：去重（按 1.1 的归一化逻辑）后合并
- 输出新 SID，turns 里记录 `merge_from: [a, b]`

### 4.3 [P1] 缺少协议评分 / 评审者

**修复要求**

`apd review -s SID` 触发独立的 reviewer LLM 跑评审：
- 每个 op 评 5 维度（输入 schema 完整性 / 校验充分性 / 风险等级合理性 / failure_policy 明确性 / LLM-程序边界清晰度）
- 每个维度 0-10 分
- 输出总分 + 改进建议
- 评分作为 protocol.review_history 落库

这个能让 APD 闭环检验设计质量，不依赖外部 codex 评审（也可以接 codex 当 reviewer）。

---

## 第 5 层：CLI 体验

### 5.1 [P1] LLM 翻译污染代码标识符

**当前问题（直接证据）**

R5 ask 后 quality_notes 出现：
```
任务交付物已引用现有代码路径，包括 rag_智能体（agent）/src/graph/template_writing_graph.py、rag_智能体（agent）/src/core/writing/智能体（agent）ic/planner.py、...
```

LLM 把代码路径里的 `agent` 翻成中文 `智能体（agent）`，导致 `rag_agent/src/...` 变成 `rag_智能体（agent）/src/...`，路径不可用。

**修复要求**

1. SYSTEM_PROMPT 增加约束："识别为代码标识符（路径、CamelCase、snake_case、被 backtick 包裹）的英文不得翻译"
2. APD 后处理：扫描 LLM 输出，发现"路径形式 + 中文混合"时自动恢复（用启发式：如果某段含 `/` 且前后是 `.py/.json/.md` 之类后缀，把段内中文还原成英文）
3. 配 dictionary：常见英文术语 → 不翻译白名单（agent/operation/validator/session/protocol/...）

### 5.2 [P2] 进度可视化

`apd ask` 没法在 stdout 之前显示"正在调用 LLM... / 已收到 LLM 输出 / 正在合并 / 正在去重..."，给人感觉卡住。

加一个 `--progress` 选项把这些步骤打到 stderr。

### 5.3 [P2] `apd ask --new` 创建新 session 时的元数据

新 session 创建后只有 session_id，没法附带 `--name "writing-system"` 给个友好标识。`apd list` 看到的就是一长串 hash。

加 `--name` 参数。

---

## 实现优先级总结

### 第 1 阶段（P0，建议 1-2 周）
- 1.2 open_questions 去重和 GC
- 1.3 `apd revert / rollback` + snapshot 完整性
- 1.4 LLM 重试带反馈 + trace 完善
- 2.1 export 4 件套支持 ask 演进 + schema 选择
- 2.2 planner_prompt 注入 rules
- 2.3 executor_skeleton 可执行（含 validator/confirmation 通用函数）
- 3.1 `apd validate` 命令（A/B/C 三类）

### 第 2 阶段（P1）
- 1.1 changes 字段结构化
- 2.4 LangGraph 适配器
- 3.1.D 矛盾检测
- 4.1/4.2 fork/merge
- 4.3 review/scoring
- 5.1 代码标识符保护

### 第 3 阶段（P2）
- 5.2 进度可视化
- 5.3 session metadata（--name）

---

## 我的承诺

1. APD v2 改造期间我可以继续用 R4 协议手工推进，不会因为这份文档卡住开发
2. v2 任一里程碑落地后我会用我现有的 session（689bcd3a7df8e784）做回归验证，把发现的问题反馈
3. 后续我开发其他 agent（要把 APD 当核心架构层）时，会基于 v2 设计再提需求

请你（codex）按上面 P0 七项实施，验证清单可参考 `/home/data/api/agent-protocol-designer/MERGE_SEMANTICS_BUGS.md` 中的回归验证模式（针对每个修复点构造一个反例 session 跑通）。落地后通知我。
