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


def _per_model(values, n: int, name: str) -> list:
    """``--title`` / ``--default-root`` given once (for all models) or once per ``--model``."""
    values = values or [None]
    if len(values) == 1:
        return values * n
    if len(values) != n:
        raise SystemExit(f"Give {name} once, or once per --model ({n})")
    return values


def serve(args) -> None:
    import json
    import os

    import uvicorn

    n = len(args.model)
    if args.title and len(args.title) != n:
        raise SystemExit(f"Give --title once per --model ({n})")
    titles = args.title or [Path(path).name for path in args.model]
    roots = _per_model(args.default_root, n, "--default-root")
    models = [
        {"path": str(Path(path).resolve()), "title": title, "default_root": root or None}
        for path, title, root in zip(args.model, titles, roots)
    ]
    if args.reload:
        # The reloader re-imports the app in a fresh process: pass settings by environment
        os.environ["LCA_VIEWER_MODELS"] = json.dumps(models)
        print(f"serving http://{args.host}:{args.port} — restarts when the Python code changes")
        uvicorn.run("lca_viewer.server:app_from_env", factory=True, reload=True,
                    reload_dirs=[str(Path(__file__).parent)], host=args.host, port=args.port, log_level="warning")
        return

    from .server import app_for

    t = time.perf_counter()
    app = app_for(models)
    loaded = "model" if n == 1 else f"{n} models"
    print(f"{loaded} loaded and factorised in {time.perf_counter() - t:.1f}s, serving http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def import_(args) -> None:
    from .importing import import_ecospold2, import_simapro_csv, setup_project

    os_env_check()
    setup_project(args.project)
    t = time.perf_counter()
    if args.format == "ecospold2":
        import_ecospold2(args.project, args.path, args.database)
    else:
        import_simapro_csv(
            args.project, args.path, args.database, link_to=args.link_to, migrations=args.migration,
            unlinked=args.unlinked, report=args.report,
        )
    print(f"imported {args.database} into project {args.project} ({time.perf_counter() - t:.0f}s)")


def os_env_check() -> None:
    import os

    if not os.environ.get("BRIGHTWAY2_DIR"):
        print("warning: BRIGHTWAY2_DIR is not set, using Brightway's default data directory")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="lca-viewer")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("build", help="Build a cached model from a Brightway project")
    p.add_argument("--project", required=True)
    p.add_argument("--database", required=True)
    p.add_argument("--methods", nargs="*", help="Method families to include (first element of the method tuple); default: all")
    p.add_argument("--output", required=True)
    p.set_defaults(func=build)

    p = sub.add_parser("import", help="Import a database into a Brightway project (before build)")
    p.add_argument("format", choices=["ecospold2", "simapro-csv"])
    p.add_argument("path", help="ecoSpold2 datasets folder, or SimaPro CSV file")
    p.add_argument("--project", required=True)
    p.add_argument("--database", required=True, help="Name of the new database")
    p.add_argument("--link-to", nargs="*", default=[],
                   help="Databases already in the project that the new one links to (e.g. ecoinvent-3.9.1-cutoff)")
    p.add_argument("--migration", nargs="*", default=None,
                   help="randonneur_data migrations applied to technosphere inputs before linking "
                        "(default: simapro-ecoinvent-3.9.1-cutoff when linking to an ecoinvent 3.9.1 database)")
    p.add_argument("--unlinked", choices=["fail", "drop"], default="fail",
                   help="Inputs or emissions that cannot be linked: stop (default), or drop them and write the database")
    p.add_argument("--report", help="CSV file listing unlinked exchanges (default: data/import-<database>-unlinked.csv)")
    p.set_defaults(func=import_)

    p = sub.add_parser("serve", help="Serve the explorer for one or several cached models")
    p.add_argument("--model", required=True, action="append",
                   help="Model directory; repeat to serve several models with a database picker")
    p.add_argument("--title", action="append", help="Title shown in the UI; once per --model (default: directory name)")
    p.add_argument("--default-root", action="append",
                   help='Functional unit shown at start, as "activity name|location"; once, or once per --model')
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--reload", action="store_true", help="Restart automatically when the Python code changes (development)")
    p.set_defaults(func=serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
