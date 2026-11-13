"""Shared project storage, optimistic concurrency and atomic saves."""

from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import RLock
from urllib.parse import quote, unquote
import os
import re
import tempfile

from .attachments import AttachmentFiles
from .contract import (
    CATALOG, RESULT, VALIDITY, WORK, StoryError, check, fields, ident, number, reference_list,
    sequence, string, validate,
)
from .operations import Edit, operation_targets
from .views import node_view, object_summary, overview, pending_inputs, requirement_view, search_nodes
from .xmlio import MAX_DOCUMENT, parse, serialize


SEARCH_FILTERS = {"type": CATALOG, "work": WORK, "result": RESULT, "validity": VALIDITY}


def token(raw):
    return sha256(raw).hexdigest() if raw is not None else "absent"


class Store:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        canonical = os.path.normcase(str(self.workspace))
        self.workspace_key = sha256(os.fsencode(canonical)).hexdigest()[:32]
        self.path = self.workspace / ".hsdd" / "story.xml"
        self.attachment_files = AttachmentFiles(self.workspace)
        self.lock = RLock()
        self.snapshots = OrderedDict()
        self.last_token = None

    @contextmanager
    def exclusive_workspace(self):
        """Prevent two server processes from managing the same workspace."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name("story.lock").open("a+b") as handle:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StoryError("WORKSPACE_BUSY", "此工作区已有 HSDD Story 服务。") from exc
            yield

    def raw(self):
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(MAX_DOCUMENT + 1)
            check(len(raw) <= MAX_DOCUMENT, "XML 文件超过 16 MiB。", code="INVALID_DOCUMENT")
            return raw
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StoryError("INVALID_DOCUMENT", f"无法读取项目文件：{exc}") from exc

    def remember(self, version, fingerprint, saved=False):
        if not saved and fingerprint != self.last_token:
            self.snapshots.clear()
        self.snapshots[fingerprint] = version
        self.last_token = fingerprint
        while len(self.snapshots) > 128:
            self.snapshots.popitem(last=False)

    def load(self):
        raw = self.raw()
        fingerprint = token(raw)
        doc, graph = parse(raw) if raw is not None else (None, None)
        self.remember(doc["revision"] if doc else 0, fingerprint)
        return doc, graph, fingerprint

    def node_reference(self, project, node):
        return ":".join(("hsdd-story", "v1", self.workspace_key, quote(project, safe=""), quote(node, safe="")))

    def resolve_reference(self, value):
        check(isinstance(value, str) and len(value) <= 4096, "节点引用格式无效。", code="INVALID_OPERATION")
        parts = value.split(":")
        check(len(parts) == 5 and parts[:2] == ["hsdd-story", "v1"]
              and re.fullmatch(r"[0-9a-f]{32}", parts[2]), "节点引用格式无效。", code="INVALID_OPERATION")
        try:
            project, node = (unquote(part, errors="strict") for part in parts[3:])
            ident(project)
            ident(node)
            check(parts[3:] == [quote(project, safe=""), quote(node, safe="")], "引用编码无效。")
        except (UnicodeError, StoryError) as exc:
            raise StoryError("INVALID_OPERATION", "节点引用中的项目或节点编号无效。") from exc
        check(parts[2] == self.workspace_key, "节点引用属于另一个工作区，请连接引用中的 MCP 地址。",
              code="PROJECT_MISMATCH")
        return {"projectId": project, "view": "nodes", "nodeIds": [node]}

    def read(self, args):
        by_reference = isinstance(args, dict) and "nodeRef" in args
        if by_reference:
            fields(args, ("nodeRef",))
            args = self.resolve_reference(args["nodeRef"])
        fields(args, (), ("projectId", "view", "requirementId", "nodeIds", "sinceRevision", "sinceReadToken",
                          "query", "limit", "offset", *SEARCH_FILTERS))
        view = args.get("view", "overview")
        check(view in ("overview", "requirement", "nodes", "changes", "search"),
              "未知读取视图。", code="INVALID_OPERATION")
        if view == "search":
            fields(args, ("view",), ("projectId", "requirementId", "query", "limit", "offset", *SEARCH_FILTERS))
            if "query" in args:
                string(args["query"])
                check(len(args["query"]) <= 200, "搜索关键词最多 200 个字符。", code="INVALID_OPERATION")
            if "requirementId" in args:
                ident(args["requirementId"])
            for field, choices in SEARCH_FILTERS.items():
                if field in args:
                    string(args[field])
                    check(args[field] in choices, "搜索筛选值无效。", code="INVALID_OPERATION", field=field)
            number(args.get("limit", 20))
            check(args.get("limit", 20) <= 50, "每次搜索最多 50 个结果。", code="INVALID_OPERATION")
            number(args.get("offset", 0), 0)
        else:
            check(not args.keys() & {"query", "limit", "offset", *SEARCH_FILTERS},
                  "搜索参数仅用于 search 视图。", code="INVALID_OPERATION")
        if "projectId" in args:
            ident(args["projectId"])
        if view == "requirement":
            ident(args.get("requirementId"))
        if view == "nodes":
            reference_list(args.get("nodeIds"))
            check(args["nodeIds"], "至少提供一个节点 ID。", code="INVALID_OPERATION")
        if view == "changes":
            number(args.get("sinceRevision"), 0)
            string(args.get("sinceReadToken"))
        with self.lock:
            doc, graph, fingerprint = self.load()
            if not doc:
                check(not by_reference, "引用所在工作区还没有项目记录。", code="PROJECT_NOT_FOUND")
                return {"exists": False, "projectId": None, "schemaVersion": "1", "revision": 0,
                        "readToken": "absent", "view": view, "data": {}, "warnings": []}
            if "projectId" in args:
                check(args["projectId"] == doc["id"], "项目 ID 与工作区不符。", code="PROJECT_MISMATCH")
            if view == "overview":
                data = overview(graph)
                visible = None
            elif view == "requirement":
                data = requirement_view(graph, args["requirementId"])
                visible = {args["requirementId"], *[n["id"] for n in data["nodes"]], *[n["id"] for n in data["children"]]}
            elif view == "nodes":
                data = node_view(graph, args["nodeIds"])
                for node in data["nodes"]:
                    node["nodeRef"] = self.node_reference(doc["id"], node["id"])
                visible = set(args["nodeIds"])
            elif view == "search":
                data = search_nodes(graph, args)
                visible = {n["id"] for n in data["nodes"]}
            else:
                data = self.changes(doc, graph, fingerprint, args["sinceRevision"], args["sinceReadToken"])
                visible = set(data.get("changedIds", []))
            return {"exists": True, "projectId": doc["id"], "schemaVersion": "1", "revision": doc["revision"],
                    "readToken": fingerprint, "view": view, "data": data,
                    "warnings": [w for w in graph.warnings if visible is None or w["id"] in visible]}

    def changes_since(self, doc, current, since, previous):
        if since == doc["revision"] and current == previous:
            return []
        if self.snapshots.get(previous) != since or not 0 < doc["revision"] - since <= len(doc["changes"]):
            return None
        changes = [entry for entry in doc["changes"] if entry["revision"] > since]
        if [entry["revision"] for entry in changes] != list(range(since + 1, doc["revision"] + 1)):
            return None
        return changes

    def changes(self, doc, graph, current, since, previous):
        changes = self.changes_since(doc, current, since, previous)
        if changes is None:
            return {"refreshRequired": True, "reason": "无法确认连续变化，请重新读取概览与有关子图。"}
        if not changes:
            return {"refreshRequired": False, "changedIds": [], "retiredIds": [], "changes": [], "objects": []}
        ids = list(dict.fromkeys(key for entry in changes for key in entry["affected"]))
        return {"refreshRequired": False, "changedIds": ids,
                "retiredIds": [key for key in ids if key in graph.retired],
                "changes": changes, "objects": [object_summary(graph, key) for key in ids
                                                 if key in graph.active or key in graph.requirements],
                "pendingInputs": pending_inputs(graph)}

    def conflict(self, doc, graph, current, args):
        targets = operation_targets(args["operations"])
        changes = (self.changes_since(doc, current, args["baseRevision"], args["readToken"])
                   if doc and doc["id"] == args["projectId"] else None)
        revision = doc["revision"] if doc else (0 if current == "absent" else None)
        details = {"currentRevision": revision, "refreshRequired": changes is None, "targetIds": targets}
        if changes is not None:
            affected = {key for entry in changes for key in entry["affected"]}
            details.update(changedIds=sorted(affected), retiredIds=sorted(affected & graph.retired.keys()),
                           overlappingIds=sorted(affected.intersection(targets)))
        return StoryError("REVISION_CONFLICT", "项目内容已变化，请重读相关节点后重新提交。", **details)

    def apply(self, args, actor="ai"):
        fields(args, ("projectId", "baseRevision", "readToken", "reason", "operations"))
        ident(args["projectId"])
        number(args["baseRevision"], 0)
        string(args["readToken"])
        string(args["reason"])
        operations = sequence(args["operations"])
        check(len(operations) <= 1000, "一个批次最多 1000 项操作。", code="INVALID_OPERATION")
        with self.lock:
            doc, previous_graph, fingerprint = self.load()
            before = doc["revision"] if doc else 0
            if (before, fingerprint) != (args["baseRevision"], args["readToken"]):
                raise self.conflict(doc, previous_graph, fingerprint, args)
            if doc:
                check(args["projectId"] == doc["id"], "项目 ID 与工作区不符。", code="PROJECT_MISMATCH")
            edit = Edit(deepcopy(doc), before + 1, args["reason"], actor, self.workspace)
            for index, op in enumerate(operations):
                try:
                    edit.apply(op)
                except StoryError as exc:
                    exc.details.setdefault("operationIndex", index)
                    if exc.code == "INVALID_STRUCTURE":
                        exc.code = "INVALID_OPERATION"
                    raise
                except (KeyError, TypeError, ValueError) as exc:
                    raise StoryError("INVALID_OPERATION", "操作字段不合法。", operationIndex=index) from exc
            modified = edit.doc
            check(modified, "项目尚未创建。", code="PROJECT_NOT_FOUND")
            check(modified["id"] == args["projectId"], "项目 ID 不一致。", code="PROJECT_MISMATCH")
            edit.update_revisions(doc)
            try:
                if modified != doc:
                    modified["revision"] = before + 1
                graph = validate(modified)
                for key in sorted(edit.acceptance_claims & graph.acceptances.keys()):
                    node = graph.active[key]
                    if (node["work"], node["result"]) == ("finished", "satisfied"):
                        decision = graph.acceptances[key]
                        check(decision["state"] == "accepted",
                              "验收通过必须引用该节点最新、仍适用于当前范围的人工通过记录。",
                              code="ACCEPTANCE_REQUIRED", id=key, acceptance=decision)
                if modified == doc:
                    return self.result(modified, before, fingerprint, False, (), (), (), ())
                acceptance_review = set()
                for key, decision in graph.acceptances.items():
                    if previous_graph is not None and decision != previous_graph.acceptances.get(key):
                        edit.touched.add(key)
                        if decision["state"] == "needs_review":
                            acceptance_review.add(key)
                review = [key for key, n in graph.active.items() if graph.stale(n)]
                for key in review:
                    if graph.active[key]["validity"] != "needs_review":
                        graph.active[key]["validity"] = "needs_review"
                        edit.touched.add(key)
                modified["changes"].append({"revision": before + 1, "actor": actor, "reason": args["reason"],
                                            "affected": sorted(edit.touched or {modified["id"]})})
                graph = validate(modified)
                serialized = serialize(modified)
            except (KeyError, TypeError, ValueError) as exc:
                raise StoryError("INVALID_STRUCTURE", "修改后的项目字段不合法。") from exc
            try:
                with self.attachment_files.publish(edit.uploads):
                    self.save(serialized, fingerprint, before)
            except StoryError as exc:
                if exc.code != "REVISION_CONFLICT":
                    raise
                try:
                    latest, latest_graph, latest_token = self.load()
                except StoryError:
                    latest, latest_graph, latest_token = None, None, None
                raise self.conflict(latest, latest_graph, latest_token, args) from exc
            saved_token = token(serialized)
            self.remember(before + 1, saved_token, saved=True)
            return self.result(modified, before, saved_token, True, edit.touched, edit.retired,
                               set(review) | acceptance_review, graph.warnings)

    def read_attachment(self, key):
        ident(key)
        with self.lock:
            _, graph, _ = self.load()
            item = graph.attachments.get(key) if graph else None
            check(item, "附件不存在。", code="ATTACHMENT_NOT_FOUND", id=key)
            return item, self.attachment_files.read(item)

    def save(self, raw, expected, before):
        path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".story-", suffix=".tmp", delete=False) as tmp:
                path = Path(tmp.name)
                tmp.write(raw)
                tmp.flush()
                os.fsync(tmp.fileno())
            check(token(self.raw()) == expected, "保存前发现文件已变化，请重读。",
                  code="REVISION_CONFLICT", currentRevision=before)
            os.replace(path, self.path)
        except OSError as exc:
            raise StoryError("SAVE_FAILED", f"无法保存项目文件：{exc}") from exc
        finally:
            if path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def result(doc, before, fingerprint, changed, ids, retired, review, warnings):
        return {"ok": True, "projectId": doc["id"], "changed": changed, "previousRevision": before,
                "revision": doc["revision"], "readToken": fingerprint, "changedIds": sorted(ids),
                "retiredIds": sorted(retired), "reviewRequiredIds": sorted(review), "warnings": list(warnings)}
