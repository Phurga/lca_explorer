"""``lca-viewer setup``: record the Python the start script runs the app with.

The app starts with no database: *Load database* offers the built models (``data/models.json``)
and the databases of the user's Brightway projects. Also the helpers that unpack an ecoSpold2
release for ``lca-viewer import``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

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


def run(args) -> None:
    data = Path(args.data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    # lca_explorer.cmd serves with the Python that ran the setup
    (data / "python.txt").write_text(sys.executable + "\n", encoding="utf-8")
    print("lca_viewer is installed.")
