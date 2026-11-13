"""Apply structured edits to a private document copy."""

from copy import deepcopy

from .attachments import prepare
from .contract import (
    ACCEPTANCE_OUTCOMES, Graph, HANDLING, NODE_FIELDS, NODE_LISTS, PROJECT_FIELDS, REQ_FIELDS, SECTIONS,
    StoryError, check, fields, human_kinds, ident, reference_list, sequence, validate_node,
)


def operation_targets(operations):
    """Known object IDs for conflict diagnosis, before the batch is validated."""
    targets = set()
    for op in operations:
        if not isinstance(op, dict):
            continue
        targets.update(value for key in ("id", "nodeId", "sourceId", "requirementId")
                       if isinstance(value := op.get(key), str))
        sources = op.get("sourceIds")
        if isinstance(sources, list):
            targets.update(value for value in sources if isinstance(value, str))
        for key, ref_fields in (("project", ("id",)), ("requirement", ("id", "parent")), ("node", ("id",)),
                                ("relation", ("id", "from", "to")), ("evidence", ("id", "subject")),
                                ("input", ("id", "target")), ("attachment", ("id",)),
                                ("set", ("from", "to"))):
            item = op.get(key)
            if isinstance(item, dict):
                targets.update(value for field in ref_fields if isinstance(value := item.get(field), str))
        replacements = op.get("targets") if isinstance(op.get("targets"), list) else [op.get("target")]
        for item in replacements:
            if not isinstance(item, dict):
                continue
            owner = item.get("requirementId")
            if isinstance(owner, str):
                targets.add(owner)
            node = item.get("node")
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                targets.add(node["id"])
    return sorted(targets)


def locate(items, key):
    for value in items:
        if value["id"] == key:
            return value
    raise StoryError("INVALID_OPERATION", "对象不存在。", id=key)


def patch(value, updates, removals, allowed, removable):
    fields(updates, (), allowed)
    reference_list(removals)
    check(set(removals) <= set(removable) and not set(removals) & updates.keys(),
          "字段不可移除，或同时设置和移除。")
    for key in removals:
        value.pop(key, None)
    value.update(deepcopy(updates))


class Edit:
    def __init__(self, doc, revision, reason, actor, workspace):
        self.doc = doc
        self.revision = revision
        self.reason = reason
        self.actor = actor
        self.workspace = workspace
        self.uploads = {}
        self.upload_size = 0
        self.used = set()
        self.touched = set()
        self.substantive = set()
        self.acceptance_claims = set()
        self.retired = set()
        if doc:
            self.used.add(doc["id"])
            self.used.update(n["id"] for r in doc["requirements"] for n in r["nodes"])
            self.used.update(item["node"]["id"] for item in doc["archive"])
            self.used.update(x["id"] for group in ("requirements", "relations", "evidence", "humanInputs", "attachments")
                             for x in doc[group])
            self.used.update(key for change in doc["changes"] for key in change["affected"])

    def reserve(self, key):
        ident(key)
        check(key not in self.used, "不能复用 ID。", id=key)
        self.used.add(key)
        self.touched.add(key)

    def requirement(self, key):
        return locate(self.doc["requirements"], key)

    def node(self, key):
        for req in self.doc["requirements"]:
            for node in req["nodes"]:
                if node["id"] == key:
                    return req, node
        raise StoryError("INVALID_OPERATION", "当前节点不存在。", id=key)

    def create_node(self, requirement_id, raw):
        req = self.requirement(requirement_id)
        fields(raw, ("id", "type", "title", "summary", "body"),
               set(NODE_FIELDS) - {"revision"})
        self.reserve(raw["id"])
        node = {"work": "planned", "result": "unknown", "validity": "current",
                **{key: [] for key in NODE_LISTS}, **deepcopy(raw), "revision": 1}
        validate_node(node)
        req["nodes"].append(node)
        if node["type"] == "acceptance":
            self.acceptance_claims.add(node["id"])
        self.touched.add(requirement_id)
        return node

    def retire(self, source, replacements):
        req, node = self.node(source)
        req["nodes"].remove(node)
        self.doc["archive"].append({"requirement": req["id"], "reason": self.reason,
                                    "atRevision": self.revision, "node": node,
                                    "replacements": replacements})
        self.retired.add(source)
        self.touched.update((source, req["id"]))

    def restructure(self, sources, targets):
        reference_list(sources)
        record_ids = set()
        attachment_ids = set()
        for key in sources:
            _, node = self.node(key)
            record_ids.update(node["humanInputRefs"])
            record_ids.update(i["id"] for i in self.doc["humanInputs"] if i["target"] == key)
            attachment_ids.update(node["attachmentRefs"])
        created = []
        for entry in targets:
            fields(entry, ("requirementId", "node"))
            created.append(self.create_node(entry["requirementId"], entry["node"]))
        moved = {ref for node in created for ref in node["humanInputRefs"]}
        check(record_ids <= moved, "拆分或合并需要指定人的记录去向。", refs=sorted(record_ids - moved))
        moved_attachments = {ref for node in created for ref in node["attachmentRefs"]}
        check(attachment_ids <= moved_attachments, "拆分或合并需要指定附件去向。",
              refs=sorted(attachment_ids - moved_attachments))
        for key in sources:
            self.retire(key, [node["id"] for node in created])

    def update_node(self, op):
        fields(op, ("op", "id", "changeKind", "set"), ("removeFields",))
        _, node = self.node(op["id"])
        mode = op["changeKind"]
        check(mode in ("editorial", "substantive"), "修改类别不合法。")
        updates = op["set"]
        removals = op.get("removeFields", [])
        allowed = set(NODE_FIELDS) - {"id", "revision"}
        check(isinstance(updates, dict), "set 需要对象。")
        reference_list(removals)
        body_keys = [key for key in updates if key.startswith("body.")]
        removals_body = [key for key in removals if key.startswith("body.")]
        check("body" not in updates or not (body_keys or removals_body), "不能同时替换正文和编辑正文字段。")
        check("type" not in updates or updates["type"] == node["type"] or "body" in updates,
              "更改类型时需要提供完整的新正文。")
        engineering = {"type", "variant", "work", "result", "validity", "basis", "evidenceRefs"}
        changed_engineering = any(key in engineering and value != node.get(key) for key, value in updates.items())
        changed_engineering |= any(key in engineering and key in node for key in removals)
        check(mode == "substantive" or not changed_engineering, "改变工程状态或依据需要实质修改。")
        patch(node, {k: v for k, v in updates.items() if k not in body_keys},
              [k for k in removals if k not in removals_body], allowed, ("variant", "next"))
        body = node["body"]
        check(isinstance(body, dict), "body 需要对象。")
        check(not set(body_keys) & set(removals_body), "同一正文字段不能同时设置和移除。")
        for key in removals_body:
            check(key[5:] in body, "无法移除不存在的正文字段。", field=key)
            body.pop(key[5:])
        for key in body_keys:
            body[key[5:]] = updates[key]
        self.touched.add(node["id"])
        if mode == "substantive":
            self.substantive.add(node["id"])
        confirms_acceptance = bool(updates.keys() & {"type", "work", "result", "humanInputRefs"})
        if node["type"] == "acceptance" and (confirms_acceptance or updates.get("validity") == "current"):
            self.acceptance_claims.add(node["id"])

    def apply(self, op):
        check(isinstance(op, dict), "操作需要对象。")
        name = op.get("op")
        if self.actor == "human":
            check(name == "input.add", "网页写入只用于添加人的记录。")
        if name == "project.create":
            fields(op, ("op", "project"))
            check(self.doc is None, "项目已经存在。")
            project = op["project"]
            fields(project, ("id", "title", "goal"), ("summary",))
            self.reserve(project["id"])
            self.doc = {"id": project["id"], "schemaVersion": "1", "revision": self.revision,
                        **{key: [] for key in SECTIONS if key != "project"},
                        "project": {k: v for k, v in project.items() if k != "id"}}
            return
        check(self.doc is not None, "先建立项目。", code="PROJECT_NOT_FOUND")
        if name == "project.update":
            fields(op, ("op", "set"), ("removeFields",))
            patch(self.doc["project"], op["set"], op.get("removeFields", []), PROJECT_FIELDS, ("summary",))
            self.touched.add(self.doc["id"])
        elif name == "requirement.create":
            fields(op, ("op", "requirement"))
            data = op["requirement"]
            fields(data, ("id", *REQ_FIELDS, "completion"), ("parent",))
            self.reserve(data["id"])
            self.doc["requirements"].append({**deepcopy(data), "nodes": []})
        elif name == "requirement.update":
            fields(op, ("op", "id", "set"), ("removeFields",))
            req = self.requirement(op["id"])
            patch(req, op["set"], op.get("removeFields", []), (*REQ_FIELDS, "completion", "parent"), ("parent",))
            self.touched.add(req["id"])
        elif name == "node.create":
            fields(op, ("op", "requirementId", "node"))
            self.create_node(op["requirementId"], op["node"])
        elif name == "node.update":
            self.update_node(op)
        elif name == "node.remove":
            fields(op, ("op", "id"))
            self.retire(op["id"], [])
        elif name == "node.merge":
            fields(op, ("op", "sourceIds", "target"))
            check(len(sequence(op["sourceIds"])) >= 2, "合并至少需要两个源节点。")
            self.restructure(op["sourceIds"], [op["target"]])
        elif name == "node.split":
            fields(op, ("op", "sourceId", "targets"))
            check(len(sequence(op["targets"])) >= 2, "拆分至少需要两个新节点。")
            self.restructure([op["sourceId"]], op["targets"])
        elif name == "relation.create":
            fields(op, ("op", "relation"))
            edge = op["relation"]
            fields(edge, ("id", "type", "from", "to"))
            self.reserve(edge["id"])
            self.doc["relations"].append(deepcopy(edge))
            self.touched.update((edge["from"], edge["to"]))
        elif name in ("relation.update", "relation.remove"):
            fields(op, ("op", "id", "set") if name == "relation.update" else ("op", "id"))
            edge = locate(self.doc["relations"], op["id"])
            self.touched.update((edge["id"], edge["from"], edge["to"]))
            if name == "relation.remove":
                self.doc["relations"].remove(edge)
            else:
                patch(edge, op["set"], [], ("type", "from", "to"), ())
                self.touched.update((edge["from"], edge["to"]))
        elif name == "evidence.add":
            fields(op, ("op", "evidence"))
            item = op["evidence"]
            fields(item, ("id", "kind", "target", "subject", "subjectRevision", "text"))
            self.reserve(item["id"])
            self.doc["evidence"].append(deepcopy(item))
            self.touched.add(item["subject"])
        elif name == "input.add":
            fields(op, ("op", "input"))
            self.add_input(op["input"])
        elif name == "input.handle":
            fields(op, ("op", "id", "handling"))
            check(op["handling"] in HANDLING, "处理状态不合法。")
            item = locate(self.doc["humanInputs"], op["id"])
            item["handling"] = op["handling"]
            self.touched.update((item["id"], item["target"]))
        elif name == "attachment.add":
            fields(op, ("op", "nodeId", "attachment"))
            _, node = self.node(op["nodeId"])
            metadata, content = prepare(self.workspace, op["attachment"])
            self.reserve(metadata["id"])
            digest = metadata["sha256"]
            if digest not in self.uploads:
                self.upload_size += len(content)
                check(self.upload_size <= 32 * 1024 * 1024, "一个批次最多上传 32 MiB 的附件。")
                self.uploads[digest] = content
            self.doc["attachments"].append(metadata)
            node["attachmentRefs"].append(metadata["id"])
            self.touched.add(node["id"])
        else:
            raise StoryError("INVALID_OPERATION", "不支持的修改操作。", op=name)

    def add_input(self, item):
        fields(item, ("id", "target", "kind", "text"),
               ("source", "recorder", "channel", "handling", "createdAt", "outcome"))
        channel = "web" if self.actor == "human" else "chat"
        expected = {"source": "human", "recorder": self.actor, "channel": channel, "handling": "pending"}
        check(all(item.get(key, value) == value for key, value in expected.items()), "人工记录的来源与入口不符。")
        if self.actor == "human":
            _, node = self.node(item["target"])
            check(item["kind"] in human_kinds(node), "此节点不提供这种人工输入。")
        if "outcome" in item or (self.actor == "human" and item["kind"] == "acceptance"):
            check(self.actor == "human" and item["kind"] == "acceptance"
                  and item.get("outcome") in ACCEPTANCE_OUTCOMES,
                  "请在网页为当前节点明确选择通过或退回。", code="INVALID_ACCEPTANCE_INPUT")
            expected["reviewScope"] = Graph(self.doc).acceptance_scope(item["target"])
        self.reserve(item["id"])
        self.doc["humanInputs"].append({**item, **expected})
        self.touched.add(item["target"])

    def update_revisions(self, previous):
        old = {n["id"]: n for req in previous["requirements"] for n in req["nodes"]} if previous else {}
        candidates = [node for req in self.doc["requirements"] for node in req["nodes"]]
        candidates += [entry["node"] for entry in self.doc["archive"] if entry["node"]["id"] in self.retired]
        for node in candidates:
            key = node["id"]
            if key in self.substantive and key in old and node != old[key]:
                node["revision"] = old[key]["revision"] + 1
