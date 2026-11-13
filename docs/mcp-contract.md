# MCP 与共享读写契约

本文件定义第一版接口。服务在本机提供 Streamable HTTP 端点 /mcp，启动方法见 [README](../README.md)。

节点格式、字段顺序和状态含义见 [节点契约](node-contract.md)。这些 MCP 工具服务于开发方向图；编码、运行测试和部署仍使用 Agent 原有工具。

## 1. 一个内容源，两个入口

```text
AI → MCP 工具 ────┐
                  ├→ 同一套读写、校验与保存逻辑 → .hsdd/story.xml
人 → 本地网页 ────┘
```

一个服务实例绑定一个业务工作区。项目文件位置固定为该工作区的 `.hsdd/story.xml`；请求使用项目 ID，不传任意文件路径。配置工作区属于启动配置，不属于节点修改操作。

网页通过本地接口调用同一读写逻辑，不能另外保存正式节点内容。布局、展开和选中状态属于阅读偏好，不写入正式 XML。首次进入时读取最新数据，保存成功后按返回版本刷新受影响区域。

职责如下：

| 部分 | 负责 |
| --- | --- |
| Skill | 理解需求、查阅代码、判断事实、拆分工作、整理叙述、确定何时读写图 |
| MCP | 将读写能力提供给 AI，接收结构化参数并报告实际结果 |
| 本地服务 | 校验结构与格式，处理批量修改和版本冲突，保存项目文件 |
| 网页 | 固定展示，局部展开，提交人的意见与明确结论 |
| XML | 保存正式内容、依据、人工记录和必要历史 |

工具不会自行推断需求、采用建议、执行开发步骤或调度节点。依赖未满足时仍允许记录真实的执行和失败情况；其中的矛盾作为提示返回。

## 2. 公共数据形状

以下示例为接口中的 JSON 对象，服务负责将它们转换为固定 XML。调用者不提交 XML 字符串、XPath、任意属性或页面 HTML。

节点对象使用以下字段：

```json
{
  "id": "D01",
  "type": "implementation",
  "title": "实现导出接口",
  "summary": "正在补齐空结果处理。",
  "work": "active",
  "result": "unknown",
  "validity": "current",
  "body": {
    "scope": "导出当前筛选范围内的记录",
    "outputs": "CSV 导出接口",
    "remaining": "空结果处理"
  },
  "next": { "actor": "ai", "text": "补齐空结果处理并运行相关测试" },
  "basis": [],
  "evidenceRefs": [],
  "humanInputRefs": []
}
```

- `body` 的键只能来自节点类型契约，值为文本或有限 Markdown。验证节点的 `body.method` 只接受 `automated` 或 `manual`。
- `variant` 可选，目前仅允许实现节点使用 `fix`。
- `basis` 元素为 `{ "ref": "节点ID", "revision": 2 }`；证据和人工记录引用数组只保存 ID。
- `next` 可省略；存在时 `actor` 为 `ai`、`human` 或 `external`。
- 节点创建时修订号由服务设为 `1`；后续修改由服务管理，调用者不能直接设置修订号。
- 需求对象使用 `id、parent（可选）、title、goal、scope、summary、completion`。`completion` 是完成节点或子需求 ID 的数组，映射为 XML 的 `gate`。工作节点通过单独的节点操作添加。
- 关系对象为 `{ "id", "type", "from", "to" }`，类型限于 `requires、references、follow_up`。
- 证据对象为 `{ "id", "kind", "target", "subject", "subjectRevision", "text" }`。`kind` 为 `code、test、document、observation、decision`。
- 人的记录对象为 `{ "id", "target", "kind", "text", "source", "recorder", "channel", "handling", "createdAt" }`，其中 `createdAt` 可选，提供时为带时区的 ISO 8601 时间。原始内容和来源提交后保留，处理状态单独维护。

未发生的结果可以省略相应正文，但不能以空白记录冒充已经执行。各阶段的必填条件以节点契约为准。

## 3. MCP 工具

### `story_schema`

读取规则，供新会话或规则版本变化时使用。

输入：

```json
{ "schemaVersion": "1", "types": ["verification"], "operations": ["node.update"] }
```

`schemaVersion` 默认当前支持的版本。无筛选时返回完整契约。单传 `types` 只返回节点规则及公共约定，单传 `operations` 只返回选定操作的 JSON Schema。两个筛选可以一起使用。工具定义直接列出写入对象的字段和枚举，按类型和阶段必填的正文仍以节点契约为准。

### `story_read`

按需要读取内容，默认获取概览。

| 参数 | 约定 |
| --- | --- |
| `projectId` | 可省略以定位服务绑定工作区的项目；提供时必须与文件中的 ID 一致 |
| `view` | `overview`（默认）、`requirement`、`nodes`、`changes`、`search` |
| `requirementId` | `requirement` 视图必填。`search` 中可用于限定该需求及其子需求 |
| `nodeIds` | `nodes` 视图必填，返回指定节点及直接关联对象的简短摘要 |
| `nodeRef` | 网页复制的节点引用。单独传入这一项，返回该节点的完整详情 |
| `sinceRevision`、`sinceReadToken` | `changes` 视图必填，表示上次读取的快照 |
| `query` | `search` 可选，关键词最多 200 字符，按空白拆分，各词均需匹配 |
| `type`、`work`、`result`、`validity` | `search` 可选，按类型及页面显示使用的状态过滤 |
| `limit`、`offset` | `search` 可选，默认 20 条，最多 50 条，偏移从 0 开始 |

所有成功读取均返回 `projectId、schemaVersion、revision、readToken、view、data、warnings`。`readToken` 是由文件实际内容生成的不透明快照标识；调用者原样传回，不解析、不写入 XML。它不是鉴权凭证。

视图内容：

| 视图 | 数据 |
| --- | --- |
| `overview` | 项目目标、需求树摘要、完成条件是否定义、阻碍、待人回应事项、未处理人工输入的 ID 与短摘要 |
| `requirement` | 需求正文、当前层节点摘要、关系、完成条件、外部引用摘要；不自动展开所有子需求 |
| `nodes` | 指定节点全部固定字段、直接依赖、证据、人工记录和必要来源；归档节点可按 ID 读取并明确标记 |
| `changes` | 变化原因、受影响 ID、退出当前图的 ID、当前相关对象和待处理输入 |
| `search` | 匹配的当前节点摘要、`total`、`nextOffset`。搜索 ID、标题、摘要和正文，不区分大小写。相同快照下按 ID 精确匹配、标题、摘要优先排序 |

摘要视图默认不含归档正文、历史全文和大量证据原文。没有节点完成百分比；需求是否完成按指定完成条件推导。

节点详情包含 `nodeRef`。格式为 `hsdd-story:v1:<工作区指纹>:<项目ID>:<节点ID>`，编号用百分号编码。服务校验工作区和项目后按节点 ID 读取。原节点归档后仍可通过引用读取归档内容及替代关系。工作区指纹根据当前目录生成，迁移工作区后使用新引用。

增量读取必须能够证明变化连续。若历史不完整、服务重启后无法确认快照、出现未记录的外部编辑，返回 `refreshRequired=true` 及原因，要求重读概览和相关子图。只有版本与快照都一致时才返回“没有变化”，不能仅凭相同版本号判断。

工作区尚无文件时，读取返回 `exists=false、revision=0、readToken="absent"`，不自动创建项目。XML 无法解析或不符合已支持的格式时返回读取错误，保留原文件，不用空项目替代。

### `story_read_attachment`

从节点详情取得附件 ID，再调用 `{"attachmentId":"ATT-01"}`。Markdown 原文在 `structuredContent.text`，可传 `offset`、`limit` 按 Unicode 字符分页，默认 6000 字符，最多 20000 字符，`nextOffset=null` 表示读完。图片通过 MCP 的 `image` 内容块返回原图，`structuredContent.attachment` 提供元数据。图片无需分页参数。附件文件读取时校验大小与 SHA-256。

### `story_apply`

在一个批次中提交相关变化。输入：

```json
{
  "projectId": "P01",
  "baseRevision": 7,
  "readToken": "opaque-token-from-story-read",
  "reason": "补齐导出边界处理，记录待运行的回归检查",
  "operations": [
    {
      "op": "node.update",
      "id": "D01",
      "changeKind": "substantive",
      "set": {
        "summary": "空结果处理已补齐，尚未运行回归检查。",
        "body.remaining": "执行回归检查",
        "next": { "actor": "ai", "text": "运行中文与空结果用例" }
      }
    }
  ]
}
```

`baseRevision` 和 `readToken` 都必须来自最近一次读取。版本比较防止普通并发覆盖；快照比较同时捕获“外部改了 XML 却没增加 revision”的情况。

工具成功返回：

```json
{
  "ok": true,
  "projectId": "P01",
  "changed": true,
  "previousRevision": 7,
  "revision": 8,
  "readToken": "new-opaque-token",
  "changedIds": ["D01"],
  "retiredIds": [],
  "reviewRequiredIds": [],
  "warnings": []
}
```

无内容差异时返回 `changed=false`，保持原版本、原文件和快照，不增加变化记录。文字整理产生实际文本变化时仍保存并增加项目版本，只是不增加节点工程修订号。保存成功才返回新版本；校验失败或写入失败不返回虚假的成功结果。

## 4. 修改操作

`set` 只允许指定契约字段；使用 `removeFields` 删除可选字段。字段路径限于列出的公共字段及 `body.<固定字段>`，不支持自由 XPath。数组字段使用完整的新数组，避免对数组下标产生歧义。

| `op` | 必要参数与行为 |
| --- | --- |
| `project.create` | `project`（含 ID、名称、目标、摘要）；仅文件不存在且 `baseRevision=0/readToken=absent` 时可用，创建完整根结构 |
| `project.update` | `set`；可改名称、目标、摘要，不可改项目 ID 或格式版本 |
| `requirement.create` | `requirement`；创建需求或通过 `parent` 创建子需求 |
| `requirement.update` | `id、set、removeFields（可选）`；允许更新范围、父子归属与完成条件，保存前检查最终结构 |
| `node.create` | `requirementId、node`；新 ID、新修订号 |
| `node.update` | `id、changeKind、set、removeFields（可选）`；更改 `type` 时必须提供完整新 `body`，保留必要依据和记录 |
| `node.remove` | `id`；退出当前图并归档，原关联必须在同批中明确移除或重连 |
| `node.merge` | `sourceIds、target`；至少两个源节点，`target={requirementId,node}` 且使用新 ID |
| `node.split` | `sourceId、targets`；至少两个新目标，各为 `{requirementId,node}` |
| `relation.create` | `relation`；新增有固定 ID 的关系 |
| `relation.update` | `id、set`；修改类型或两端，重新检查最终图 |
| `relation.remove` | `id`；移除关系，变化记录保存原因和相关对象 |
| `evidence.add` | `evidence`；追加证据记录，不改写既有执行证据；纠错采用新证据并调整节点引用 |
| `input.add` | `input`；提交人的原始输入及来源 |
| `input.handle` | `id、handling`；仅更新 `pending/incorporated/needs_clarification`，处理理由写入本次变化原因 |
| `attachment.add` | `nodeId、attachment`；保存图片或 Markdown 并链接到当前节点 |

一个批次按操作顺序作用于内存副本，在全部操作结束后检查完整性；所以可先创建节点，再在同批添加证据和连线。操作目标不存在、参数不合法等局部错误立即使整个批次失败。重复执行请求不会跳过冲突检查：若上次保存成功而响应丢失，客户端先读取，再判断是否仍需修改。

### 节点附件

把操作放在 `story_apply` 的 `operations` 中。

```json
{
  "op": "attachment.add",
  "nodeId": "AC01",
  "attachment": {
    "id": "ATT-01",
    "filename": "验收清单.md",
    "text": "# 验收清单\n\n- 检查中文 CSV\n- 检查空结果"
  }
}
```

`id` 使用字母、数字、点、下划线或连字符，以字母或数字开头。`filename` 保留扩展名。来源从 `sourcePath`、`text`（仅 Markdown）和 `dataBase64` 中选一项。`sourcePath` 只能读取绑定工作区内的源文件，例如 `docs/export.png`。Markdown 最大 1 MiB，PNG、JPEG、GIF、WebP 最大 8 MiB，单批内容上限 32 MiB。

`story_read` 的节点视图返回 `attachmentRefs` 及 `attachments`。后者包含类型、文件大小、校验值和相对于 MCP 服务所在地址的预览与下载路径。相同内容复用同一个文件；XML 保存失败时撤回本批新增文件。使用节点补丁更新 `attachmentRefs` 可调整关联，归档与其他节点保留的引用继续可读。

### 合并与拆分

新节点的内容、状态、证据引用和人工记录引用必须显式提供，不从旧节点自动复制“已完成”或“已通过”。是否仍适用由 AI 核对并提供依据，结构工具不替 AI 作结论。

源节点使用以下归档结构保留最后状态和替代关系：

```xml
<retired requirement="R01" reason="拆分接口与前端工作" atRevision="8">
  <!-- 原 node 的完整内容 -->
  <replacement ref="D02"/>
  <replacement ref="D03"/>
</retired>
```

原有连线、需求完成条件必须在同批内明确调整，禁止服务根据标题猜测。源节点关联的人工记录至少在一个新节点中保留引用；拆分后可让多个新节点引用同一原始记录。人工记录的原 `target` 不改写，它仍可指向归档源节点。关联历史依据按节点契约处理，不因结构整理而删除。

没有替代节点的删除也会归档。原 ID 保留，节点退出当前图。第一版不提供永久删除历史和人工原文的接口。

### 人的输入来源

网页提交：服务写入 `source=human、recorder=human、channel=web`。MCP 不能伪造网页提交。

聊天中的人工意见通过 `input.add` 保存原话，服务标记 `source=human、recorder=ai、channel=chat`。正式验收结论由人在网页选择「通过」或「退回」。AI 写入验收成功时在 `humanInputRefs` 引用 `display.acceptance.inputId`，仅最新且仍适用于当前范围的通过可用。AI 可通过 `input.handle` 更新处理状态。

所有人的输入初始为 `pending`，工具不自动把意见转换为有效决定。AI 整合明确结论后再调用 `input.handle`。标记已整合不代表删除输入，也不抹去其中的异议。

## 5. 校验、警告与状态变化

硬校验只处理能确定的结构或格式错误：

- 格式版本、类型、字段、枚举和阶段性必填条件符合契约。
- 活跃与归档节点 ID 不重复且不复用；引用存在，引用种类正确。
- 父子需求无环，`requires` 无环；包含需求完成条件的依赖展开也不能产生循环。
- 当前图不能依赖归档节点继续推进；归档仅用于证据、人工记录与历史引用。
- 合并、拆分后无悬空连线，必要记录已显式保留。
- 人的来源及原文没有被修改；只读依赖节点并非不可编辑节点。

以下情况保存事实后返回语义提示，不阻止保存：上游未满足但下游已经执行、完成条件尚未定义、失败或跳过的验证仍被当作前提、存在待处理人工意见、发现可能过期的代码依据。

实质修改使用 `changeKind=substantive`，相应节点修订号在本批中最多增加一次。措辞整理使用 `editorial`，不改变工程结论的适用版本。调用者主动改变类型、工程结果、有效性或工程依据必须作为实质修改；正文是否改变工程含义由 AI 判断并写明原因。仅整理人工记录的展示引用不改变节点工程修订号。工具不会把一切文本修改都解释成需求变更。

当前 `basis` 或 `evidenceRefs` 明确引用的依据版本变旧后，服务将使用旧依据的结论标为 `needs_review`，保留原结果和证据，并返回受影响节点。自动标记不增加被标记节点的工程修订号，避免因标记动作继续制造全图级联。泛化的下游影响只作提示，由 AI 决定是否复核。代码实际变化是否影响结论仍需要 AI 核对；服务不扫描整个项目后承诺事实一致。

`basis` 与 `evidenceRefs` 表示当前采用的依据集合。复测后更新此集合，旧证据继续留在全局记录中供追溯，不因历史证据的旧版本而持续阻止恢复有效状态。

一批修改中可同时更新依据和复核结论；以批次的最终状态判断是否过期。自动标记的变化纳入同一次保存与返回结果，不能在返回成功后再静默写第二次文件。

## 6. 并发与可靠保存

一次保存按以下顺序执行：

1. 取得工作区写入互斥，网页和 MCP 进入同一串行写入入口。
2. 重新读取磁盘文件，检查格式，并比较 `baseRevision` 与 `readToken`。
3. 在内存副本应用全部操作，进行完整性校验和明确依据的过期检查。
4. 无内容差异则返回未变化；否则项目版本增加一次，生成简短变化记录。记录的 `actor` 由实际入口决定：MCP 为 `ai`，网页人工提交为 `human`，不接受调用者冒充另一入口。
5. 将完整结果写入同目录临时文件，确认写入成功，再以原子替换方式替换正式文件。
6. 文件替换成功后更新服务快照，返回新版本，通知网页重读受影响区域。

只允许一个服务管理同一工作区；启动时已被另一实例占用则报告冲突。普通编辑器不一定遵守服务锁，保存前还需再次核对磁盘内容。发现外部修改时中止本次提交、重读并要求客户端刷新，不拿旧内存覆盖它。

原子替换解决半份文件，不保证普通编辑器与服务能无限制同时保存。支持的协作入口为网页与 MCP；使用外部编辑器修改正式 XML 时，先暂停服务写入，完成后重新读取。服务无法消除不遵守同一锁的外部程序在最终检查与替换之间竞争的风险。

外部文件合法但版本未增加时，读取返回新快照并要求旧客户端刷新；不为了“修复版本”自行改写外部文件。外部 XML 非法时报告错误并保留原文件，禁止自动还原、清空或覆盖。

## 7. 错误返回与验收点

MCP 的工具错误同时返回 `isError=true` 和结构化的 `ok=false、code、message、details`。`details` 包含具体操作下标、对象 ID 或字段路径；冲突可附当前版本，不能把本次请求标成已保存。

| 错误码 | 含义与后续动作 |
| --- | --- |
| `PROJECT_NOT_FOUND / PROJECT_MISMATCH` | 未创建项目或 ID 不匹配；定位正确项目 |
| `UNSUPPORTED_SCHEMA / INVALID_DOCUMENT` | 文件版本不支持或文件无效；保留文件并报告具体问题 |
| `INVALID_OPERATION / INVALID_STRUCTURE` | 参数、字段、引用或图结构不合法；修正整批操作后重试 |
| `REVISION_CONFLICT` | 版本或快照已变化。根据冲突详情重读，再重新组织提交 |
| `ACCEPTANCE_REQUIRED / INVALID_ACCEPTANCE_INPUT` | 验收成功缺少当前有效的人工通过，或试图从其他入口提交结构化验收结论 |
| `WORKSPACE_BUSY` | 工作区已有写入者或服务实例；等待或使用已存在的服务 |
| `SAVE_FAILED` | 文件未成功保存；不得报告已同步 |

冲突详情返回 `currentRevision`、`targetIds` 和 `refreshRequired`。能确认历史连续时还返回 `changedIds`、`retiredIds` 和 `overlappingIds`。后者表示提交显式涉及的对象与变化的交集，业务相关依赖仍需核对。若 `refreshRequired=true`，重读概览和相关子图。每次重试都使用重新读取的版本与快照。

最低验收场景：

- 局部读取只返回所需节点与必要上下文，完整恢复工作不依赖聊天历史。
- AI 修改后网页读取到同一版本；人的输入能够被下一会话读取。
- 合并、拆分、删除含有人工记录的节点，原文仍可追溯，当前关系不悬空。
- 合法操作与非法操作在同一批时，整个批次均不保存。
- 两个入口基于同一快照提交，后提交者得到冲突，先提交者内容保留。
- 外部编辑未增加 XML 版本时，旧快照仍被拒绝；非法外部 XML 不被自动覆盖。
- 写入或替换失败时没有虚假成功提示；响应丢失后可以通过重读确定实际状态。
- 无变化提交不增加版本；旧依据失效时保留原验证结果并标记待复核。
- 不满足前提的实际测试结果可以保存，同时返回清楚的语义提示。

运行自动化验收的命令见 [README](../README.md)。
