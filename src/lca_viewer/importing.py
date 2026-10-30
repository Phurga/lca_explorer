"""Import LCI databases into a Brightway project, before ``lca-viewer build``.

- ecoSpold2 (ecoinvent releases) with bw2io's ``SingleOutputEcospold2Importer``.
- ecoinvent releases downloaded with the user's account, with bw2io's ``import_ecoinvent_release``
  (credentials: :mod:`lca_viewer.credentials`).
- SimaPro CSV (e.g. Agribalyse) with bw2io's ``SimaProBlockCSVImporter``; inputs taken
  from another database already in the project (e.g. ecoinvent) are renamed from SimaPro
  to ecoinvent names with Pré's correspondence files (``randonneur_data``) then linked.

Nothing is written when exchanges stay unlinked, unless ``unlinked="drop"``: a matrix
built from a database with dangling inputs would silently miss part of the supply chain.
"""

from __future__ import annotations

import collections
import csv
import json
import math
import pickle
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


def default_versions() -> list[str]:
    """ecoinvent versions whose biosphere and impact methods files.brightway.dev publishes (as the
    projects ``ecoinvent-<version>-biosphere``), newest first."""
    from .sources import remote_projects

    found = [m.group(1) for k in remote_projects() if (m := re.fullmatch(r"ecoinvent-([\d.]+)-biosphere", k))]
    return sorted(found, key=lambda v: [int(x) for x in v.split(".")], reverse=True)


class _PlainUnpickler(pickle.Unpickler):
    """The archive's pickles are nodes and exchanges (dicts) and characterisation factors: the only
    class to build is numpy's scalars, the numbers some of them hold."""

    NUMPY = {("numpy._core.multiarray", "scalar"), ("numpy.core.multiarray", "scalar"), ("numpy", "dtype")}

    def find_class(self, module, name):
        if (module, name) in self.NUMPY:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"{module}.{name} not allowed")


def read_project_archive(path) -> tuple[dict, dict, list]:
    """The databases and impact methods of a Brightway project archive (``.tar.gz``), read without
    restoring the project: ``({database: metadata}, {database: {(database, code): node}}, [(name,
    metadata, [((database, code), factor), …]), …])``. Each node holds its ``exchanges``."""
    import io
    import sqlite3
    import tarfile
    import tempfile
    from contextlib import closing

    def load(blob):
        return _PlainUnpickler(io.BytesIO(blob)).load()

    with tarfile.open(path) as tar, tempfile.TemporaryDirectory() as tmp:
        root = tar.getnames()[0].split("/")[0]
        databases = json.load(tar.extractfile(f"{root}/databases.json"))
        db_file = Path(tmp) / "databases.db"
        db_file.write_bytes(tar.extractfile(f"{root}/lci/databases.db").read())
        nodes, key_of = {database: {} for database in databases}, {}
        exchanges = collections.defaultdict(list)
        with closing(sqlite3.connect(db_file)) as con:
            for db, code, data in con.execute("SELECT output_database, output_code, data FROM exchangedataset"):
                exchanges[(db, code)].append(load(data))
            for i, db, code, data in con.execute("SELECT id, database, code, data FROM activitydataset"):
                key_of[i] = (db, code)
                if db in nodes:
                    nodes[db][(db, code)] = {**load(data), "exchanges": exchanges.pop((db, code), [])}
        methods = []
        for name, meta in json.load(tar.extractfile(f"{root}/methods.json")):
            blob = tar.extractfile(f"{root}/intermediate/{meta['abbreviation']}.pickle").read()
            cfs = [(key_of[key] if isinstance(key, int) else tuple(key), *rest) for key, *rest in load(blob)]
            methods.append((tuple(name), meta, cfs))
    return databases, nodes, methods


def _download(archive: str, downloads, message: str) -> Path:
    """An archive of files.brightway.dev, downloaded once into ``downloads``."""
    import urllib.request

    from .sources import REMOTE

    path = Path(downloads) / archive
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        print(message, flush=True)
        with urllib.request.urlopen(REMOTE + archive, timeout=60) as response, open(part, "wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        part.replace(path)
    return path


def _write_methods(methods: list, prefix: str) -> None:
    """Impact methods read from an archive, named ``(prefix, …)`` so that they never replace those
    the project has."""
    import bw2data as bd

    for name, meta, cfs in methods:
        method = bd.Method((prefix, *name))
        method.register(**{k: v for k, v in meta.items() if k not in ("abbreviation", "num_cfs", "geocollections", "filepath")})
        method.write(cfs)
    print(f"{len(methods)} impact methods installed", flush=True)


def install_defaults(project: str, version: str, downloads) -> str:
    """The default biosphere and impact methods of ecoinvent ``version`` in ``project``, as
    Activity Browser's default data; returns the biosphere database's name.

    ``"bundled"``: biosphere3 and the ecoinvent 3.9 methods that come with bw2io (offline).
    Otherwise files.brightway.dev's project ``ecoinvent-<version>-biosphere`` (ecoinvent's
    elementary flows and LCIA implementation of that version), downloaded once into ``downloads``
    and read without restoring it: its biosphere database, and its methods named
    ``("ecoinvent-<version>", …)`` as bw2io's ``import_ecoinvent_release`` names them, so that they
    never replace methods of another version. What the project has already is kept.
    """
    import bw2data as bd

    from .sources import remote_projects

    if version == "bundled":
        print("Installing the biosphere and the impact methods of Brightway (ecoinvent 3.9, a few minutes)", flush=True)
        setup_project(project)
        return "biosphere3"
    bd.projects.set_current(project)
    database, prefix = f"ecoinvent-{version}-biosphere", f"ecoinvent-{version}"
    has_methods = any(m[0] == prefix for m in bd.methods)
    if database in bd.databases and has_methods:
        print(f"{database} and its impact methods are already in {project}", flush=True)
        return database
    archive = remote_projects().get(database)
    if not archive:
        raise SystemExit(f"files.brightway.dev has no biosphere for ecoinvent {version}")
    path = _download(archive, downloads, f"Downloading the biosphere and impact methods of ecoinvent {version} from files.brightway.dev")
    databases, nodes, methods = read_project_archive(path)
    if database not in databases:
        raise SystemExit(f"{archive} holds {', '.join(databases)}, not {database}")
    if database not in bd.databases:
        print(f"Writing {database} ({len(nodes[database])} elementary flows)", flush=True)
        bd.Database(database).write(nodes[database])
    if not has_methods:
        print(f"Installing {len(methods)} impact methods of ecoinvent {version} (about 3 minutes)", flush=True)
        _write_methods(methods, prefix)
    return database


def install_remote_project(project: str, remote: str, downloads) -> list[str]:
    """The databases and impact methods of a project of files.brightway.dev (e.g. BAFU), added to
    ``project``, which keeps what it has; returns the databases' names. Read without restoring it,
    as for :func:`install_defaults`: a restore can only make a new project. The methods are named
    ``(remote, …)``. A database with its own biosphere stays apart from the rest of the project:
    the project's other methods score nothing on it, and its methods nothing on other databases.
    """
    import bw2data as bd

    from .sources import remote_projects

    bd.projects.set_current(project)
    archive = remote_projects().get(remote)
    if not archive:
        raise SystemExit(f"No project {remote!r} on files.brightway.dev")
    has_methods = any(m[0] == remote for m in bd.methods)
    path = _download(archive, Path(downloads), f"Downloading {remote} from files.brightway.dev")
    databases, nodes, methods = read_project_archive(path)
    if all(d in bd.databases for d in databases) and (has_methods or not methods):
        print(f"{remote} is already in {project}", flush=True)
        return list(databases)
    for database in sorted(databases, key=lambda d: len(databases[d].get("depends", []))):  # what it links to first
        if database not in bd.databases:
            print(f"Writing {database} ({len(nodes[database])} nodes)", flush=True)
            bd.Database(database).register(**{k: v for k, v in databases[database].items() if k in ("format", "comment")})
            bd.Database(database).write(nodes[database])
    if not has_methods:
        print(f"Installing {len(methods)} impact methods of {remote}", flush=True)
        _write_methods(methods, remote)
    return list(databases)


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


COPIED_FROM_ECOINVENT = re.compile(r", S - Copied from Ecoinvent(?: U)?$", re.I)
# SimaPro compartments that are not elementary flows in ecoinvent's biosphere
NOT_ELEMENTARY = {"final waste flows", "non material emissions", "social issues", "economic issues"}
# randonneur_data correspondences SimaPro -> ecoinvent elementary flows (the first two made with
# Flowmapper by its authors, the third by hand), tried in this
# order; a flow is linked only to a flow of the project's biosphere with the same ecoinvent UUID, so
# a list made for another ecoinvent version can still link the many flows whose UUID didn't change
BIOSPHERE_CORRESPONDENCES = [
    "SimaPro-9-ecoinvent-3.9-biosphere", "SimaPro-9-ecoinvent-3.8-biosphere",
    "simapro-2024-biosphere-ecoinvent-3.10-biosphere",
]


def is_adapter_file(migration) -> bool:
    """A migration that is a file of the user's (a randonneur datapackage, ``.json``) rather than the label of one
    of randonneur_data's."""
    return str(migration).lower().endswith(".json") or any(c in str(migration) for c in "/\\")


def adapter_file(path) -> dict:
    """The transformations of an adapter file: a randonneur datapackage (JSON) whose ``replace``, ``update`` and
    ``disaggregate`` lists each hold ``{"source": {...}, "target": {...}}`` (fields as of the exchanges, e.g. name,
    reference product, location, unit, categories)."""
    import json

    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"Adapter file {path} not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise SystemExit(f"Adapter file {path.name} is not valid JSON: {e}") from None
    verbs = ("replace", "update", "disaggregate", "delete", "create")
    if not isinstance(data, dict) or not any(isinstance(data.get(v), list) for v in verbs):
        raise SystemExit(f"Adapter file {path.name} has no transformations: expected a randonneur datapackage "
                         f"with a list under one of {', '.join(verbs)}")
    return data


def _adapter_data(migration, registry=None) -> dict:
    """The transformations of a migration: the user's file, or randonneur_data's by label."""
    if is_adapter_file(migration):
        return adapter_file(migration)
    import randonneur_data

    return (registry or randonneur_data.Registry()).get_file(migration)


def apply_adapter_file(importer, path, edge_filter=None, log=print) -> None:
    """Apply the transformations of an adapter file to the exchanges of an import (what ``importer.randonneur``
    does for randonneur_data's), matching on the fields of each transformation's ``source``."""
    import randonneur as rn

    migrations = adapter_file(path)
    config = rn.MigrationConfig(fields=None, edge_filter=edge_filter, edges_label="exchanges", verbose=False,
                                case_sensitive=False, add_extra_attributes=True, verbs=rn.utils.SAFE_VERBS)
    before = sum(1 for ds in importer.data for e in ds.get("exchanges", []) if edge_filter is None or edge_filter(e))
    try:
        importer.data = rn.migrate_edges(graph=importer.data, migrations=migrations, config=config)
    except rn.errors.WrongGraphContext:
        log(f"Adapter file {Path(path).name}: it is not for exchanges, skipped")
        return
    after = sum(1 for ds in importer.data for e in ds.get("exchanges", []) if edge_filter is None or edge_filter(e))
    log(f"Adapter file {Path(path).name}: {before - after} of {before} unlinked exchanges changed")


def _replacements(labels) -> dict[str, dict]:
    """SimaPro process name -> ecoinvent target (name, reference product, location) of the
    randonneur_data migrations ``labels`` (or adapter files)."""
    out = {}
    registry = None
    if any(not is_adapter_file(label) for label in labels):
        import randonneur_data

        registry = randonneur_data.Registry()
    for label in labels:
        data = _adapter_data(label, registry)
        for verb in ("replace", "update"):
            for t in data.get(verb, []):
                name = t["source"].get("name")
                if name and t.get("target", {}).get("name"):
                    out.setdefault(name, t["target"])
    return out


def replace_aggregated_copies(data: list[dict], link_to, labels, log=print) -> list[dict]:
    """Remove the aggregated ecoinvent processes copied into a SimaPro export (products named
    ``"…| Cut-off, S - Copied from Ecoinvent"``, without technosphere inputs: system processes,
    e.g. Agribalyse's background) when the ecoinvent process they copy is in ``link_to``; inputs
    of their products get their SimaPro ecoinvent name back (``"…| Cut-off, U"``), so they link to
    that process. Processes based on ecoinvent but changed (with inputs) are kept, as are copies
    with no match. As randonneur_data's ``agribalyse-3.1.1-delete-aggregated-ecoinvent`` and
    ``…-restore-simapro-ecoinvent-names``, which go by process identifiers instead: those change
    with every SimaPro export.
    """
    from bw2data.backends import ActivityDataset as AD

    targets = _replacements(labels)
    known = {key for db in link_to for key in AD.select(AD.name, AD.product, AD.location).where(AD.database == db).tuples()}
    deleted_products, deleted_codes, kept = {}, set(), 0
    for ds in data:
        if ds.get("type") not in ("process", "multifunctional"):
            continue
        functional = [e for e in ds.get("exchanges", []) if e.get("functional")]
        if len(functional) != 1 or not COPIED_FROM_ECOINVENT.search(functional[0].get("name") or ""):
            continue
        if any(e.get("type") == "technosphere" and not e.get("functional") for e in ds["exchanges"]):
            continue  # inputs: not an aggregated copy, maybe changed by the database's authors
        restored = COPIED_FROM_ECOINVENT.sub(", U", functional[0]["name"])
        target = targets.get(restored)
        if target and (target["name"], target.get("reference product"), target.get("location")) in known:
            deleted_products[functional[0]["name"]] = restored
            deleted_codes.add(ds["code"])
        else:
            kept += 1
    if not deleted_codes:
        return data
    database = data[0].get("database")

    def gone(ds) -> bool:
        parent = ds.get("mf_parent_key") or ds.get("reference process")
        return ds.get("code") in deleted_codes or bool(parent and tuple(parent)[1] in deleted_codes)

    data[:] = [ds for ds in data if not (ds.get("database") == database and gone(ds))]
    renamed = 0
    for ds in data:
        for e in ds.get("exchanges", []):
            if e.get("type") != "biosphere" and not e.get("functional") and e.get("name") in deleted_products:
                e["name"] = deleted_products[e["name"]]
                e.pop("input", None)
                renamed += 1
    log(f"Replaced {len(deleted_codes)} aggregated ecoinvent copies by links to {', '.join(link_to)} "
        f"({renamed} inputs){f'; kept {kept} without a match there' if kept else ''}")
    return data


# Unit -> (base unit, its size in the base unit), to convert inputs to the unit of the process they
# link to: SimaPro gives some ecoinvent processes in other units than ecoinvent (combine harvesting
# in m2, ecoinvent in hectare; electricity often in MJ) and Pré's correspondence maps names only
UNIT_SIZES = {
    "joule": ("joule", 1.0), "kilojoule": ("joule", 1e3), "megajoule": ("joule", 1e6), "gigajoule": ("joule", 1e9),
    "watt hour": ("joule", 3.6e3), "kilowatt hour": ("joule", 3.6e6), "megawatt hour": ("joule", 3.6e9),
    "square meter": ("square meter", 1.0), "hectare": ("square meter", 1e4), "square kilometer": ("square meter", 1e6),
    "cubic meter": ("cubic meter", 1.0), "litre": ("cubic meter", 1e-3),
    "meter": ("meter", 1.0), "kilometer": ("meter", 1e3),
    "gram": ("kilogram", 1e-3), "kilogram": ("kilogram", 1.0), "ton": ("kilogram", 1e3),
    "second": ("second", 1.0), "s": ("second", 1.0), "minute": ("second", 60.0), "hour": ("second", 3600.0),
    "day": ("second", 86400.0), "year": ("second", 365 * 86400.0),
    "kilogram day": ("kilogram second", 86400.0), "kgy": ("kilogram second", 365 * 86400.0),
    "kilogram year": ("kilogram second", 365 * 86400.0),
    "ton kilometer": ("kilogram meter", 1e6), "kilogram kilometer": ("kilogram meter", 1e3),
    "square meter-year": ("square meter year", 1.0), "hectare year": ("square meter year", 1e4),
    "cubic meter-year": ("cubic meter year", 1.0),
    "person kilometer": ("person meter", 1e3), "vehicle kilometer": ("vehicle meter", 1e3),
}


def unit_factor(source: str, target: str) -> float | None:
    """Amount in ``target`` per amount in ``source`` (normalised unit names), or None."""
    s, t = UNIT_SIZES.get(source), UNIT_SIZES.get(target)
    return s[1] / t[1] if s and t and s[0] == t[0] else None


def convert_to_linked_units(data: list[dict], databases, log=print) -> list[dict]:
    """Inputs linked to a process of ``databases`` in another unit than the process's are converted
    to it (1.25 m2 of combine harvesting -> 1.25e-4 hectare); without a known conversion, they are
    unlinked again, so that they are reported rather than wrong by a factor."""
    from bw2data.backends import ActivityDataset as AD
    from bw2io.units import normalize_units
    from bw2io.utils import rescale_exchange

    unit_of = {(db, code): d.get("unit") for db in databases
               for code, d in AD.select(AD.code, AD.data).where(AD.database == db).tuples()}
    converted, unlinked = collections.Counter(), collections.Counter()
    for ds in data:
        for e in ds.get("exchanges", []):
            key = tuple(e["input"]) if e.get("input") and e.get("type") != "biosphere" else None
            if key not in unit_of or not unit_of[key]:
                continue
            source, target = normalize_units(e.get("unit") or ""), unit_of[key]
            if source == target:
                continue
            factor = unit_factor(source, target)
            if factor is None:
                e.pop("input")
                unlinked[(source, target)] += 1
            else:
                rescale_exchange(e, factor)
                e["unit"] = target
                converted[(source, target)] += 1
    if converted:
        log("Converted inputs to the unit of the process they link to: "
            + ", ".join(f"{n} {s} -> {t}" for (s, t), n in converted.most_common()))
    if unlinked:
        log("Not linked, units that don't convert: " + ", ".join(f"{n} {s} -> {t}" for (s, t), n in unlinked.most_common()))
    return data


def drop_non_elementary(data: list[dict], log=print) -> list[dict]:
    """Leave out SimaPro's final waste flows, non material emissions, social and economic issues:
    ecoinvent's biosphere has no such flows."""
    n = 0
    for ds in data:
        before = len(ds.get("exchanges", []))
        ds["exchanges"] = [e for e in ds.get("exchanges", []) if not (
            e.get("type") == "biosphere" and (e.get("context") or ("",))[0].lower() in NOT_ELEMENTARY)]
        n += before - len(ds["exchanges"])
    if n:
        log(f"Left out {n} exchanges of SimaPro's final waste flows, non material emissions, social or economic issues")
    return data


def link_biosphere_by_correspondence(data: list[dict], biosphere: str, labels=BIOSPHERE_CORRESPONDENCES,
                                     log=print) -> list[dict]:
    """Link SimaPro elementary flows (name, compartment, unit) to ``biosphere`` with Flowmapper's
    correspondences (randonneur_data), only to a flow with the target's ecoinvent UUID, converting
    the amount when the units differ."""
    import randonneur_data
    from bw2data.backends import ActivityDataset as AD
    from bw2io.utils import rescale_exchange

    codes = {code for code, in AD.select(AD.code).where(AD.database == biosphere).tuples()}
    registry = randonneur_data.Registry()
    table: dict[tuple, dict] = {}
    for label in labels:
        try:
            transformations = registry.get_file(label)
        except (KeyError, FileNotFoundError):
            continue
        for verb in ("update", "replace"):
            for t in transformations.get(verb, []):
                s, target = t["source"], t.get("target", {})
                factor = t.get("conversion_factor", 1.0)
                if not isinstance(factor, (int, float)) or not math.isfinite(factor) or factor <= 0:
                    continue  # units that don't convert: a wrong match (Manganese, kg -> a flow in kBq)
                if target.get("identifier") in codes:
                    key = ((s.get("name") or "").lower(), tuple(s.get("context") or ()), (s.get("unit") or "").lower())
                    table.setdefault(key, t)
    n = 0
    for ds in data:
        for e in ds.get("exchanges", []):
            if e.get("type") != "biosphere" or e.get("input"):
                continue
            context = tuple(e.get("context") or ())
            for ctx in (context, tuple(c for c in context if c)):  # "(unspecified)" written as "" or left out
                t = table.get(((e.get("name") or "").lower(), ctx, (e.get("unit") or "").lower()))
                if t:
                    e["input"] = (biosphere, t["target"]["identifier"])
                    factor = t.get("conversion_factor") or 1.0
                    if factor != 1.0:
                        rescale_exchange(e, factor)
                    n += 1
                    break
    log(f"Linked {n} elementary flow exchanges to {biosphere} with Flowmapper's SimaPro correspondences")
    return data


BIOSPHERE_RENAMES = [
    # randonneur_data: names (and some compartments) of SimaPro flows -> ecoinvent, without UUIDs
    "agribalyse-3.1.1-ecoinvent-3.10-biosphere-manual-matches", "simapro-2024-biosphere-ecoinvent-3.10-biosphere",
]


def rename_biosphere_flows(data: list[dict], labels=BIOSPHERE_RENAMES, log=print) -> list[dict]:
    """Give unlinked SimaPro flows their ecoinvent name (and compartment, for some) from manual
    correspondences, e.g. Agribalyse's EF names (``"COD (Chemical Oxygen Demand)"``); they are
    then linked on name, compartment and unit like the other flows."""
    import randonneur_data

    registry = randonneur_data.Registry()
    table: dict[tuple, dict] = {}
    for label in labels:
        try:
            transformations = registry.get_file(label)
        except (KeyError, FileNotFoundError):
            continue
        for verb in ("update", "replace"):
            for t in transformations.get(verb, []):
                s = t["source"]
                if s.get("name") and isinstance(s.get("context", []), (list, tuple)):
                    context = tuple(s["context"]) if s.get("context") else None
                    table.setdefault((s["name"].lower(), context), t["target"])
    n = 0
    for ds in data:
        for e in ds.get("exchanges", []):
            if e.get("type") != "biosphere" or e.get("input"):
                continue
            name, context = (e.get("name") or "").lower(), tuple(e.get("context") or ())
            target = (table.get((name, context)) or table.get((name, tuple(c for c in context if c)))
                      or table.get((name, None)))
            if target:
                if target.get("name"):
                    e["name"] = target["name"]
                if target.get("context"):
                    e["categories"] = tuple(c for c in target["context"] if c)
                n += 1
    log(f"Renamed {n} elementary flow exchanges to ecoinvent names (manual correspondences)")
    return data


EF_CORRESPONDENCES = [
    # randonneur_data: ecoinvent -> EF 3.1 elementary flows, read backwards for flows named as in EF
    "ecoinvent-3.9.1-biosphere-EF-3.1-biosphere", "ecoinvent-3.10.1-biosphere-EF-3.1-biosphere",
    "ecoinvent-3.11-biosphere-EF-3.1-biosphere",
]
EF30_NAMES = {
    # EF 3.0 names used by Agribalyse 3.x -> EF 3.1 names of the correspondence files
    "nitrogen, total": "nitrogen, total (excluding n2)",
    "phosphorus, total": "phosphorus",
}


def rename_ef_flows(data: list[dict], labels=EF_CORRESPONDENCES) -> list[dict]:
    """Give flows named as in the EF reference package (Agribalyse uses it: ``"Phosphorus, total"``)
    the name of the ecoinvent flow that ecoinvent's EF 3.1 correspondence maps to that EF name in
    the same compartment, when it maps exactly one there; then linked on name, compartment and unit."""
    import randonneur_data

    registry = randonneur_data.Registry()
    found: dict[tuple, set] = {}
    for label in labels:
        try:
            transformations = registry.get_file(label)
        except (KeyError, FileNotFoundError):
            continue
        for t in transformations.get("replace", []) + transformations.get("update", []):
            s, target = t["source"], t.get("target", {})
            if s.get("name") and target.get("name"):
                key = (target["name"].lower(), tuple(c.lower() for c in s.get("context") or ()))
                found.setdefault(key, set()).add(s["name"])
    for ds in data:
        for e in ds.get("exchanges", []):
            if e.get("type") == "biosphere" and not e.get("input"):
                name = (e.get("name") or "").lower()
                key = (EF30_NAMES.get(name, name), tuple(c.lower() for c in e.get("categories") or ()))
                if len(found.get(key, ())) == 1:
                    e["name"] = next(iter(found[key]))
    return data


def link_biosphere_ignoring_case(data: list[dict], biosphere: str) -> list[dict]:
    """Last pass: link the remaining flows on name, compartment and unit, whatever the case
    (correspondence files write some ecoinvent names in lower case)."""
    import bw2data as bd

    flows = {}
    for flow in bd.Database(biosphere):
        key = (flow["name"].lower(), tuple(c.lower() for c in flow.get("categories") or ()), (flow.get("unit") or "").lower())
        flows.setdefault(key, flow.key)
    for ds in data:
        for e in ds.get("exchanges", []):
            if e.get("type") == "biosphere" and not e.get("input"):
                key = ((e.get("name") or "").lower(), tuple(c.lower() for c in e.get("categories") or ()),
                       (e.get("unit") or "").lower())
                if key in flows:
                    e["input"] = flows[key]
    return data


def link_internal_by_product_name(data: list[dict]) -> list[dict]:
    """Link unlinked inputs and wastes to the product of this database with the same name and unit.

    Applied to SimaPro's own names, before any renaming: they are unique, while bw2io's
    ``split_simapro_name_geo`` turns ``"Yeast, at plant/FR U"`` and ``"Yeast, at plant/RER U"``
    into the same name. bw2io's own internal matching also compares the location, which products
    take from their process (``Geography``) while inputs have none.
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


def name_allocated_processes(data: list[dict]) -> list[dict]:
    """Allocated (read-only) processes take their product's name, as ``multifunctional`` names them
    when it allocates a SimaPro database (``products_as_process``)."""
    for ds in data:
        if ds.get("type") == "readonly_process":
            functional = [e for e in ds.get("exchanges", []) if e.get("functional")]
            if len(functional) == 1 and functional[0].get("name"):
                ds["name"] = functional[0]["name"]
    return data


def _allocated_database(name: str, requested_backend: str = "sqlite"):
    """The database class bw2io writes with, for data allocated before writing (bw_simapro_csv
    does it): ``multifunctional``'s, without allocating again when processed. That second allocation
    saves every allocated process and its exchanges one by one, each in its own transaction: about
    20 minutes for Agribalyse, for the same result."""
    from multifunctional import MultifunctionalDatabase

    class AllocatedDatabase(MultifunctionalDatabase):
        def process(self, csv: bool = False, allocate: bool = False) -> None:
            super().process(csv=csv, allocate=allocate)

    return AllocatedDatabase(name)


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

    ``migrations``: randonneur_data labels, or paths of adapter files (randonneur datapackages in JSON), renaming
    unlinked inputs before linking them (default: SimaPro -> ecoinvent names for the ecoinvent versions in
    ``link_to``). ``unlinked``: "fail"
    (write nothing) or "drop" (remove unlinked exchanges). Unlinked exchanges are listed in the CSV
    ``report``. Returns counts: ``{"processes", "unlinked", "report"}``.
    """
    import bw2data as bd
    import bw2io as bi

    for migration in migrations or []:  # a file of the user's is checked before the long reading of the export
        if is_adapter_file(migration):
            adapter_file(migration)
    bd.projects.set_current(project)
    _check_new(database)
    for db in [biosphere, *link_to]:
        if db not in bd.databases:
            raise SystemExit(f"Database {db!r} is not in project {project!r}: import it first")
    if "simapro-water" not in bi.migrations:
        bi.create_core_migrations()

    print("Reading the SimaPro CSV (a few minutes for a large export)", flush=True)
    importer = bi.SimaProBlockCSVImporter(Path(path), database_name=database, biosphere_database_name=biosphere)
    link_simapro(importer, link_to, default_migrations(link_to) if migrations is None else migrations, biosphere)

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
    importer.apply_strategy(name_allocated_processes)
    importer.apply_strategy(split_name_geography)
    print(f"Writing {database}", flush=True)
    if importer.needs_multifunctional_database:
        importer.database_class = _allocated_database
    importer.write_database()
    processes = sum(1 for ds in importer.data if ds.get("type") in bd.labels.process_node_types)
    return {"processes": processes, "unlinked": len(rows), "report": str(report) if rows else None}


def link_simapro(importer, link_to, migrations, biosphere: str, log=None) -> None:
    """Link the exchanges of a parsed SimaPro CSV: within the database, to the databases
    ``link_to`` (renamed with the randonneur_data ``migrations``) and to ``biosphere``.

    Order matters: aggregated ecoinvent copies are replaced first, and internal links are made on
    SimaPro's own names, which bw2io's strategies then shorten (``"Yeast, at plant/FR U"`` ->
    ``"Yeast, at plant"``, location FR) into names that are no longer unique.
    """
    import functools

    import bw2data as bd
    from bw2io import strategies as s

    log = log or (lambda message: print(message, flush=True))
    if link_to:
        replace_aggregated_copies(importer.data, link_to, migrations, log=log)
    drop_non_elementary(importer.data, log=log)
    importer.apply_strategy(link_internal_by_product_name)

    # Inputs from other databases: SimaPro names -> ecoinvent names (Pré's correspondence), then link
    def unlinked_input(edge) -> bool:
        return not edge.get("input") and edge.get("type") != "biosphere"

    log("Linking the inputs")
    for label in migrations:
        if is_adapter_file(label):
            apply_adapter_file(importer, label, edge_filter=unlinked_input, log=log)
        else:
            importer.randonneur(label, fields=MIGRATION_FIELDS.get(label, ["name"]), edge_filter=unlinked_input)
    for db in link_to:
        # "production": SimaPro's waste to treatment, as bw_simapro_csv writes it
        importer.apply_strategy(functools.partial(
            s.link_iterable_by_fields, other=bd.Database(db), edge_kinds=["technosphere", "substitution", "production"],
            fields=("name", "reference product", "location"),
        ))
    convert_to_linked_units(importer.data, link_to, log=log)
    log("Linking the elementary flows")
    link_biosphere_by_correspondence(importer.data, biosphere, log=log)
    rename_biosphere_flows(importer.data, log=log)
    importer.apply_strategies()  # bw2io defaults: names and locations, exact links

    # Remaining emissions and resources: SimaPro -> ecoinvent names and compartments, then link
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
        rename_ef_flows,
        functools.partial(link_biosphere_ignoring_case, biosphere=biosphere),
    ])
    # bw2data refuses to write an amount that isn't a number, after the whole import: report such
    # exchanges with the unlinked ones instead
    for ds in importer.data:
        for e in ds.get("exchanges", []):
            if not isinstance(e.get("amount"), (int, float)) or not math.isfinite(e["amount"]):
                e.pop("input", None)


def import_ecoinvent_release(project: str, version: str, system_model: str, username: str, password: str,
                             biosphere: str | None = None) -> str:
    """Download an ecoinvent release with the user's account (``ecoinvent_interface``, which keeps
    the archives in its own cache) and import it into ``project``. Returns the database's name.

    Without ``biosphere``, with the release's own biosphere (``ecoinvent-<version>-biosphere``,
    patched with the flows it lacks if the project has it already) and its LCIA methods (named
    ``("ecoinvent-<version>", …)``, skipped if the project has them). With ``biosphere``, a
    database of the project, linked to it (bw2io adds the release's flows it lacks), and no methods.
    A project created here is deleted again if the import fails.
    """
    import bw2data as bd
    import bw2io as bi
    import ecoinvent_interface as ei

    existed = project in bd.projects
    bd.projects.set_current(project)
    database = f"ecoinvent-{version}-{system_model}"
    _check_new(database)
    if biosphere and biosphere not in bd.databases:
        raise SystemExit(f"Database {biosphere!r} is not in project {project!r}")
    lcia = not biosphere and not any(m[0] == f"ecoinvent-{version}" for m in bd.methods)
    try:
        settings = ei.Settings(username=username, password=password)
        release = ei.EcoinventRelease(settings)
        print("Logging in to ecoinvent", flush=True)
        if version not in release.list_versions():
            raise SystemExit(f"ecoinvent {version} is not available to this account")
        if system_model not in release.list_system_models(version):  # short names: cutoff, apos, …
            raise SystemExit(f"No system model {system_model!r} in ecoinvent {version}")
        print(f"Downloading ecoinvent {version} {system_model} (once: kept in ecoinvent_interface's cache, "
              f"{release.storage.dir})", flush=True)
        release.get_release(version=version, system_model=system_model, release_type=ei.ReleaseType.ecospold)
        print(f"Importing ecoinvent {version} {system_model} (about 10 minutes)"
              + (", then its impact methods" if lcia else ""), flush=True)
        bi.import_ecoinvent_release(version=version, system_model=system_model, username=username, password=password,
                                    biosphere_name=biosphere, lcia=lcia)
    except BaseException:
        if not existed:
            others = [p.name for p in bd.projects if p.name != project]
            if others:
                bd.projects.set_current(others[0], update=False)
                bd.projects.delete_project(project, delete_dir=True)
        raise
    return database


def import_ecospold2(project: str, path, database: str, biosphere: str | None = None) -> None:
    """An ecoinvent release, extracted (``path`` is its ``datasets`` folder), linked to ``biosphere``
    (default: Brightway's, ``biosphere3``)."""
    import bw2data as bd
    import bw2io as bi

    bd.projects.set_current(project)
    _check_new(database)
    if biosphere and biosphere not in bd.databases:
        raise SystemExit(f"Database {biosphere!r} is not in project {project!r}")
    importer = bi.SingleOutputEcospold2Importer(str(path), database, biosphere_database_name=biosphere, use_mp=True)
    importer.apply_strategies()
    importer.statistics()
    importer.write_database()
