# 节点与 XML 契约 v1

本文约定节点内容、展开格式、XML 结构和确定性校验。工作方式见 [设计说明](design.md)，修改入口见 [MCP 契约](mcp-contract.md)。

## 1. 内容边界

- 每个业务项目使用 `.hsdd/story.xml` 保存正式内容；XML 是网页与 MCP 共用的数据源。
- 一项需求对应一个需求容器。容器拥有目标、范围、当前摘要、工作节点和最终完成条件；“需求分析”只是其中一种工作节点。
- XML 保存事实、计划、依据与人的记录。节点坐标、缩放、展开和选中状态属于阅读偏好，不写入正式内容。
- 节点 ID、需求 ID 和记录 ID 是稳定标识，不以排列位置或显示编号代替，不因重新排版而改变。
- 文档表示当前方向及已发生的事实，不执行工作，也不授予修改业务项目的权限。

## 2. 固定展开格式

节点详情保持下列顺序；同类节点使用相同字段和标签。

| 顺序 | 展示内容 | 数据来源 |
| --- | --- | --- |
| 1 | 名称、类型、当前状态 | 节点身份及三个状态维度 |
| 2 | 当前情况 | `summary`，简要说明结果和主要差距 |
| 3 | 本类内容 | `body` 的类型专属字段，顺序见下一节 |
| 4 | 下一步 | `next` 的行动及参与方 |
| 5 | 前提、后继、引用 | 从 `relations` 和需求层次生成，不另抄一份列表 |
| 6 | 依据 | `evidenceRef`、`basis` 引用的内容与适用范围 |
| 7 | 附件 | `attachmentRef` 引用的图片与 Markdown |
| 8 | 人的记录 | `humanInputRef`，以及目标为本节点的人工输入 |

缺少的可选字段隐藏，不改变其他字段顺序。必需但尚未形成的结果显示“尚未执行”“尚未形成结论”等状态，不用 AI 编造内容填满面板。正文允许段落、列表、强调、行内代码和链接；不允许原始 HTML、脚本、自定义样式或任意嵌套布局。每个正文 XML 字段只含文本，不含其他业务元素。

人的填写入口默认出现在 `analysis`、`decision`、`verification` 且 `method=manual`、`acceptance` 节点。页面填写的是意见或明确结论，由 AI 整合正文与结构；不开放任意 XML 编辑器或账号权限面板。

## 3. 十三类节点

`body` 只允许本类列出的三个字段，按表中顺序序列化。创建或进行中的节点至少具有“初始必要内容”；未产生的其他内容可以省略。`work=finished` 时三项专属内容均应非空，以明确结束时的实际结果、剩余差距或不适用原因。

| 类型 | 页面名称 | 专属字段及显示顺序 | 初始必要内容 |
| --- | --- | --- | --- |
| `analysis` | 需求分析 | `problem` 问题；`scope` 范围；`criteria` 验收依据 | `problem` |
| `investigation` | 调研 | `questions` 待回答问题；`findings` 调查发现；`unknowns` 未知项 | `questions` |
| `dependency` | 只读依赖 | `target` 引用对象；`capability` 所需能力；`verification` 核对情况 | `target`、`capability` |
| `assumption` | 假设 | `statement` 假设内容；`verification` 验证方法；`impact` 影响 | `statement` |
| `decision` | 决策 | `options` 候选方案；`choice` 当前选择；`rationale` 理由 | `options` |
| `design` | 设计 | `structure` 结构；`contracts` 接口与约定；`constraints` 约束 | `structure` |
| `implementation` | 实现 | `scope` 实现范围；`outputs` 产出；`remaining` 剩余工作 | `scope` |
| `integration` | 集成 | `inputs` 汇合对象；`contracts` 连接约定；`observations` 实际情况 | `inputs` |
| `verification` | 自动化测试／人工验证 | `checks` 检查项；`method` 方式；`observations` 执行情况 | `checks`、`method` |
| `review` | 评审 | `target` 评审对象；`findings` 发现；`disposition` 处理情况 | `target` |
| `acceptance` | 交付验收 | `criteria` 验收条件；`coverage` 覆盖情况；`unresolved` 未满足项 | `criteria` |
| `delivery` | 发布交付 | `deliverables` 交付内容；`execution` 操作记录；`handoff` 交接说明 | `deliverables` |
| `follow_up` | 后续事项 | `item` 待办内容；`reason` 暂缓原因；`revisit` 重启条件 | `item`、`reason` |

类型特例：

- `implementation` 可设置 `variant="fix"`，页面显示“问题修复”，仍使用实现格式；以 `references` 关联问题来源。
- `verification/body/method` 只允许 `automated` 或 `manual`。所有其他 `body` 字段为文本。
- `dependency` 的“只读”指本次业务开发引用已有能力，不禁止 AI 维护该节点。
- `decision` 需要区分尚在比较的候选方案与已经采用的选择；`choice` 尚未确定时可以省略。
- `follow_up` 不默认参与当前需求的完成条件。开始实施后，可以将其改为适合的工作类型，或建立新的需求／工作节点并保留来源关系。
- 改变类型必须一次替换 `body` 为新格式。保留身份的类型更正仍使用原 ID，记为实质变化；不要将不兼容的旧字段留在新节点内。

## 4. 状态、结果与有效性

### 4.1 三个独立维度

| 属性 | 值 | 含义 |
| --- | --- | --- |
| `work` | `planned` | 尚未开始 |
| | `active` | 正在推进 |
| | `blocked` | 有明确阻碍，需要说明原因和下一步 |
| | `finished` | 本轮工作已结束；是否达到目的由 `result` 表达 |
| | `deferred` | 暂缓，保留原因或重启条件 |
| | `cancelled` | 不再继续，保留取消原因 |
| `result` | `unknown` | 尚无结果 |
| | `satisfied` | 达到本节点声明的目的／条件 |
| | `unsatisfied` | 未达到条件，例如测试失败、假设不成立、验收需修改 |
| | `inconclusive` | 已经调查或执行，但不足以形成结论 |
| | `skipped` | 明确跳过，记录原因；不代表通过 |
| `validity` | `current` | 当前记录的适用范围未被已知变化否定；不代表已经验证 |
| | `needs_review` | 结论适用性需要复核，旧结果仍保留 |

新节点默认 `planned / unknown / current`。三个属性均显式写入 XML。不得把 `current` 显示为“验证通过”，或把 `finished` 显示为“验收通过”。

页面以工作类型和实际状态合成一句话，例如：

| 节点内容 | 页面状态 |
| --- | --- |
| 实现 `finished / satisfied / current` | 实现完成 |
| 验证 `finished / unsatisfied / current` | 测试失败／人工验证未满足 |
| 验证 `finished / satisfied / needs_review` | 曾通过 · 待复核 |
| 假设 `finished / inconclusive / current` | 尚无定论 |
| 验收 `planned / unknown / current` | 等待人的验收 |
| 后续事项 `deferred / unknown / current` | 后续事项 · 暂缓 |

未枚举的组合使用“工作状态 + 结果（若有）+ 待复核（若有）”表达，保留事实，不猜测通过与否。

### 4.2 完成依据

`finished` 必须有结束结论，不能仍是 `unknown`。`satisfied` 或 `unsatisfied` 的结束结论必须引用至少一条证据或人工记录；`inconclusive`、`skipped` 必须在专属内容中交代不足或跳过原因。引用存在仅证明结构完整，不能证明执行真实，事实核对由 AI 负责。

不同类型的成功含义固定如下：

- 需求分析：问题、范围和验收依据已经整理清楚；不代表已经获得实施授权。
- 调研／设计／实现／集成／交付：声明的工作产出已经完成，并有适用依据。
- 只读依赖：所需能力已经核对且可用。
- 假设：在声明范围内已经证实。
- 决策：选择明确且有来源；AI 在已有授权内作出的技术决定可以记录为 AI 决定，不要求一律人工审批。
- 验证：检查通过；执行结束但失败应为 `unsatisfied`。
- 评审：评审完成，声明范围内没有未处理的阻断项。
- 交付验收：已有明确接受结论，且引用对应的人工决定／验收输入。普通评论不能充当接受结论。
- 后续事项：保留事项本身已有明确处理结论；它仍不自动变成本期交付前提。

工作状态为 `blocked` 时应有 `next`，当前摘要说明阻碍；`deferred`、`cancelled` 应有暂缓或取消原因。此类说明缺失产生语义警告，不阻止记录事实。

验收通过必须显式引用同一节点最新、当前有效的网页通过记录。人工退回即时反映在页面和读取视图中。验收条件、所属需求、必要前提链及上游节点修订参与验收范围校验。范围改变后显示待复核。已有 XML 中未带结构化人工结论的历史通过也显示待复核，原结论和记录保留。

### 4.3 依赖与需求完成

一个节点满足依赖，当且仅当它为 `finished / satisfied / current`，且其成功含义符合本类约定。

整个需求的完成由 `completion/gate` 判断：所有 gate 及其沿 `requires` 回溯的必要前提均满足。gate 引用子需求时递归评估子需求。没有 gate 显示“完成条件未定义”；不按已结束节点数量计算需求是否交付。

上游未满足时仍允许保存下游实际发生的开发、测试和结果。服务返回“必要前提未满足”等警告；页面保留事实，不自动改写下游状态，也不据此宣布需求已交付。

## 5. XML 结构

使用 UTF-8 XML 1.0。根元素为 `story`，前七个一级区域必须存在，`attachments` 可选。按下列顺序保存。XML 声明可有可无，禁用 DTD 与外部实体。未知的 `schemaVersion` 拒绝写入。

```text
story [schemaVersion, id, revision]
├─ project
│  ├─ title
│  ├─ goal
│  └─ summary（可选）
├─ requirements
│  └─ requirement [id, parent?] × N
│     ├─ title / goal / scope / summary
│     ├─ completion → gate [ref] × N
│     └─ nodes → node × N
├─ relations → edge [id, type, from, to] × N
├─ evidence → item × N
├─ humanInputs → input × N
├─ archive → retired × N
├─ changes → change × N
└─ attachments（可选）→ attachment [id, filename, mediaType, size, sha256] × N
```

### 5.1 标识、项目与需求

- `story/schemaVersion` 固定为 `1`；`story/revision` 为大于等于 1 的整数。
- `story/id`、需求、活动节点、归档节点、连线、证据、人工输入的 ID 在同一文件内唯一且非空。ID 区分大小写；退出当前图的 ID 不复用。
- `project/title`、`project/goal` 必填；`summary` 可选，是 AI 的阅读摘要，不另设进度字段。
- `requirement` 的 `title`、`goal`、`scope`、`summary` 必填且非空，`completion` 与 `nodes` 容器必须存在；初始规划中可以没有 gate 或节点，但应显示相应未定义状态。
- 所有需求平铺在 `requirements` 下；`parent` 若存在，必须指向另一个现存需求。父子关系无环，子需求不能引用自身为父需求。
- `completion/gate/ref` 可指本需求内活动节点、后代需求或后代需求内节点，不能指归档对象、父需求或无关需求。
- 需求可通过关系引用其他需求的成果；外部依赖不必复制到当前需求中。

### 5.2 节点

节点属性 `id`、`type`、`revision`、`work`、`result`、`validity` 必填。`revision` 为大于等于 1 的整数；`variant` 只支持实现节点的 `fix`。

节点子元素顺序固定：

```text
title（必填）
summary（必填）
body（必填，字段由 type 决定）
next [actor]（可选）
basis [ref, revision] × N
evidenceRef [ref] × N
humanInputRef [ref] × N
attachmentRef [ref] × N
```

- `title` 和 `summary` 非空。`next/actor` 为 `ai`、`human` 或 `external`，正文是一条明确行动。
- `basis` 记录当前结论明确依赖的节点及其修订号，不替代图上的关系。它可指活动或归档节点；归档来源仅供追溯，是否仍适用由当前结论说明。
- `basis/revision` 必须为正整数且不大于被引用节点的修订号。旧号是合法历史依据，不作为 XML 错误；其适用性需要标示。
- `evidenceRef`、`humanInputRef` 引用全局记录，避免在多个节点复制原文。来自旧节点的记录可被新节点引用，但其原始目标与来源不变。
- `attachmentRef` 指向全局附件信息。实际内容保存在 `.hsdd/attachments/<sha256>`，`size` 以字节计。旧 XML 没有附件区和引用时按空列表读取。归档节点继续持有其引用，合并或拆分时应在新节点中明确指定附件去向。仅添加附件增加项目修订号，不增加节点工程修订号。

### 5.3 连线

`relations/edge` 的四个属性均必填。端点可以是活动节点或需求；归档对象不参与当前连线。

| `type` | 方向和含义 | 校验 |
| --- | --- | --- |
| `requires` | 前置 → 后继；按当前计划需要前提成立 | 不允许自环和有向循环 |
| `references` | 依据／来源 → 使用者；不影响可推进性 | 端点存在，不允许完全重复的同类关系 |
| `follow_up` | 当前工作 → 后续事项；表达未来演进 | 不参与本期完成计算 |

父子归属不会自动生成 `requires`。并行关系由必要前提自然推导，不保存独立的并行状态。需求作为端点时使用其完成条件参与依赖评估；校验必须同时展开需求 gate，拒绝由 gate 和 `requires` 组合形成的隐含循环。

### 5.4 证据

每条 `evidence/item` 必填属性：`id`、`kind`、`target`、`subject`、`subjectRevision`。正文为非空文本，说明核对范围、实际观察或执行结果。

- `kind` 为 `code`、`test`、`document`、`observation`、`decision`。
- `target` 可以是项目相对路径、文档／运行记录标识或 URI；它是引用，不要求服务自动读取或运行它。
- `subject` 指活动或归档节点；`subjectRevision` 指证据针对的该节点修订号，为正整数且不超过其现存修订号。
- 测试证据正文说明实际执行的方法或命令、范围与结果；未运行的检查不得登记成已通过的测试证据。大日志保存在外部产物中，本文件只保留必要摘要与引用。
- `basis` 与 `subjectRevision` 保留验证所针对的版本；不能仅修改版本号让旧证据看起来重新有效。
- 示例文件中的虚构依据统一使用 `simulation:` URI，并在项目概览明确标注模拟数据。示例通过结构检查，不代表其业务场景实际执行过。

### 5.5 人的输入

每条 `humanInputs/input` 属性：

| 属性 | 约定 |
| --- | --- |
| `id` | 唯一且稳定 |
| `target` | 原始目标节点或需求，可在归档后继续保留 |
| `kind` | `comment`、`answer`、`decision`、`acceptance` |
| `source` | 固定为 `human`，表达原始内容来自人 |
| `recorder` | `human` 或 `ai`，表达由谁登记 |
| `channel` | `web` 或 `chat` |
| `handling` | `pending`、`incorporated`、`needs_clarification` |
| `createdAt` | 可选，提供时使用带时区的 ISO 8601 时间 |
| `outcome` | 网页验收结论为 `accepted` 或 `rejected`，历史及聊天文本可省略 |
| `reviewScope` | 服务为结构化结论保存的验收范围指纹 |

正文保存非空原文。只有 `handling` 可由后续 AI 更新；原始正文、来源、登记者、渠道和目标不能被改写。人的补充或更正创建新输入，由节点引用并说明采用关系，旧记录保留。

AI 记录聊天中的人类决定时使用 `source="human" recorder="ai" channel="chat"`，仅记录真实存在的原话；AI 自己的归纳写在节点正文中。Agent 通过 `story_guide` 查询人工记录工作流，保存聊天原话前应核对来源。

`incorporated` 表示已处理并同步，不能单凭它推断普通评论已被采纳。具体采纳、暂缓或未采用的理由体现在相关节点中。只有网页提交的 `kind="acceptance"` 结构化结论参与验收判断。聊天和普通意见可以作为上下文。

### 5.6 归档与变化记录

退出当前图且仍有追溯价值的节点保存为：

```xml
<retired requirement="R01" reason="拆分为接口与界面实现" atRevision="8">
  <node><!-- 退出时的完整节点内容，保留原 ID 和修订号 --></node>
  <replacement ref="D02"/>
  <replacement ref="D03"/>
</retired>
```

`requirement`、`reason`、`atRevision` 必填。`replacement` 可有多条或没有；引用活动或后续归档节点，不能形成替代循环。活动区与归档区不能同时存在相同节点 ID。归档记录不进入当前需求的完成条件。

`changes/change` 必填 `revision`、`actor`、`reason`，内含一个或多个 `affected ref="..."`。`actor` 为 `ai` 或 `human`；可引用受影响的需求、节点、连线、证据或输入 ID。变化记录是简短事实，不存整段聊天或重复快照；本次删除的 ID 可以出现在变化记录中。

第一版删除节点统一移入归档，变化记录保留 ID 与原因，不提供永久删除历史的入口。合并、拆分的旧节点也必须归档，并记录新节点对应关系。

## 6. 修订与结构调整

### 6.1 两层修订

- **项目 `revision`**：每次确实改变 XML 的成功批量保存递增一次。没有变化不递增，也不制造变化记录。
- MCP 的 `readToken` 用于识别读取时的文件快照，防止外部编辑未增加 `revision` 时被覆盖；它不是 XML 正式字段，具体传输约定见 MCP 契约。
- **节点 `revision`**：创建时为 1；影响目的、范围、实际行为、结论或依据适用性的实质变更递增。正文措辞整理、阅读布局、人工记录处理标记变化不单独改变节点工程修订号。
- 服务因上游变化自动标记 `needs_review` 时，只改变项目版本，不递增被标记节点的工程修订号；旧结论和对应证据仍指向原来的工作。避免一次自动标记触发无依据的全图级联。
- 单批多次改同一节点，项目和该节点均只按最终变化递增一次。记录最终修订号后再校验证据与 `basis` 的引用。
- 修改请求区分文字整理与实质变更，并附理由。字段和关系决定了明显实质影响时，服务不能接受“文字整理”的声明规避修订。

### 6.2 依据失效

节点修订后，当前节点通过 `basis` 直接引用旧修订，或通过 `evidenceRef` 使用针对旧修订的证据时，该节点结论需要复核。服务保留旧结论，将明确受影响的结论标示为 `needs_review`，并返回受影响节点。没有直接版本依据的潜在影响由 AI 核对，不对整个项目一律级联失效。

节点内的 `basis` 和 `evidenceRef` 是当前结论采用的依据集合。新的执行结果形成后，AI 更新这些引用；旧证据仍留在全局 `evidence` 中供历史追溯。仅有一条未被当前结论引用的旧证据，不会使节点永远处于待复核状态。

同一批次内若 AI 已重新核对并提交新的依据及版本，可维持或恢复 `current`；只改有效性标签而不交代复核依据，不足以恢复一个已结束的有效成功结论。

业务代码在其他工具中发生变化时，不会天然改动 XML。AI 必须核对相关代码、更新记录或标为待复核，不能把文件未变化当成实现仍然一致。

### 6.3 纠正、删除、合并、拆分

- 纠正保留 ID，按变化性质处理修订。
- 合并／拆分创建新节点 ID；旧节点归档，保留人工输入、证据和替代关系。
- 操作批次必须明确原连线的新去向；不能留下引用已退出对象的活动连线或完成 gate。
- 新节点引用旧记录用于追溯时保留原 `target` 和原文；记录可以被多个节点引用，无须复制。
- 新节点不自动继承旧节点的 `satisfied / current`。若结论仍然适用，AI 必须明确给出新范围内的依据。
- 修改图的结构不会扩大需求范围或开发授权；新增建议保持建议，人的有效决定保留来源。

## 7. 保存校验与语义提示

### 拒绝整个修改批次

- XML 无法解析、未知 schema 版本、未知节点类型／字段／枚举、必需内容缺失。
- ID 冲突、引用不存在、未来修订引用、非法类型字段或修复变体。
- 需求父子环、`requires` 环、需求完成展开后的隐含循环、替代循环。
- 删除／合并／拆分后仍有悬空活动连线、gate 或不可解析的记录引用。
- 篡改人的原始输入，或合并／拆分丢失需保留的人工记录与来源。
- `finished` 没有结束结论，成功／失败结论没有任何依据引用，或者提交验收通过时未引用当前有效的网页通过记录。
- 基础版本已过期、外部文件变化未重新读取、保存未成功。

### 保存事实，同时提示

- 上游未满足，下游已经开始或记录完成。
- 存在失败、跳过、尚无定论或待复核结果。
- 需求没有完成 gate；摘要称已交付但完成条件不满足。
- 受阻、延期或取消缺少足够原因／下一步。
- 普通意见被处理，但相关决定与实施记录之间仍有差距。
- 外部代码或证据是否仍适用尚未核对。

格式与引用校验保证记录可解释，无法代替对真实代码、实际测试和人类意图的核对。页面展示警告，AI 负责理解并维护，工具不自动执行开发或改写工程事实。
