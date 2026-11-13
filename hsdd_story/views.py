"""Compact projections of one validated project snapshot."""

from .contract import CATALOG, check, human_kinds, status_label
from urllib.parse import quote


def short(text, limit=220):
    return text if len(text) <= limit else text[:limit - 1] + "…"


def node_summary(graph, node):
    stale = bool(graph.stale(node))
    state = {k: node[k] for k in ("id", "type", "title", "revision", "work", "result", "validity")}
    if stale:
        state["validity"] = "needs_review"
    acceptance = graph.acceptances.get(node["id"])
    decision = acceptance["state"] if acceptance else None
    if decision == "rejected":
        state.update(work="finished", result="unsatisfied", validity="current")
    if decision == "approved":
        state.update(work="active", result="unknown")
    if decision == "needs_review":
        state["validity"] = "needs_review"
    pending = [key for key in graph.input_ids(node) if graph.inputs[key]["handling"] != "incorporated"]
    label = CATALOG[node["type"]][0]
    if node["type"] == "verification":
        label = "人工验证" if node["body"]["method"] == "manual" else "自动化测试"
    if node.get("variant") == "fix":
        label = "问题修复"
    unmet = [key for key in graph.requires.get(node["id"], []) if not graph.complete[key]]
    status = status_label(state)
    if node["work"] == "planned" and unmet and state["validity"] == "current":
        status = "等待前提"
    status = {"rejected": "验收退回", "approved": "人已通过 · 待 AI 同步",
              "needs_review": "验收待复核"}.get(decision, status)
    next_step = node.get("next")
    if decision in ("accepted", "approved", "rejected") and (next_step or {}).get("actor") == "human":
        next_step = None
    return {**state, "typeLabel": label, "status": status, "summary": short(node["summary"]),
            "requirementId": graph.owners.get(node["id"]),
            "satisfied": graph.complete.get(node["id"], False), "unmet": unmet,
            "humanKinds": human_kinds(node), "pendingInputs": pending,
            **({"acceptance": acceptance} if acceptance else {}),
            **({"next": {**next_step, "text": short(next_step["text"])}} if next_step else {})}


def requirement_summary(graph, req):
    own = req["nodes"]
    blockers = [node["id"] for node in own if is_blocker(node_summary(graph, node))]
    complete = graph.complete[req["id"]]
    status = "已完成" if complete else "推进中" if req["completion"] else "完成条件未定义"
    return {**{key: req[key] for key in ("id", "parent", "title") if key in req},
            "type": "requirement", "typeLabel": "需求", "status": status,
            "satisfied": complete, "summary": short(req["summary"]), "goal": short(req["goal"]),
            "completionDefined": bool(req["completion"]), "blockers": blockers,
            "unmet": [key for key in graph.requires[req["id"]] if not graph.complete[key]]}


def object_summary(graph, key):
    if key in graph.requirements:
        return requirement_summary(graph, graph.requirements[key])
    return node_summary(graph, graph.nodes[key])


def is_blocker(state):
    return (state["work"] == "blocked" or state["validity"] == "needs_review"
            or state["result"] == "unsatisfied")


def overview(graph):
    blockers = []
    actions = []
    for node in graph.active.values():
        state = node_summary(graph, node)
        if is_blocker(state) or (state["unmet"] and state["work"] in ("active", "finished")):
            blockers.append(state)
        if state.get("next", {}).get("actor") == "human":
            actions.append(state)
    return {
        "project": graph.doc["project"],
        "requirements": [requirement_summary(graph, r) for r in graph.requirements.values()],
        "blockers": blockers,
        "humanActions": actions,
        "pendingInputs": pending_inputs(graph),
    }


def pending_inputs(graph):
    return [{**item, "text": short(item["text"])} for item in graph.inputs.values()
            if item["handling"] != "incorporated"]


def search_nodes(graph, args):
    scope = args.get("requirementId")
    allowed = None
    if scope is not None:
        check(scope in graph.requirements, "需求不存在。", code="INVALID_OPERATION", id=scope)
        children = {}
        for req in graph.requirements.values():
            children.setdefault(req.get("parent"), []).append(req["id"])
        allowed = {scope}
        pending = [scope]
        while pending:
            descendants = children.get(pending.pop(), [])
            pending.extend(descendants)
            allowed.update(descendants)
    query = args.get("query", "").strip().casefold()
    terms = query.split()
    matches = []
    for key, node in graph.active.items():
        if allowed is not None and graph.owners[key] not in allowed:
            continue
        if "type" in args and node["type"] != args["type"]:
            continue
        ident = key.casefold()
        title = node["title"].casefold()
        summary = node["summary"].casefold()
        if terms:
            text = "\n".join([ident, title, summary, *(value.casefold() for value in node["body"].values())])
            if not all(term in text for term in terms):
                continue
        state = node_summary(graph, node)
        if any(field in args and state[field] != args[field] for field in ("work", "result", "validity")):
            continue
        rank = (query != ident, query not in title, query not in summary)
        matches.append((rank, key, {field: state[field] for field in
                        ("id", "title", "summary", "type", "status", "requirementId", "work", "result", "validity")}))
    matches.sort()
    offset = args.get("offset", 0)
    end = offset + args.get("limit", 20)
    return {"nodes": [item for _, _, item in matches[offset:end]], "total": len(matches),
            "nextOffset": end if end < len(matches) else None}


def requirement_view(graph, key):
    check(key in graph.requirements, "需求不存在。", code="INVALID_OPERATION", id=key)
    req = graph.requirements[key]
    children = [r for r in graph.requirements.values() if r.get("parent") == key]
    visible = {key, *[n["id"] for n in req["nodes"]], *[r["id"] for r in children]}
    relations = [edge for edge in graph.doc["relations"]
                 if edge["from"] in visible or edge["to"] in visible]
    external = list(dict.fromkeys(i for edge in relations for i in (edge["from"], edge["to"])
                                  if i not in visible))
    return {
        "requirement": {**requirement_summary(graph, req), "goal": req["goal"],
                        "summary": req["summary"], "scope": req["scope"], "completion": req["completion"]},
        "nodes": [node_summary(graph, n) for n in req["nodes"]],
        "children": [requirement_summary(graph, r) for r in children],
        "relations": relations,
        "external": [object_summary(graph, i) for i in external],
    }


def node_view(graph, keys):
    records = []
    for key in keys:
        check(key in graph.nodes, "节点不存在。", code="INVALID_OPERATION", id=key)
        node = graph.nodes[key]
        relations = graph.edges.get(key, [])
        neighbors = list(dict.fromkeys(
            i for edge in relations for i in (edge["from"], edge["to"]) if i != key
        ))
        retired = graph.retired.get(key)
        attachments = []
        for ref in node["attachmentRefs"]:
            item = graph.attachments[ref]
            path = "/api/attachments/" + quote(ref, safe="")
            attachments.append({**item, "downloadPath": path + "?download=1",
                                "previewPath": path + ("/preview" if item["mediaType"] == "text/markdown" else "")})
        records.append({
            **node, "display": node_summary(graph, node), "relations": relations,
            "related": [object_summary(graph, i) for i in neighbors],
            "basisDetails": [{"ref": b["ref"], "revision": b["revision"],
                              "currentRevision": graph.nodes[b["ref"]]["revision"],
                              "title": graph.nodes[b["ref"]]["title"],
                              "archived": b["ref"] in graph.retired}
                             for b in node["basis"]],
            "evidence": [graph.evidence[i] for i in node["evidenceRefs"]],
            "attachments": attachments,
            "humanInputs": [graph.inputs[i] for i in graph.input_ids(node)],
            "archived": bool(retired),
            **({"archive": {k: v for k, v in retired.items() if k != "node"}} if retired else {}),
        })
    return {"nodes": records}
