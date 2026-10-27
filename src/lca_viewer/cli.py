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
    model = from_brightway(args.project, args.database, methods, log=lambda message: print(message, flush=True))
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

    if args.model:
        n = len(args.model)
        if len(args.title or []) > n:
            raise SystemExit(f"More --title than --model ({n})")
        # Titles go to the models in order; models without one are named after their directory
        titles = (args.title or []) + [Path(path).name for path in args.model[len(args.title or []):]]
        roots = _per_model(args.default_root, n, "--default-root")
        models = [
            {"path": str(Path(path).resolve()), "title": title, "default_root": root or None}
            for path, title, root in zip(args.model, titles, roots)
        ]
    else:
        models, n = [], 0  # loaded from the page (Load database)
    if args.reload:
        # The reloader re-imports the app in a fresh process: pass settings by environment
        os.environ["LCA_VIEWER_MODELS"] = json.dumps(models)
        os.environ["LCA_VIEWER_DATA"] = str(Path(args.data).resolve())
        print(f"serving http://{args.host}:{args.port} — restarts when the Python code changes")
        uvicorn.run("lca_viewer.server:app_from_env", factory=True, reload=True,
                    reload_dirs=[str(Path(__file__).parent)], host=args.host, port=args.port, log_level="warning")
        return

    from .server import app_for

    t = time.perf_counter()
    app = app_for(models, args.data)
    if n:
        loaded = "model" if n == 1 else f"{n} models"
        print(f"{loaded} loaded and factorised in {time.perf_counter() - t:.1f}s, serving http://{args.host}:{args.port}")
    else:
        print(f"serving http://{args.host}:{args.port}: choose a database to load in the page (Load database)")
    if args.open:
        import threading
        import webbrowser

        host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
        print("Stop with Ctrl+C")
        threading.Timer(1.5, webbrowser.open, [f"http://{host}:{args.port}"]).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def setup(args) -> None:
    from .quickstart import run

    run(args)


def import_(args) -> None:
    import os

    if not os.environ.get("BRIGHTWAY2_DIR"):
        print("warning: BRIGHTWAY2_DIR is not set, using Brightway's default data directory")
    from .importing import import_ecospold2, import_simapro_csv, setup_project

    t = time.perf_counter()
    setup_project(args.project)  # biosphere and LCIA methods, first time only
    if args.format == "ecospold2":
        import_ecospold2(args.project, args.path, args.database)
        print(f"imported {args.database} into project {args.project} ({time.perf_counter() - t:.0f}s)")
    else:
        result = import_simapro_csv(
            args.project, args.path, args.database, link_to=args.link_to, migrations=args.migration,
            unlinked=args.unlinked, report=args.report,
        )
        dropped = f", {result['unlinked']} unlinked exchanges dropped" if result["unlinked"] else ""
        print(f"imported {result['processes']} processes into {args.database}, project {args.project}"
              f"{dropped} ({time.perf_counter() - t:.0f}s)")


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

    p = sub.add_parser("setup", help="First-time setup; optionally unpacks, imports and builds an ecoinvent ecoSpold2 release")
    p.add_argument("source", nargs="?", help="ecoSpold2 archive (.7z) or the folder it was unpacked to; asked when omitted (Enter skips ecoinvent)")
    p.add_argument("--data", default="data", help="Folder for the Brightway project, the models and models.json (default: data)")
    p.add_argument("--yes", action="store_true", help="Take the default answer to every question (without a source: no ecoinvent)")
    p.set_defaults(func=setup)

    p = sub.add_parser("serve", help="Serve the explorer; databases are loaded from the page, or with --model")
    p.add_argument("--model", action="append",
                   help="Model directory loaded at start; repeat to load several (default: none, "
                        "choose them in the page with Load database)")
    p.add_argument("--data", default="data",
                   help="Folder of the built models (models.json, cache/) and of lca_viewer's Brightway "
                        "projects (brightway/), offered by Load database (default: data)")
    p.add_argument("--title", action="append",
                   help="Title shown in the UI; repeat for each --model, in the same order (default: directory name)")
    p.add_argument("--default-root", action="append",
                   help='Functional unit shown at start, as "activity name|location"; once, or once per --model')
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--reload", action="store_true", help="Restart automatically when the Python code changes (development)")
    p.add_argument("--open", action="store_true", help="Open the app in the browser once it is ready")
    p.set_defaults(func=serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
