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

---

## 18. APD、业务 Agent 与 open_claude Engine 的关系

本轮讨论补充了一个关键认知：

```text
APD 设计出来的不是最终可运行 Agent 本体，而是业务 Agent 的语义边界、能力协议和治理规则。
```

更准确的分层是：

```text
APD 协议层
  ↓
业务 Agent 服务层
  ↓
Agent Runtime / Engine 执行层
```

### 18.1 APD 协议层负责什么

APD 负责把模糊需求收敛成明确协议：

- 业务目标
- 领域对象
- 用户意图
- 可执行操作
- 状态边界
- 权限规则
- 风险操作
- 人工确认点
- 产物要求
- 验收标准
- 评测样本

它本质上是：

```text
业务语义层 / 场景边界层 / Agent 治理层
```

不是直接等同于代码，也不等同于一个已经成熟的 Runtime。

### 18.2 业务 Agent 服务层负责什么

业务 Agent 服务层是把某个 APD 协议真正部署成可访问服务的中间层。

它负责：

- 用户登录和会话
- 任务入口 API
- 业务参数校验
- Task Pack 组装
- 工作区创建
- Job 状态管理
- 事件流转发
- 产物归档
- 版本记录
- 运行诊断

它不一定自己完成全部智能执行，而是可以调用底层 Engine。

### 18.3 open_claude Engine 负责什么

open_claude 可以理解为：

```text
通用工程类 Agent 执行引擎
```

它具备：

- LLM 推理
- 多步执行循环
- 文件读取和修改
- 命令执行
- 观察结果处理
- 错误修复
- 工程上下文管理
- 终端交互能力

因此，open_claude 更像一个底层 Agent Engine，而不是普通模型接口。

类比 Java 开发：

```text
APD 协议 ≈ 业务配置 / 领域规约 / 契约
业务 Agent 服务 ≈ 具体业务应用
open_claude Engine ≈ Spring Boot + 工作流执行器 + 工具系统 + LLM 大脑
```

这个类比不是说 open_claude 等于 Spring Boot，而是说它可以作为多个业务 Agent 复用的底层执行框架。

### 18.4 两条落地路线的真实区别

APD 当前有两条平级路线：

```text
路线 A：委托型真实调试 / Delegated Agent
APD 协议 → Task Pack → open_claude Engine → 结果 / 产物 / Trace

路线 B：自研工程开发 / 自研 Runtime
APD 协议 → 生成工程骨架 → 自己实现 Planner / Validator / Executor / Tools / Runtime
```

这两条路线共享 APD 协议，但执行实现不同。

路线 A 短期效果更强，因为 open_claude 已经有成熟的工程 Agent 执行能力。

路线 B 长期更可控，因为核心 Runtime 是自己开发和维护的。

### 18.5 APD 协议能不能通用

APD 协议可以在“业务语义层”通用：

```text
这个 Agent 要做什么
哪些事情能做
哪些事情不能做
什么情况下要追问
什么情况下要人工确认
最终产物怎么算合格
```

但协议不能自动保证所有 Runtime 都有同样执行效果。

原因是 Runtime 还需要具备：

- 工具系统
- 多步循环
- 文件操作能力
- 命令执行能力
- 上下文管理能力
- 错误恢复能力
- 并发隔离能力
- 事件追踪能力

所以正确理解是：

```text
APD 让业务边界通用。
Runtime 决定执行能力上限。
```

### 18.6 open_claude 作为每个业务 Agent 的执行引擎

如果把 open_claude 服务化，每个业务 Agent 都可以这样运行：

```text
用户请求
  ↓
业务 Agent API
  ↓
读取 APD 协议
  ↓
生成 Task Pack
  ↓
调用 open_claude Engine
  ↓
订阅结构化事件
  ↓
返回 Agent 回复 / 产物 / Trace
```

这种设计下，open_claude 不是 APD 平台的一部分，而是每个业务 Agent 的底层依赖。

也就是说：

```text
APD 负责生成和治理业务 Agent。
open_claude Engine 负责帮助业务 Agent 执行复杂任务。
```

### 18.7 为什么需要把 open_claude 从 CLI 改成服务化 Engine

CLI 适合一个人、一个终端、一个任务。

业务 Agent 服务需要支持：

- 多用户
- 多会话
- 多任务
- 任务排队
- 任务取消
- 超时控制
- 工作区隔离
- 事件流输出
- 产物归档
- 权限控制

因此不能长期依赖一个 CLI 进程和终端文本解析。

目标应该是：

```text
open_claude CLI
  ↓
open_claude Core Engine
  ↓
open_claude Engine Server
```

CLI 保留，但 CLI 和 Server 共用同一个 Core Engine。

### 18.8 对后续 APD 开发的要求

后续 APD 如果继续走委托型 Agent 路线，需要补齐：

- Task Pack 标准
- Engine Adapter 标准
- Job 状态模型
- 事件协议
- Artifact 契约
- 权限策略
- 工作区隔离
- 多用户并发模型
- 失败恢复与诊断

这部分不应混在普通协议设计里，而应作为：

```text
Agent Engine 接入层 / Delegated Runtime 层
```

单独设计和演进。

---

## 19. 真实调试台 AI 修复的分类原则

真实调试台里的“让 AI 修这个问题”，默认修复的是当前生成出来的业务 Agent 工程，而不是 APD 平台源码，也不是通用模板。

当前链路是：

```text
APD 协议
  ↓
生成 Delegated Agent 工程
  ↓
真实调试台运行这个工程
  ↓
发现问题
  ↓
AI 诊断与修复
  ↓
修改当前生成工程
  ↓
重启当前 Agent 验证
```

关键原则：

```text
场景修复默认局部化。
通用修复必须显式评审后再回灌。
```

### 19.1 三类修复

| 修复类型 | 典型问题 | 应修改位置 | 是否回灌 |
|---|---|---|---|
| 场景业务修复 | 投标 Agent 评分办法解析不准、写作 Agent 品牌语气不准、某个 Agent 的 Task Pack 不够细 | 当前生成的业务 Agent 工程 | 默认不回灌 |
| Runtime 通用修复 | Job 一直 queued、result.json 解析失败、cancel 不生效、artifact 列表不完整、状态流转错误 | Delegated Runtime SDK / APD 生成模板 | 需要评审后回灌 |
| Engine 能力修复 | open_claude 多用户串上下文、CLI 输出不可结构化、权限控制太粗、无法稳定事件流 | open_claude Engine | 单独进入 Engine 改造 |

### 19.2 为什么不能默认回灌

真实调试台中大量修复是场景相关的。

例如：

```text
投标 Agent 需要更重视评分办法。
写作 Agent 需要保持品牌语气。
知识图谱 Agent 需要提高证据绑定权重。
```

这些改动如果直接进入通用模板，会污染其他 Agent：

```text
一个业务场景的偏好，变成所有 Agent 的默认行为。
```

所以默认策略必须是：

```text
当前 Agent 修当前 Agent。
只有被明确识别为通用 Runtime 缺陷，才允许进入模板回灌流程。
```

### 19.3 后续产品能力要求

真实调试台后续应增加“修复归类”能力。

AI 诊断时不应只给修复建议，还要输出：

```json
{
  "problem_type": "scenario|runtime|engine",
  "should_patch_current_agent": true,
  "should_backport_template": false,
  "should_create_engine_task": false,
  "reason": "这是当前投标 Agent 的评分办法解析策略问题，不应污染通用模板。"
}
```

当 `problem_type=runtime` 时，页面才应该提示：

```text
这可能是通用 Runtime 问题，是否生成“回灌 APD 模板”的任务包？
```

当 `problem_type=engine` 时，页面应该提示：

```text
这可能是 open_claude Engine 能力问题，是否生成 Engine 改造任务包？
```

这样可以避免把业务定制、Runtime 缺陷、Engine 能力缺口混在一起。

---

## 20. 首页协议产物与两套脚手架路线

本轮再次澄清 APD 当前系统结构。

### 20.1 首页左侧对话产物

首页左侧对话的核心产物是：

```text
APD 协议草案 JSON
```

它不是完整代码工程，也不是可直接运行的 Agent。

协议草案 JSON 主要包含：

```text
objects
operations
validators
workflow
memory_policy
state_model
artifacts
eval_cases
architecture_check
```

页面右侧可以基于协议导出辅助产物，例如：

```text
planner_prompt.md
executor_skeleton.py
workflow_plan.md
eval_cases.json
```

但这些属于辅助导出，不等于首页左侧对话直接产出完整工程代码。

准确理解：

```text
首页左侧对话 = 通过对话生成和迭代协议草案 JSON。
```

### 20.2 协议完成后进入两条落地路线

当协议草案通过对话收敛到可用状态后，APD 应引导用户选择两条平级落地路线：

```text
路线 1：委托型真实调试 / Delegated Agent
路线 2：自研工程开发 / 自研 Runtime
```

两条路线共享同一份 APD 协议草案，但使用不同脚手架和不同执行模型。

### 20.3 路线 1：Delegated Agent 脚手架

委托型真实调试路线使用 Delegated Agent 模板脚手架。

当前模板主要在：

```text
protocol_designer/delegated_generator.py
```

它会生成：

```text
FastAPI 服务
Job 管理
Task Pack Builder
Workspace Manager
Trace Store
Artifact Store
openclaude_runner.py
runner_worker.py
前端调试页
runner/open_claude/
```

这条路线的执行核心是：

```text
open_claude CLI / 未来 open_claude Engine API
```

所以可以理解为：

```text
APD 协议 JSON
  ↓
Delegated Agent 模板脚手架
  ↓
Delegated Agent 工程
  ↓
open_claude 执行引擎
```

### 20.4 路线 2：自研工程开发脚手架

自研工程开发路线使用另一套工程脚手架。

当前相关代码主要在：

```text
protocol_designer/scaffold_generator.py
protocol_designer/dev_studio.py
```

它更偏向基于 APD 协议生成一个自研 Agent 工程骨架，然后通过 Agent IDE / 工程开发台继续开发。

这条路线理论上需要自己实现：

```text
Planner
Validator
Executor
Tool Registry
State
Memory
Trace
Runtime Loop
```

所以可以理解为：

```text
APD 协议 JSON
  ↓
自研 Agent 模板脚手架
  ↓
自研 Agent 工程
  ↓
自己逐步补齐 Runtime 能力
```

### 20.5 两条路线的本质区别

| 维度 | 委托型真实调试 | 自研工程开发 |
|---|---|---|
| 脚手架 | Delegated Agent 模板 | 自研 Agent 工程模板 |
| 执行核心 | open_claude CLI / Engine | 自己实现 Runtime |
| 短期效果 | 更强 | 取决于实现完成度 |
| 可控性 | 依赖 open_claude 能力边界 | 完全自研可控 |
| 适合阶段 | 快速验证业务效果 | 长期沉淀专用 Agent |

最终准确公式：

```text
首页协议 JSON
  ↓
路线 1：Delegated Agent 模板 + open_claude Runtime
  ↓
生成委托型 Agent 工程

首页协议 JSON
  ↓
路线 2：自研 Agent 模板 + 自己实现 Runtime
  ↓
生成自研 Agent 工程
```

---

## 21. 导出产物区的新定位

随着 APD 从早期“协议导出工具”演进到“Agent 工程落地平台”，页面右侧的导出产物区需要重新定义主次关系。

### 21.1 早期 APD 的导出定位

早期 APD 的核心能力是：

```text
通过对话生成协议草案
  ↓
导出 protocol.json / prompt / skeleton
  ↓
开发者拿去手工开发
```

所以当时页面右侧以“导出协议产物”为主是合理的。

### 21.2 当前 APD 的导出定位

当前 APD 已经具备两条 Agent 工程落地路线：

```text
路线 1：委托型真实调试 / Delegated Agent
路线 2：自研工程开发 / 自研 Runtime
```

因此，页面右侧不应继续把“下载协议草案”放在主位。

更合理的主路径是：

```text
协议完成后
  ↓
选择落地路线
  ├── 打开委托型真实调试
  ├── 生成 Delegated Agent 工程
  ├── 打开自研工程开发台
  └── 生成自研 Agent 工程
```

### 21.3 协议导出仍然有价值

协议导出不能删除，只是应该降级到高级能力。

它仍然用于：

- 两条工程路线的源数据
- 调试问题时的依据
- 给 Claude Code / Codex 读取上下文
- 版本对比和回滚
- 迁移到其他 Runtime 或 Engine 的契约

所以准确定位是：

```text
协议导出 = 底层源文件 / 调试资料 / 高级开发者产物
```

而不是主流程入口。

### 21.4 建议 UI 分组

页面右侧建议整理成三组：

```text
1. 下一步落地
- 打开委托型真实调试
- 生成 Delegated Agent 工程 zip
- 打开自研工程开发台
- 生成自研 Agent 工程 zip

2. 协议源文件
- 查看 protocol.json
- 下载 protocol.json
- 下载 workflow.json
- 下载 eval_cases.json

3. 开发者辅助
- planner_prompt.md
- executor_skeleton.py
- architecture_check.md
- task_pack.md
```

这样可以保证新用户先看到“怎么落地 Agent”，高级用户仍然能拿到底层协议文件。

---

## 22. 自研 Runtime 路线的成长模型

自研 Runtime 路线不是不能生成完整 Agent，而是刚生成时更像工程骨架，需要通过工程开发台持续开发成熟。

### 22.1 初始产物

自研路线初始产物是：

```text
自研 Agent 工程骨架
```

它可以包含：

```text
Planner
Validator
Executor
Tool Registry
State
Memory
Trace
Runtime Loop
```

但这些能力在初始阶段可能只是骨架或基础实现，不等同于成熟完整 Runtime。

### 22.2 成熟路径

自研路线的成长过程是：

```text
APD 协议 JSON
  ↓
生成自研 Agent 工程骨架
  ↓
工程开发台让 AI 修改代码
  ↓
补 Planner / Validator / Executor / Tools / Memory / Trace
  ↓
不断调试、评测、修复
  ↓
形成成熟完整 Agent
```

也就是说，自研路线是可成长路线。

### 22.3 与 Delegated 路线的区别

| 路线 | 刚生成时 | 成熟方式 |
|---|---|---|
| Delegated Agent | 已借用 open_claude，短期执行力更强 | 主要调协议、Task Pack、业务约束、Runner 适配 |
| 自研 Runtime | 初始是工程骨架，执行力取决于实现完成度 | 通过工程开发台持续补 Runtime 和业务代码 |

### 22.4 页面文案应避免误导

不应写成：

```text
一键生成成熟完整 Agent
```

更准确的文案是：

```text
生成可运行 Delegated Agent。
生成自研 Agent 工程骨架，并在工程开发台持续开发成完整 Agent。
```

核心理解：

```text
Delegated 路线 = 先借成熟引擎跑起来。
自研路线 = 先生成骨架，再把 Runtime 养成熟。
```

---

## 23. APD 架构图展示偏好

后续在 APD 页面或文档中展示系统架构时，不建议默认使用 Mermaid 流程图作为主要展示方式。

原因：

```text
Mermaid 适合开发者快速表达逻辑，但视觉观感偏工程化，不够直观。
AI 生图视觉可能更好，但文字容易错，后续不方便维护。
```

更推荐的展示形式：

```text
HTML / SVG 卡片式架构图
PPT 风格分层图
表格 + 简洁文本图
```

推荐风格示例：

```text
┌──────────────────────────────────────────────┐
│                  APD 平台                     │
│        通过对话生成 Agent 协议草案 JSON        │
└──────────────────────┬───────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        │                             │
┌───────▼────────┐            ┌───────▼────────┐
│ 路线 A          │            │ 路线 B          │
│ 委托型真实调试   │            │ 自研工程开发     │
└───────┬────────┘            └───────┬────────┘
        │                             │
        ▼                             ▼
Delegated Agent 脚手架          自研 Agent 脚手架
        │                             │
        ▼                             ▼
Delegated Runtime              工程开发台 AI 编码
        │                             │
        ▼                             ▼
open_claude Engine             自研 Runtime 成熟化
        │                             │
        ▼                             ▼
快速可运行 Agent                长期可控 Agent
```

产品页面建议：

```text
使用 HTML/SVG 卡片式架构图，保证中文文字准确、可维护、可点击展开说明。
```

会议汇报建议：

```text
使用 PPT 风格分层图，突出 APD 协议层、两条落地路线、Runtime 差异和最终产物。
```
