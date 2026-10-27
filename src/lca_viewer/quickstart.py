"""``lca-viewer setup``: from an ecoinvent ecoSpold2 download to a model the app serves.

Asks for the archive (or the folder it was unpacked to), then unpacks it, imports it into a
Brightway project in ``data/brightway`` and builds the model, skipping the steps already
done, so it can simply be run again after an interruption. Built models are listed in
``data/models.json``, which the app offers in its *Load database* dialog.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

from .sources import register

SYSTEM_MODELS = {"cutoff": "cut-off", "apos": "APOS", "consequential": "consequential", "en15804": "EN15804"}
DEFAULT_ROOT = "market for wheat grain, feed|GLO"


class Prompt:
    """Questions on the terminal; with ``yes``, defaults are taken without asking."""

    def __init__(self, yes: bool = False):
        self.yes = yes

    def ask(self, question: str, default: str = "") -> str:
        if self.yes and default:
            print(f"{question}: {default}")
            return default
        answer = input(f"{question} [{default}]: " if default else f"{question}: ").strip()
        return answer or default

    def confirm(self, question: str, default: bool = True) -> bool:
        answer = self.ask(f"{question} (y/n)", "y" if default else "n")
        return answer.lower().startswith("y")


def clean_path(text: str) -> Path:
    """A path typed, pasted or dragged onto the terminal (quotes and PowerShell's ``& `` removed)."""
    text = text.strip()
    if text.startswith("& "):
        text = text[2:]
    return Path(text.strip().strip("'\"")).expanduser()


def parse_release(name: str) -> tuple[str | None, str | None]:
    """ecoinvent version and system model from a release name, e.g. ``ecoinvent 3.9.1_cutoff_ecoSpold02.7z``."""
    version = re.search(r"\d+\.\d+(?:\.\d+)?", name)
    flat = re.sub(r"[-_ ]", "", name.lower())
    model = next((m for m in SYSTEM_MODELS if m in flat), "consequential" if "conseq" in flat else None)
    return (version.group() if version else None), model


def find_datasets(folder: Path) -> Path | None:
    """The folder of ``.spold`` files in an unpacked release: ``folder``, ``folder/datasets`` or one level down."""
    if not folder.is_dir():
        return None
    candidates = [folder / "datasets", folder] + [child / "datasets" for child in sorted(folder.iterdir()) if child.is_dir()]
    return next((c for c in candidates if c.is_dir() and any(c.glob("*.spold"))), None)


def unpack(archive: Path, target: Path) -> None:
    """Into ``target``, through ``target.part`` so that an interrupted run starts again cleanly."""
    try:
        import py7zr
    except ImportError:
        raise SystemExit("Unpacking needs py7zr (pip install py7zr), or unpack the archive with 7-Zip "
                         "and give the folder instead.") from None
    if target.exists():
        raise SystemExit(f"{target} exists but has no ecoSpold2 datasets: delete it and run setup again.")
    partial = target.with_name(target.name + ".part")
    shutil.rmtree(partial, ignore_errors=True)
    print(f"Unpacking {archive.name} to {target} (a few minutes)...")
    with py7zr.SevenZipFile(archive) as z:
        z.extractall(partial)
    partial.rename(target)


def ask_source(p: Prompt, given: str | None) -> tuple[Path | None, Path]:
    """(archive or None, datasets folder), asking until the path is usable."""
    while True:
        text = given or p.ask("Path of the ecoinvent ecoSpold2 archive (.7z), or of the folder it was unpacked to\n"
                              "(you can drag the file onto this window)")
        given = None
        path = clean_path(text)
        if path.is_file() and path.suffix.lower() == ".7z":
            return path, None
        datasets = find_datasets(path)
        if datasets:
            return None, datasets
        print(f"  No .7z archive or folder of .spold files at {path}. Try again.")
        if p.yes:
            raise SystemExit(1)


def run(args) -> None:
    data = Path(args.data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    # The start scripts serve with the Python that ran the setup
    (data / "python.txt").write_text(sys.executable + "\n", encoding="utf-8")
    p = Prompt(args.yes)

    print("\nlca_viewer setup: ecoinvent ecoSpold2 -> Brightway -> model for the app\n")
    archive, datasets = ask_source(p, args.source)
    version, model = parse_release(archive.name if archive else f"{datasets.parent.name} {datasets.name}")
    version = p.ask("ecoinvent version", version or "")
    while not re.fullmatch(r"\d+\.\d+(\.\d+)?", version):
        version = p.ask("ecoinvent version, e.g. 3.9.1")
    model = p.ask(f"System model ({', '.join(SYSTEM_MODELS)})", model or "cutoff").lower().replace("-", "")
    if not version.startswith("3.9."):
        print(f"  The impact methods installed with lca_viewer are those of ecoinvent 3.9: with {version}, "
              f"some characterisation factors may not match the elementary flows.")
        if not p.confirm("Continue anyway?", default=False):
            raise SystemExit(1)

    digits = version.replace(".", "")
    project = f"lca_viewer_ei{digits}"
    database = f"ecoinvent-{version}-{model}"
    output = data / "cache" / f"ei{digits}_{model}_all"
    title = f"ecoinvent {version} {SYSTEM_MODELS.get(model, model)}"

    print("\n[1/3] Unpack")
    unpacked = data / f"ei{digits}_{model}"
    if archive:
        datasets = find_datasets(unpacked)
        if datasets:
            print(f"  Already unpacked in {unpacked}")
        else:
            unpack(archive, unpacked)
            datasets = find_datasets(unpacked)
            if not datasets:
                raise SystemExit(f"No .spold files found in {unpacked}: is this an ecoSpold2 archive?")
    else:
        print(f"  Using {datasets}")

    print(f"\n[2/3] Import into Brightway (project {project}, database {database})")
    brightway_dir = data / "brightway"
    brightway_dir.mkdir(exist_ok=True)
    previous = os.environ.get("BRIGHTWAY2_DIR")
    if previous and Path(previous).resolve() != brightway_dir:
        print(f"  Using {brightway_dir}, not BRIGHTWAY2_DIR={previous}")
    os.environ["BRIGHTWAY2_DIR"] = str(brightway_dir)  # before bw2data is imported
    import bw2data as bd

    from .importing import import_ecospold2, setup_project

    bd.projects.set_current(project)
    if database in bd.databases:
        print("  Already imported")
    else:
        print("  About 10 minutes the first time (biosphere and impact methods, then the datasets)...")
        t = time.perf_counter()
        setup_project(project)
        import_ecospold2(project, datasets, database)
        print(f"  Imported in {(time.perf_counter() - t) / 60:.0f} min")

    print(f"\n[3/3] Build the model ({output})")
    if (output / "A.npz").exists():
        print("  Already built")
    else:
        from .brightway import from_brightway

        t = time.perf_counter()
        built = from_brightway(project, database, list(bd.methods))
        partial = output.with_name(output.name + ".part")
        shutil.rmtree(partial, ignore_errors=True)
        built.save(partial)
        partial.rename(output)
        print(f"  {built.size} activities, {len(built.methods)} methods ({time.perf_counter() - t:.0f} s)")
    source = {"brightway": str(brightway_dir), "project": project, "database": database,
              "modified": bd.databases[database].get("modified")}
    register(data, output, title, DEFAULT_ROOT, source)

    if archive and unpacked.exists() and p.confirm(f"\nDelete the unpacked files in {unpacked}? "
                                                   f"They are not needed any more", default=True):
        shutil.rmtree(unpacked)

    start = "double-click start.cmd" if os.name == "nt" else "run: sh start.sh"
    print(f"\nDone. To start the app next time, {start} (or run: lca-viewer serve)")
    if p.confirm("Start the app now?", default=True):
        from .cli import main

        main(["serve", "--data", str(data), "--open"])
