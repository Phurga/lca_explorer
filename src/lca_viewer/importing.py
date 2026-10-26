"""Import LCI databases into a Brightway project, before ``lca-viewer build``.

- ecoSpold2 (ecoinvent releases) with bw2io's ``SingleOutputEcospold2Importer``.
- SimaPro CSV (e.g. Agribalyse) with bw2io's ``SimaProBlockCSVImporter``; inputs taken
  from another database already in the project (e.g. ecoinvent) are renamed from SimaPro
  to ecoinvent names with Pré's correspondence files (``randonneur_data``) then linked.

Nothing is written when exchanges stay unlinked, unless ``unlinked="drop"``: a matrix
built from a database with dangling inputs would silently miss part of the supply chain.
"""

from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path

ECOINVENT_MIGRATIONS = {
    # ecoinvent version in the database name -> randonneur_data migration SimaPro -> ecoinvent
    ("3.9", "cutoff"): "simapro-ecoinvent-3.9.1-cutoff",
    ("3.10", "cutoff"): "simapro-ecoinvent-3.10-cutoff",
    ("3.8", "cutoff"): "simapro-ecoinvent-3.8-cutoff",
}


def setup_project(project: str) -> None:
    """Biosphere database and LCIA methods (ecoinvent 3.9, bundled with bw2io), once per project.

    ``bw2io.bw2setup()`` fails with bw2data 4.7 + bw2io 0.9.17 (the bundled LCIA data uses lists
    as flow keys and ``Method.write`` requires tuples), hence the conversion here.
    """
    import bw2data as bd
    import bw2io as bi
    from bw2io.importers.base_lcia import LCIAImporter

    bd.projects.set_current(project)
    if "biosphere3" not in bd.databases:
        bi.create_default_biosphere3()
    if not bd.methods:
        fp = Path(bi.__file__).parent / "data" / "lcia" / "lcia_39_ecoinvent.zip"
        with zipfile.ZipFile(fp) as archive:
            data = json.load(archive.open("data.json"))
        for m in data:
            m["name"] = tuple(m["name"])
            for exc in m["exchanges"]:
                if isinstance(exc.get("input"), list):
                    exc["input"] = tuple(exc["input"])
        importer = LCIAImporter("lcia_39_ecoinvent.zip")
        importer.data = data
        importer.write_methods()
        bi.create_core_migrations()


def _check_new(database: str) -> None:
    import bw2data as bd

    if database in bd.databases:
        raise SystemExit(
            f"Database {database!r} already exists in project {bd.projects.current!r}. "
            f"Delete it first if you want to re-import it: "
            f'python -c "import bw2data as bd; bd.projects.set_current(\'{bd.projects.current}\'); del bd.databases[\'{database}\']"'
        )


def import_ecospold2(project: str, path, database: str) -> None:
    """An ecoinvent release, extracted (``path`` is its ``datasets`` folder)."""
    import bw2data as bd
    import bw2io as bi

    bd.projects.set_current(project)
    _check_new(database)
    importer = bi.SingleOutputEcospold2Importer(str(path), database, use_mp=True)
    importer.apply_strategies()
    importer.statistics()
    importer.write_database()
