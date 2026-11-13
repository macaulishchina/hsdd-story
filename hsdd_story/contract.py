"""Data contract and deterministic graph checks."""

from collections import defaultdict, deque
from datetime import datetime
from hashlib import sha256
import json
import re

CATALOG = {
    "analysis": ("需求分析", ("problem", "问题"), ("scope", "范围"), ("criteria", "验收依据")),
    "investigation": ("调研", ("questions", "待回答问题"), ("findings", "调查发现"), ("unknowns", "未知项")),
    "dependency": ("只读依赖", ("target", "引用对象"), ("capability", "所需能力"), ("verification", "核对情况")),
    "assumption": ("假设", ("statement", "假设内容"), ("verification", "验证方法"), ("impact", "影响")),
    "decision": ("决策", ("options", "候选方案"), ("choice", "当前选择"), ("rationale", "理由")),
    "design": ("设计", ("structure", "结构"), ("contracts", "接口与约定"), ("constraints", "约束")),
    "implementation": ("实现", ("scope", "实现范围"), ("outputs", "产出"), ("remaining", "剩余工作")),
    "integration": ("集成", ("inputs", "汇合对象"), ("contracts", "连接约定"), ("observations", "实际情况")),
    "verification": ("验证", ("checks", "检查项"), ("method", "方式"), ("observations", "执行情况")),
    "review": ("评审", ("target", "评审对象"), ("findings", "发现"), ("disposition", "处理情况")),
    "acceptance": ("交付验收", ("criteria", "验收条件"), ("coverage", "覆盖情况"), ("unresolved", "未满足项")),
    "delivery": ("发布交付", ("deliverables", "交付内容"), ("execution", "操作记录"), ("handoff", "交接说明")),
    "follow_up": ("后续事项", ("item", "待办内容"), ("reason", "暂缓原因"), ("revisit", "重启条件")),
}
WORK = ("planned", "active", "blocked", "finished", "deferred", "cancelled")
RESULT = ("unknown", "satisfied", "unsatisfied", "inconclusive", "skipped")
VALIDITY = ("current", "needs_review")
ACTORS = ("ai", "human", "external")
RELATIONS = ("requires", "references", "follow_up")
EVIDENCE = ("code", "test", "document", "observation", "decision")
INPUT_KINDS = ("comment", "answer", "decision", "acceptance")
ACCEPTANCE_OUTCOMES = ("accepted", "rejected")
HANDLING = ("pending", "incorporated", "needs_clarification")
SECTIONS = ("project", "requirements", "relations", "evidence", "humanInputs", "archive", "changes", "attachments")
PROJECT_FIELDS = ("title", "goal", "summary")
REQ_FIELDS = ("title", "goal", "scope", "summary")
NODE_ATTRS = ("id", "type", "revision", "work", "result", "validity", "variant")
NODE_LISTS = ("basis", "evidenceRefs", "humanInputRefs", "attachmentRefs")
NODE_FIELDS = (*NODE_ATTRS, "title", "summary", "body", "next", *NODE_LISTS)
OPERATIONS = (
    "project.create", "project.update", "requirement.create", "requirement.update",
    "node.create", "node.update", "node.remove", "node.merge", "node.split",
    "relation.create", "relation.update", "relation.remove", "evidence.add",
    "input.add", "input.handle", "attachment.add",
)
ATTACHMENT_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "webp": "image/webp", "md": "text/markdown", "markdown": "text/markdown",
}
ATTACHMENT_LIMITS = {media: (1 if media == "text/markdown" else 8) * 1024 * 1024
                     for media in set(ATTACHMENT_TYPES.values())}
FORBIDDEN_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
MAX_TEXT = 80_000


class StoryError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def payload(self):
        return {"ok": False, "code": self.code, "message": str(self), "details": self.details}


def check(condition, message, *, code="INVALID_STRUCTURE", **details):
    if not condition:
        raise StoryError(code, message, **details)


def fields(value, required, optional=()):
    check(isinstance(value, dict), "需要对象。")
    required = set(required)
    missing = required - value.keys()
    extra = value.keys() - required - set(optional)
    check(not missing and not extra, "对象字段不符合契约。", missing=sorted(missing), extra=sorted(extra))


def string(value):
    check(isinstance(value, str) and 0 < len(value.strip()) <= MAX_TEXT
          and len(value) <= MAX_TEXT and not FORBIDDEN_XML_CHARS.search(value),
          "文本不能为空、超过长度上限或包含 XML 不支持的字符。")


def ident(value):
    string(value)
    check(len(value) <= 128 and not any(c.isspace() for c in value), "ID 不允许空白，最长 128 字符。")


def filename_type(name):
    string(name)
    check(len(name) <= 180 and name == name.strip() and not re.search(r"[/\\:\x00-\x1f]", name),
          "filename 必须是文件名，不含路径。")
    stem, dot, extension = name.rpartition(".")
    media = ATTACHMENT_TYPES.get(extension.lower())
    check(stem and dot and media, "附件支持 PNG、JPEG、GIF、WebP 和 Markdown。")
    return media


def number(value, minimum=1):
    check(type(value) is int and minimum <= value <= 2**53 - 1, "版本必须是有效整数。")


def sequence(value):
    check(isinstance(value, list), "需要数组。")
    return value


def reference_list(value):
    for item in sequence(value):
        ident(item)
    check(len(set(value)) == len(value), "引用不能重复。")


def initial_fields(kind):
    names = [field for field, _ in CATALOG[kind][1:]]
    return names[:2 if kind in ("dependency", "verification", "follow_up") else 1]


def human_kinds(node):
    if node["type"] == "analysis":
        return ["comment", "answer", "decision"]
    if node["type"] == "decision":
        return ["comment", "decision"]
    if node["type"] == "acceptance":
        return ["comment", "acceptance"]
    if node["type"] == "verification" and node["body"].get("method") == "manual":
        return ["comment", "answer"]
    return []


def schema(types=None):
    selected = list(CATALOG) if types is None else sequence(types)
    reference_list(selected)
    check(all(kind in CATALOG for kind in selected), "节点类型不存在。", code="INVALID_OPERATION")
    return {
        "schemaVersion": "1",
        "nodeTypes": {
            kind: {"label": CATALOG[kind][0],
                   "fields": [{"key": key, "label": label} for key, label in CATALOG[kind][1:]],
                   "initialRequired": initial_fields(kind),
                   "finishedRequiresAllFields": True,
                   "humanInputKinds": human_kinds({"type": kind, "body": {"method": "manual"}}),
                   **({"methods": ["automated", "manual"], "humanInputOnlyFor": "manual"}
                      if kind == "verification" else {}),
                   **({"variants": ["fix"]} if kind == "implementation" else {})}
            for kind in selected
        },
        "nodeFields": list(NODE_FIELDS),
        "work": list(WORK), "result": list(RESULT), "validity": list(VALIDITY), "actors": list(ACTORS),
        "evidenceKinds": list(EVIDENCE), "humanInputKinds": list(INPUT_KINDS),
        "handling": list(HANDLING),
        "acceptanceOutcomes": list(ACCEPTANCE_OUTCOMES),
        "relations": {
            "requires": "前置 → 后继，必要前提",
            "references": "依据 → 使用者，提供上下文",
            "follow_up": "当前工作 → 后续事项",
        },
        "operations": list(OPERATIONS),
        "attachments": {"extensions": ATTACHMENT_TYPES, "maxBytes": ATTACHMENT_LIMITS,
                        "sources": ["sourcePath", "text", "dataBase64"]},
    }


def validate_node(node):
    fields(node, set(NODE_FIELDS) - {"next", "variant"}, ("next", "variant"))
    ident(node["id"])
    check(node["type"] in CATALOG, "未知节点类型。", id=node["id"])
    number(node["revision"])
    for key, choices in (("work", WORK), ("result", RESULT), ("validity", VALIDITY)):
        check(node[key] in choices, "节点状态不合法。", id=node["id"], field=key)
    if "variant" in node:
        check(node["type"] == "implementation" and node["variant"] == "fix", "节点变体不合法。")
    string(node["title"])
    string(node["summary"])
    names = [key for key, _ in CATALOG[node["type"]][1:]]
    finished = node["work"] == "finished"
    fields(node["body"], names if finished else initial_fields(node["type"]), names)
    for value in node["body"].values():
        string(value)
    if node["type"] == "verification":
        check(node["body"]["method"] in ("manual", "automated"), "验证方式必须为 automated 或 manual。")
    if "next" in node:
        fields(node["next"], ("actor", "text"))
        check(node["next"]["actor"] in ACTORS, "下一步参与方无效。")
        string(node["next"]["text"])
    for key in ("evidenceRefs", "humanInputRefs", "attachmentRefs"):
        reference_list(node[key])
    for item in sequence(node["basis"]):
        fields(item, ("ref", "revision"))
        ident(item["ref"])
        number(item["revision"])
    reference_list([item["ref"] for item in node["basis"]])
    if finished:
        check(node["result"] != "unknown", "已结束节点缺少结论。", id=node["id"])
        if node["result"] in ("satisfied", "unsatisfied"):
            check(node["evidenceRefs"] or node["humanInputRefs"],
                  "成功或失败的结束结论需要证据或人工记录。", id=node["id"])


def dependency_order(deps, label):
    pending = {key: len(prereqs) for key, prereqs in deps.items()}
    successors = defaultdict(list)
    for key, prereqs in deps.items():
        for prerequisite in prereqs:
            check(prerequisite in deps, "引用对象不存在。", id=key, ref=prerequisite)
            successors[prerequisite].append(key)
    ready = deque(key for key, count in pending.items() if not count)
    ordered = []
    while ready:
        key = ready.popleft()
        ordered.append(key)
        for successor in successors[key]:
            pending[successor] -= 1
            if not pending[successor]:
                ready.append(successor)
    check(len(ordered) == len(deps), f"{label}存在循环。", ids=[k for k, n in pending.items() if n][:12])
    return ordered


class Graph:
    """Indexed snapshot for validation and consistent derived views."""

    def __init__(self, doc):
        self.doc = doc
        self.requirements = {r["id"]: r for r in doc["requirements"]}
        self.active = {n["id"]: n for r in doc["requirements"] for n in r["nodes"]}
        self.owners = {n["id"]: r["id"] for r in doc["requirements"] for n in r["nodes"]}
        self.retired = {r["node"]["id"]: r for r in doc["archive"]}
        self.nodes = {**self.active, **{key: r["node"] for key, r in self.retired.items()}}
        self.evidence = {e["id"]: e for e in doc["evidence"]}
        self.attachments = {item["id"]: item for item in doc["attachments"]}
        self.inputs = {i["id"]: i for i in doc["humanInputs"]}
        self.inputs_by_target = defaultdict(list)
        for item in self.inputs.values():
            self.inputs_by_target[item["target"]].append(item["id"])
        self.edges = defaultdict(list)
        self.requires = {key: [] for key in (*self.requirements, *self.active)}
        for edge in doc["relations"]:
            self.edges[edge["from"]].append(edge)
            if edge["to"] != edge["from"]:
                self.edges[edge["to"]].append(edge)
            if edge["type"] == "requires" and edge["to"] in self.requires:
                self.requires[edge["to"]].append(edge["from"])

    def stale(self, node):
        basis = [b["ref"] for b in node["basis"]
                 if b["revision"] < self.nodes[b["ref"]]["revision"]]
        evidence = [ref for ref in node["evidenceRefs"] if
                    self.evidence[ref]["subjectRevision"] <
                    self.nodes[self.evidence[ref]["subject"]]["revision"]]
        return basis + evidence

    def input_ids(self, node):
        return list(dict.fromkeys([*node["humanInputRefs"], *self.inputs_by_target[node["id"]]]))

    def acceptance_scope(self, key):
        """Bind a decision to the criteria and the reviewed prerequisite graph."""
        reviewed = {}
        pending = [key]
        while pending:
            ref = pending.pop()
            if ref in reviewed:
                continue
            prereqs = sorted(self.requires[ref])
            if ref == key:
                node = self.active[ref]
                version = [self.owners[ref], node["body"]["criteria"]]
            elif ref in self.requirements:
                req = self.requirements[ref]
                gates = sorted(req["completion"])
                version = [req["goal"], req["scope"], gates]
                prereqs = sorted(set(prereqs + gates))
            else:
                version = self.active[ref]["revision"]
            reviewed[ref] = [version, prereqs]
            pending.extend(prereqs)
        raw = json.dumps(reviewed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(raw.encode("utf-8")).hexdigest()

    def acceptance(self, node):
        # Append order is the order in which the service received decisions.
        key = node["id"]
        latest = next((self.inputs[ref] for ref in reversed(self.inputs_by_target[key])
                       if "outcome" in self.inputs[ref]), None)
        claimed = (node["work"], node["result"]) == ("finished", "satisfied")
        if latest is None:
            return {"state": "needs_review" if claimed else "pending"}
        decision = {"inputId": latest["id"], "outcome": latest["outcome"]}
        if latest["reviewScope"] != self.acceptance_scope(key):
            state = "needs_review"
        elif latest["outcome"] == "rejected":
            state = "rejected"
        elif claimed and latest["id"] in node["humanInputRefs"]:
            state = "accepted"
        else:
            state = "approved"
        return {**decision, "state": state}

    def compute(self):
        deps = {key: list(items) for key, items in self.requires.items()}
        for req in self.requirements.values():
            deps[req["id"]] = list(dict.fromkeys([*deps[req["id"]], *req["completion"]]))
        self.acceptances = {key: self.acceptance(node) for key, node in self.active.items()
                            if node["type"] == "acceptance"}
        self.complete = {}
        for key in dependency_order(deps, "必要依赖与完成条件"):
            if key in self.requirements:
                own = bool(self.requirements[key]["completion"])
            else:
                node = self.active[key]
                own = (node["work"], node["result"], node["validity"]) == ("finished", "satisfied", "current")
                own = own and not self.stale(node)
                if key in self.acceptances:
                    own = own and self.acceptances[key]["state"] == "accepted"
            self.complete[key] = own and all(self.complete[p] for p in deps[key])
        self.warnings = self.get_warnings()
        return self

    def get_warnings(self):
        warnings = []
        for req in self.requirements.values():
            if not req["completion"]:
                warnings.append({"code": "UNDEFINED_COMPLETION", "id": req["id"], "message": "完成条件未定义。"})
        for node in self.active.values():
            key = node["id"]
            unmet = [ref for ref in self.requires[key] if not self.complete[ref]]
            if unmet and node["work"] in ("active", "finished"):
                warnings.append({"code": "UNMET_PREREQUISITES", "id": key,
                                 "refs": unmet, "message": "已记录实际工作，必要前提仍未满足。"})
            if node["validity"] == "needs_review" or self.stale(node):
                warnings.append({"code": "NEEDS_REVIEW", "id": key, "message": "既有结论需要复核。"})
            acceptance = self.acceptances.get(key, {}).get("state")
            if acceptance == "needs_review":
                warnings.append({"code": "ACCEPTANCE_NEEDS_REVIEW", "id": key,
                                 "message": "需要人工重新确认当前验收范围。"})
            if acceptance == "rejected":
                warnings.append({"code": "ACCEPTANCE_REJECTED", "id": key, "message": "人工验收已退回。"})
            if node["result"] in ("unsatisfied", "skipped", "inconclusive"):
                warnings.append({"code": "RESULT_" + node["result"].upper(), "id": key,
                                 "message": status_label(node)})
            if node["work"] == "blocked" and not node.get("next"):
                warnings.append({"code": "MISSING_NEXT", "id": key, "message": "受阻节点尚未填写下一步。"})
        for item in self.inputs.values():
            if item["handling"] != "incorporated":
                warnings.append({"code": "HUMAN_INPUT_PENDING", "id": item["target"],
                                 "refs": [item["id"]], "message": "有人工输入待处理。"})
        return warnings


def status_label(node):
    work, result, validity = (node[key] for key in ("work", "result", "validity"))
    kind = node["type"]
    if validity == "needs_review":
        prefix = {"satisfied": "曾通过", "unsatisfied": "曾未通过"}.get(result)
        return f"{prefix} · 待复核" if prefix else "待复核"
    if work == "finished":
        if result == "satisfied":
            return {"verification": "检查通过", "acceptance": "验收接受", "delivery": "已交付",
                    "analysis": "分析完成", "dependency": "已核对", "assumption": "已证实"}.get(kind, "已完成")
        return {"unsatisfied": "未通过" if kind != "assumption" else "假设不成立",
                "skipped": "已跳过", "inconclusive": "尚无定论"}.get(result, "已结束")
    if kind == "acceptance" and work == "planned":
        return "待验收"
    return {"planned": "待开始", "active": "进行中", "blocked": "受阻",
            "deferred": "后续", "cancelled": "已取消"}[work]


def validate(doc):
    fields(doc, ("schemaVersion", "id", "revision", *SECTIONS))
    check(doc["schemaVersion"] == "1", "不支持的 XML 格式版本。", code="UNSUPPORTED_SCHEMA")
    ident(doc["id"])
    number(doc["revision"])
    fields(doc["project"], PROJECT_FIELDS[:2], PROJECT_FIELDS[2:])
    for value in doc["project"].values():
        string(value)
    ids = {doc["id"]}

    def register(key):
        ident(key)
        check(key not in ids, "ID 已存在或已经使用。", id=key)
        ids.add(key)

    for req in sequence(doc["requirements"]):
        fields(req, ("id", *REQ_FIELDS, "completion", "nodes"), ("parent",))
        register(req["id"])
        for key in REQ_FIELDS:
            string(req[key])
        if "parent" in req:
            ident(req["parent"])
        reference_list(req["completion"])
        for node in sequence(req["nodes"]):
            validate_node(node)
            register(node["id"])
    for retired in sequence(doc["archive"]):
        fields(retired, ("requirement", "reason", "atRevision", "node", "replacements"))
        validate_node(retired["node"])
        register(retired["node"]["id"])
        ident(retired["requirement"])
        string(retired["reason"])
        number(retired["atRevision"])
        check(retired["atRevision"] <= doc["revision"], "归档引用未来项目版本。")
        reference_list(retired["replacements"])
    for edge in sequence(doc["relations"]):
        fields(edge, ("id", "type", "from", "to"))
        register(edge["id"])
        check(edge["type"] in RELATIONS, "未知关系。")
        ident(edge["from"])
        ident(edge["to"])
    for item in sequence(doc["evidence"]):
        fields(item, ("id", "kind", "target", "subject", "subjectRevision", "text"))
        register(item["id"])
        check(item["kind"] in EVIDENCE, "未知证据类型。")
        ident(item["subject"])
        number(item["subjectRevision"])
        string(item["target"])
        string(item["text"])
    for item in sequence(doc["attachments"]):
        fields(item, ("id", "filename", "mediaType", "size", "sha256"))
        register(item["id"])
        check(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", item["id"]),
              "附件 ID 只支持字母、数字、点、下划线和连字符。", id=item["id"])
        media = filename_type(item["filename"])
        check(item["mediaType"] == media, "附件类型与文件名不符。", id=item["id"])
        number(item["size"])
        check(item["size"] <= ATTACHMENT_LIMITS[media], "附件超过大小上限。", id=item["id"])
        check(isinstance(item["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]),
              "附件校验值无效。", id=item["id"])
    for item in sequence(doc["humanInputs"]):
        fields(item, ("id", "target", "kind", "source", "recorder", "channel", "handling", "text"),
               ("createdAt", "outcome", "reviewScope"))
        register(item["id"])
        ident(item["target"])
        check(item["source"] == "human" and item["kind"] in INPUT_KINDS
              and item["handling"] in HANDLING, "人工记录格式无效。")
        check(item["channel"] in ("web", "chat") and item["recorder"] in ("human", "ai")
              and (item["channel"] != "web" or item["recorder"] == "human"), "人工记录来源无效。")
        if "outcome" in item or "reviewScope" in item:
            check(item.get("outcome") in ACCEPTANCE_OUTCOMES and item["kind"] == "acceptance"
                  and item["channel"] == "web" and item["recorder"] == "human"
                  and isinstance(item.get("reviewScope"), str)
                  and re.fullmatch(r"[0-9a-f]{64}", item["reviewScope"]),
                  "验收结论必须来自网页，且带有验收范围。", id=item["id"])
        string(item["text"])
        if "createdAt" in item:
            try:
                valid_date = datetime.fromisoformat(item["createdAt"]).tzinfo is not None
            except (ValueError, TypeError):
                valid_date = False
            check(valid_date, "createdAt 需要带时区的 ISO 8601 时间。")
    previous = 0
    for change in sequence(doc["changes"]):
        fields(change, ("revision", "actor", "reason", "affected"))
        number(change["revision"])
        check(previous < change["revision"] <= doc["revision"], "变化记录版本顺序不合法。")
        previous = change["revision"]
        check(change["actor"] in ("ai", "human"), "变化来源不合法。")
        string(change["reason"])
        reference_list(change["affected"])
        check(change["affected"], "变化记录缺少关联对象。")

    graph = Graph(doc)
    parents = {key: [req["parent"]] if "parent" in req else [] for key, req in graph.requirements.items()}
    dependency_order(parents, "需求层次")
    for key, req in graph.requirements.items():
        for ref in req["completion"]:
            check(ref != key, "需求不能以自己作为完成条件。", id=key)
            owner = graph.owners.get(ref, ref)
            while owner in parents and owner != key and parents[owner]:
                owner = parents[owner][0]
            check(owner == key and ref in graph.requires, "完成条件必须属于当前或下级需求。",
                  id=key, ref=ref)
    signatures = set()
    for edge in doc["relations"]:
        a, b = edge["from"], edge["to"]
        key = (edge["type"], a, b)
        check(a in graph.requires and b in graph.requires and a != b, "连线端点无效。", id=edge["id"])
        check(key not in signatures, "连线重复。", id=edge["id"])
        signatures.add(key)
    replacements = {key: [] for key in graph.nodes}
    for key, entry in graph.retired.items():
        check(entry["requirement"] in graph.requirements, "归档需求不存在。", id=key)
        replacements[key] = entry["replacements"]
    dependency_order(replacements, "替代关系")

    for item in graph.evidence.values():
        subject = graph.nodes.get(item["subject"])
        check(subject and item["subjectRevision"] <= subject["revision"], "证据目标或修订号无效。",
              id=item["id"])
    for item in graph.inputs.values():
        check(item["target"] in graph.nodes or item["target"] in graph.requirements,
              "人工记录原始目标不存在。", id=item["id"])
        if "outcome" in item:
            check(item["target"] in graph.nodes, "验收记录必须关联节点。", id=item["id"])
    for node in graph.nodes.values():
        for item in node["basis"]:
            target = graph.nodes.get(item["ref"])
            check(target and item["revision"] <= target["revision"], "结论依据不存在或引用未来修订。", id=node["id"])
        check(set(node["evidenceRefs"]) <= graph.evidence.keys(), "证据引用不存在。", id=node["id"])
        check(set(node["humanInputRefs"]) <= graph.inputs.keys(), "人工记录引用不存在。", id=node["id"])
        check(set(node["attachmentRefs"]) <= graph.attachments.keys(), "附件引用不存在。", id=node["id"])
    return graph.compute()
