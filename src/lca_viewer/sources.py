"""Databases the app can load: built models listed in ``data/models.json``, and Brightway projects.

Brightway projects are listed by reading their files directly (``projects.db`` and each
project's ``databases.json`` and ``lci/databases.db``), without importing bw2data: listing is
fast, and never changes the projects. Loading one builds a model from it with
``lca-viewer build`` (bw2data, in a separate process), cached in ``data/cache``.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import pickle
import re
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path

# Node types of bw2data that are not processes: elementary flows, and products of SimaPro-style imports
NON_PROCESS_TYPES = {"emission", "natural resource", "economic", "inventory indicator", "social", "product", "waste"}


def catalog(data) -> list[dict]:
    """Models listed in ``data/models.json``, paths made absolute; [] when there is none."""
    path = Path(data) / "models.json"
    if not path.exists():
        return []
    return [{**m, "path": str((path.parent / m["path"]).resolve()), "title": m.get("title") or Path(m["path"]).name,
             "default_root": m.get("default_root")}
            for m in json.loads(path.read_text(encoding="utf-8"))]


def register(data, output, title: str, default_root: str | None, source: dict | None = None) -> None:
    """Add (or update) a model in ``data/models.json``; paths are relative to ``data`` (when inside it).

    ``source``: where the model was built from (``{"brightway": dir, "project", "database",
    "modified"}``), to tell when it is out of date.
    """
    data, output = Path(data), Path(output)
    path = data / "models.json"
    models = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    output = output.resolve()
    inside = output.is_relative_to(data.resolve())
    entry = {"path": (output.relative_to(data.resolve()) if inside else output).as_posix(), "title": title,
             "default_root": default_root}
    if source:
        entry["source"] = source
    for i, m in enumerate(models):
        if m["path"] == entry["path"]:
            models[i] = entry
            break
    else:
        models.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(models, indent=2) + "\n", encoding="utf-8")


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-") or "model"


def cache_dir(data, folder, project: str, database: str) -> Path:
    """Where the model built from a Brightway database is kept: one folder per Brightway data
    folder, project and database (the same names in two data folders, or names that differ
    only in punctuation, are different models)."""
    origin = "\n".join([str(Path(folder).expanduser().resolve()), project, database])
    digest = hashlib.sha1(origin.encode("utf-8")).hexdigest()[:10]
    return Path(data) / "cache" / f"bw-{digest}" / f"{slug(project)}-{slug(database)}"


# ---------------------------------------------------------------- Brightway

def brightway_dirs(data=None) -> list[dict]:
    """Brightway data folders found on this computer: lca_viewer's own (``data/brightway``),
    ``BRIGHTWAY2_DIR`` and Brightway's default folder (also Activity Browser's)."""
    candidates = []
    if data is not None:
        candidates.append(("lca_viewer", Path(data) / "brightway"))
    if os.environ.get("BRIGHTWAY2_DIR"):
        candidates.append(("BRIGHTWAY2_DIR", Path(os.environ["BRIGHTWAY2_DIR"])))
    candidates.append(("Brightway default folder", default_brightway_dir()))
    out, seen = [], set()
    for label, path in candidates:
        path = path.expanduser().resolve()
        if path in seen or not (path / "projects.db").is_file():
            continue
        seen.add(path)
        out.append({"label": label, "path": str(path)})
    return out


def default_brightway_dir() -> Path:
    """Brightway's default data folder (``platformdirs.user_data_dir("Brightway3", "pylca")``)."""
    try:
        import platformdirs

        return Path(platformdirs.user_data_dir("Brightway3", "pylca"))
    except ImportError:
        if os.name == "nt":
            return Path(os.environ.get("LOCALAPPDATA", "~")) / "pylca" / "Brightway3"
        if os.uname().sysname == "Darwin":
            return Path("~/Library/Application Support/Brightway3")
        return Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")) / "Brightway3"


def _safe_filename(name: str, full: bool) -> str:
    """Folder name of a project, as bw2data makes it (``bw_processing.safe_filename``)."""
    safe = re.sub(r"[-\s]+", "-", re.sub(r"[^\w\s-]", "", unicodedata.normalize("NFKD", name)).strip())
    digest = hashlib.md5(name.encode("utf8")).hexdigest()
    return f"{safe}.{digest if full else digest[:8]}"


class _PlainUnpickler(pickle.Unpickler):
    """Project metadata is a pickled dict: read it without running any code."""

    def find_class(self, module, name):
        raise pickle.UnpicklingError(f"{module}.{name} not allowed")


def _project_data(blob) -> dict:
    try:
        data = _PlainUnpickler(io.BytesIO(blob)).load()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _methods_stamp(project_dir: Path) -> str | None:
    """Changes when impact methods are added, removed or written again. ``methods.json`` has no
    dates, but each method's factors are written to ``processed/<abbreviation>.zip``."""
    try:
        text = (project_dir / "methods.json").read_bytes()
        methods = json.loads(text)
    except (OSError, ValueError):
        return None
    digest = hashlib.sha1(text)
    # bw2data writes [[name, metadata], ...]; older versions {"(name)": metadata}
    metadata = methods.values() if isinstance(methods, dict) else [m[1] for m in methods if isinstance(m, list) and len(m) == 2]
    for meta in metadata:
        abbreviation = meta.get("abbreviation") if isinstance(meta, dict) else None
        if not abbreviation:
            continue
        try:
            stat = (project_dir / "processed" / f"{abbreviation}.zip").stat()
        except OSError:
            continue
        digest.update(f"{abbreviation} {stat.st_mtime_ns} {stat.st_size}".encode())
    return digest.hexdigest()[:16]


def _node_counts(project_dir: Path) -> dict[str, dict[str, int]]:
    """database -> {node type -> count}, from the project's SQLite database."""
    path = project_dir / "lci" / "databases.db"
    if not path.is_file():
        return {}
    out: dict[str, dict[str, int]] = {}
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
        for database, kind, n in con.execute("SELECT database, type, COUNT(*) FROM activitydataset GROUP BY database, type"):
            out.setdefault(database, {})[kind or "process"] = n
    return out


def _projects(base) -> list[tuple[str, bool, Path]]:
    """(name, made with Brightway 2.5, folder) of the projects in a Brightway data folder."""
    base = Path(base).expanduser()
    projects_db = base / "projects.db"
    if not projects_db.is_file():
        raise FileNotFoundError(f"No Brightway projects in {base} (no projects.db)")
    with closing(sqlite3.connect(f"file:{projects_db.as_posix()}?mode=ro", uri=True)) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(projectdataset)")}
        full = "full_hash" if "full_hash" in columns else "0"
        rows = con.execute(f"SELECT name, data, {full} FROM projectdataset ORDER BY name COLLATE NOCASE").fetchall()
    return [(name, bool(_project_data(blob).get("25")), base / _safe_filename(name, bool(full_hash)))
            for name, blob, full_hash in rows]


def brightway_projects(base) -> list[dict]:
    """Projects of a Brightway data folder and their databases, read-only.

    Each project: ``{"name", "twofive", "methods", "methods_stamp", "databases": [{"name",
    "processes", "flows", "depends", "modified", "backend"}], "dir"}``. ``twofive`` is False for
    projects made with Brightway 2 (bw2data 3), which lca_viewer (Brightway 2.5) cannot read
    without upgrading them. ``methods_stamp`` changes when impact methods are added, removed or
    written.
    """
    out = []
    for name, twofive, project_dir in _projects(base):
        project = {"name": name, "twofive": twofive, "methods": 0, "methods_stamp": None, "databases": [],
                   "dir": str(project_dir)}
        if not project_dir.is_dir():
            out.append({**project, "missing": True})
            continue
        metadata = _read_json(project_dir / "databases.json")
        project["methods"] = len(_read_json(project_dir / "methods.json"))
        project["methods_stamp"] = _methods_stamp(project_dir)
        try:
            counts = _node_counts(project_dir)
        except sqlite3.Error:
            counts = {}
        for database, meta in sorted(metadata.items(), key=lambda kv: kv[0].lower()):
            types = counts.get(database, {})
            project["databases"].append({
                "name": database,
                "processes": sum(n for t, n in types.items() if t not in NOT_SUPPLIERS),
                "flows": sum(n for t, n in types.items() if t in NON_PROCESS_TYPES - {"product", "waste"}),
                "depends": meta.get("depends", []),
                "modified": meta.get("modified"),
                "backend": meta.get("backend"),
            })
        out.append(project)
    return out


def database_state(project: dict, database: str) -> dict:
    """What a model built from ``database`` of ``project`` (as :func:`brightway_projects` lists
    it) depends on: when the database and the databases it links to (theirs too) were last
    written, and the impact methods. Recorded with the build, to tell when it is out of date."""
    by_name = {d["name"]: d for d in project["databases"]}
    depends, todo = {}, list(by_name[database]["depends"])
    while todo:
        name = todo.pop()
        if name == database or name in depends:
            continue
        linked = by_name.get(name)
        depends[name] = linked and linked["modified"]
        todo.extend(linked["depends"] if linked else [])
    return {"modified": by_name[database]["modified"], "depends": depends, "methods": project.get("methods_stamp")}


def changes(source: dict, state: dict) -> str | None:
    """What changed in the project since a model was built (``source``: as registered with the
    build; ``state``: :func:`database_state` now), or None. Builds registered before the linked
    databases and the methods were recorded are compared on their database alone."""
    if source.get("modified") != state["modified"]:
        return "changed since the model was built"
    if "depends" in source:
        before, now = source["depends"], state["depends"]
        changed = sorted((n for n in set(before) | set(now) if before.get(n) != now.get(n)), key=str.lower)
        if changed:
            return f"{', '.join(changed)} (linked) changed since the model was built"
    if source.get("methods") and source["methods"] != state["methods"]:
        return "impact methods changed since the model was built"
    return None


def project_dir(base, project: str) -> Path:
    """Folder of a Brightway 2.5 project (its databases, ``lci/databases.db``)."""
    for name, twofive, folder in _projects(base):
        if name == project:
            if not twofive:
                raise ValueError(f"Project {project!r} was made with Brightway 2: upgrade it to Brightway 2.5 first")
            return folder
    raise ValueError(f"No project {project!r} in {base}")


FLOW_TYPES = NON_PROCESS_TYPES - {"product", "waste"}
NOT_SUPPLIERS = NON_PROCESS_TYPES | {"multifunctional"}  # what a dataset cannot take as a technosphere input


_flows_cache: dict[str, tuple[float, list[dict]]] = {}


def _node(row) -> dict:
    database, code, name, product, location, kind, blob = row
    data = _project_data(blob)
    return {"database": database, "code": code, "name": name, "reference_product": product, "location": location,
            "unit": data.get("unit"), "categories": list(data.get("categories") or ()), "type": kind}


def _flows(path: Path) -> list[dict]:
    """Elementary flows of a project, cached until its database file changes."""
    stamp = path.stat().st_mtime
    cached = _flows_cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
        rows = con.execute(f"SELECT database, code, name, product, location, type, data FROM activitydataset "
                           f"WHERE type IN ({','.join('?' * len(FLOW_TYPES))})", sorted(FLOW_TYPES)).fetchall()
    flows = sorted((_node(r) for r in rows), key=lambda f: (len(f["name"] or ""), f["name"] or ""))
    for f in flows:
        f["_text"] = " ".join([f["name"] or "", *f["categories"]]).lower()
    _flows_cache[str(path)] = (stamp, flows)
    return flows


RESOURCE_COMPARTMENTS = {"natural resource", "resources", "resource", "raw", "raw materials"}


def _resource(flow: dict) -> bool:
    """A resource (input from nature): by its type, or by its compartment in databases from
    SimaPro, where every flow is an "emission" and resources are in "Resources" (e.g. BAFU)."""
    return flow["type"] == "natural resource" or (bool(flow["categories"])
                                                   and flow["categories"][0].lower() in RESOURCE_COMPARTMENTS)


def search_nodes(base, project: str, text: str, kind: str = "process", database: str | None = None,
                 limit: int = 40) -> list[dict]:
    """Processes (``kind="process"``), resources or emissions of a project matching the words of
    ``text`` (in the name, product and location of processes; name and compartment of flows), read-only.

    Each: ``{"database", "code", "name", "reference_product", "location", "unit", "categories", "type"}``.
    """
    path = project_dir(base, project) / "lci" / "databases.db"
    words = [w for w in text.lower().split() if w]
    first = words[0] if words else ""
    if kind != "process":
        found = [f for f in _flows(path)
                 if _resource(f) == (kind == "resource") and (not database or f["database"] == database)
                 and all(w in f["_text"] for w in words)]
        found.sort(key=lambda f: not (f["name"] or "").lower().startswith(first))  # stable: then shortest names
        return [{k: v for k, v in f.items() if k != "_text"} for f in found[:limit]]
    where = [f"COALESCE(type, 'process') NOT IN ({','.join('?' * len(NOT_SUPPLIERS))})"]
    params: list = sorted(NOT_SUPPLIERS)
    if database:
        where.append("database = ?")
        params.append(database)
    for word in words:
        where.append("(LOWER(name) LIKE ? OR LOWER(COALESCE(product, '')) LIKE ? OR LOWER(COALESCE(location, '')) LIKE ?)")
        params += [f"%{word}%"] * 3
    query = (f"SELECT database, code, name, product, location, type, data FROM activitydataset "
             f"WHERE {' AND '.join(where)} ORDER BY LOWER(name) NOT LIKE ?, LENGTH(name), name LIMIT ?")
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
        found = [_node(r) for r in con.execute(query, [*params, f"{first}%", limit * 10])]
    # A word that is a location code ("fr") picks that location rather than words containing it ("from")
    found.sort(key=lambda n: (n["location"] or "").lower() not in words)  # stable: keeps the SQL order
    return found[:limit]


EDITOR_KEY = "lca_viewer"  # set by lca_viewer.editing on the datasets written from the app
NODE_COLUMNS = "database, code, name, product, location, type, data"


def guess_system_model(databases) -> str | None:
    """The system model of databases, from their names (as ecoinvent's are named, e.g.
    ``ecoinvent-3.12-consequential``): "consequential", "attributional" (allocation: cut-off,
    APOS, EN15804), or None when none tells or they disagree."""
    found = set()
    for name in databases:
        name = (name or "").lower()
        if "conseq" in name:
            found.add("consequential")
        elif re.search(r"cut-?off|apos|en15804|attributional", name):
            found.add("attributional")
    return found.pop() if len(found) == 1 else None


def _own_comment(data: dict) -> str:
    """The comment entered in the app, without the documentation appended to it in the process comment."""
    from .editing import DOCUMENTATION

    stored = (data.get(EDITOR_KEY) or {}).get("comment")
    if stored is not None:
        return stored
    doc = data.get("documentation") or {}
    added = {f"{label}: {doc[key]}" for key, label in DOCUMENTATION.items() if doc.get(key)}
    return "\n\n".join(p for p in (data.get("comment") or "").split("\n\n") if p not in added)


def read_dataset(base, project: str, database: str, code: str) -> dict:
    """A process of a project as the *Create dataset* panel enters it (the ``spec`` of
    :func:`lca_viewer.editing.write_dataset`), to copy it or, when written from the app, edit it.

    A process allocated from one with co-products gives that one. Technosphere inputs with a
    negative amount are waste sent to treatment when their supplier treats waste (negative
    production, as in ecoinvent), else avoided products (substitution, as in consequential
    databases). Returns ``{"database", "code", "editable", "process", "products", "exchanges",
    "warnings"}``, exchanges with the node they link to (as :func:`search_nodes` gives them).
    """
    path = project_dir(base, project) / "lci" / "databases.db"
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
        def node(db, c):
            row = con.execute(f"SELECT {NODE_COLUMNS} FROM activitydataset WHERE database = ? AND code = ?", (db, c)).fetchone()
            return (_node(row), _project_data(row[6])) if row else (None, {})

        def edges(where: str, *params):
            return [(kind, (in_db, in_code), (out_db, out_code), _project_data(blob)) for kind, in_db, in_code, out_db, out_code, blob
                    in con.execute("SELECT type, input_database, input_code, output_database, output_code, data "
                                   f"FROM exchangedataset WHERE {where} ORDER BY id", params)]

        def supplier(n):
            """The process supplying node ``n`` (itself, or the producer of a product node) and the
            sign of its production."""
            if n["type"] not in NON_PROCESS_TYPES:
                made = [e[3].get("amount", 1) for e in edges("output_database = ? AND output_code = ? AND type = 'production'",
                                                             n["database"], n["code"])]
                return n, -1 if made and made[0] < 0 else 1
            for _, _, out_key, data in edges("input_database = ? AND input_code = ? AND type = 'production'",
                                             n["database"], n["code"]):
                producer, _ = node(*out_key)
                if producer and (producer["type"] or "process") not in NOT_SUPPLIERS:
                    return producer, -1 if data.get("amount", 1) < 0 else 1
            return None, 1

        info, data = node(database, code)
        if info is None:
            raise ValueError(f"No process {code!r} in {database}")
        if data.get("mf_parent_key"):
            info, data = node(*data["mf_parent_key"])
            if info is None:
                raise ValueError(f"The process with co-products that {code!r} was allocated from is gone")
        key = (info["database"], info["code"])
        from .editing import ALLOCATION_RULES

        record = data.get(EDITOR_KEY) if isinstance(data.get(EDITOR_KEY), dict) else {}
        rule = record.get("allocation") if record.get("allocation") in ALLOCATION_RULES else "manual"
        products, exchanges, warnings, lost_uncertainty = [], [], [], 0
        waste_products = []
        for kind, in_key, _, e in edges("output_database = ? AND output_code = ?", *key):
            amount = e.get("amount", 0)
            if kind == "production":
                product = info if in_key == key else node(*in_key)[0]
                if product is None:
                    warnings.append(f"A product of the process is missing ({in_key[1]})")
                    continue
                properties = e.get("properties") or {}
                share = properties.get("manual_allocation")
                if share is None and e.get("mf_allocation_factor") is not None:
                    share = 100 * e["mf_allocation_factor"]
                products.append({
                    "name": (data.get("reference product") or info["name"]) if in_key == key else product["name"],
                    "amount": amount, "unit": product["unit"] or "unit", "class": "allocatable", "allocation": share,
                    "property": properties.get(ALLOCATION_RULES[rule]) if ALLOCATION_RULES[rule] else None,
                    "comment": e.get("comment") or "", "code": None if in_key == key else in_key[1],
                })
                continue
            other, _ = node(*in_key)
            if other is None:
                warnings.append(f"An exchange links to a missing node ({in_key[0]}, {in_key[1]}): left out")
                continue
            if kind == "biosphere":
                section = "resource" if _resource(other) else "emission"
            elif kind in ("technosphere", "substitution"):
                other, sign = supplier(other)
                if other is None:
                    warnings.append(f"No process supplies {node(*in_key)[0]['name']}: left out")
                    continue
                if isinstance(e.get("lca_viewer_product"), dict):  # a waste product of the dataset, sent to treatment
                    waste_products.append({**e["lca_viewer_product"], "amount": -amount, "class": "waste", "treatment": other,
                                           "allocation": None, "property": None, "comment": e.get("comment") or "", "code": None})
                    continue
                if kind == "substitution":
                    section = "avoided"
                elif amount >= 0:
                    section = "technosphere"
                else:
                    section, amount = ("waste" if sign < 0 else "avoided"), -amount
            else:
                warnings.append(f"Exchange of type {kind!r} with {other['name']}: left out")
                continue
            sd2 = None
            if e.get("uncertainty type") == 2 and e.get("scale"):
                sd2 = round(math.exp(2 * e["scale"]), 4)
            elif (e.get("uncertainty type") or 0) > 2:
                lost_uncertainty += 1
            exchanges.append({"section": section, "node": other, "amount": amount, "comment": e.get("comment") or "", "sd2": sd2})
    if lost_uncertainty:
        warnings.append(f"{lost_uncertainty} exchanges have an uncertainty other than lognormal: not kept")
    products += [{"name": p.get("name") or "", "amount": p.get("amount"), "unit": p.get("unit") or "unit", "class": "recyclable",
                  "allocation": None, "property": None, "comment": p.get("comment") or "", "code": None}
                 for p in record.get("recyclable") or []]
    products += waste_products
    return {
        "database": key[0], "code": key[1], "editable": bool(record),
        "system_model": record.get("system_model") or guess_system_model([key[0]]), "allocation": rule,
        "process": {"name": info["name"] or "", "location": info["location"] or "GLO", "comment": _own_comment(data),
                    "documentation": {k: v for k, v in (data.get("documentation") or {}).items() if isinstance(v, str)}},
        "products": products, "exchanges": exchanges, "warnings": warnings,
    }


def adapters() -> list[dict]:
    """randonneur_data transformations (Départ de Sentier), applied when importing a SimaPro CSV to
    rename its inputs and flows to those of a background database: ``{"name", "description",
    "source", "target"}``."""
    try:
        import randonneur_data
    except ImportError:
        return []
    out = []
    for name, meta in randonneur_data.Registry().items():
        out.append({"name": name, "description": meta.get("description", ""),
                    "source": meta.get("source_id", ""), "target": meta.get("target_id", "")})
    return sorted(out, key=lambda a: a["name"].lower())


REMOTE = "https://files.brightway.dev/"
REMOTE_PROJECTS = {  # files.brightway.dev/projects-config.json, when it cannot be reached
    "BAFU-2025-v2": "bafu-2025-v2.tar.gz",
    "USEEIO-1.1": "USEEIO-1.1.tar.gz",
    "forwast": "forwast.tar.gz",
    "ecoinvent-3.9.1-biosphere": "ecoinvent-3.9.1-biosphere.tar.gz",
    "ecoinvent-3.10-biosphere": "ecoinvent-3.10-biosphere.tar.gz",
    "ecoinvent-3.11-biosphere": "ecoinvent-3.11-biosphere.tar.gz",
    "ecoinvent-3.12-biosphere": "ecoinvent-3.12-biosphere.tar.gz",
}


def remote_projects() -> dict[str, str]:
    """Brightway 2.5 projects published on files.brightway.dev (e.g. BAFU), name -> archive file."""
    import urllib.request

    try:
        with urllib.request.urlopen(REMOTE + "projects-config.json", timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return dict(REMOTE_PROJECTS)
