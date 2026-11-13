"""Read and write the v1 XML format without losing fields."""

import xml.etree.ElementTree as ET

from .contract import (
    CATALOG, NODE_ATTRS, PROJECT_FIELDS, REQ_FIELDS, SECTIONS,
    StoryError, check, validate,
)

MAX_DOCUMENT = 16 * 1024 * 1024


def attributes(element, required=(), optional=(), integers=()):
    keys = element.attrib.keys()
    check(set(required) <= keys <= set(required) | set(optional),
          f"XML 属性不符合契约：{element.tag}。")
    values = dict(element.attrib)
    for key in integers:
        if key in values:
            check(values[key].isascii() and values[key].isdigit() and len(values[key]) <= 16,
                  "XML 版本不是有效整数。", field=key)
            values[key] = int(values[key])
    return values


def ordered(element, names, repeated=(), required=()):
    check(not (element.text or "").strip(), f"XML 容器不能含有正文：{element.tag}。")
    positions = {name: i for i, name in enumerate(names)}
    last = -1
    seen = set()
    for child in element:
        i = positions.get(child.tag, -1)
        check(i >= 0 and i >= last and (child.tag not in seen or child.tag in repeated),
              f"XML 元素未定义、重复或顺序错误：{element.tag}/{child.tag}。")
        check(not (child.tail or "").strip(), f"XML 容器不能含有额外正文：{element.tag}。")
        last = i
        seen.add(child.tag)
    check(set(required) <= seen, f"XML 容器缺少字段：{element.tag}。")
    return list(element)


def leaf(element, required=(), optional=(), integers=()):
    check(not len(element), f"XML 正文不能含有子元素：{element.tag}。")
    data = attributes(element, required, optional, integers)
    return data, element.text or ""


def empty(element, required, optional=(), integers=()):
    data, text = leaf(element, required, optional, integers)
    check(not text.strip(), f"XML 元素不接受正文：{element.tag}。")
    return data


def text_fields(element, names, required):
    attributes(element)
    return {child.tag: leaf(child)[1] for child in ordered(element, names, required=required)}


def items(element, name):
    attributes(element)
    return ordered(element, (name,), repeated=(name,))


def references(element, tag):
    return [empty(child, ("ref",))["ref"] for child in element.findall(tag)]


def read_node(element):
    data = attributes(element, NODE_ATTRS[:-1], ("variant",), ("revision",))
    check(data["type"] in CATALOG, "未知 XML 节点类型。")
    names = ("title", "summary", "body", "next", "basis", "evidenceRef", "humanInputRef", "attachmentRef")
    ordered(element, names, repeated=names[4:], required=names[:3])
    for tag in ("title", "summary"):
        data[tag] = leaf(element.find(tag))[1]
    data["body"] = text_fields(element.find("body"), [key for key, _ in CATALOG[data["type"]][1:]], ())
    nxt = element.find("next")
    if nxt is not None:
        attrs, text = leaf(nxt, ("actor",))
        data["next"] = {**attrs, "text": text}
    data["basis"] = [empty(child, ("ref", "revision"), integers=("revision",)) for child in element.findall("basis")]
    data["evidenceRefs"] = references(element, "evidenceRef")
    data["humanInputRefs"] = references(element, "humanInputRef")
    data["attachmentRefs"] = references(element, "attachmentRef")
    return data


def read_requirement(element):
    attrs = attributes(element, ("id",), ("parent",))
    names = (*REQ_FIELDS, "completion", "nodes")
    ordered(element, names, required=names)
    return {**attrs, **{key: leaf(element.find(key))[1] for key in REQ_FIELDS},
            "completion": [empty(gate, ("ref",))["ref"] for gate in items(element.find("completion"), "gate")],
            "nodes": [read_node(node) for node in items(element.find("nodes"), "node")]}


def read_item(element, required, optional=(), integers=()):
    attrs, text = leaf(element, required, optional, integers)
    return {**attrs, "text": text}


def read_archive(element):
    attrs = attributes(element, ("requirement", "reason", "atRevision"), integers=("atRevision",))
    ordered(element, ("node", "replacement"), repeated=("replacement",), required=("node",))
    return {**attrs, "node": read_node(element.find("node")), "replacements": references(element, "replacement")}


def read_change(element):
    attrs = attributes(element, ("revision", "actor", "reason"), integers=("revision",))
    ordered(element, ("affected",), repeated=("affected",), required=("affected",))
    return {**attrs, "affected": references(element, "affected")}


def parse(raw):
    try:
        check(len(raw) <= MAX_DOCUMENT, "XML 文件超过 16 MiB。")
        source = raw.decode("utf-8-sig")
        check("<!DOCTYPE" not in source and "<!ENTITY" not in source, "XML 不支持 DTD 或外部实体。")
        root = ET.fromstring(source)
        check(root.tag == "story", "XML 根元素必须为 story。")
        doc = attributes(root, ("schemaVersion", "id", "revision"), integers=("revision",))
        check(doc["schemaVersion"] == "1", "不支持的 XML 格式版本。", code="UNSUPPORTED_SCHEMA")
        ordered(root, SECTIONS, required=SECTIONS[:-1])
        attachments = root.find("attachments")
        doc.update(
            project=text_fields(root.find("project"), PROJECT_FIELDS, PROJECT_FIELDS[:2]),
            requirements=[read_requirement(e) for e in items(root.find("requirements"), "requirement")],
            relations=[empty(e, ("id", "type", "from", "to")) for e in items(root.find("relations"), "edge")],
            evidence=[read_item(e, ("id", "kind", "target", "subject", "subjectRevision"),
                                integers=("subjectRevision",)) for e in items(root.find("evidence"), "item")],
            humanInputs=[read_item(e, ("id", "target", "kind", "source", "recorder", "channel", "handling"),
                                   ("createdAt", "outcome", "reviewScope"))
                         for e in items(root.find("humanInputs"), "input")],
            archive=[read_archive(e) for e in items(root.find("archive"), "retired")],
            changes=[read_change(e) for e in items(root.find("changes"), "change")],
            attachments=[empty(e, ("id", "filename", "mediaType", "size", "sha256"), integers=("size",))
                         for e in items(attachments, "attachment")] if attachments is not None else [],
        )
        return doc, validate(doc)
    except StoryError as exc:
        if exc.code == "UNSUPPORTED_SCHEMA":
            raise
        raise StoryError("INVALID_DOCUMENT", str(exc), **exc.details) from exc
    except (UnicodeError, ET.ParseError, ValueError, TypeError) as exc:
        raise StoryError("INVALID_DOCUMENT", f"无法读取项目 XML：{exc}") from exc


def child(parent, tag, attrs=None, text=None):
    element = ET.SubElement(parent, tag, {key: str(value) for key, value in (attrs or {}).items()})
    element.text = text
    return element


def write_texts(parent, data, names):
    for key in names:
        if key in data:
            child(parent, key, text=data[key])


def write_refs(parent, tag, refs):
    for ref in refs:
        child(parent, tag, {"ref": ref})


def write_node(parent, node):
    entry = child(parent, "node", {key: node[key] for key in NODE_ATTRS if key in node})
    write_texts(entry, node, ("title", "summary"))
    write_texts(child(entry, "body"), node["body"], [key for key, _ in CATALOG[node["type"]][1:]])
    if "next" in node:
        child(entry, "next", {"actor": node["next"]["actor"]}, node["next"]["text"])
    for basis in node["basis"]:
        child(entry, "basis", basis)
    write_refs(entry, "evidenceRef", node["evidenceRefs"])
    write_refs(entry, "humanInputRef", node["humanInputRefs"])
    write_refs(entry, "attachmentRef", node["attachmentRefs"])


def serialize(doc):
    root = ET.Element("story", {key: str(doc[key]) for key in ("schemaVersion", "id", "revision")})
    sections = {key: child(root, key) for key in SECTIONS if key != "attachments" or doc[key]}
    write_texts(sections["project"], doc["project"], PROJECT_FIELDS)
    for req in doc["requirements"]:
        entry = child(sections["requirements"], "requirement", {key: req[key] for key in ("id", "parent") if key in req})
        write_texts(entry, req, REQ_FIELDS)
        write_refs(child(entry, "completion"), "gate", req["completion"])
        nodes = child(entry, "nodes")
        for node in req["nodes"]:
            write_node(nodes, node)
    for item in doc["relations"]:
        child(sections["relations"], "edge", item)
    for section, tag in (("evidence", "item"), ("humanInputs", "input")):
        for item in doc[section]:
            child(sections[section], tag, {key: v for key, v in item.items() if key != "text"}, item["text"])
    for item in doc["archive"]:
        entry = child(sections["archive"], "retired", {key: item[key] for key in ("requirement", "reason", "atRevision")})
        write_node(entry, item["node"])
        write_refs(entry, "replacement", item["replacements"])
    for item in doc["changes"]:
        entry = child(sections["changes"], "change", {key: item[key] for key in ("revision", "actor", "reason")})
        write_refs(entry, "affected", item["affected"])
    for item in doc["attachments"]:
        child(sections["attachments"], "attachment", item)
    ET.indent(root)
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True).replace(b"\r", b"&#13;") + b"\n"
    check(len(raw) <= MAX_DOCUMENT, "XML 文件超过 16 MiB。")
    return raw
