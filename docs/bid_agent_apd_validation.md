# 招投标 Agent 对 APD 的验证报告

> 验证对象：`/home/data/rag/ragyuyan/archive/bid-writing/design-c`
>
> 验证目标：用 AI 投标文件生成 C 方案验证 APD 是否能承载复杂 Dynamic Workflow、文档产物、知识库/RAG/图谱、人工确认、合规检查和导出链路。

---

## 1. 一句话结论

第 17 项验证通过，但暴露出 APD 后续必须继续加强 `Knowledge/RAG Policy` 和 `Agent Runtime`。

招投标 Agent 不是一个“会写标书的大 Agent”，而是一个典型的 Dynamic Workflow 系统：

```text
确定性工作流主干 + 受控 LLM 叶子节点 + 人工确认点 + 产物版本 + 知识库检索 + 合规校验 + 编辑器副驾驶
```

这正好验证 APD 的核心判断：

```text
复杂业务不能只靠自由 agent_loop，必须显式设计节点、边、产物、权限、校验、失败恢复和评测。
```

---

## 2. C 方案应被建模为 Dynamic Workflow

核心流程应拆成两个阶段，而不是一个单体 Agent：

```text
Phase A：投标文件生成主工作流
上传招标文件
  → 解析招标内容
  → 抽取资格项 / 评分项 / 废标项 / 格式要求
  → 生成响应矩阵
  → 人工确认响应矩阵
  → 生成商务标 / 技术标目录
  → 检索企业知识库素材
  → 绑定素材与招标条款
  → 并行生成章节
  → 合规检查
  → 人工确认初稿
  → 导出 DOCX / PDF

Phase B：编辑器副驾驶
用户在编辑器中选中内容或章节
  → AI 提出 Diff Proposal
  → 用户接受 / 拒绝
  → 写回 BidDoc
  → 重新合规检查 / 导出
```

关键原则：

- intake 和 generate 是两个独立子图；
- intake 输出 `requirement_matrix`，必须人审确认后才能启动 generate；
- 所有改文档操作必须先生成 Diff Proposal；
- 用户 accept 后才能写入 `BidDoc`；
- Phase A 不应变成自由 AgentLoop；
- Phase B 的唯一真 Agent 是编辑器副驾驶；
- 新能力应 clean-room 落到 `rag_agent/src/bid/` 和 `fhapp/src/renderer/src/bid/`。

---

## 3. 建议 Workflow 节点

| 节点 | 类型 | 输入 | 输出 | 是否可自动执行 |
|---|---|---|---|---|
| `upload_tender_file` | Tool Node | PDF / DOC / DOCX | tender_file | 是 |
| `parse_tender_document` | Tool Node | tender_file | parsed_tender_document | 是 |
| `extract_requirement_items` | Agent + Validator Node | parsed_tender_document | requirement_items | 是，但需证据 |
| `extract_scoring_items` | Agent + Validator Node | parsed_tender_document | scoring_items | 是，但需证据 |
| `extract_rejection_items` | Agent + Validator Node | parsed_tender_document | rejection_items | 是，但需证据 |
| `build_requirement_matrix` | Agent Node | requirement_items / scoring_items / rejection_items | requirement_matrix | 是 |
| `confirm_requirement_matrix` | Human Review Node | requirement_matrix | confirmed_requirement_matrix | 否，必须人工确认 |
| `plan_bid_outline` | Agent Node | confirmed_requirement_matrix | bid_outline | 否，建议人工确认 |
| `retrieve_enterprise_materials` | RAG / Graph Node | bid_outline / requirement_matrix | material_candidates | 是 |
| `link_materials_to_requirements` | Agent + Validator Node | material_candidates | material_match_report | 是，但需证据和置信度 |
| `generate_business_volume` | Agent Group | confirmed matrix / materials | business_sections | 可自动草稿 |
| `generate_technical_volume` | Agent Group | confirmed matrix / materials | technical_sections | 可自动草稿 |
| `merge_bid_document` | Tool Node | sections | BidDoc | 是 |
| `run_compliance_check` | Validator Node | BidDoc / matrix / tender | compliance_report | 是 |
| `human_review_bid_draft` | Human Review Node | BidDoc / compliance_report | reviewed_bid_doc | 否，必须人工确认 |
| `export_docx_pdf` | Tool Node | reviewed_bid_doc | export_package | 否，导出前确认 |

---

## 4. APD 验证维度

| 验证维度 | APD 当前能力 | C 方案验证结果 | 结论 |
|---|---|---|---|
| Dynamic Workflow | 已支持 nodes / edges / parallel_groups / human_review_points / failure_strategy / artifacts | 可以表达 C 方案主流程 | 通过 |
| Artifact Model | 已支持文档、报告、DOCX、图谱、代码等产物模型 | 可以描述 parsed tender、matrix、outline、BidDoc、export package | 通过 |
| Permission Policy | 已支持自动、确认、禁止、角色规则 | 可以表达 matrix 确认、初稿确认、导出确认、Diff accept | 通过 |
| Error Recovery | 已支持重试、追问、回滚、降级、转人工 | 可以表达解析失败、素材不足、合规失败、导出失败 | 通过 |
| Eval Cases | 已支持意图、绑定、校验、记忆、工具、权限、恢复、工作流用例 | 可以沉淀废标项遗漏、素材错配、导出失败等回归样本 | 通过 |
| Knowledge/RAG Policy | 有 schema，但还未形成完整产品化能力 | 招投标强依赖企业素材库、条款证据、引用追踪、图谱关系 | 暴露缺口 |
| Runtime | 已有预览运行和生成 Demo，但还不是真实 DAG Runtime | C 方案需要节点级运行、暂停、恢复、产物版本和人工确认 | 暴露缺口 |

---

## 5. 必须建模的产物

| 产物 | 说明 | 是否版本化 | 是否需要来源追踪 | 是否需要人工确认 |
|---|---|---:|---:|---:|
| `tender_file` | 用户上传的招标文件 | 是 | 是 | 否 |
| `parsed_tender_document` | 代码解析后的结构化文本、章节、表格 | 是 | 是 | 否 |
| `requirement_items` | 资格、技术、商务、格式、废标条款 | 是 | 是 | 建议抽样确认 |
| `requirement_matrix` | 招标要求 ↔ 响应位置 ↔ 候选素材 | 是 | 是 | 必须确认 |
| `bid_outline` | 商务标 / 技术标目录 | 是 | 是 | 必须确认 |
| `material_match_report` | 素材候选、置信度、证据、缺口 | 是 | 是 | 高风险项确认 |
| `BidDoc` | 可编辑投标文档结构 | 是 | 是 | 修改需确认 |
| `diff_proposal` | 编辑器副驾驶提出的修改建议 | 是 | 是 | 必须 accept 后写入 |
| `compliance_report` | 覆盖率、废标项、格式、幻觉、素材缺口检查 | 是 | 是 | 必须确认 |
| `export_package` | DOCX / PDF / 附件包 | 是 | 是 | 导出前确认 |

---

## 6. 必须建模的知识库 / 图谱能力

招投标场景验证出：APD 后续不能只停留在“是否需要 RAG”，必须明确检索策略和证据策略。

需要建模：

- 企业资质库：证书、荣誉、人员、案例、财务、合同、截图、PDF 附件；
- 条款证据：每个响应点必须能回溯到招标文件位置；
- 素材证据：每个企业素材必须能回溯到知识库文件、页码、段落或对象 ID；
- 图谱关系：项目案例 ↔ 行业 ↔ 技术能力 ↔ 资质 ↔ 人员 ↔ 证明文件；
- 置信度：素材匹配必须输出置信度和不足原因；
- 冲突处理：多个素材冲突时不能自动编造，必须列候选或转人工；
- 禁止项：不得虚构资质、案例、金额、人员、证书编号、客户名称。

这说明后续 APD 应把 `Knowledge/RAG Policy` 从 schema 升级为可导出、可预览、可评测的完整模块。

---

## 7. 必须人工确认的节点

| 人工确认点 | 原因 |
|---|---|
| `confirm_requirement_matrix` | 错过废标项或评分项会导致投标失败 |
| `confirm_bid_outline` | 目录决定响应覆盖率和评审结构 |
| `confirm_material_selection` | 错用素材、虚构资质、错引案例风险高 |
| `accept_diff_proposal` | 编辑器副驾驶不能直接改文档 |
| `confirm_compliance_report` | 合规报告可能有误判，需要人工判断 |
| `confirm_export_package` | 导出即交付，必须确认版本和格式 |

---

## 8. 第 17 项验证结论

第 17 项通过。

招投标 Agent 证明 APD 的 Dynamic Workflow 方向是必要的：

```text
单 Agent 不够
自由 agent_loop 不够
只设计 operation 不够
必须设计 workflow + artifact + knowledge + permission + recovery + eval
```

同时它暴露出 APD 的下一阶段重点：

1. `Knowledge/RAG Policy` 需要补齐为完整模块；
2. `Agent Runtime` 需要从预览运行升级为节点级 workflow runtime；
3. 产物版本、人工确认、导出确认要进入 runtime，而不是只停留在协议文档；
4. 招投标是 APD 从“设计器/生成器”走向“Runtime 平台”的关键验证场景。

因此第 18 项应进入：

```text
Agent Runtime 雏形：生成的 Harness Demo 可在 APD 内运行，并逐步支持 workflow 节点级执行、暂停、恢复、人工确认和产物追踪。
```
