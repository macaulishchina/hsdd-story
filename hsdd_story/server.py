"""Loopback HTTP service for the reader and MCP client."""

from base64 import b64encode
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from markdown_it import MarkdownIt
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ImageContent, TextContent, Tool, ToolAnnotations
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from . import VERSION
from .contract import (
    ACTORS, CATALOG, EVIDENCE, HANDLING, INPUT_KINDS, RELATIONS, RESULT, VALIDITY, WORK,
    StoryError, check, fields, number, reference_list, schema,
)
from .guide import TOPICS, guide
from .store import SEARCH_FILTERS, Store


STRING = {"type": "string", "minLength": 1}
INTEGER = {"type": "integer", "minimum": 0}
ARRAY_IDS = {"type": "array", "items": STRING, "uniqueItems": True}
REVISION = {"type": "integer", "minimum": 1}
MAX_REQUEST = 4 * 1024 * 1024
ATTACHMENT_TEXT_LIMIT = 20_000
ASSETS = Path(__file__).with_name("web")
MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")


def object_schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


def choices(values):
    return {"enum": list(values)}


PROJECT_PROPS = {"title": STRING, "goal": STRING, "summary": STRING}
REQUIREMENT_PROPS = {**PROJECT_PROPS, "scope": STRING, "completion": ARRAY_IDS, "parent": STRING}
BODY_PROPS = {key: STRING for entry in CATALOG.values() for key, _ in entry[1:]}
BODY_PROPS["method"] = choices(("automated", "manual"))
NODE_PROPS = {
    "type": choices(CATALOG), "title": STRING, "summary": STRING,
    "work": choices(WORK), "result": choices(RESULT), "validity": choices(VALIDITY),
    "variant": {"const": "fix"}, "body": object_schema(BODY_PROPS),
    "next": object_schema({"actor": choices(ACTORS), "text": STRING}, ("actor", "text")),
    "basis": {"type": "array", "items": object_schema({"ref": STRING, "revision": REVISION}, ("ref", "revision"))},
    "evidenceRefs": ARRAY_IDS, "humanInputRefs": ARRAY_IDS, "attachmentRefs": ARRAY_IDS,
}
NEW_NODE = object_schema({"id": STRING, **NODE_PROPS}, ("id", "type", "title", "summary", "body"))
NODE_TARGET = object_schema({"requirementId": STRING, "node": NEW_NODE}, ("requirementId", "node"))
NODE_PATCH = object_schema({**NODE_PROPS, **{f"body.{key}": spec for key, spec in BODY_PROPS.items()}})
RELATION_PROPS = {"type": choices(RELATIONS), "from": STRING, "to": STRING}
INPUT_PROPS = {"id": STRING, "target": STRING, "kind": choices(INPUT_KINDS), "text": STRING,
               "createdAt": {"type": "string", "format": "date-time"},
               "source": {"const": "human"}, "recorder": {"const": "ai"},
               "channel": {"const": "chat"}, "handling": {"const": "pending"}}

OP_FIELDS = {
    "project.create": ({"project": object_schema({"id": STRING, **PROJECT_PROPS}, ("id", "title", "goal"))},
                       ("project",)),
    "project.update": ({"set": object_schema(PROJECT_PROPS), "removeFields": ARRAY_IDS}, ("set",)),
    "requirement.create": ({"requirement": object_schema({"id": STRING, **REQUIREMENT_PROPS},
                           ("id", "title", "goal", "scope", "summary", "completion"))}, ("requirement",)),
    "requirement.update": ({"id": STRING, "set": object_schema(REQUIREMENT_PROPS),
                            "removeFields": ARRAY_IDS}, ("id", "set")),
    "node.create": ({"requirementId": STRING, "node": NEW_NODE}, ("requirementId", "node")),
    "node.update": ({"id": STRING, "set": NODE_PATCH, "removeFields": ARRAY_IDS,
                     "changeKind": choices(("editorial", "substantive"))}, ("id", "changeKind", "set")),
    "node.remove": ({"id": STRING}, ("id",)),
    "node.merge": ({"sourceIds": {**ARRAY_IDS, "minItems": 2}, "target": NODE_TARGET}, ("sourceIds", "target")),
    "node.split": ({"sourceId": STRING, "targets": {"type": "array", "minItems": 2, "items": NODE_TARGET}},
                   ("sourceId", "targets")),
    "relation.create": ({"relation": object_schema({"id": STRING, **RELATION_PROPS},
                                                    ("id", *RELATION_PROPS))}, ("relation",)),
    "relation.update": ({"id": STRING, "set": object_schema(RELATION_PROPS)}, ("id", "set")),
    "relation.remove": ({"id": STRING}, ("id",)),
    "evidence.add": ({"evidence": object_schema({"id": STRING, "kind": choices(EVIDENCE), "target": STRING,
                                                 "subject": STRING, "subjectRevision": REVISION, "text": STRING},
                                                ("id", "kind", "target", "subject", "subjectRevision", "text"))},
                     ("evidence",)),
    "input.add": ({"input": object_schema(INPUT_PROPS, ("id", "target", "kind", "text"))}, ("input",)),
    "input.handle": ({"id": STRING, "handling": choices(HANDLING)}, ("id", "handling")),
    "attachment.add": (
        {"nodeId": STRING, "attachment": {
            **object_schema({"id": STRING, "filename": STRING, "sourcePath": STRING,
                             "text": STRING, "dataBase64": STRING}, ("id", "filename")),
            "oneOf": [{"required": [key]} for key in ("sourcePath", "text", "dataBase64")],
        }},
        ("nodeId", "attachment"),
    ),
}


def operation_schema(name):
    properties, required = OP_FIELDS[name]
    return object_schema({"op": {"const": name}, **properties}, ("op", *required))


def tool_definitions():
    return [
        Tool(name="story_guide", title="查询方向图工作流",
             description="不传参数时列出主题。按 topic 查询接手、更新、结构维护、附件、人工验收或冲突处理。",
             inputSchema=object_schema({"topic": choices(TOPICS)}),
             annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)),
        Tool(name="story_schema", title="查看节点契约",
             description="获取节点字段和操作契约。传 types 只取指定节点契约，传 operations 只取指定操作，可同时传。",
             inputSchema=object_schema({
                 "schemaVersion": {"const": "1"}, "types": {**ARRAY_IDS, "items": choices(CATALOG)},
                 "operations": {**ARRAY_IDS, "items": choices(OP_FIELDS)},
             }),
             annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)),
        Tool(name="story_read", title="读取开发方向图",
             description="用 nodeRef 直达节点，用 search 按关键词或状态找节点，或读取概览、需求、节点和变化。"
                         "传 nodeRef 时只传这一项。保存前使用返回的 revision 与 readToken。"
                         "接手工作时可查 story_guide 的 start。",
             inputSchema={
                 **object_schema({"nodeRef": STRING, "projectId": STRING,
                                  "view": {"enum": ["overview", "requirement", "nodes", "changes", "search"]},
                                  "requirementId": STRING, "nodeIds": ARRAY_IDS,
                                  "sinceRevision": INTEGER, "sinceReadToken": STRING,
                                  "query": {**STRING, "maxLength": 200},
                                  "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                                  "offset": INTEGER,
                                  **{key: choices(values) for key, values in SEARCH_FILTERS.items()}}),
                 "if": {"required": ["nodeRef"]},
                 "then": {"maxProperties": 1},
             },
             annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)),
        Tool(name="story_read_attachment", title="读取节点附件",
             description="按 story_read 返回的附件 ID 读取内容。图片返回原图，Markdown 可按字符分页。",
             inputSchema=object_schema({
                 "attachmentId": STRING, "offset": INTEGER,
                 "limit": {"type": "integer", "minimum": 1, "maximum": ATTACHMENT_TEXT_LIMIT},
             }, ("attachmentId",)),
             annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)),
        Tool(name="story_apply", title="保存方向图变更",
             description="对绑定工作区的 XML 原子应用一组结构化操作。旧快照、断链、环或缺失依据会返回错误。"
                         "节点补丁的 set 支持公共字段和 body.<固定字段>，合并/拆分需同批重连并迁移人的记录。"
                         "更新、验收与冲突处理可查 story_guide。",
             inputSchema=object_schema({
                 "projectId": STRING, "baseRevision": INTEGER, "readToken": STRING, "reason": STRING,
                 "operations": {"type": "array", "maxItems": 1000, "items": {"oneOf": [
                     operation_schema(name) for name in OP_FIELDS
                 ]}},
             }, ("projectId", "baseRevision", "readToken", "reason", "operations")),
             annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)),
    ]


def describe(args):
    fields(args, (), ("schemaVersion", "types", "operations"))
    check(args.get("schemaVersion", "1") == "1", "不支持的契约版本。", code="UNSUPPORTED_SCHEMA")
    if "operations" in args:
        names = args["operations"]
        reference_list(names)
        check(all(name in OP_FIELDS for name in names), "未知操作。", code="INVALID_OPERATION")
    else:
        names = [] if "types" in args else list(OP_FIELDS)
    data = {"schemaVersion": "1"}
    if "types" in args or "operations" not in args:
        data = schema(args.get("types"))
    if names:
        data["operationSchemas"] = {name: operation_schema(name) for name in names}
    if "types" in args or "operations" in args:
        return data
    data["protocol"] = {
        "node": {"required": ["id", "type", "title", "summary", "body"],
                 "optional": ["work", "result", "validity", "variant", "next", "basis",
                              "evidenceRefs", "humanInputRefs", "attachmentRefs"],
                 "next": {"actor": "ai|human|external", "text": "string"},
                 "basis": [{"ref": "nodeId", "revision": 1}]},
        "project": {"id": "P01", "title": "string", "goal": "string", "summary": "optional string"},
        "requirement": {"id": "R01", "title": "string", "goal": "string", "scope": "string",
                        "summary": "string", "parent": "optional requirementId", "completion": ["nodeId"]},
        "target": {"requirementId": "R01", "node": "node object"},
        "relation": {"id": "E01", "type": "requires|references|follow_up", "from": "id", "to": "id"},
        "evidence": {"id": "EV01", "kind": "code|test|document|observation|decision",
                     "target": "location", "subject": "nodeId", "subjectRevision": 1, "text": "string"},
        "input": {"id": "H01", "target": "id", "kind": "comment|answer|decision|acceptance",
                  "text": "人的聊天原话", "createdAt": "optional ISO 8601"},
        "attachment": {"id": "ATT-01", "filename": "diagram.png",
                       "sourcePath": "工作区内相对路径，也可改用 text 或 dataBase64"},
    }
    return data


def read_attachment(store, args):
    fields(args, ("attachmentId",), ("offset", "limit"))
    offset = args.get("offset", 0)
    limit = args.get("limit", 6000)
    number(offset, 0)
    number(limit)
    check(limit <= ATTACHMENT_TEXT_LIMIT, "Markdown 单次读取最多 20000 个字符。", code="INVALID_OPERATION")
    item, raw = store.read_attachment(args["attachmentId"])
    data = {"attachment": item}
    if item["mediaType"] == "text/markdown":
        text = raw.decode("utf-8-sig")
        check(offset <= len(text), "offset 超出 Markdown 字符范围。", code="INVALID_OPERATION", length=len(text))
        end = min(offset + limit, len(text))
        data.update(text=text[offset:end], offset=offset, nextOffset=end if end < len(text) else None,
                    totalCharacters=len(text))
        return data, None
    check(not args.keys() & {"offset", "limit"}, "图片以完整内容返回，不使用字符分页。", code="INVALID_OPERATION")
    return data, ImageContent(type="image", mimeType=item["mediaType"], data=b64encode(raw).decode("ascii"))


def mcp_server(store):
    server = Server("hsdd-story", VERSION, instructions=(
        "用 story_guide 按主题查询本服务的工作流程，用 story_schema 查询字段与操作契约。"
        "用户粘贴 HSDD Story 节点引用时，先用 story_read 的 nodeRef 参数读取原节点。"
        "读取项目概览与相关节点，核对真实工作和人的输入，再提交局部修改。"
        "保持验证事实与人工结论的来源。"
    ))
    handlers = {"story_guide": guide, "story_schema": describe,
                "story_read": store.read, "story_apply": store.apply}

    @server.list_tools()
    async def list_tools():
        return tool_definitions()

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        media = None
        try:
            if name == "story_read_attachment":
                data, media = await run_in_threadpool(read_attachment, store, arguments)
            else:
                check(name in handlers, "工具不存在。", code="INVALID_OPERATION")
                data = await run_in_threadpool(handlers[name], arguments)
            failed = False
        except StoryError as exc:
            data = exc.payload()
            failed = True
        content = [TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]
        if media is not None:
            content.append(media)
        return CallToolResult(content=content, structuredContent=data, isError=failed)

    return server


def response_error(exc):
    status = {
        "REVISION_CONFLICT": 409, "PROJECT_MISMATCH": 409, "PROJECT_NOT_FOUND": 404,
        "INVALID_DOCUMENT": 422, "UNSUPPORTED_SCHEMA": 422, "SAVE_FAILED": 500,
        "ATTACHMENT_NOT_FOUND": 404, "ATTACHMENT_UNAVAILABLE": 404,
    }.get(exc.code, 400)
    return JSONResponse(exc.payload(), status_code=status)


class LocalOnly:
    """Restrict browser and MCP access to the loopback host and matching origin."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope, receive)
            host = request.headers.get("host", "")
            origin = request.headers.get("origin")
            if host.partition(":")[0] not in ("127.0.0.1", "localhost") or (
                origin is not None and origin != f"http://{host}"
            ):
                await JSONResponse({"ok": False, "message": "只接受本机同源请求。"}, status_code=403)(
                    scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(workspace):
    store = Store(workspace)
    security = TransportSecuritySettings(
        allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"],
        allowed_origins=["http://127.0.0.1", "http://127.0.0.1:*", "http://localhost", "http://localhost:*"],
    )
    manager = StreamableHTTPSessionManager(mcp_server(store), json_response=True, stateless=True,
                                           security_settings=security)

    @asynccontextmanager
    async def lifespan(app):
        with store.exclusive_workspace():
            async with manager.run():
                yield

    async def read_story(request):
        try:
            args = dict(request.query_params)
            if "nodeIds" in args:
                args["nodeIds"] = request.query_params.getlist("nodeIds")
            for key in ("sinceRevision", "limit", "offset"):
                if key in args:
                    try:
                        args[key] = int(args[key])
                    except ValueError as exc:
                        raise StoryError("INVALID_OPERATION", "参数必须为整数。", field=key) from exc
            return JSONResponse(await run_in_threadpool(store.read, args), headers={"Cache-Control": "no-store"})
        except StoryError as exc:
            return response_error(exc)

    async def read_schema(request):
        return JSONResponse(schema())

    async def add_input(request):
        try:
            check(request.headers.get("content-type", "").split(";")[0] == "application/json",
                  "请提交 JSON。", code="INVALID_OPERATION")
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                check(len(body) <= MAX_REQUEST, "输入超过请求大小上限。", code="INVALID_OPERATION")
            try:
                args = json.loads(body)
            except (ValueError, UnicodeError) as exc:
                raise StoryError("INVALID_OPERATION", "请求不是有效 JSON。") from exc
            fields(args, ("projectId", "baseRevision", "readToken", "target", "kind", "text"), ("outcome",))
            apply = {key: args[key] for key in ("projectId", "baseRevision", "readToken")}
            apply["reason"] = "保存页面中的人工输入"
            apply["operations"] = [{"op": "input.add", "input": {
                "id": "H-" + uuid4().hex[:16], "target": args["target"], "kind": args["kind"],
                "text": args["text"], "createdAt": datetime.now(timezone.utc).isoformat(),
                **({"outcome": args["outcome"]} if "outcome" in args else {}),
            }}]
            return JSONResponse(await run_in_threadpool(store.apply, apply, "human"))
        except StoryError as exc:
            return response_error(exc)

    async def get_attachment(request):
        try:
            item, raw = await run_in_threadpool(store.read_attachment, request.path_params["key"])
            name = quote(item["filename"], safe="")
            mode = "attachment" if request.query_params.get("download") == "1" else "inline"
            return Response(raw, media_type=item["mediaType"], headers={
                "Content-Disposition": f"{mode}; filename*=UTF-8''{name}",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "private, no-cache",
            })
        except StoryError as exc:
            return response_error(exc)

    async def preview_attachment(request):
        try:
            item, raw = await run_in_threadpool(store.read_attachment, request.path_params["key"])
            check(item["mediaType"] == "text/markdown", "此附件使用图片预览。", code="INVALID_OPERATION")
            title = escape(item["filename"])
            rendered = await run_in_threadpool(MARKDOWN.render, raw.decode("utf-8-sig"))
            return HTMLResponse(
                '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                f'<title>{title}</title><link rel="stylesheet" href="/app.css"></head>'
                f'<body class="markdown-preview"><main>{rendered}</main></body></html>',
                headers={"Content-Security-Policy": "default-src 'none'; style-src 'self'; "
                                                    "img-src 'self'; base-uri 'none'; frame-ancestors 'self'",
                         "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"},
            )
        except StoryError as exc:
            return response_error(exc)

    def asset(name):
        async def send_asset(request):
            return FileResponse(ASSETS / name, headers={
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                           "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            })
        return send_asset

    class McpEndpoint:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)

    app = Starlette(routes=[
        Route("/", asset("index.html")),
        Route("/app.css", asset("app.css")),
        Route("/app.js", asset("app.js")),
        Route("/api/story", read_story),
        Route("/api/schema", read_schema),
        Route("/api/input", add_input, methods=["POST"]),
        Route("/api/attachments/{key}", get_attachment),
        Route("/api/attachments/{key}/preview", preview_attachment),
        Route("/mcp", McpEndpoint(), methods=["GET", "POST", "DELETE"]),
    ], lifespan=lifespan)
    return LocalOnly(app)
