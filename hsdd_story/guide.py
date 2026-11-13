"""On-demand HSDD Story workflow guidance for MCP clients."""

from .contract import check, fields, string


TOPICS = {
    "start": (
        "接手项目与定位节点",
        (
            "使用绑定到用户指定工作区的 MCP 连接。先调用 story_read({})，核对项目标识、目标、当前进展、阻碍和未处理的人工输入。",
            "按需读取相关需求子图和节点。只有标题或描述时，用 story_read 的 search 视图按关键词和状态定位。"
            "用户粘贴节点引用时，只传 nodeRef。工作区不匹配时，确认正确的连接；原节点已归档时，可按替代节点 ID 继续定位。",
            "已有快照时，可以用 changes 视图读取增量。收到 refreshRequired 时，重读概览与相关子图。",
            "仅在工作区没有方向图且用户要求初始化时创建项目。编码、测试和交付使用 Agent 自己的工具与当前授权。"
            "如果 MCP 无法连接，明确告知方向图未同步。",
        ),
    ),
    "update": (
        "更新进展与记录证据",
        (
            "需求、决定、进展、阻碍、验证结果或结构有实质变化时更新。合并本轮相关修改，长任务在阶段结果出现时保存。",
            "分别记录工作进展、验证结果和人的结论。保留失败、跳过和尚无定论的检查；相关旧结论需要复核时，保留原结果和依据。",
            "摘要简短，依据引用代码位置、测试结果或文档。写清下一步和参与方。聊天中的人工记录保存真实原话，AI 的解释写在节点正文。",
            "用 story_schema 的 types 或 operations 查询所需字段。提交 story_apply 时沿用最新读取的 projectId、revision（作为 baseRevision）和 readToken。"
            "检查返回的变化和复核事项，保存成功后再报告同步。",
        ),
    ),
    "structure": (
        "拆分工作、连接依赖与归档",
        (
            "把可独立阅读的工作组织成需求，把可独立推进、检查或交接的步骤组织成节点。",
            "requires 从必要前提指向后继；references 用于补充上下文；follow_up 用于本期之外的后续事项。",
            "纠正原节点时保留其 ID。合并、拆分或归档时，在同批处理相关连线和完成条件，"
            "明确人工记录、证据和附件的去向。原节点和替代关系用于追溯，旧验证结果需要核对后才能采用。",
        ),
    ),
    "attachments": (
        "读写图片与 Markdown 附件",
        (
            "简短结论留在节点正文；截图、图示和长说明可以用 story_apply 的 attachment.add 挂到节点。",
            "工作区文件传相对路径 sourcePath，Markdown 可直接传 text，二进制内容可传 dataBase64。三种来源任选其一。",
            "从节点详情取得附件 ID，用 story_read_attachment 读取。图片直接返回，Markdown 可以按字符分页。",
            "拆分或合并节点时，将 attachmentRefs 分配给相关新节点。",
        ),
    ),
    "acceptance": (
        "同步人工验收",
        (
            "正式验收由人在网页上选择通过或退回。用 story_read 查看目标节点的 display.acceptance 和原始人工记录。",
            "将节点写为 finished 且 satisfied 前，核对最新结论仍适用于该节点和当前范围，"
            "在 humanInputRefs 中引用 display.acceptance.inputId 对应的人工通过记录。",
            "退回时记录缺口。缺少通过记录或结果待复核时，请人到网页提交或重新确认。聊天中的意见可以作为上下文。",
        ),
    ),
    "conflict": (
        "处理版本冲突",
        (
            "收到 REVISION_CONFLICT 时，查看 details.changedIds、overlappingIds 和 retiredIds，"
            "重读涉及的节点并核对业务相关依赖。refreshRequired 为 true 时重读项目总览与相关子图。",
            "用重读返回的新版本和 readToken 重新组织操作。需要修改已归档节点时，先核对替代关系。",
            "提交后核对保存结果和复核提示。失败的提交仍需处理。",
        ),
    ),
}


def guide(args):
    fields(args, (), ("topic",))
    if "topic" not in args:
        return {"topics": [{"topic": topic, "title": title} for topic, (title, _) in TOPICS.items()]}
    topic = args["topic"]
    string(topic)
    check(topic in TOPICS, "未知指南主题。", code="INVALID_OPERATION", topic=topic, topics=list(TOPICS))
    title, steps = TOPICS[topic]
    return {"topic": topic, "title": title, "steps": list(steps)}
