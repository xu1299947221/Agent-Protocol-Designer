# Agent 开发架构笔记

这份笔记总结了我们围绕“写作 Agent、ReAct、AgentScope、能力协议、Planner/Executor”讨论出来的核心认知。它也是本项目存在的原因：帮助开发者在写 Agent 之前，先把业务能力拆清楚。

---

## 1. ReAct 不是完整 Agent 架构

ReAct 可以理解为：

```text
思考 / 决策 -> 行动 / 工具调用 -> 观察 / 反馈 -> 下一步决策
```

它解决的是：

- LLM 无法一次性完成多步任务
- LLM 需要调用外部工具
- LLM 需要根据工具结果继续调整下一步

但 ReAct 本身不能保证：

- 工具一定选对
- 参数一定填对
- 状态一定不乱
- 不会误删、误改、误覆盖
- 复杂业务一定稳定收敛

所以 ReAct 是一种循环模式，不等于可靠的业务 Agent 架构。

---

## 2. Tool Calling 不是 Agent

Tool Calling / Function Calling 是一种能力机制：

```text
把函数或工具说明给模型，模型根据上下文请求调用工具。
```

但模型并不真正执行函数，真正执行的是程序代码。

Agent 通常还需要：

- 目标
- 状态
- 多步循环
- 工具
- 观察反馈
- 停止条件
- 控制边界

所以：

```text
Tool Calling = Agent 的手
ReAct = Agent 的行动循环
Agent 架构 = 目标 + 状态 + 决策 + 工具 + 校验 + 执行 + 观察
```

---

## 3. 自由 ReAct 为什么容易失控

很多人一开始会这样设计：

```text
用户输入 -> LLM 理解意图 -> LLM 自己选工具 -> LLM 填参数 -> 工具执行
```

这在搜索、问答、资料整理里可以工作。

但在复杂业务里，尤其是会修改状态的场景，比如写作、退款、日程、订单、文档编辑，就容易出问题。

因为 LLM 同时承担了太多责任：

- 理解用户意图
- 判断目标对象
- 选择工具
- 填工具参数
- 判断风险
- 修改状态
- 决定是否结束

一旦任何一步错了，就可能改错数据、删错内容、覆盖全文或提前提交。

---

## 4. LLM 和程序的边界

核心原则：

```text
LLM 负责模糊语义、归纳、创作、自然语言理解。
程序负责确定性规则、权限、校验、状态修改和执行。
```

LLM 适合做：

- 判断用户想表达什么
- 把自然语言归一化为结构化意图
- 生成内容
- 总结素材
- 改写一段文字
- 解释复杂语义

程序适合做：

- ID 是否存在
- 当前状态是否允许操作
- 用户是否有权限
- 参数是否完整
- 操作是否高风险
- 是否需要确认
- 状态如何写回
- 是否可回滚

好的 Agent 系统不是让 LLM 替代规则，而是让 LLM 在规则内发挥。

---

## 5. Planner / Validator / Executor 分离

可控 Agent 常见结构：

```text
用户输入
  ↓
Planner：LLM 把自然语言转成结构化计划
  ↓
Validator：程序校验计划是否合法、可执行、是否高风险
  ↓
Executor：程序执行确定性状态修改或调用局部生成器
  ↓
State：程序写回结构化状态
  ↓
Observation：把结果反馈给用户或下一轮 Agent
```

Planner 不应该直接改状态。
Executor 通常不需要 LLM。
Validator 是安全边界。

---

## 6. 什么是能力协议

能力协议就是你给 Agent 设计的“操作语言”。

它描述：

- 业务里有哪些对象
- Agent 可以执行哪些 operation
- 每个 operation 需要哪些参数
- 每个 operation 的风险等级
- 每个 operation 的校验规则
- 哪些情况需要追问
- 哪些情况需要用户确认
- 程序如何执行这些 operation

能力协议的目标是：

```text
把无限自然语言，收敛成有限、稳定、可校验的业务操作。
```

例如：

```json
{
  "operation": "rewrite_section",
  "target_section_ids": ["sec_2"],
  "instruction": "改得更正式，保留原意和结构"
}
```

---

## 7. 通用拆解流程

换任何 Agent 场景，都可以按这个流程拆：

### 第一步：找领域对象

问：这个业务世界里有哪些东西？

例子：

- 写作：Document、Section、Paragraph、Template
- 客服：User、Order、RefundRequest、Ticket
- 日程：Calendar、Event、Participant、TimeSlot
- 数据分析：Dataset、Column、Metric、Chart

### 第二步：找用户常见动词

问：用户通常想做什么？

例如：查询、创建、删除、修改、合并、拆分、导出、确认、投诉、退款、改期。

### 第三步：收敛成 operation

把用户无数说法压缩成有限操作。

例如：

```text
“我不要了”
“帮我退一下”
“这个订单买错了”
```

都可以映射成：

```json
{"operation": "request_refund"}
```

### 第四步：定义参数 schema

每个 operation 都要有明确输入。

```json
{
  "operation": "request_refund",
  "order_id": "string",
  "reason": "string"
}
```

### 第五步：定义校验规则

程序校验通用安全边界：

- object_id 是否存在
- 当前状态是否允许
- 用户是否有权限
- 参数是否缺失
- 是否高风险
- 是否需要确认

### 第六步：设计失败出口

协议必须有：

- ask_clarification
- need_confirmation
- unsupported
- retry_plan

不确定就追问，不要硬猜。

---

## 8. 不要枚举所有场景，要设计稳定操作

错误思路：

```text
if 用户说“压缩第二章” -> ...
if 用户说“把案例提到附录” -> ...
if 用户说“改成政府公文” -> ...
```

正确思路：

```text
用户自然语言 -> Planner -> operation -> Validator -> Executor
```

不要写场景逻辑，要写能力协议。

需求变化时，优先改 Planner 映射；必要时再新增 operation。

---

## 9. 原子操作不是越底层越好

原子操作不是技术最底层，而是业务上稳定可执行的一层。

对文档系统来说，初期不要直接暴露：

- XML run
- paragraph node
- styleId
- OOXML 细节

更适合暴露：

- rewrite_section
- append_to_section
- replace_selected_text
- add_section
- delete_section
- rename_section
- move_section
- render_to_template

也就是：业务可理解，程序可执行，LLM 可映射。

---

## 10. 写作 Agent 的经验教训

写作系统里，最容易失控的是：

```text
让 LLM 直接看全文、所有工具、所有模板结构，然后自己决定怎么改。
```

更稳的是：

```text
用户输入
  ↓
EditPlanner 输出结构化编辑计划
  ↓
程序校验 section_id / 风险 / 阶段
  ↓
SectionEditor 只看目标章节并局部改写
  ↓
程序写回 DocumentState
  ↓
用户确认后再渲染模板
```

模板渲染不要和内容编辑混在一起。内容先稳定，最后再套模板。

---

## 11. 场景分类器：先判断架构，再设计 operation

我们刚才进一步明确了一点：开发者不需要、也通常无法一次列出所有写作流程。写作、设计、文档生成这类场景的流程天然开放，用户可能随时跳到任意章节、语气、模板、材料或导出问题。

所以 APD 不能要求用户先枚举所有流程，而应该先帮助判断：这个场景到底应该用哪种 Agent 形态。

### ReAct 不等于 agent_loop

ReAct 是一种“边思考、边行动、边观察”的推理范式；agent_loop 是程序层面的循环实现。ReAct 可以在某个局部节点里使用，但不应该自动成为全局控制架构。

关键问题不是“有没有循环”，而是：

```text
这个循环由谁控制？
循环在哪里结束？
状态谁负责？
风险谁兜底？
```

### 五类常见场景

| 类型 | 典型场景 | 推荐形态 |
|---|---|---|
| 直接问答 | FAQ、翻译、摘要、普通 RAG | 单次 LLM / RAG |
| 单步操作调用 | 一条消息触发一个明确业务动作 | OpCall + validator + confirmation |
| 协议驱动多轮操作调用 | 写作、文档生成、设计、模板填充 | Planner + Validator + Executor + 版本快照 |
| 工作流/状态机 | 审批、法律、医疗、金融、ETL | 状态机 + 强 gate |
| 自主 agent_loop / 多 Agent | 编程、研究、浏览器自动化、大规模迁移 | agent_loop / 多 Agent 编排 |

### 判断维度

APD 应该优先判断这五个维度：

- 可逆性：出错后能不能撤回？
- 路径确定性：流程能不能大致列出来？
- 规则量：业务规则是否很多、能否表格化？
- 意图密度：用户一句话通常是一个操作，还是多个串联任务？
- 副作用范围：只是生成文本，还是会删除、覆盖、写库、导出、调用外部系统？

### 写作场景的正确判断

写作流程列不完，这是正常的。错误目标是“列出所有流程”。正确目标是：

```text
流程开放组合，能力协议收敛。
```

写作 Agent 通常不适合纯状态机，因为流程无法穷举；也不适合自由 agent_loop，因为文档状态、模板规则、删除/覆盖/导出风险都很强。

更合适的是：

```text
协议驱动多轮操作调用
```

也就是：

```text
用户每轮说需求
  ↓
Planner 判断意图并映射到一个或少量受控 operation
  ↓
Validator 校验参数、状态、风险和确认规则
  ↓
Executor 执行局部确定性修改或局部生成
  ↓
保存 DocumentState、版本快照和 trace
```

### APD 要提供的能力

APD 在进入 operation 设计前，应该先输出 `scene_classification`：

```json
{
  "scene_type": "protocol_driven_opcall",
  "recommended_architecture": "协议驱动多轮操作调用",
  "dimensions": {
    "reversibility": "medium",
    "path_determinism": "partially_known",
    "rule_volume": "high",
    "intent_density": "mixed",
    "side_effect_scope": "local"
  },
  "why_not_agent_loop": ["规则多、状态强、自由循环容易误选工具"],
  "why_not_pure_state_machine": ["写作流程无法穷举"],
  "flow_strategy": "流程开放组合",
  "capability_strategy": "先收敛稳定写作能力，而不是列出所有流程"
}
```

这让 APD 从“协议生成器”升级成“Agent 架构选型器 + 协议设计器”。

---

## 12. 意图理解协议：Planner 不能只是薄映射

写作 Agent 开发到后期常见的失败不是执行器不会改，而是上游意图识别错了。方案里如果只写“Planner 负责自然语言到操作调用（OpCall）归一化”，就默认了 LLM 能在任何上下文下正确理解用户意图，这个默认太强。

更合理的 Planner 应该是：

```text
观察结构化上下文包
  ↓
理解用户意图、指代、目标范围和风险
  ↓
给出证据、置信度和缺失信息
  ↓
输出受控 operation 或追问/确认/拒绝出口
```

核心原则是：

```text
让 LLM 充分理解，但不要让 LLM 自由执行。
```

### 不是完整上下文越多越好

ReAct 强调观察、推理、行动，但在业务写作场景里，“观察完整上下文”不等于把全文、全部模板、全部历史无差别塞给模型。上下文太多会让 LLM 抓错重点，导致把局部编辑误判成全文重写。

APD 需要定义上下文包（Context Pack）：

- 用户当前消息
- 当前文档结构摘要
- 当前章节或选区
- 最近几轮对话和操作 trace
- 模板约束和锁定区域
- 待确认操作
- 版本快照摘要

上下文包的目标是：

```text
足够理解当前意图，但不提供无关干扰。
```

### 写作场景必须处理指代

写作用户经常说：

- 这段
- 这里
- 刚才那个
- 上一版
- 第二部分
- 太长的地方

这些不能靠 LLM 随便猜。协议应该明确：

```text
“这段/这里”优先解析为 selected_text；
没有 selected_text 时再看 active_section_id；
仍不唯一就追问。
```

同理：

```text
“第二章”必须能在 document_outline 中唯一匹配；
“刚才那个”必须能在 recent_operations 中定位；
“太长/太啰嗦”默认是局部改写，不得路由到全文重写。
```

### Planner 输出要带证据和置信度

不要只输出：

```json
{"operation": "rewrite_section", "params": {}}
```

应该输出：

```json
{
  "intent": "缩短第二章",
  "operation": "rewrite_section",
  "target": {"type": "section", "id": "chapter_2", "source": "explicit"},
  "params": {"rewrite_goal": "压缩到约一半", "preserve_meaning": true},
  "confidence": 0.86,
  "evidence": ["用户明确提到第二章", "缩短一半是章节级局部改写"],
  "needs_clarification": false,
  "needs_confirmation": false
}
```

证据（evidence）可以让开发者知道模型为什么这样路由，避免 Planner 成为黑盒。

### APD 新增的协议层

APD 现在除了能力协议，还应该输出：

- `context_policy`：Planner 每轮需要哪些上下文、怎么裁剪、哪些上下文禁止提供。
- `intent_recognition`：指代消解、歧义处理、置信度、证据输出和示例。
- `routing_policy`：允许路由到哪些操作，哪些路由被禁止，多意图如何拆分。
- `intent_eval_cases.json`：专门测试意图识别和路由是否正确。

这补上的是“能力协议”的上游：

```text
场景分类 → 上下文供给 → 意图理解 → 操作协议 → 校验确认 → 执行回写
```

### 写作 Agent 的推荐形态

写作 Agent 不应该回到完全自由 ReAct，也不能把 Planner 做成薄薄的自然语言映射。

更合适的是：

```text
受控 ReAct 式 Planner + 协议化 OpCall Executor
```

也就是：Planner 可以观察上下文、推理和解释依据；但最终只能输出受控 operation，执行器只执行通过校验和确认的 operation。

---

## 13. 意图绑定层：推理结果如何对上 OpCall

仅有意图规划器（Intent Planner）还不够。Planner 的推理结果不能直接等同于操作调用（OpCall），中间必须有一层意图绑定层（Intent Binding）。

完整链路应该是：

```text
Context Pack
  ↓
Intent Planner
  ↓
Intent Frame
  ↓
Intent Binding
  ↓
OpCall
  ↓
Validator / Executor
```

### Intent Frame 是什么

意图框架（Intent Frame）描述的是“用户到底想做什么”，还不是“系统马上调用哪个 operation”。

例如用户说：“第二章太长，缩短一半”：

```json
{
  "intent_type": "modify",
  "action": "shorten",
  "target_type": "section",
  "target_ref": "第二章",
  "target_resolved_id": "chapter_2",
  "scope": "local",
  "constraints": {"length_ratio": 0.5, "preserve_meaning": true},
  "risk": "medium",
  "confidence": 0.87,
  "evidence": ["用户明确提到第二章", "缩短一半是章节级局部改写"],
  "missing_info": []
}
```

### Intent Binding 做什么

意图绑定层负责三件事：

1. 路由：根据 intent_type、action、target_type、scope 选择 operation。
2. 参数映射：把 Intent Frame 字段映射到 operation 的 input_schema。
3. 禁止错误绑定：例如局部编辑不能绑定到全文重写，模糊删除不能绑定到删除章节。

例如：

```json
{
  "when": {
    "intent_type": "modify",
    "action": ["shorten", "rewrite", "polish"],
    "target_type": "section"
  },
  "operation": "rewrite_section_content"
}
```

参数映射：

```json
{
  "operation": "rewrite_section_content",
  "map": {
    "section_id": "intent_frame.target_resolved_id",
    "instruction": "compose(action,constraints,user_message)",
    "target": "content"
  }
}
```

### 为什么不能让 LLM 直接输出 OpCall

LLM 可以建议 operation，但程序必须校验建议是否符合绑定规则。否则会出现：

```text
用户说“第二章太长”
LLM 直接 suggested_operation=apply_document_wide_revision
程序照执行 → 全文被改
```

正确做法：

```text
LLM 输出 Intent Frame + suggested_operation
程序按 binding rules 校验 suggested_operation
冲突则追问、拒绝或改成安全 operation
```

APD 现在应该导出：

- `intent_binding_spec.md`：Intent Frame、routing rules、param mapping、forbidden bindings。
- `binding_eval_cases.json`：测试“这个 Intent Frame 应该绑定哪个 OpCall”。

这让 APD 从“生成 operation 列表”升级为：

```text
生成 Planner 上游协议 + 意图绑定协议 + 可执行能力协议
```

---

## 14. 本项目的作用

Agent Protocol Designer 不是为了替你直接做业务 Agent。

它的作用是：

```text
在写代码之前，帮你把业务场景拆成可控 Agent 协议。
```

它输出：

- protocol.json
- planner_prompt.md
- executor_skeleton.py
- eval_cases.json
- context_pack_schema.md
- intent_planner_prompt.md
- intent_eval_cases.json
- intent_binding_spec.md
- binding_eval_cases.json

目标是让开发者从：

```text
我想做一个 Agent，但不知道怎么拆
```

变成：

```text
我知道这个 Agent 有哪些对象、操作、校验和执行边界
```

---

## 15. 记住一句话

```text
用户侧要灵活，系统内部要收敛。
```

也就是：

```text
外部自然语言无限，内部操作协议有限。
```

这就是可控 Agent 架构的核心。

---

## 16. 写作 Agent 落地案例索引

APD 页面里的“写作 Agent 案例”记录了 `/home/data/rag/ragyuyan/rag_agent` 的真实落地情况。

这个案例说明了一件很重要的事：

```text
效果已经不错的 Agent，不一定要马上推倒重来。
```

更合理的演进方式是：

```text
当前可用架构 → 收集失败 case → 补意图评测 → 补意图框架 → 补绑定层 → 再拆上下文和状态写回
```

当前写作 Agent 可以概括为：

```text
混合路由式受控写作 Agent（Hybrid OpCall Writing Agent）
```

它已经具备：

- 意图规划器（Intent Planner）
- 操作调用（OpCall）
- 追问门（ClarificationGate）
- 校验器（Validator）
- 执行器（Executor）
- 观察反馈（SSE Observation）

未来主要补齐：

- 上下文包（Context Pack）独立化
- 意图框架（Intent Frame）显式化
- 意图绑定（Intent Binding）规则化
- 操作追踪（OpCall Trace）增强
- 状态写回（State Writer）统一化

这个案例可以作为以后开发新 Agent 的参照：先看真实系统怎么跑，再决定哪些理想架构层值得落地。


---

## 17. Agent Harness 架构补全清单

本项目后续不应只停留在“能力协议”层，还要升级为 Agent Harness Designer / Generator。

后续继续开发前，必须阅读：

```text
docs/agent_harness_architecture_gaps.md
```

本次新增的关键结论：

```text
APD 以后不是只问“这个 Agent 能做哪些操作”，还要检查“这个 Agent 作为一个可运行系统缺哪些架构层”。
```

当前最重要的补齐方向：

- 记忆策略（Memory Policy）
- 状态模型（State Model）
- 产物模型（Artifact Model）
- 工具注册（Tool Registry）
- 权限策略（Permission Policy）
- 失败恢复（Error Recovery）
- 评测用例（Eval Cases）
- 知识检索策略（Knowledge/RAG Policy）
- 版本回滚（Versioning/Rollback）
- 成本耗时预算（Cost/Latency Budget）

尤其要注意：之前 APD 有上下文、历史、状态和 Trace，但没有把“记忆”作为一等架构层。后续应补 `memory_policy`，并明确记忆如何读取、写入、校验、删除、进入 Context Pack。
