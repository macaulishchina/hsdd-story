"""Workspace-managed attachment blobs."""

import base64
import binascii
from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path
import tempfile

from .contract import ATTACHMENT_LIMITS, ATTACHMENT_TYPES, StoryError, check, fields, filename_type


def prepare(workspace, entry):
    fields(entry, ("id", "filename"), ("sourcePath", "text", "dataBase64"))
    sources = [key for key in ("sourcePath", "text", "dataBase64") if key in entry]
    check(len(sources) == 1, "附件必须提供一种来源：sourcePath、text 或 dataBase64。")
    media = filename_type(entry["filename"])
    limit = ATTACHMENT_LIMITS[media]
    key = sources[0]
    value = entry[key]
    check(isinstance(value, str) and value, "附件来源必须为非空文本。")
    if key == "sourcePath":
        raw = read_source(workspace, value, limit)
    elif key == "text":
        check(media == "text/markdown", "只有 Markdown 附件支持直接填写 text。")
        try:
            raw = value.encode("utf-8")
        except UnicodeError as exc:
            raise StoryError("INVALID_OPERATION", "Markdown 必须是有效 UTF-8。") from exc
    else:
        check(len(value) <= ((limit + 2) // 3) * 4, "附件超过大小上限。", maxBytes=limit)
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise StoryError("INVALID_OPERATION", "dataBase64 不是有效 Base64。") from exc
    check(0 < len(raw) <= limit, "附件为空或超过大小上限。", maxBytes=limit)
    check_content(media, raw)
    return {"id": entry["id"], "filename": entry["filename"], "mediaType": media,
            "size": len(raw), "sha256": sha256(raw).hexdigest()}, raw


def read_source(workspace, source, limit):
    relative = Path(source)
    check(not relative.is_absolute() and not relative.drive and not relative.anchor
          and all(":" not in part for part in relative.parts), "sourcePath 必须是工作区相对路径。")
    try:
        path = (workspace / relative).resolve(strict=True)
        in_workspace = path.relative_to(workspace)
        check(in_workspace.parts and in_workspace.parts[0].casefold() != ".hsdd",
              "sourcePath 必须指向工作区内的普通源文件。")
        check(path.is_file(), "sourcePath 必须指向文件。")
        with path.open("rb") as handle:
            return handle.read(limit + 1)
    except (OSError, ValueError, RuntimeError) as exc:
        raise StoryError("INVALID_OPERATION", "无法读取 sourcePath；请使用工作区内的文件。") from exc


def check_content(media, raw):
    if media == "text/markdown":
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise StoryError("INVALID_OPERATION", "Markdown 必须是有效 UTF-8。") from exc
        check(text.strip() and "\0" not in text, "Markdown 为空或包含二进制内容。")
    elif media == "image/png":
        check(raw.startswith(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR") and len(raw) >= 33,
              "文件内容不是 PNG。")
    elif media == "image/jpeg":
        check(raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9"), "文件内容不是 JPEG。")
    elif media == "image/gif":
        check(raw[:6] in (b"GIF87a", b"GIF89a") and len(raw) >= 13, "文件内容不是 GIF。")
    elif media == "image/webp":
        check(raw.startswith(b"RIFF") and raw[8:12] == b"WEBP" and len(raw) >= 16,
              "文件内容不是 WebP。")


class AttachmentFiles:
    def __init__(self, workspace):
        self.workspace = workspace
        self.directory = workspace / ".hsdd" / "attachments"

    def blob_path(self, digest):
        # The name is always a validated SHA-256, never the uploaded filename.
        folder = self.directory.resolve()
        check(folder == self.directory, "附件目录必须位于工作区内。", code="ATTACHMENT_UNAVAILABLE")
        path = folder / digest
        check(not path.is_symlink(), "附件文件不能是链接。", code="ATTACHMENT_UNAVAILABLE")
        return path

    def read(self, item):
        path = self.blob_path(item["sha256"])
        try:
            with path.open("rb") as handle:
                data = handle.read(item["size"] + 1)
        except OSError as exc:
            raise StoryError("ATTACHMENT_UNAVAILABLE", "项目附件文件不可读取。", id=item["id"]) from exc
        check(len(data) == item["size"] and sha256(data).hexdigest() == item["sha256"],
              "项目附件与保存的校验值不符。", code="ATTACHMENT_UNAVAILABLE", id=item["id"])
        return data

    @contextmanager
    def publish(self, pending):
        created = []
        try:
            if pending:
                self.blob_path(next(iter(pending)))
                self.directory.mkdir(parents=True, exist_ok=True)
            for digest, data in pending.items():
                path = self.blob_path(digest)
                if path.exists():
                    self.read({"id": digest, "sha256": digest, "size": len(data)})
                    continue
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".upload-", delete=False) as handle:
                        temporary = Path(handle.name)
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, path)
                    created.append(path)
                finally:
                    if temporary:
                        temporary.unlink(missing_ok=True)
            yield
        except (StoryError, OSError) as exc:
            for path in created:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(exc, OSError):
                raise StoryError("SAVE_FAILED", f"无法保存附件：{exc}") from exc
            raise
