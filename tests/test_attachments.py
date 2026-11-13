"""Test attachment round trips, retention and failed transactions."""

import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hsdd_story.contract import StoryError
from hsdd_story.store import Store
from test_story import batch, follow_up, update, workspace

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1sAAAAASUVORK5CYII="
)


def upload(node, key, filename, **source):
    return {"op": "attachment.add", "nodeId": node,
            "attachment": {"id": key, "filename": filename, **source}}


class AttachmentAcceptance(unittest.TestCase):
    def setUp(self):
        self.store = workspace(self)

    def read_node(self, key):
        return self.store.read({"view": "nodes", "nodeIds": [key]})["data"]["nodes"][0]

    def apply(self, *operations):
        return self.store.apply(batch(self.store.read({}), *operations))

    def test_legacy_xml_and_managed_copy(self):
        before = self.read_node("AC01")
        self.assertEqual(before["attachments"], [])
        source = self.store.workspace / "diagram.png"
        source.write_bytes(PNG)
        text = "# 验收说明\n\n- 检查中文\n"
        saved = self.apply(upload("AC01", "IMG-01", "链路图.png", sourcePath="diagram.png"),
                           upload("AC01", "DOC-01", "验收说明.md", text=text))
        source.unlink()
        reopened = Store(self.store.workspace)
        detail = reopened.read({"view": "nodes", "nodeIds": ["AC01"]})["data"]["nodes"][0]
        self.assertEqual(detail["revision"], before["revision"])
        self.assertEqual(detail["attachmentRefs"], ["IMG-01", "DOC-01"])
        self.assertEqual(reopened.read_attachment("IMG-01")[1], PNG)
        self.assertEqual(reopened.read_attachment("DOC-01")[1].decode("utf-8"), text)
        self.assertEqual(set(saved["changedIds"]), {"AC01", "IMG-01", "DOC-01"})
        self.assertEqual(detail["attachments"][1]["previewPath"], "/api/attachments/DOC-01/preview")

    def test_blob_dedup_and_failed_xml_save(self):
        self.apply(upload("AC01", "DOC-01", "验收.md", text="# 同一份内容"))
        before = self.store.path.read_bytes()
        existing = list(self.store.attachment_files.directory.iterdir())
        with patch.object(self.store, "save", side_effect=StoryError("SAVE_FAILED", "模拟写入失败")):
            with self.assertRaises(StoryError) as raised:
                self.apply(upload("AC01", "DOC-02", "设计.md", text="# 另一份内容"),
                           upload("DC01", "DOC-03", "同一份.md", text="# 同一份内容"))
            self.assertEqual(raised.exception.code, "SAVE_FAILED")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.store.attachment_files.directory.iterdir()), existing)
        self.apply(upload("DC01", "DOC-03", "同一份.md", text="# 同一份内容"))
        self.assertEqual(list(self.store.attachment_files.directory.iterdir()), existing)
        self.assertEqual(self.store.read_attachment("DOC-01")[1], self.store.read_attachment("DOC-03")[1])

    def test_invalid_batch_or_stale_snapshot_does_not_publish_blob(self):
        old = self.store.read({})
        with self.assertRaises(StoryError):
            self.apply(upload("AC01", "DOC-01", "设计.md", text="# 说明"),
                       update("DOES-NOT-EXIST", "editorial", summary="无效"))
        self.apply(update("AC01", "editorial", summary="状态已经更新"))
        with self.assertRaises(StoryError) as raised:
            self.store.apply(batch(old, upload("AC01", "DOC-02", "设计.md", text="# 说明")))
        self.assertEqual(raised.exception.code, "REVISION_CONFLICT")
        self.assertFalse(self.store.attachment_files.directory.exists())

    def test_rejects_outside_source_and_mislabelled_content(self):
        with tempfile.NamedTemporaryFile(dir=self.store.workspace.parent, suffix=".md") as outside:
            outside.write(b"# outside")
            outside.flush()
            invalid = [
                upload("AC01", "DOC-01", "外部.md", sourcePath="../" + Path(outside.name).name),
                upload("AC01", "DOC-01", "外部.md", sourcePath=outside.name),
                upload("AC01", "DOC-01", "../文件.md", text="# 内容"),
                upload("AC01", "DOC-01", "图片.svg", text="<svg/>"),
                upload("AC01", "DOC-01", "假图.png", dataBase64="PHNjcmlwdD4="),
                upload("AC01", "DOC-01", "无效.md", text="# 内容", sourcePath="同名.md"),
            ]
            for op in invalid:
                with self.subTest(attachment=op["attachment"]["filename"]), self.assertRaises(StoryError):
                    self.apply(op)
        self.assertFalse(self.store.attachment_files.directory.exists())

    def test_migrates_attachment_on_split_and_retains_in_archive(self):
        self.apply(upload("FU01", "PLAN-01", "扩展方案.md", text="# 大数据异步导出"))
        targets = [follow_up("F02", ["H03"]), follow_up("F03")]
        with self.assertRaises(StoryError):
            self.apply({"op": "node.split", "sourceId": "FU01", "targets": targets})
        targets[0]["node"]["attachmentRefs"] = ["PLAN-01"]
        self.apply(
            {"op": "node.split", "sourceId": "FU01", "targets": targets},
            {"op": "relation.update", "id": "E21", "set": {"to": "F02"}},
            {"op": "relation.update", "id": "E22", "set": {"to": "F03"}},
        )
        self.assertEqual(self.read_node("F02")["attachmentRefs"], ["PLAN-01"])
        old = self.read_node("FU01")
        self.assertTrue(old["archived"])
        self.assertEqual(old["attachmentRefs"], ["PLAN-01"])
        self.assertEqual(self.store.read_attachment("PLAN-01")[1].decode("utf-8"), "# 大数据异步导出")
