# HSDD Story

HSDD Story 把需求、依赖、并行实施、验证和交付组织成一张方向图。人从项目总览进入需求子图，在节点里看实际情况、证据和下一步。AI 通过 MCP 更新同一份项目 XML，人可以在网页提交意见和明确结论。

## Windows 一键部署

安装 Python 3.12 或更新版本后，双击仓库根目录的 `deploy.cmd`。脚本安装独立的 Python 环境，注册并启动当前用户的 Windows 登录任务。默认使用 `.demo` 和端口 `8765`，首次创建演示数据，之后复用原数据。脚本会打印网页和 MCP 地址。首次安装需要能够下载 Python 依赖。

正式使用时，可以在 PowerShell 中指定已有的业务目录和端口。

```powershell
.\deploy.cmd -Workspace 'D:\path\to\project' -Port 8766
```

更新源码后，用同样的参数再次运行即可更新该任务。每份仓库副本为当前用户管理一个任务。如果端口被手动启动的服务占用，请先停止旧进程，或选择其他端口。同一个工作区由一个服务管理。

双击 `uninstall.cmd` 可停止并移除这个计划任务，同时清理 `.hsdd-service` 中的部署环境。演示和业务工作区的项目记录、附件均保留。MCP 客户端的连接独立配置，首次接入方法见下文。

## 运行演示

需要 Python 3.12 或更新版本。在仓库目录执行：

```powershell
python -m pip install .
python -m hsdd_story demo --workspace .demo
python -m hsdd_story serve --workspace .demo
```

打开 [本地网页](http://127.0.0.1:8765/)。`.demo` 是独立的模拟工作区。已经创建过时，直接执行 `serve` 即可。

选择「批量导出筛选结果」，进入「CSV 导出能力实现与回归」，可以看到三条并行分支、旧联调结果和待复核的失败回归。回到上层图，打开「接受本期导出能力」，可以填写意见，或选择「验收结论」并明确提交「通过」或「退回」。模拟数据中的测试与业务结论都是演示记录。

## 接入自己的项目

```powershell
python -m hsdd_story serve --workspace 'D:\path\to\project' --port 8765
```

MCP 客户端使用 **Streamable HTTP** 地址 `http://127.0.0.1:8765/mcp`。启动服务时选择的目录是工具绑定的工作区。使用另一个工作区时，可以换一个端口并在客户端使用独立连接名。

在支持 Streamable HTTP 的 Agent 客户端中添加 MCP 连接，URL 填部署时打印的 `/mcp` 地址。连接后即可查询工作流和读写方向图。首次接入可以让 Agent「通过 HSDD Story 读一下项目总览，告诉我当前进展和需要我决定的事项」。

Agent 调用 `story_guide({})` 获取主题目录，或传 `{"topic":"start"}` 查看接手步骤。更新、结构维护、附件、人工验收和版本冲突也有各自的主题。团队如果希望 Agent 在普通开发任务中主动同步，可以在业务项目规则里约定触发时机，例如「开发前读取方向图，有实质进展时按 `story_guide` 写回」。

可以让 Agent「在这个工作区建立方向图，整理当前需求，并标出需要我决定的事项」。首次创建通过 `story_apply` 保存到业务项目的 `.hsdd/story.xml`。

AI 用 `story_schema` 了解固定节点格式，用 `story_read` 获取概览、子图、节点或连续变更，用 `story_apply` 一次提交相关修改。保存成功后网页会更新。若 AI 和人同时修改，页面会提示冲突并保留草稿，供核对后再提交。

### 把节点发给 AI

打开节点详情，点「复制引用」，把整段文字粘贴到 AI 对话。复制内容带 MCP 地址和 `story_read({ "nodeRef": "…" })`。接入同一工作区的 AI 可以直接读到这个节点，包括它的附件索引和人的记录。没有引用时，可以用 `story_read` 的 `search` 视图按关键词、需求和状态定位节点。

节点改名、归档或服务重启后，引用仍指向原节点。工作区迁移到新目录后，从新页面复制引用。

### 节点附件

可以让 AI 把流程图、截图或较长的说明挂到相关节点。AI 在 `story_apply` 中使用 `attachment.add`，网页从节点详情的「附件」区预览和下载。支持 PNG、JPEG、GIF、WebP 和 UTF-8 Markdown。Markdown 可展示标题、列表、表格和代码块。AI 可通过 `story_read_attachment` 按 ID 直接读取图片或分页读取 Markdown。

上传项目中的文件时传 `sourcePath`（工作区相对路径）；新写的 Markdown 可以直接传 `text`；已有字节可用 `dataBase64`。附件原件由服务保存在 `.hsdd/attachments`，节点在 XML 中引用它。备份项目时将该目录与 `.hsdd/story.xml` 一起保存。参数示例见 [MCP 接口契约](docs/mcp-contract.md#节点附件)。

## 数据与检查

正式记录保存在 `.hsdd/story.xml`。用 Git 跟踪业务项目的这份 XML 可以保留变更历史。需要直接用编辑器改 XML 时，先停止同一工作区的服务，改完后再启动。

```powershell
python -m hsdd_story check --workspace .demo
python -m unittest discover -s tests -v
```

校验命令检查字段、引用和依赖，列出需要复核的结果。测试使用隔离工作区，覆盖合并、拆分、归档、并发、失败回滚、旧依据，以及真实 MCP 客户端和网页 API 的双向同步。

| 资料 | 内容 |
| --- | --- |
| [设计说明](docs/design.md) | 阅读方式、状态和维护规则 |
| [节点与 XML 契约](docs/node-contract.md) | 十三类节点、固定字段、完成条件与归档 |
| [MCP 接口契约](docs/mcp-contract.md) | 接口参数、批量操作与错误返回 |
| [批量导出示例](examples/README.md) | 从一张有并行、失败和人工输入的图开始阅读 |

服务在本机环回地址运行，适用于本地单用户协作。图记录开发事实和方向，实际编码、检查和交付由 Agent 的原有工具完成。工作流通过 `story_guide` 按需查询。
