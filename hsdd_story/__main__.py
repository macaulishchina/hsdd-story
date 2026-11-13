"""Command line entry points for serving, checking and creating a demo."""

import argparse
from importlib.resources import files
from pathlib import Path
import sys

from . import VERSION
from .contract import StoryError
from .store import Store
from .xmlio import parse


def demo_source():
    checkout = Path(__file__).resolve().parent.parent / "examples" / "export.story.xml"
    if checkout.is_file():
        return checkout.read_bytes()
    return files("hsdd_story.examples").joinpath("export.story.xml").read_bytes()


def main():
    cli = argparse.ArgumentParser(prog="hsdd-story", description="人和 AI 共用的开发方向图")
    cli.add_argument("--version", action="version", version=VERSION)
    sub = cli.add_subparsers(dest="command", required=True)
    for command, help_text in (("serve", "启动网页和 MCP"), ("demo", "在独立工作区安装模拟示例"),
                               ("check", "检查项目 XML")):
        item = sub.add_parser(command, help=help_text)
        item.add_argument("--workspace", type=Path, default=Path.cwd())
        if command == "serve":
            item.add_argument("--port", type=int, default=8765, choices=range(1, 65536), metavar="PORT")
    args = cli.parse_args()
    store = Store(args.workspace)
    try:
        if args.command == "demo":
            raw = demo_source()
            parse(raw)
            with store.exclusive_workspace():
                if store.path.exists():
                    raise FileExistsError(store.path)
                store.save(raw, "absent", 0)
            print(f"示例已保存到 {store.path}")
        elif args.command == "check":
            doc, graph, _ = store.load()
            if doc is None:
                raise StoryError("PROJECT_NOT_FOUND", f"找不到 {store.path}")
            print(f"XML 校验通过。项目 {doc['id']}，修订 {doc['revision']}，"
                  f"{len(graph.requirements)} 项需求，{len(graph.active)} 个当前节点。")
            for warning in graph.warnings:
                print(f"  {warning['id']}  {warning['message']}")
        else:
            import uvicorn
            from .server import create_app
            uvicorn.run(create_app(args.workspace), host="127.0.0.1", port=args.port,
                        proxy_headers=False, ws="none")
    except FileExistsError:
        print(f"项目文件已存在：{store.path}", file=sys.stderr)
        return 1
    except (StoryError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
