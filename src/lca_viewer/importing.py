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


MIGRATION_FIELDS = {
    # Fields compared when matching an exchange to a randonneur_data transformation
    "agribalyse-3.1.1-biosphere-ecoinvent-3.8-biosphere": ["name", "unit", "context"],
}
TECHNOSPHERE = ("technosphere", "production", "substitution")
SIMAPRO_GEO = re.compile(r"^(?P<name>.+?),? \{(?P<geo>[^{}]+)\}\s*(?:[SU])?$")


def default_migrations(link_to) -> list[str]:
    """randonneur_data migrations renaming SimaPro inputs to the ecoinvent databases in ``link_to``."""
    out = []
    for db in link_to:
        version = re.search(r"3\.(\d+)", db)
        model = "cutoff" if re.search(r"cut-?off", db, re.I) else None
        label = ECOINVENT_MIGRATIONS.get((f"3.{version.group(1)}" if version else None, model))
        if label and label not in out:
            out.append(label)
    return out


def link_internal_by_product_name(data: list[dict]) -> list[dict]:
    """Link unlinked inputs and wastes to the product of this database with the same name and unit.

    SimaPro product names are unique, but bw2io's own internal matching also compares the location,
    which products take from their process (``Geography``) while inputs have none.
    """
    products = {}
    for ds in data:
        if ds.get("type") == "product":
            products.setdefault((ds["name"], ds.get("unit")), (ds["database"], ds["code"]))
    for ds in data:
        for exc in ds.get("exchanges", []):
            if exc.get("type") in TECHNOSPHERE and not exc.get("input"):
                key = products.get((exc.get("name"), exc.get("unit")))
                if key:
                    exc["input"] = key
    return data


def context_as_categories(data: list[dict]) -> list[dict]:
    """bw_simapro_csv gives emissions a SimaPro ``context`` (``("Emissions to air", "low. pop.")``);
    bw2io's SimaPro normalisation strategies work on ``categories``."""
    for ds in data:
        for exc in ds.get("exchanges", []):
            if exc.get("type") == "biosphere" and not exc.get("categories") and exc.get("context"):
                exc["categories"] = tuple(c for c in exc["context"] if c)
    return data


def split_name_geography(data: list[dict]) -> list[dict]:
    """``"Soft wheat grain, at farm gate {FR} U"`` -> name without the suffix, location ``FR``.

    Applied after linking (exchanges are matched on the full SimaPro names), to nodes and to
    functional exchanges, after which processes allocated at write time are named.
    """
    for ds in data:
        for exc in ds.get("exchanges", []):
            match = SIMAPRO_GEO.match(exc.get("name") or "") if exc.get("functional") else None
            if match:
                exc["name"] = match["name"]
        match = SIMAPRO_GEO.match(ds.get("name") or "")
        if match:
            ds["simapro name"] = ds["name"]
            ds["name"], ds["location"] = match["name"], match["geo"]
            if ds.get("reference product") == ds["simapro name"]:
                ds["reference product"] = match["name"]
        elif SIMAPRO_GEO.match(ds.get("reference product") or ""):
            ds["reference product"] = SIMAPRO_GEO.match(ds["reference product"])["name"]
    return data


def unlinked_exchanges(data: list[dict]) -> list[dict]:
    """One row per distinct unlinked exchange (zero-amount placeholders excluded)."""
    rows = {}
    for ds in data:
        for exc in ds.get("exchanges", []):
            if exc.get("input") or (exc.get("type") == "production" and not exc.get("amount")):
                continue
            key = (exc.get("type"), exc.get("name"), tuple(exc.get("categories") or exc.get("context") or ()), exc.get("unit"))
            row = rows.setdefault(key, {
                "type": key[0], "name": key[1], "categories": "/".join(key[2]), "unit": key[3],
                "exchanges": 0, "example process": ds.get("name"),
            })
            row["exchanges"] += 1
    return sorted(rows.values(), key=lambda r: (r["type"] or "", -r["exchanges"]))


def write_report(rows: list[dict], path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["type", "name", "categories", "unit", "exchanges", "example process"])
        writer.writeheader()
        writer.writerows(rows)


def drop_unlinked(data: list[dict]) -> list[dict]:
    for ds in data:
        ds["exchanges"] = [
            exc for exc in ds.get("exchanges", [])
            if exc.get("input") or (exc.get("type") == "production" and not exc.get("amount"))
        ]
    return data


def import_simapro_csv(
    project: str,
    path,
    database: str,
    link_to=(),
    migrations=None,
    unlinked: str = "fail",
    report=None,
    biosphere: str = "biosphere3",
) -> dict:
    """Import a SimaPro CSV process export as ``database``, linked to the databases in ``link_to``.

    ``migrations``: randonneur_data labels renaming unlinked inputs before linking them (default:
    SimaPro -> ecoinvent names for the ecoinvent versions in ``link_to``). ``unlinked``: "fail"
    (write nothing) or "drop" (remove unlinked exchanges). Unlinked exchanges are listed in the CSV
    ``report``. Returns counts: ``{"processes", "unlinked", "report"}``.
    """
    import functools

    import bw2data as bd
    import bw2io as bi
    from bw2io import strategies as s

    bd.projects.set_current(project)
    _check_new(database)
    for db in [biosphere, *link_to]:
        if db not in bd.databases:
            raise SystemExit(f"Database {db!r} is not in project {project!r}: import it first")
    if "simapro-water" not in bi.migrations:
        bi.create_core_migrations()

    importer = bi.SimaProBlockCSVImporter(Path(path), database_name=database, biosphere_database_name=biosphere)
    importer.apply_strategies()  # bw2io defaults: allocation of multi-output processes, exact links
    importer.apply_strategy(link_internal_by_product_name)

    # Inputs from other databases: SimaPro names -> ecoinvent names (Pré's correspondence), then link
    def unlinked_edge(edge) -> bool:
        return not edge.get("input")

    for label in default_migrations(link_to) if migrations is None else migrations:
        importer.randonneur(label, fields=MIGRATION_FIELDS.get(label, ["name"]), edge_filter=unlinked_edge)
    for db in link_to:
        importer.apply_strategy(functools.partial(
            s.link_iterable_by_fields, other=bd.Database(db), edge_kinds=["technosphere", "substitution"],
            fields=("name", "reference product", "location"),
        ))

    # Emissions and resources: SimaPro -> ecoinvent flow names and compartments, then link
    importer.apply_strategies([
        context_as_categories,
        s.drop_unspecified_subcategories,
        s.normalize_simapro_biosphere_categories,
        s.normalize_simapro_biosphere_names,
        s.normalize_units,
        functools.partial(s.migrate_exchanges, migration="simapro-water"),
        s.fix_localized_water_flows,
        s.strip_biosphere_exc_locations,
        functools.partial(
            s.link_iterable_by_fields, other=bd.Database(biosphere), edge_kinds=["biosphere"],
            fields=("name", "categories", "unit"),
        ),
    ])

    rows = unlinked_exchanges(importer.data)
    report = Path(report or Path("data") / f"import-{re.sub(r'[^A-Za-z0-9_.-]+', '-', database)}-unlinked.csv")
    if rows:
        write_report(rows, report)
        n = sum(r["exchanges"] for r in rows)
        print(f"{len(rows)} distinct unlinked exchanges ({n} in all), listed in {report}")
        if unlinked == "fail":
            raise SystemExit(
                "Nothing written. Fix the links (e.g. --link-to, --migration), or re-run with "
                "--unlinked drop to write the database without these exchanges."
            )
        importer.apply_strategy(drop_unlinked)
    importer.apply_strategy(split_name_geography)
    importer.write_database()
    processes = sum(1 for ds in importer.data if ds.get("type") in bd.labels.process_node_types)
    return {"processes": processes, "unlinked": len(rows), "report": str(report) if rows else None}


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
