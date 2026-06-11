# Agent Protocol Designer — diff/合并语义缺陷修复需求

## 背景

我（Claude Code agent）正在通过 `apd ask` 推进一份"写作智能体协议"的演进（session id `689bcd3a7df8e784`）。本轮（A 类反馈）目标只是给协议补 4 条高风险与条件确认规则。结果连续两个 turn 出现 9 个回归，全部都是 APD 在合并 LLM 输出到 protocol 本体时**没有正确处理"差异语义 vs 替换语义 vs 去重语义"**。问题清单和证据如下，请按这份文档定位修复。

session id：`689bcd3a7df8e784`
session 文件路径：`/home/data/api/agent-protocol-designer/data/sessions/689bcd3a7df8e784.json`

---

## 证据 1：Turn 1（R3）的 LLM 输出污染了协议本体字段

我发的 prompt 内容是 4 条 A 类反馈（要求修改高风险确认规则、字段失效逻辑、章节改写拦截等）。LLM 把"差异说明"误当作 protocol 字段的新值写回，APD 直接落库到了 `protocol`，造成：

### 1.1 description 被改成 "差异：..." 元描述

| operation | 改动前 R2 | 改动后 R3（错误） |
|---|---|---|
| `set_field_value.description` | "从用户话语中提取字段设置或字段纠正意图，并写入会话字段值。" | "差异：设置字段值（set_field_value）在大纲已确认后写入字段时，必须使当前大纲失效..." |
| `rewrite_section_content.description` | "按用户指令改写指定章节，例如缩短、扩写、改正式..." | "差异：章节改写（rewrite_section_content）必须拦截章节范围与指令范围不一致的情况..." |
| `delete_sections.description` | "删除一个或多个章节，并更新章节序号、引用映射和版本快照。" | "差异：删除章节（delete_sections）强化批量删除防御..." |
| `apply_document_wide_revision.description` | "对全文进行明确范围的全局修改..." | "差异：全文修改（apply_document_wide_revision）不再负责把模糊范围请求降级为章节改写..." |

description 是协议本体描述，不是 changelog。这种"差异：..."文案显然属于 LLM 给出的解释性输出，不该写入 description。

### 1.2 input_schema 被清空成 `{}`

| operation | R2 input_schema | R3（错误） |
|---|---|---|
| `set_field_value` | `{type:object, properties:{session_id, user_message, field_id, value, source}, required:[session_id, field_id, value]}` | `{type:object, properties:{}, required:[]}` |
| `rewrite_section_content` | `{type:object, properties:{session_id, section_id, instruction, rewrite_mode, preserve_constraints}, required:[session_id, section_id, instruction, rewrite_mode]}` | `{type:object, properties:{}, required:[]}` |
| `delete_sections` | `{type:object, properties:{session_id, section_ids, delete_reason}, required:[session_id, section_ids]}` | `{type:object, properties:{}, required:[]}` |
| `apply_document_wide_revision` | `{type:object, properties:{session_id, revision_instruction, revision_type, target_section_ids}, required:[session_id, revision_instruction, revision_type]}` | `{type:object, properties:{}, required:[]}` |

LLM 在输出 R3 时只输出了它有改动的字段，没输出 input_schema → APD 把"未提及"误识别为"应该清空"。这是合并语义错误：未提及字段必须保留 R2 原值，不能默认填空。

### 1.3 llm_role 被加了 "无变化：" 前缀

4 个 op 的 llm_role 全部从 "理解用户自然语言..." 变成 "无变化：理解用户自然语言..."。这是 LLM 在响应里附加的元注解，APD 应该剥掉前缀或直接保留原值，不应原样写入 protocol。

---

## 证据 2：Turn 2（R3-fix）继续制造新回归

我发了第二轮 prompt 要 APD 修上面 6 个回归。它确实修好了 input_schema / description / llm_role（这部分正确）。但**又把 confirmation_rules / clarification_rules 各重复加了 3 条**：

### 2.1 confirmation_rules：10 → 13 → 16（每轮 +3，但内容重复）

执行 `apd get -s 689bcd3a7df8e784 confirmation_rules` 得到 16 条规则，第 11-13 条带"新增："前缀，第 14-16 条是同样语义但去掉了前缀的版本，并且**两份都留下了**：

```
# 第 11 条
新增：大纲已确认后变更字段，必须在告知用户"此修改会让大纲失效，需要重新规划"之后才执行；字段写入成功后必须将 outline_version 标为 stale。
# 第 14 条（重复）
大纲已确认后变更字段，必须在告知用户"此修改会让大纲失效，需要重新规划"之后才执行；字段写入成功后必须将 outline_version 标为 stale。

# 第 12 条
新增：当删除章节数量大于等于根级章节数量的百分之八十，或覆盖全部根级章节时，必须二次确认...
# 第 15 条（重复）
当删除章节数量大于等于根级章节数量的百分之八十，或覆盖全部根级章节时，必须二次确认...

# 第 13 条
新增：章节改写请求如果升级为全文修改，必须重新走全文修改的高风险确认流程...
# 第 16 条（重复）
章节改写请求如果升级为全文修改，必须重新走全文修改的高风险确认流程...
```

### 2.2 clarification_rules：9 → 12 → 15（同样 +3 重复）

```
# 第 10 条
新增：章节级操作的指令中如果包含"全文、整篇、全部章节、整个文档"等全文级范围词，但目标范围只覆盖局部章节，必须追问"要改这一章还是整篇"。
# 第 13 条（重复）
章节级操作的指令中如果包含"全文..."，但目标范围只覆盖局部章节，必须追问...

# 第 11 条 / 第 14 条 重复
# 第 12 条 (调整：...) / 第 15 条 重复
```

### 2.3 协议元数据证据

`session.json.turns[1].changes`（即 R3 → R3-fix）：

```json
{
  "added_operations": [],
  "removed_operations": [],
  "changed_operations": ["apply_document_wide_revision","delete_sections","rewrite_section_content","set_field_value"],
  "added_objects": [],
  "removed_objects": [],
  "changed_objects": [],
  "confirmation_rules_added": 3,
  "clarification_rules_added": 3
}
```

`*_added: 3` 是个**单纯计数器**，没有 "modified / removed / deduplicated" 语义。Turn 2 我明确要求"修回回归"，APD 把 LLM 输出的 R3-fix 协议中那 3 条规则当作"新增 3 条"叠加到 R3 的 13 条之上，得到 16 条 → 重复了。

---

## 根因（推测）

合并 LLM 输出到 protocol 时，缺少以下三类语义：

### 根因 A：LLM 输出 → protocol 写回缺校验

LLM 输出包含两类字段：
- **协议本体字段**（input_schema, description, llm_role, executor_role, validators, ...）
- **元注解字段**（差异说明、"无变化：" 前缀、"新增："/"调整：" 前缀）

当前合并逻辑大概是直接 `protocol.update(llm_output)`。需要：
1. 元注解前缀剥离器：扫描 `description / executor_role / failure_policy / llm_role / 各 rule 文本`，剥掉 "差异：/无变化：/新增：/调整：/原值：/补充：" 等中文元注解前缀（白名单匹配）
2. 协议本体字段空值守卫：对 input_schema / output_schema 这种**结构化字段**，如果 LLM 输出的是空对象 `{}`，但 R(N-1) 有非空值，必须保留 R(N-1) 原值，不可覆盖

### 根因 B：rules 数组没有去重 / 替换语义

`confirmation_rules` 和 `clarification_rules` 当前是 `list[str]`。LLM 在多轮中可能：
1. 添加新规则（应该 append）
2. 修改已有规则的措辞（应该替换匹配项，不是再 append）
3. 删除已有规则（应该 remove）

当前合并只是单向 append，没有任何"按相似度去重 / 按 ID 替换 / 显式删除"机制。建议：
- **方案 1（最小成本）**：合并前对 rules 数组做去重——文本归一化（去前缀 "新增："/"调整：" / 去标点 / 转小写 / 去空白） + 子串包含判断（A 是 B 的子串则保留更长的那条）
- **方案 2（结构化）**：把 rules 改成 `list[{id, text, status, source_turn}]`，让 LLM 必须用 id 引用，规避此类合并问题

我建议先做方案 1（向后兼容、改动小）。

### 根因 C：`changes` 计数器太弱

`turns[*].changes.*_added: int` 这种计数没有 diff 语义。应该改成结构化变更记录：

```json
{
  "confirmation_rules": {
    "added": ["text 1", "text 2"],
    "removed": ["text 3"],
    "modified": [{"old": "...", "new": "..."}],
    "deduplicated": ["text X"]   // 新增：本轮去重的条目
  }
}
```

这样 `apd diff` 输出能告诉用户"本轮去重 N 条"，避免静默重复。

---

## 修复要求

请按优先级修：

### P0（必须修）

1. **协议本体字段空值守卫**：合并时对 input_schema / output_schema / properties / required 等结构化字段，若 LLM 返回空对象/空数组但 R(N-1) 非空，**保留旧值**。日志记录 "field X kept from previous turn because new value is empty"。
2. **rules 数组合并前去重**：对 confirmation_rules / clarification_rules（以及未来类似的 list[str] 字段）合并前先做归一化去重。归一化规则：
   - 剥前缀 "新增："、"调整："、"补充："、"差异："（白名单匹配）
   - 转半角、去首尾空白
   - 计算归一化文本相似度，相同的视为重复，保留无前缀且文本最长的那条
3. **元注解前缀剥离**：扫描所有协议字符串字段（description / executor_role / failure_policy / llm_role），剥掉 "差异："、"无变化："、"新增："、"调整：" 等前缀。白名单可配。

### P1（建议）

4. **changes 字段结构化**：把 `*_added: int` 升级为 `{added: [], removed: [], modified: [], deduplicated: []}`。`apd diff` 命令输出对应的去重提示。
5. **合并自检报告**：每轮合并完成后，APD 自查"是否有本轮新增条目与已有条目归一化文本相同"，若有则警告并放进 `last_response.trace.merge_warnings`，便于 `apd show` 看到。

### P2（远期）

6. 把 confirmation_rules / clarification_rules / validators 改成 `list[{id, text, ...}]`，规避位置依赖问题。这是 schema 升级，需要迁移现有 sessions，不必这次做。

---

## 验证清单

修复后，对当前 session `689bcd3a7df8e784` 执行：

```bash
./apd get -s 689bcd3a7df8e784 confirmation_rules | wc -l   # 期望 13（去重后）
./apd get -s 689bcd3a7df8e784 clarification_rules | wc -l  # 期望 12（去重后）
./apd get -s 689bcd3a7df8e784 operations.set_field_value.input_schema  # 期望非空，含 session_id/field_id/value
./apd get -s 689bcd3a7df8e784 operations.set_field_value.description   # 期望不以"差异："开头
./apd get -s 689bcd3a7df8e784 operations.set_field_value.llm_role      # 期望不以"无变化："开头
```

模拟回归测试：构造一个新 session，在两轮中分别让 LLM 输出包含 "新增：" 前缀的规则、"无变化：" 前缀的 llm_role、空 input_schema，断言合并后协议本体不被污染。

---

## 注意

我（Claude Code agent）是 APD 的真实使用者。这套问题不只影响这一个 session，所有"演进型协议设计"流程都会遇到。如果不修，开发者每用一次 APD 都要回头手动清理协议字段，APD 的核心价值（可控、确定性合并）会打折扣。

请你（codex）按上面 P0 三项修复，P1 两项可选，把验证清单跑通后告诉我。

---

## 追加 P0-4：引号字符未剥离导致 R5 出现重复规则

### 现象

R5 协议（session 689bcd3a7df8e784）clarification_rules 出现两条同义重复：

```
索引 10：用户使用范围模糊词，例如"改正式点、改一下语气、再凝练点"等，且当前活跃章节唯一时...
索引 13：用户使用范围模糊词，例如改正式点、改一下语气、再凝练点等，且当前活跃章节唯一时...
```

差异仅在引号有/无。预期 dedupe 应识别为同一条，实际两条都保留下来。

### 根因

`protocol_designer/core.py:157-165` 的 `normalize_rule_text`：

```python
replacements = {
    "，": ",", "。": ".", "；": ";", "：": ":", "（": "(", "）": ")",
    "“": '"', "”": '"', "‘": "'", "’": "'", " ": "", "\t": "", "\n": "",
}
```

中文弯引号 `"` `"` `'` `'` 被替换为直引号 `"` `'`——**只是字符转换，没有剥离**。所以"含弯引号 vs 无引号"归一化后仍然差两个 `"` 字符，dedupe 判定为不同。

复现：
```python
a = '用户...例如"改正式点..."等...'
b = '用户...例如改正式点...等...'
normalize_rule_text(a) != normalize_rule_text(b)  # True，但应当 False
```

### 修复

`normalize_rule_text` 中把引号类字符全部剥离（替换为空字符串），不是转换：

```python
QUOTE_CHARS = '"\'""''「」『』""''《》〈〉'  # 各种引号 + 书名号

def normalize_rule_text(value: str) -> str:
    text = strip_meta_prefix(str(value))
    replacements = {
        "，": ",", "。": ".", "；": ";", "：": ":", "（": "(", "）": ")",
        " ": "", "\t": "", "\n": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # 所有引号剥离
    for q in QUOTE_CHARS:
        text = text.replace(q, "")
    return text.lower().strip(".,;:。；， ")
```

### 验证

1. 拿 R5 case 跑：
   ```python
   a = '用户使用范围模糊词，例如"改正式点、改一下语气、再凝练点"等'
   b = '用户使用范围模糊词，例如改正式点、改一下语气、再凝练点等'
   assert normalize_rule_text(a) == normalize_rule_text(b)
   ```
2. unit test 增加引号去重 case
3. 修复后对 R5 协议跑 dedupe，应该把 14 条 clarification_rules 看作 14 条（已无重复）；同时构造一个含弯引号 + 无引号的混合 session，跑一轮 ask 应自动合并

注：R5 协议的重复条目我已手工删除（删了索引 10 含引号版），保留无引号简洁版。当前 session 的 clarification_rules 是 14 条。但 APD 本身的 bug 必须修，否则下次还会复现。

### 影响

- 影响所有依赖 normalize_rule_text 的去重场景
- 不影响协议本体字段（description / executor_role 等），那些字段不走 dedupe
