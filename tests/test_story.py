"""Acceptance tests for XML persistence and the shared human/AI entry points."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import uvicorn

from hsdd_story.contract import CATALOG, StoryError
from hsdd_story.server import create_app
from hsdd_story.store import Store
from hsdd_story.xmlio import parse, serialize


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "export.story.xml"


def workspace(test, demo=True):
    tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
    test.addCleanup(tmp.cleanup)
    store = Store(tmp.name)
    if demo:
        store.path.parent.mkdir()
        shutil.copyfile(EXAMPLE, store.path)
    return store


def batch(snapshot, *operations, reason="验收场景"):
    return {"projectId": snapshot["projectId"] or "P01", "baseRevision": snapshot["revision"],
            "readToken": snapshot["readToken"], "reason": reason, "operations": list(operations)}


def update(key, mode="substantive", **values):
    return {"op": "node.update", "id": key, "changeKind": mode, "set": values}


def follow_up(key, refs=()):
    return {"requirementId": "R01", "node": {
        "id": key, "type": "follow_up", "title": "后续计划 " + key, "summary": "延后。",
        "body": {"item": "异步导出", "reason": "超出本期范围"}, "humanInputRefs": list(refs),
    }}


class StoreAcceptance(unittest.TestCase):
    def setUp(self):
        self.store = workspace(self)

    def read(self, **args):
        return self.store.read(args)

    def apply(self, *operations):
        return self.store.apply(batch(self.read(), *operations))

    def node(self, key):
        return self.read(view="nodes", nodeIds=[key])["data"]["nodes"][0]

    def test_sample_and_roundtrip(self):
        doc, graph = parse(EXAMPLE.read_bytes())
        self.assertEqual(set(CATALOG), {node["type"] for node in graph.active.values()})
        self.assertEqual(len(graph.active), 16)
        self.assertFalse(graph.complete["R01"])
        self.assertFalse(graph.complete["R02"])
        self.assertEqual(parse(serialize(doc))[0], doc)
        data = self.read(view="requirement", requirementId="R02")["data"]
        self.assertEqual({n["id"] for n in data["nodes"]}, {"IM01", "IM02", "VT01", "IG01", "AT01"})
        self.assertIn("RV01", {n["id"] for n in data["external"]})
        self.assertEqual(self.node("AT01")["display"]["status"], "曾未通过 · 待复核")
        self.assertEqual(self.read()["data"]["pendingInputs"][0]["id"], "H02")

    def test_create_empty_project_and_reopen(self):
        store = workspace(self, demo=False)
        read = store.read({})
        self.assertFalse(read["exists"])
        created = store.apply(batch(
            read,
            {"op": "project.create", "project": {"id": "P01", "title": "示例", "goal": "验证初始化"}},
            {"op": "requirement.create", "requirement": {"id": "R01", "title": "验收", "goal": "交付",
             "scope": "本地", "summary": "待开始", "completion": ["A01"]}},
            {"op": "node.create", "requirementId": "R01", "node": {
                "id": "A01", "type": "acceptance", "title": "验收", "summary": "等待",
                "body": {"criteria": "功能符合要求"}}},
        ))
        self.assertEqual(created["revision"], 1)
        reopened = Store(store.workspace).read({"view": "nodes", "nodeIds": ["A01"]})
        self.assertEqual(reopened["data"]["nodes"][0]["work"], "planned")

    def test_noop_editorial_and_incremental_read(self):
        first = self.read()
        same = self.apply(update("IM01", "editorial", summary=self.node("IM01")["summary"]))
        self.assertFalse(same["changed"])
        self.assertEqual(first["readToken"], same["readToken"])
        revision = self.node("IM01")["revision"]
        saved = self.apply(update("IM01", "editorial", summary="已完成编码，待复测。"))
        self.assertEqual(saved["revision"], first["revision"] + 1)
        self.assertEqual(self.node("IM01")["revision"], revision)
        delta = self.read(view="changes", sinceRevision=first["revision"], sinceReadToken=first["readToken"])
        self.assertEqual(delta["data"]["changedIds"], ["IM01"])
        after_restart = Store(self.store.workspace).read({
            "view": "changes", "sinceRevision": first["revision"], "sinceReadToken": first["readToken"],
        })
        self.assertTrue(after_restart["data"]["refreshRequired"])

    def test_stale_revision_retains_old_results(self):
        old = self.node("IM01")["revision"]
        saved = self.apply(update("DS01", **{"body.contracts": "接口约定改变，需复核引用。"}))
        self.assertIn("IM01", saved["reviewRequiredIds"])
        self.assertEqual(self.node("IM01")["validity"], "needs_review")
        self.assertEqual(self.node("IM01")["revision"], old)
        self.assertEqual(self.node("IM01")["result"], "satisfied")

    def test_node_reference_resolves_original_node_and_checks_workspace(self):
        reference = self.node("FU01")["nodeRef"]
        another_project = workspace(self)
        with self.assertRaises(StoryError) as mismatch:
            another_project.read({"nodeRef": reference})
        self.assertEqual(mismatch.exception.code, "PROJECT_MISMATCH")
        self.apply(update("FU01", "editorial", title="异步导出调研"))
        self.assertEqual(self.read(nodeRef=reference)["data"]["nodes"][0]["title"], "异步导出调研")
        self.apply({"op": "node.remove", "id": "FU01"},
                   {"op": "relation.remove", "id": "E21"}, {"op": "relation.remove", "id": "E22"})
        archived = Store(self.store.workspace).read({"nodeRef": reference})["data"]["nodes"][0]
        self.assertEqual(archived["nodeRef"], reference)
        self.assertTrue(archived["archived"])

    def test_node_reference_round_trips_encoded_ids(self):
        key = "验证:中文/百分号%"
        self.apply({"op": "node.create", **follow_up(key)})
        ref = self.node(key)["nodeRef"]
        self.assertEqual(self.read(nodeRef=ref)["data"]["nodes"][0]["id"], key)
        with self.assertRaises(StoryError) as invalid:
            self.read(nodeRef=ref.replace("%25", "%FF"))
        self.assertEqual(invalid.exception.code, "INVALID_OPERATION")

    def test_retest_replaces_current_evidence(self):
        saved = self.apply(
            update("IG01", validity="current", evidenceRefs=["EV-IG-NEW"], basis=[
                {"ref": "IM01", "revision": 2}, {"ref": "IM02", "revision": 1}, {"ref": "VT01", "revision": 1},
            ], **{"body.observations": "新的模拟联调已通过。"}),
            {"op": "evidence.add", "evidence": {"id": "EV-IG-NEW", "kind": "test",
             "target": "simulation:retest", "subject": "IG01", "subjectRevision": 2,
             "text": "新的模拟联调记录。"}},
        )
        self.assertIn("AT01", saved["reviewRequiredIds"])
        node = self.node("IG01")
        self.assertEqual(node["display"]["status"], "已完成")
        self.assertEqual(node["revision"], 2)
        self.assertEqual(self.node("AT01")["result"], "unsatisfied")
        self.assertIn("EV-IG01", {e["id"] for e in self.store.load()[0]["evidence"]})

    def test_archiving_split_merge_and_remove_preserves_input(self):
        targets = [follow_up("F02", ["H03"]), follow_up("F03")]
        self.apply(
            {"op": "node.split", "sourceId": "FU01", "targets": targets},
            {"op": "relation.update", "id": "E21", "set": {"to": "F02"}},
            {"op": "relation.update", "id": "E22", "set": {"to": "F03"}},
        )
        self.assertEqual(self.node("FU01")["archive"]["replacements"], ["F02", "F03"])
        self.assertEqual(self.node("F02")["humanInputs"][0]["target"], "F00")
        self.apply(
            {"op": "node.merge", "sourceIds": ["F02", "F03"], "target": follow_up("F04", ["H03"])},
            {"op": "relation.update", "id": "E21", "set": {"to": "F04"}},
            {"op": "relation.update", "id": "E22", "set": {"to": "F04"}},
        )
        self.apply({"op": "node.remove", "id": "F04"},
                   {"op": "relation.remove", "id": "E21"}, {"op": "relation.remove", "id": "E22"})
        self.assertTrue(self.node("F04")["archived"])
        self.assertEqual(self.node("F04")["humanInputs"][0]["id"], "H03")
        with self.assertRaises(StoryError):
            self.apply({"op": "node.create", **follow_up("F04")})

    def test_rejects_unmigrated_human_records_and_dangling_edges(self):
        before = self.store.path.read_bytes()
        with self.assertRaises(StoryError):
            self.apply({"op": "node.split", "sourceId": "FU01",
                        "targets": [follow_up("F02"), follow_up("F03")]})
        with self.assertRaises(StoryError):
            self.apply({"op": "node.remove", "id": "FU01"})
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_rejects_cycle_even_through_requirement_gate(self):
        before = self.store.path.read_bytes()
        with self.assertRaises(StoryError) as raised:
            self.apply(update("AN01", "editorial", summary="这条修改也应回滚。"),
                       {"op": "relation.create", "relation": {"id": "CYCLE", "type": "requires",
                                                            "from": "R02", "to": "IM01"}})
        self.assertEqual(raised.exception.code, "INVALID_STRUCTURE")
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_concurrent_commit_has_one_winner(self):
        before = self.read()
        def write(summary):
            try:
                return self.store.apply(batch(before, update("IM01", "editorial", summary=summary)))["ok"]
            except StoryError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(write, ["甲", "乙"]))
        self.assertCountEqual(results, [True, "REVISION_CONFLICT"])
        self.assertEqual(self.read()["revision"], before["revision"] + 1)

    def test_external_edit_and_invalid_xml_are_not_overwritten(self):
        old = self.read()
        manual = self.store.path.read_bytes() + b"<!-- external edit -->"
        self.store.path.write_bytes(manual)
        with self.assertRaises(StoryError) as conflict:
            self.store.apply(batch(old, update("IM01", "editorial", summary="不会保存")))
        self.assertEqual(conflict.exception.code, "REVISION_CONFLICT")
        self.assertEqual(self.store.path.read_bytes(), manual)
        delta = self.read(view="changes", sinceRevision=old["revision"], sinceReadToken=old["readToken"])
        self.assertTrue(delta["data"]["refreshRequired"])
        self.store.path.write_bytes(b'<!DOCTYPE story SYSTEM "file:///a"><story/>')
        with self.assertRaises(StoryError) as bad:
            self.read()
        self.assertEqual(bad.exception.code, "INVALID_DOCUMENT")
        self.assertTrue(self.store.path.read_bytes().startswith(b"<!DOCTYPE"))

    def test_save_failure_retains_exact_file(self):
        before = self.store.path.read_bytes()
        with patch("hsdd_story.store.os.replace", side_effect=PermissionError("simulated")), self.assertRaises(StoryError) as failure:
            self.apply(update("IM01", "editorial", summary="保存应该失败"))
        self.assertEqual(failure.exception.code, "SAVE_FAILED")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.store.path.parent.glob("*.tmp")), [])

    def submit_acceptance(self, key, outcome):
        item = {"id": key, "target": "AC01", "kind": "acceptance",
                "text": "测试验收结论。", "outcome": outcome}
        self.store.apply(batch(self.read(), {"op": "input.add", "input": item}), actor="human")

    def claim_acceptance(self, ref):
        return self.apply(update("AC01", work="finished", result="satisfied", validity="current",
                                 humanInputRefs=[ref]))

    def test_rejects_ai_or_unrelated_acceptance_and_tracks_latest_web_decision(self):
        with self.assertRaises(StoryError) as unrelated:
            self.claim_acceptance("H02")
        self.assertEqual(unrelated.exception.code, "ACCEPTANCE_REQUIRED")
        raw = {"id": "H-AI", "target": "AC01", "kind": "acceptance", "text": "通过。"}
        with self.assertRaises(StoryError) as forged:
            self.apply({"op": "input.add", "input": {**raw, "outcome": "accepted"}})
        self.assertEqual(forged.exception.code, "INVALID_ACCEPTANCE_INPUT")
        self.apply({"op": "input.add", "input": raw})
        with self.assertRaises(StoryError):
            self.claim_acceptance("H-AI")

        self.submit_acceptance("H-AC", "accepted")
        self.assertEqual(self.node("AC01")["display"]["acceptance"]["state"], "approved")
        self.claim_acceptance("H-AC")
        node = self.node("AC01")
        self.assertEqual(node["result"], "satisfied")
        self.assertFalse(node["display"]["satisfied"])
        warnings = self.read(view="nodes", nodeIds=["AC01"])["warnings"]
        self.assertIn("UNMET_PREREQUISITES", {w["code"] for w in warnings})

        self.submit_acceptance("H-REJECT", "rejected")
        self.assertEqual(self.node("AC01")["display"]["status"], "验收退回")
        self.assertNotIn("AC01", {n["id"] for n in self.read()["data"]["humanActions"]})
        for ref in ("H-AC", "H-REJECT"):
            with self.subTest(ref=ref), self.assertRaises(StoryError) as refused:
                self.claim_acceptance(ref)
            self.assertEqual(refused.exception.code, "ACCEPTANCE_REQUIRED")
        self.submit_acceptance("H-NEW", "accepted")
        self.assertEqual(self.node("AC01")["display"]["acceptance"]["state"], "approved")
        self.claim_acceptance("H-NEW")
        self.assertEqual(self.node("AC01")["display"]["status"], "验收接受")

    def test_acceptance_stales_on_prerequisite_or_criteria_change_and_survives_restart(self):
        self.submit_acceptance("H-AC", "accepted")
        self.claim_acceptance("H-AC")
        self.apply(update("AC01", "editorial", summary="说明微调。"))
        self.assertEqual(self.node("AC01")["display"]["acceptance"]["state"], "accepted")
        saved = self.apply(update("IM01", **{"body.outputs": "修改后的导出接口。"}))
        self.assertIn("AC01", saved["changedIds"])
        self.assertIn("AC01", saved["reviewRequiredIds"])
        reread = Store(self.store.workspace).read({"view": "nodes", "nodeIds": ["AC01"]})
        self.assertEqual(reread["data"]["nodes"][0]["display"]["acceptance"]["state"], "needs_review")
        self.submit_acceptance("H-NEW", "accepted")
        self.claim_acceptance("H-NEW")
        self.apply(update("AC01", **{"body.criteria": "增加对中文的检查。"}))
        with self.assertRaises(StoryError) as stale:
            self.claim_acceptance("H-NEW")
        self.assertEqual(stale.exception.code, "ACCEPTANCE_REQUIRED")
        self.assertEqual(self.node("AC01")["display"]["status"], "验收待复核")

    def test_legacy_success_is_readable_but_requires_explicit_reconfirmation(self):
        doc, _ = parse(self.store.path.read_bytes())
        node = next(node for req in doc["requirements"] for node in req["nodes"] if node["id"] == "AC01")
        node.update(work="finished", result="satisfied", humanInputRefs=["H02"])
        self.store.path.write_bytes(serialize(doc))
        self.assertEqual(self.node("AC01")["display"]["status"], "验收待复核")
        self.apply(update("FU01", "editorial", summary="普通修改仍可保存。"))
        self.apply(update("AC01", validity="needs_review"))
        self.assertEqual(self.node("AC01")["result"], "satisfied")
        self.assertFalse(self.node("AC01")["display"]["satisfied"])

    def test_search_filters_ranks_and_pages_without_node_details(self):
        data = self.read(view="search", query="csv", requirementId="R01", limit=1)["data"]
        self.assertGreater(data["total"], 1)
        self.assertNotIn("body", data["nodes"][0])
        following = self.read(view="search", query="CSV", requirementId="R01", limit=1,
                              offset=data["nextOffset"])["data"]
        self.assertNotEqual(data["nodes"][0]["id"], following["nodes"][0]["id"])
        exact = self.read(view="search", query="ac01", type="acceptance")["data"]
        self.assertEqual([n["id"] for n in exact["nodes"]], ["AC01"])
        self.submit_acceptance("H-REJECT", "rejected")
        filtered = self.read(view="search", result="unsatisfied", type="acceptance")["data"]
        self.assertEqual([n["id"] for n in filtered["nodes"]], ["AC01"])
        with self.assertRaises(StoryError):
            self.read(view="search", limit=51)

    def test_conflict_diagnoses_known_overlap_and_falls_back_after_restart(self):
        before = self.read()
        stale = batch(before, update("IM01", "editorial", summary="我的编辑。"))
        self.apply(update("IM01", "editorial", summary="已保存的修改。"))
        with self.assertRaises(StoryError) as conflict:
            self.store.apply(stale)
        self.assertEqual(conflict.exception.details["overlappingIds"], ["IM01"])
        self.assertFalse(conflict.exception.details["refreshRequired"])
        with self.assertRaises(StoryError) as unknown:
            Store(self.store.workspace).apply(stale)
        self.assertTrue(unknown.exception.details["refreshRequired"])
        self.assertNotIn("changedIds", unknown.exception.details)

    def test_cannot_spoof_web_input_and_preserves_original_text(self):
        raw = {"id": "H-CHAT", "target": "AC01", "kind": "comment", "text": "保留\r\n原文与 <符号>。"}
        with self.assertRaises(StoryError):
            self.apply({"op": "input.add", "input": {**raw, "channel": "web"}})
        self.apply({"op": "input.add", "input": raw}, {"op": "input.handle", "id": "H-CHAT", "handling": "incorporated"})
        record = [r for r in self.node("AC01")["humanInputs"] if r["id"] == raw["id"]][0]
        self.assertEqual(record["text"], raw["text"])
        self.assertEqual((record["recorder"], record["channel"], record["handling"]), ("ai", "chat", "incorporated"))


class HttpAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_official_mcp_client_and_web_share_persistence(self):
        store = workspace(self)
        sock = socket.socket()
        self.addCleanup(sock.close)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(store.workspace), log_level="error", access_log=False,
                                               ws="none"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(150):
                if server.started:
                    break
                self.assertFalse(task.done())
                await asyncio.sleep(.02)
            self.assertTrue(server.started)
            async with (asyncio.timeout(20),
                        httpx.AsyncClient(base_url=base, trust_env=False) as web,
                        streamable_http_client(base + "/mcp") as (receive, send, _),
                        ClientSession(receive, send) as session):
                initial = await session.initialize()
                self.assertEqual(initial.serverInfo.name, "hsdd-story")
                self.assertIn("story_guide", initial.instructions)
                tools = {t.name: t for t in (await session.list_tools()).tools}
                self.assertEqual(set(tools),
                                 {"story_guide", "story_schema", "story_read", "story_read_attachment", "story_apply"})
                self.assertTrue(tools["story_guide"].annotations.readOnlyHint)
                before_guide = store.path.read_bytes()
                catalog = await session.call_tool("story_guide", {})
                self.assertFalse(catalog.isError)
                topics = {item["topic"] for item in catalog.structuredContent["topics"]}
                self.assertEqual(topics, {"start", "update", "structure", "attachments", "acceptance", "conflict"})
                for topic in topics:
                    with self.subTest(topic=topic):
                        guide = await session.call_tool("story_guide", {"topic": topic})
                        self.assertFalse(guide.isError)
                        self.assertEqual(guide.structuredContent["topic"], topic)
                        self.assertTrue(guide.structuredContent["steps"])
                        self.assertNotIn("topics", guide.structuredContent)
                for args in ({"topic": "unknown"}, {"topic": []}, {"unknown": "start"}):
                    invalid = await session.call_tool("story_guide", args)
                    self.assertTrue(invalid.isError)
                self.assertEqual(store.path.read_bytes(), before_guide)
                rules = await session.call_tool("story_schema", {"types": ["acceptance"]})
                self.assertFalse(rules.isError)
                self.assertEqual(list(rules.structuredContent["nodeTypes"]), ["acceptance"])
                self.assertNotIn("operationSchemas", rules.structuredContent)
                patch_rules = await session.call_tool("story_schema", {"operations": ["node.update"]})
                patch_schema = patch_rules.structuredContent["operationSchemas"]["node.update"]
                self.assertIn("body.criteria", patch_schema["properties"]["set"]["properties"])
                self.assertNotIn("nodeTypes", patch_rules.structuredContent)
                search = await session.call_tool("story_read", {"view": "search", "query": "AC01"})
                self.assertEqual(search.structuredContent["data"]["nodes"][0]["id"], "AC01")
                snapshot = (await session.call_tool("story_read", {})).structuredContent
                self.assertEqual((await web.get("/")).status_code, 200)
                self.assertIn("开发方向图", (await web.get("/")).text)
                self.assertEqual((await web.get("/app.js")).status_code, 200)
                self.assertEqual((await web.get("/api/story", headers={"Origin": "https://example.test"})).status_code, 403)
                payload = {**{key: snapshot[key] for key in ("projectId", "readToken")},
                           "baseRevision": snapshot["revision"], "target": "AC01", "kind": "acceptance",
                           "text": "从网页保存的验收结论", "outcome": "rejected"}
                saved = await web.post("/api/input", json=payload)
                self.assertEqual(saved.status_code, 200, saved.text)
                conflict = await session.call_tool("story_apply", batch(snapshot, update("IM01", "editorial", summary="过期")))
                self.assertTrue(conflict.isError)
                self.assertEqual(conflict.structuredContent["code"], "REVISION_CONFLICT")
                self.assertIn("AC01", conflict.structuredContent["details"]["changedIds"])
                self.assertEqual(conflict.structuredContent["details"]["overlappingIds"], [])
                detail = await session.call_tool("story_read", {"view": "nodes", "nodeIds": ["AC01"]})
                record = detail.structuredContent["data"]["nodes"][0]["humanInputs"][-1]
                self.assertEqual(record["channel"], "web")
                self.assertEqual(record["text"], payload["text"])
                self.assertEqual(record["outcome"], "rejected")
                attempt = await session.call_tool("story_apply", batch(detail.structuredContent,
                    update("AC01", work="finished", result="satisfied", humanInputRefs=[record["id"]])))
                self.assertTrue(attempt.isError)
                self.assertEqual(attempt.structuredContent["code"], "ACCEPTANCE_REQUIRED")
                result = await session.call_tool("story_apply", batch(detail.structuredContent,
                    {"op": "input.handle", "id": record["id"], "handling": "incorporated"},
                    update("AC01", "editorial", summary="AI 已整合网页记录。")))
                self.assertFalse(result.isError, result)
                response = (await web.get("/api/story", params={"view": "nodes", "nodeIds": "AC01"})).json()
                self.assertEqual(response["data"]["nodes"][0]["summary"], "AI 已整合网页记录。")
                self.assertEqual(response["data"]["nodes"][0]["humanInputs"][-1]["handling"], "incorporated")
                upload = await session.call_tool("story_apply", batch(response,
                    {"op": "attachment.add", "nodeId": "AC01", "attachment": {
                        "id": "ATT-NOTE", "filename": "验收.md",
                        "text": "# 验收\n\n| 项目 | 结果 |\n| --- | --- |\n| 中文 | 通过 |\n\n<script>alert(1)</script>",
                    }},
                    {"op": "attachment.add", "nodeId": "AC01", "attachment": {
                        "id": "ATT-PNG", "filename": "链路.png",
                        "dataBase64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1sAAAAASUVORK5CYII=",
                    }}))
                self.assertFalse(upload.isError, upload)
                node_result = await session.call_tool("story_read", {"view": "nodes", "nodeIds": ["AC01"]})
                markdown, picture = node_result.structuredContent["data"]["nodes"][0]["attachments"]
                ref = node_result.structuredContent["data"]["nodes"][0]["nodeRef"]
                located = await session.call_tool("story_read", {"nodeRef": ref})
                self.assertFalse(located.isError, located)
                self.assertEqual(located.structuredContent, node_result.structuredContent)
                first_page = await session.call_tool("story_read_attachment", {
                    "attachmentId": markdown["id"], "limit": 4})
                self.assertEqual(first_page.structuredContent["text"], "# 验收")
                next_page = await session.call_tool("story_read_attachment", {
                    "attachmentId": markdown["id"], "offset": first_page.structuredContent["nextOffset"]})
                self.assertIn("| 中文 | 通过 |", next_page.structuredContent["text"])
                self.assertIsNone(next_page.structuredContent["nextOffset"])
                mcp_picture = await session.call_tool("story_read_attachment", {"attachmentId": picture["id"]})
                image_part = next(part for part in mcp_picture.content if part.type == "image")
                self.assertEqual(image_part.mimeType, "image/png")
                self.assertTrue(image_part.data.startswith("iVBORw0K"))
                preview = await web.get(markdown["previewPath"])
                self.assertEqual(preview.status_code, 200)
                self.assertIn("<table>", preview.text)
                self.assertIn("&lt;script&gt;", preview.text)
                self.assertNotIn("<script>", preview.text)
                download = await web.get(markdown["downloadPath"])
                self.assertIn("attachment;", download.headers["content-disposition"])
                self.assertTrue(download.text.startswith("# 验收"))
                image = await web.get(picture["previewPath"])
                self.assertEqual(image.headers["content-type"], "image/png")
                self.assertTrue(image.content.startswith(b"\x89PNG"))
                self.assertEqual((await web.get("/api/attachments/UNKNOWN")).status_code, 404)
                current = node_result.structuredContent
                approved = await web.post("/api/input", json={
                    **payload, "outcome": "accepted", "text": "网页复验通过。",
                    "baseRevision": current["revision"], "readToken": current["readToken"],
                })
                self.assertEqual(approved.status_code, 200, approved.text)
                stale = await session.call_tool("story_apply", batch(current, update("AC01", "editorial",
                                                                                   summary="过期的验收说明")))
                self.assertTrue(stale.isError)
                self.assertEqual(stale.structuredContent["details"]["overlappingIds"], ["AC01"])
                latest = (await session.call_tool("story_read", {"nodeRef": ref})).structuredContent
                approval = latest["data"]["nodes"][0]["display"]["acceptance"]
                self.assertEqual(approval["state"], "approved")
                saved = await session.call_tool("story_apply", batch(latest, update(
                    "AC01", work="finished", result="satisfied", humanInputRefs=[approval["inputId"]])))
                self.assertFalse(saved.isError, saved)
                web_node = (await web.get("/api/story", params={"view": "nodes", "nodeIds": "AC01"})).json()
                self.assertEqual(web_node["data"]["nodes"][0]["display"]["status"], "验收接受")
                self.assertEqual(web_node["revision"], saved.structuredContent["revision"])
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)
