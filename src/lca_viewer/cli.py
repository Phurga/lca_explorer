"""Command line: build a cached model from Brightway, then serve the UI."""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def build(args) -> None:
    import bw2data as bd

    from .brightway import from_brightway

    bd.projects.set_current(args.project)
    if args.methods:
        methods = [m for m in bd.methods if m[0] in args.methods]
    else:
        methods = list(bd.methods)
    t = time.perf_counter()
    model = from_brightway(args.project, args.database, methods)
    model.save(args.output)
    print(f"{model.size} activities, {len(model.methods)} methods -> {args.output} ({time.perf_counter() - t:.1f}s)")


def serve(args) -> None:
    import os

    import uvicorn

    title = args.title or Path(args.model).name
    if args.reload:
        # The reloader re-imports the app in a fresh process: pass settings by environment
        os.environ.update({
            "LCA_VIEWER_MODEL": str(Path(args.model).resolve()),
            "LCA_VIEWER_TITLE": title,
            "LCA_VIEWER_DEFAULT_ROOT": args.default_root or "",
        })
        print(f"serving http://{args.host}:{args.port} — restarts when the Python code changes")
        uvicorn.run("lca_viewer.server:app_from_env", factory=True, reload=True,
                    reload_dirs=[str(Path(__file__).parent)], host=args.host, port=args.port, log_level="warning")
        return

    from .model import LCAModel
    from .server import create_app

    t = time.perf_counter()
    model = LCAModel.load(args.model)
    app = create_app(model, title=title, default_root=args.default_root)
    print(f"model loaded and factorised in {time.perf_counter() - t:.1f}s, serving http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="lca-viewer")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("build", help="Build a cached model from a Brightway project")
    p.add_argument("--project", required=True)
    p.add_argument("--database", required=True)
    p.add_argument("--methods", nargs="*", help="Method families to include (first element of the method tuple); default: all")
    p.add_argument("--output", required=True)
    p.set_defaults(func=build)

    p = sub.add_parser("serve", help="Serve the explorer for a cached model")
    p.add_argument("--model", required=True)
    p.add_argument("--title")
    p.add_argument("--default-root", help='Functional unit shown at start, as "activity name|location"')
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--reload", action="store_true", help="Restart automatically when the Python code changes (development)")
    p.set_defaults(func=serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
