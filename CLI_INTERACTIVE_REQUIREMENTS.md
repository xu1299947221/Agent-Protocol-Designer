# Agent Protocol Designer CLI 交互模式改造需求

## 背景

当前 `apd run` 是单条命令模式，每次都要把完整协议 + 全部修改要求一次塞过去。这违背了 APD "自适应教练风格、每轮只问 1 个问题" 的原则，也让外部 agent（如 Claude Code）无法跟它真正"对话"。

需要的是：让外部 CLI 调用方能像人类用户在 webui 里那样，**一问一答推进协议演进**。

## 改造目标

让外部调用方（包括 Claude Code agent）能用纯 CLI 完成多轮对话式协议设计，每轮命令的 stdout 是**人类/agent 都能直接读懂的简短文本**，而不是完整的 JSON 协议。

---

## 需求清单（按重要度）

### 1. 新增 `apd ask` 命令（最关键）

**功能**：在已有 session 上发一轮回复，只返回简洁的对话状态，不返回完整协议。

**用法**：
```bash
./apd ask -s <session_id> "用户回复的内容"
```

或 stdin 模式（用于大段内容）：
```bash
echo "我的回复" | ./apd ask -s <session_id> -
cat /tmp/big-context.md | ./apd ask -s <session_id> -
```

**stdout 格式**（纯文本，多段，每段一个 header）：
```
[stage] review
[assistant] APD 给你的回复（不超过 3 行，多余的截断或省略号）
[next_question] APD 下一步要追问的问题（每轮最多 1 个）
[quality_notes]
- 笔记 1
- 笔记 2
[open_questions]
- 待决策的开放问题
[changes] 本轮协议变化简要（如 +1 operation, ~2 fields, -1 object，无变化时省略此段）
[done] no
```

**不要在 stdout 输出完整 protocol**。协议状态由 session 自己持久化，需要时调用方用 `apd get` 或 `apd export` 单独取。

**`[done]` 字段判定**：
- 当 `stage == "final"` 时输出 `yes`
- 否则 `no`

**保持现有 `apd run --json` 不动**（向后兼容），新增 `apd ask` 作为交互友好版本。

---

### 2. 新增 `apd diff` 命令

**功能**：显示 session 内最近两轮（或指定两轮）协议的变化，给调用方"这一步改了什么"的清晰信号。

**用法**：
```bash
./apd diff -s <session_id>                  # 默认对比上一轮 vs 当前
./apd diff -s <session_id> --turn -2         # 对比倒数第二轮 vs 当前
./apd diff -s <session_id> --json            # 机器可读格式
```

**stdout 格式（人类可读）**：
```
+ operations: inspect_session_state (risk=low, confirm=false)
+ operations: set_field_value (risk=low, confirm=false)
+ operations: clear_field_value (risk=medium, confirm=false)
- operations: update_field_values
+ objects: TemplatePolicy (key_fields=11)
+ objects: TemplateCapabilities (key_fields=10)
~ operations.render_document_artifact.requires_confirmation: true → false
~ operations.render_document_artifact.validators: +3
+ confirmation_rules: 4 条新规则
+ clarification_rules: 2 条新规则
```

**实现要点**：APD 需要在 session 内保留每一轮的 protocol 快照（不只是最新），diff 时按 path 比较。

---

### 3. 新增 `apd get` 局部查询命令

**功能**：精准取协议的某一部分，不用拉完整 JSON 再过滤。

**用法**：
```bash
./apd get -s <sid> operations                                          # 列所有 operation 名（一行一个）
./apd get -s <sid> operations.draft_section_content                    # 完整结构
./apd get -s <sid> operations.draft_section_content.input_schema       # 局部
./apd get -s <sid> objects.TemplatePolicy.key_fields                   # 数组
./apd get -s <sid> confirmation_rules                                  # 列表
./apd get -s <sid> --json operations.draft_section_content             # 机器可读
```

**默认输出格式**：
- 字符串：直接输出
- 数组：每行一个元素
- 对象：YAML-like，浅层 key=value，深层省略
- 加 `--json` 才输出原始 JSON

---

### 4. 新增 `apd next` 命令

**功能**：只输出 APD 当前最关心的"下一步问题"，一行。

**用法**：
```bash
./apd next -s <sid>
```

**stdout**（一行）：
```
下一步你希望先评审高风险与条件确认规则，还是直接生成开发任务清单？
```

如果当前没有 next_question（stage=final 或协议已稳定），输出：
```
(no pending question; stage=final)
```

---

### 5. 改造 `chat_json` 调用稳定性

当前问题：
- max-tokens 默认 2000，遇到大协议输出直接截断进 fallback
- LLM 调用失败时 trace 是空的，无法诊断

**改造**：
- 默认 max-tokens 提升到 **8000**
- 加重试机制：检测到 `finish_reason=length` 或 JSON parse 失败时，自动把 max-tokens 翻倍重试一次（最多两次）
- 失败时把详细错误（HTTP code、错误体前 500 字、finish_reason）写到 last_response.trace 里，便于 `apd show <sid>` 时诊断

---

### 6. 加 `apd show -s <sid> --history` 选项

**功能**：以人类可读方式列出 session 全部对话历史 + 每轮的关键变化（可选含 diff）。

**用法**：
```bash
./apd show -s <sid>                  # 当前已有，列协议
./apd show -s <sid> --history        # 改为列对话历史
./apd show -s <sid> --history --diff # 每轮显示该轮协议变化
```

**stdout 格式**：
```
=== Turn 1 (2026-05-30T10:23:45) [stage: discover → operations] ===
[user] 我要为已有的写作系统重构...
[assistant] 我先把你的现状收敛成阶段性能力协议草案...
[+ 12 operations] [+ 10 objects]

=== Turn 2 (2026-05-30T10:35:12) [stage: operations → review] ===
[user] 针对第一轮草案，我有 6 个具体调整意见...
[assistant] 已基于第一版协议应用 6 点调整...
[+ 2 operations] [+ 2 objects] [- 1 operation] [~ 1 operation]
```

---

## 不需要改动的部分

- `apd run`（保留向后兼容）
- `apd export` / `apd list` / `apd delete`（保留）
- 现有 SYSTEM_PROMPT（不要改）
- 现有 protocol schema（不要改）
- 现有 session 持久化机制（保留 data/sessions/*.json）

---

## 调用方使用示例（改造完成后）

```bash
# 启动新 session
SID=$(./apd ask --new "我要设计一个写作智能体，企业内部员工用模板生成文档" | grep '\[session\]' | awk '{print $2}')

# 看 APD 问什么
./apd next -s $SID
# 输出: 你想设计的智能体主要帮用户完成什么业务任务？请用一句话描述。

# 回复一轮
./apd ask -s $SID "帮用户上传 docx 模板后，通过对话生成文档内容"

# 查看本轮协议变化
./apd diff -s $SID

# 取局部协议
./apd get -s $SID operations
./apd get -s $SID objects.WritingSession.key_fields

# 看 APD 下一步问什么
./apd next -s $SID

# 继续回复...
./apd ask -s $SID "..."

# 协议稳定后导出全部产物
./apd export $SID --out ./my-protocol/
```

---

## 关键设计原则

1. **stdout 是给 agent 读的，不是给协议读的**：每个命令的 stdout 应该足够短、结构清晰、用 `[header]` 分段，让外部 agent（如 Claude Code）一眼看懂当前状态，而不是拉一堆 JSON 自己 parse。

2. **协议状态在 session 文件里，不在 stdout 里**：调用方可以用 `apd get / diff / show / export` 按需取。

3. **每轮信号尽量小**：默认输出 < 30 行。需要详细时显式 flag。

4. **错误诊断要充分**：失败时不能只说"fallback 了"，要在 trace 里说清 HTTP code / finish_reason / 错误体片段。

5. **向后兼容**：现有 `apd run` / `apd export` / `apd list` 不动，新功能加在新子命令上。

---

## 验收标准

改造完成后，外部 agent 应该能这样推进一次完整设计：

```bash
SID=$(./apd ask --new "我要设计 X" | parse_session_id)
while [ "$(./apd ask -s $SID '...' | grep '\[done\]' | awk '{print $2}')" != "yes" ]; do
    Q=$(./apd next -s $SID)
    REPLY="...根据 Q 给出的回复..."
    ./apd ask -s $SID "$REPLY"
    ./apd diff -s $SID  # 可选，看每轮变化
done
./apd export $SID --out ./final-protocol/
```

整个过程不需要 agent 自己 parse JSON，不需要看完整 protocol，每轮只看 next_question + diff，跟人在 webui 里点击操作一致的体验。
