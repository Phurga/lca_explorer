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


def init_brightway() -> None:
    """Brightway's data folder, created with a ``default`` project the first time the app runs (as
    Brightway itself would), in a separate process: the server never imports bw2data."""
    import importlib.util
    import subprocess
    import sys

    from .sources import brightway_dir

    folder = brightway_dir()
    if (folder / "projects.db").is_file() or importlib.util.find_spec("bw2data") is None:
        return
    print(f"Creating the Brightway data folder {folder}, with a default project")
    folder.mkdir(parents=True, exist_ok=True)  # bw2data refuses a BRIGHTWAY2_DIR that does not exist
    done = subprocess.run([sys.executable, "-m", "lca_viewer.cli", "project", "init"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode:
        lines = (done.stdout + done.stderr).strip().splitlines()
        print(f"warning: it could not be created: {lines[-1] if lines else 'no output'}")


def serve(args) -> None:
    import json
    import os

    import uvicorn

    init_brightway()

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
    import shutil

    from .importing import import_ecospold2, import_simapro_csv, install_defaults, setup_project
    from .sources import brightway_dir

    print(f"Brightway data folder: {brightway_dir()}", flush=True)

    t = time.perf_counter()
    if args.defaults:
        args.biosphere = install_defaults(args.project, args.defaults, Path(args.data) / "downloads")
    elif args.biosphere == "biosphere3" and not args.no_setup:
        setup_project(args.project)  # biosphere and LCIA methods, first time only
    if args.format == "ecospold2":
        from .quickstart import find_datasets, unpack

        path, unpacked = Path(args.path), None
        if path.is_file() and path.suffix.lower() == ".7z":
            unpacked = Path(args.data) / "unpacked" / path.stem.replace(" ", "_")
            if not find_datasets(unpacked):
                shutil.rmtree(unpacked, ignore_errors=True)
                unpack(path, unpacked)
            path = unpacked
        datasets = find_datasets(path)
        if datasets is None:
            raise SystemExit(f"No ecoSpold2 datasets (.spold files) in {path}")
        print(f"Importing the datasets of {datasets} (about 10 minutes for ecoinvent)", flush=True)
        import_ecospold2(args.project, datasets, args.database, biosphere=args.biosphere)
        if unpacked:
            shutil.rmtree(unpacked, ignore_errors=True)
        print(f"imported {args.database} into project {args.project} ({time.perf_counter() - t:.0f}s)")
    else:
        print("Reading the SimaPro CSV, then linking it (a few minutes)", flush=True)
        result = import_simapro_csv(
            args.project, args.path, args.database, link_to=args.link_to, migrations=args.migration,
            unlinked=args.unlinked, report=args.report, biosphere=args.biosphere,
        )
        dropped = f", {result['unlinked']} unlinked exchanges dropped" if result["unlinked"] else ""
        print(f"imported {result['processes']} processes into {args.database}, project {args.project}"
              f"{dropped} ({time.perf_counter() - t:.0f}s)")


def project(args) -> None:
    """Brightway projects of BRIGHTWAY2_DIR: create, delete, restore from a backup archive."""
    from . import editing

    if args.action == "init":
        editing.init_folder()
    elif args.action == "create":
        editing.create_project(args.name, setup=args.setup)
    elif args.action == "delete":
        editing.delete_project(args.name)
    else:
        editing.restore_project(args.file, args.name)


def database(args) -> None:
    """Delete a database from a Brightway project."""
    from . import editing

    editing.delete_database(args.project, args.name)


def defaults(args) -> None:
    """Install the biosphere and impact methods of an ecoinvent version in a project."""
    from .importing import install_defaults
    from .sources import brightway_dir

    print(f"Brightway data folder: {brightway_dir()}", flush=True)
    name = install_defaults(args.project, args.version, Path(args.data) / "downloads")
    print(f"{name} and its impact methods are ready in project {args.project}", flush=True)


def ecoinvent(args) -> None:
    """ecoinvent account (kept in the system's credential store) and release downloads."""
    from . import credentials

    if args.action == "login":
        import getpass

        username = input("ecoinvent user name: ").strip()
        password = getpass.getpass("ecoinvent password: ")
        versions = credentials.check(username, password)
        credentials.save(username, password)
        print(f"Logged in; saved in {credentials.store()}. Versions available: {', '.join(versions)}")
    elif args.action == "logout":
        credentials.forget()
        print("ecoinvent credentials removed from this computer")
    else:
        from .importing import import_ecoinvent_release

        if not args.version or not args.project:
            raise SystemExit("Give --version and --project")
        found = credentials.load()
        if found is None:
            raise SystemExit("No ecoinvent credentials: run lca-viewer ecoinvent login first")
        t = time.perf_counter()
        database = import_ecoinvent_release(args.project, args.version, args.system_model, *found,
                                            biosphere=args.biosphere)
        print(f"imported {database} into project {args.project} ({time.perf_counter() - t:.0f}s)")


def dataset(args) -> None:
    """Write the dataset described in a JSON file (the Create dataset panel) into a project, or in
    place of the dataset it names."""
    import json

    from .editing import write_dataset

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    try:
        result = write_dataset(args.project, spec)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print(f"Wrote {spec['process']['name']} into {result['database']}", flush=True)
    print("RESULT " + json.dumps(result), flush=True)


def apply(args) -> None:
    """Make the changes staged in the app (a JSON file: a list of changes) in one transaction."""
    import json

    from .editing import apply_changes

    changes = json.loads(Path(args.changes).read_text(encoding="utf-8"))
    try:
        result = apply_changes(args.project, changes)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print(f"Applied {result['applied']} changes to {', '.join(result['databases'])}", flush=True)
    print("RESULT " + json.dumps(result), flush=True)


def delete_dataset(args) -> None:
    """Delete a dataset written by the Create dataset panel, if nothing uses it."""
    import json

    from .editing import delete_dataset as delete

    try:
        result = delete(args.project, args.database, args.code)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print(f"Deleted {result['deleted']} from {result['database']}", flush=True)
    print("RESULT " + json.dumps(result), flush=True)


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
    p.add_argument("path", help="ecoSpold2 release (.7z archive, or the folder it was unpacked to), or SimaPro CSV file")
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
    p.add_argument("--biosphere", default="biosphere3",
                   help="Biosphere database of the project that emissions link to (default: biosphere3, installed "
                        "with the ecoinvent 3.9 impact methods if missing)")
    p.add_argument("--no-setup", action="store_true",
                   help="With --biosphere biosphere3: do not install it nor the impact methods (it must exist)")
    p.add_argument("--defaults", metavar="VERSION",
                   help="First install the default biosphere and impact methods of this ecoinvent version, from "
                        "files.brightway.dev (e.g. 3.10; 'bundled': biosphere3 and the ecoinvent 3.9 methods that "
                        "come with Brightway), and link to that biosphere instead of --biosphere")
    p.add_argument("--data", default="data", help="Folder where a .7z archive is unpacked during the import (default: data)")
    p.set_defaults(func=import_)

    p = sub.add_parser("project", help="Create, delete or restore a Brightway project (in BRIGHTWAY2_DIR); "
                                       "init: create the data folder with a default project, as Brightway does")
    p.add_argument("action", choices=["init", "create", "delete", "restore"])
    p.add_argument("name", nargs="?", help="Project name (for restore: default, the name in the archive)")
    p.add_argument("--file", help="Backup archive (.tar.gz) to restore, e.g. BAFU from files.brightway.dev")
    p.add_argument("--setup", action="store_true",
                   help="With create: install the biosphere and impact methods bundled with Brightway (ecoinvent 3.9)")
    p.set_defaults(func=project)

    p = sub.add_parser("database", help="Delete a database from a Brightway project (in BRIGHTWAY2_DIR)")
    p.add_argument("action", choices=["delete"])
    p.add_argument("name", help="Database name")
    p.add_argument("--project", required=True)
    p.set_defaults(func=database)

    p = sub.add_parser("defaults", help="Install the default biosphere and impact methods of an ecoinvent version "
                                        "from files.brightway.dev into a project (in BRIGHTWAY2_DIR)")
    p.add_argument("version", help="e.g. 3.10; 'bundled': biosphere3 and the ecoinvent 3.9 methods that come with Brightway")
    p.add_argument("--project", required=True)
    p.add_argument("--data", default="data", help="Folder where the download is kept (default: data)")
    p.set_defaults(func=defaults)

    p = sub.add_parser("ecoinvent", help="Download an ecoinvent release into a project (in BRIGHTWAY2_DIR) "
                                         "with your ecoinvent account")
    p.add_argument("action", choices=["login", "logout", "import"],
                   help="login: save your account in the system's credential store (Windows Credential Manager, "
                        "macOS Keychain, Secret Service); logout: remove it; import: download and import a release")
    p.add_argument("--project", help="With import: the project (created if needed)")
    p.add_argument("--biosphere", help="With import: a biosphere database of the project to link to, without "
                                       "impact methods (default: the release's own biosphere and methods)")
    p.add_argument("--version", help="With import: e.g. 3.10.1")
    p.add_argument("--system-model", default="cutoff", choices=["cutoff", "apos", "consequential", "EN15804"])
    p.set_defaults(func=ecoinvent)

    p = sub.add_parser("dataset", help="Write a dataset described in a JSON file into a project (in BRIGHTWAY2_DIR)")
    p.add_argument("spec", help="JSON file: see lca_viewer.editing.write_dataset")
    p.add_argument("--project", required=True)
    p.set_defaults(func=dataset)

    p = sub.add_parser("apply", help="Make the changes staged in the app (a JSON file) in one transaction")
    p.add_argument("changes", help="JSON file: see lca_viewer.editing.apply_changes")
    p.add_argument("--project", required=True)
    p.set_defaults(func=apply)

    p = sub.add_parser("delete-dataset", help="Delete a dataset written by the Create dataset panel, if nothing uses it")
    p.add_argument("--project", required=True)
    p.add_argument("--database", required=True)
    p.add_argument("--code", required=True, help="Code of a process of the dataset")
    p.set_defaults(func=delete_dataset)

    p = sub.add_parser("setup", help="First-time setup; optionally unpacks, imports and builds an ecoinvent ecoSpold2 release")
    p.add_argument("source", nargs="?", help="ecoSpold2 archive (.7z) or the folder it was unpacked to; asked when omitted (Enter skips ecoinvent)")
    p.add_argument("--data", default="data", help="Folder for the models and models.json (default: data); "
                                                  "ecoinvent goes into a project of BRIGHTWAY2_DIR")
    p.add_argument("--yes", action="store_true", help="Take the default answer to every question (without a source: no ecoinvent)")
    p.set_defaults(func=setup)

    p = sub.add_parser("serve", help="Serve the explorer; databases are loaded from the page, or with --model")
    p.add_argument("--model", action="append",
                   help="Model directory loaded at start; repeat to load several (default: none, "
                        "choose them in the page with Load database)")
    p.add_argument("--data", default="data",
                   help="Folder of the built models (models.json, cache/), offered by Load database "
                        "with the projects of BRIGHTWAY2_DIR (default: data)")
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
