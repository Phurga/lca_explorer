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

import numpy as np
import pandas as pd

from .query import evaluate, field_options

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

def brightway_dir() -> Path:
    """The Brightway data folder lca_viewer works in, as bw2data picks it: ``BRIGHTWAY2_DIR``,
    else Brightway's default folder (also Activity Browser's)."""
    folder = os.environ.get("BRIGHTWAY2_DIR")
    return (Path(folder) if folder else default_brightway_dir()).expanduser().resolve()


def brightway_dirs() -> list[dict]:
    """The Brightway data folder offered by *Load database* (other folders can be typed in)."""
    label = "BRIGHTWAY2_DIR" if os.environ.get("BRIGHTWAY2_DIR") else "Brightway default folder"
    return [{"label": label, "path": str(brightway_dir())}]


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


def affected_databases(project: dict, names) -> list[str]:
    """``names`` and the databases of the project that use them, directly or through others: those
    whose results change when something in ``names`` is edited, and whose models must be built again."""
    out = set(names)
    grew = True
    while grew:
        grew = False
        for d in project["databases"]:
            if d["name"] not in out and out & set(d["depends"]):
                out.add(d["name"])
                grew = True
    return sorted(out)


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


_process_cache: dict[str, tuple[float, pd.DataFrame]] = {}


def _process_frame(path: Path) -> pd.DataFrame:
    """The processes of a project as a table (database, code, name, reference_product, location), without
    reading their data: what the filters of the process search are evaluated on. Cached until the file changes."""
    stamp = path.stat().st_mtime
    cached = _process_cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
        rows = con.execute(f"SELECT database, code, name, COALESCE(product, ''), COALESCE(location, '') FROM activitydataset "
                           f"WHERE COALESCE(type, 'process') NOT IN ({','.join('?' * len(NOT_SUPPLIERS))})",
                           sorted(NOT_SUPPLIERS)).fetchall()
    frame = pd.DataFrame(rows, columns=["database", "code", "name", "reference_product", "location"])
    _process_cache[str(path)] = (stamp, frame)
    return frame


def _flow_frame(path: Path, kind: str, database: str | None = None) -> pd.DataFrame:
    """The resources or the emissions of a project as a table (name, compartment, subcompartment, unit, database, code)."""
    rows = [f for f in _flows(path) if _resource(f) == (kind == "resource") and (not database or f["database"] == database)]
    return pd.DataFrame({
        "database": [f["database"] for f in rows], "code": [f["code"] for f in rows], "name": [f["name"] or "" for f in rows],
        "compartment": [f["categories"][0] if f["categories"] else "" for f in rows],
        "subcompartment": [", ".join(f["categories"][1:]) for f in rows], "unit": [f["unit"] or "" for f in rows],
    })


def node_options(base, project: str, kind: str, field: str, text: str = "", limit: int = 200,
                 database: str | None = None) -> dict:
    """The values of a field of the nodes ``search_nodes`` looks through, as ``query.field_options`` gives them
    for a model (with names for locations): the helper of the filters of the search."""
    path = project_dir(base, project) / "lci" / "databases.db"
    frame = _process_frame(path) if kind == "process" else _flow_frame(path, kind, database)
    if field not in frame.columns or field == "code":
        raise ValueError(f"Unknown field {field!r}")
    if kind == "process" and database:
        frame = frame[frame["database"] == database]
    return field_options(frame, field, text, limit)


RESOURCE_COMPARTMENTS = {"natural resource", "resources", "resource", "raw", "raw materials"}


def _resource(flow: dict) -> bool:
    """A resource (input from nature): by its type, or by its compartment in databases from
    SimaPro, where every flow is an "emission" and resources are in "Resources" (e.g. BAFU)."""
    return flow["type"] == "natural resource" or (bool(flow["categories"])
                                                   and flow["categories"][0].lower() in RESOURCE_COMPARTMENTS)


def _filtered_nodes(path: Path, words: list[str], kind: str, database: str | None, limit: int, filters: dict) -> list[dict]:
    """``search_nodes`` with ``filters`` (a predicate of ``query``, over name, reference_product, location and database
    of processes; name, compartment, subcompartment, unit and database of flows) as well as the words."""
    first = words[0] if words else ""
    if kind == "process":
        frame = _process_frame(path)
        mask = evaluate(filters, frame).copy()
        if database:
            mask &= (frame["database"] == database).to_numpy()
        name = frame["name"].str.lower()
        for word in words:
            mask &= np.asarray(name.str.contains(word, regex=False) | frame["reference_product"].str.lower().str.contains(word, regex=False)
                               | (frame["location"].str.lower() == word))
        hits = frame[mask]
        order = np.lexsort([hits["name"].str.len().to_numpy(), ~hits["name"].str.lower().str.startswith(first).to_numpy(),
                            ~hits["location"].str.lower().isin(words).to_numpy()])
        hits = hits.iloc[order].head(limit)
        found = []
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
            for database_, code in zip(hits["database"], hits["code"]):  # the data (unit) of the few shown only
                row = con.execute("SELECT database, code, name, product, location, type, data FROM activitydataset "
                                  "WHERE database = ? AND code = ?", (database_, code)).fetchone()
                if row:
                    found.append(_node(row))
        return found
    frame = _flow_frame(path, kind, database)
    mask = evaluate(filters, frame).copy()
    text = (frame["name"] + " " + frame["compartment"] + " " + frame["subcompartment"]).str.lower()
    for word in words:
        mask &= np.asarray(text.str.contains(word, regex=False))
    hits = frame[mask]
    order = np.lexsort([hits["name"].str.len().to_numpy(), ~hits["name"].str.lower().str.startswith(first).to_numpy()])
    shown = hits.iloc[order].head(limit)
    by_key = {(f["database"], f["code"]): f for f in _flows(path)}
    return [{k: v for k, v in by_key[key].items() if k != "_text"} for key in zip(shown["database"], shown["code"])]


def search_nodes(base, project: str, text: str, kind: str = "process", database: str | None = None,
                 limit: int = 40, filters: dict | None = None) -> list[dict]:
    """Processes (``kind="process"``), resources or emissions of a project matching the words of
    ``text`` (in the name, product and location of processes; name and compartment of flows) and the
    ``filters`` (a predicate as in ``query``), read-only.

    Each: ``{"database", "code", "name", "reference_product", "location", "unit", "categories", "type"}``.
    """
    path = project_dir(base, project) / "lci" / "databases.db"
    words = [w for w in text.lower().split() if w]
    first = words[0] if words else ""
    if filters:
        return _filtered_nodes(path, words, kind, database, limit, filters)
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


# ---------------------------------------------------------------- reading a dataset

def _fetch(con, key):
    return con.execute("SELECT database, code, name, product, location, type, data FROM activitydataset "
                       "WHERE database = ? AND code = ?", tuple(key)).fetchone()


def _exchanges_of(con, key) -> list[dict]:
    rows = con.execute("SELECT id, type, input_database, input_code, data FROM exchangedataset "
                       "WHERE output_database = ? AND output_code = ?", tuple(key)).fetchall()
    return [{**_project_data(blob), "id": i, "type": kind, "input": (db, code)} for i, kind, db, code, blob in rows]


def dataset_parts(con, database: str, code: str) -> dict:
    """The dataset of a process in a project's SQLite file: ``{"node", "data", "key", "exchanges",
    "products", "own"}``.

    A process allocated from a multi-output one (``readonly_process``) belongs to the dataset of its
    parent: that is what is edited, copied or deleted. ``products`` are the keys of its products
    (a process that is its own product: itself); ``own``, the keys of every node going with the
    dataset: itself, its allocated processes and its products.
    """
    row = _fetch(con, (database, code))
    if row is None:
        raise ValueError(f"No process {code!r} in {database!r}")
    if row[5] == "product":  # the product of a dataset stands for it (e.g. what a process was before it got co-products)
        producer = con.execute("SELECT output_database, output_code FROM exchangedataset WHERE input_database = ? "
                               "AND input_code = ? AND type = 'production' ORDER BY id", (database, code)).fetchone()
        row = (producer and _fetch(con, producer)) or row
    data = _project_data(row[6])
    if row[5] == "readonly_process" and data.get("mf_parent_key"):
        row = _fetch(con, data["mf_parent_key"])
        if row is None:
            raise ValueError(f"The parent of {code!r} is missing from {database!r}")
        data = _project_data(row[6])
    if row[5] in NON_PROCESS_TYPES:
        raise ValueError(f"{row[2]} is not a process (it is a {row[5]})")
    key = (row[0], row[1])
    exchanges = _exchanges_of(con, key)
    functional = [e for e in exchanges if e["type"] == "production" and e.get("functional")]
    if not functional:  # ecoinvent: the production exchange of the process, linked to itself
        functional = [e for e in exchanges if e["type"] == "production" and e["input"] == key and e.get("functional") is None]
    own = {key}
    for e in functional:
        own.add(e["input"])
        if e.get("mf_allocated_process_code"):
            own.add((key[0], e["mf_allocated_process_code"]))
    return {"node": _node(row), "data": data, "key": key, "exchanges": exchanges, "functional": functional,
            "products": [e["input"] for e in functional], "own": own}


USERS_SHOWN = 30  # a popular ecoinvent process has thousands of users: only the first are listed


def _users(con, parts: dict) -> list[dict]:
    """The datasets, other than this one, whose exchanges take a product of it (or the process
    itself) as input, an allocated process standing for its parent (the first ``USERS_SHOWN`` of
    each product): ``[{"database", "code", "name", "uses": key of the product}]``."""
    out, seen = [], set()
    for key in parts["products"]:
        for db, code in con.execute("SELECT output_database, output_code FROM exchangedataset "
                                    "WHERE input_database = ? AND input_code = ? AND type != 'biosphere' LIMIT ?",
                                    (*key, USERS_SHOWN + len(parts["own"]) + 5)).fetchall():
            if (db, code) in parts["own"] or (db, code) in seen:
                continue
            seen.add((db, code))
            row = _fetch(con, (db, code))
            data = _project_data(row[6]) if row else {}
            if row and row[5] == "readonly_process" and data.get("mf_parent_key"):
                if tuple(data["mf_parent_key"]) in parts["own"]:
                    continue  # an allocated process of the dataset itself
                row = _fetch(con, data["mf_parent_key"]) or row
            if row and not any((u["database"], u["code"], tuple(u["uses"])) == (row[0], row[1], key) for u in out):
                out.append({"database": row[0], "code": row[1], "name": row[2], "uses": list(key)})
    return out


def dataset_users(base, project: str, database: str, code: str) -> list[dict]:
    """Datasets of a project that use the products of the dataset of a process (read-only)."""
    path = project_dir(base, project) / "lci" / "databases.db"
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
        return _users(con, dataset_parts(con, database, code))


def _treats_waste(con, key: tuple) -> bool:
    """Whether the node ``key`` is a waste, or a process treating one: something produces it in a
    negative amount (ecoinvent), or takes it as its functional input in a negative amount (SimaPro)."""
    rows = con.execute("SELECT type, data FROM exchangedataset WHERE input_database = ? AND input_code = ? "
                       "AND (type = 'production' OR (type = 'technosphere' AND output_code = ?))", (*key, key[1])).fetchall()
    for kind, blob in rows:
        data = _project_data(blob)
        if (kind == "production" or data.get("functional")) and (data.get("amount") or 0) < 0:
            return True
    return False


def _sd2(data: dict):
    """SimaPro's SD² of a lognormal uncertainty; None for the other kinds."""
    if data.get("uncertainty type") == 2 and data.get("scale") is not None:
        return round(math.exp(2 * float(data["scale"])), 6)
    return None


def read_dataset(base, project: str, database: str, code: str) -> dict:
    """A process as the *Create dataset* form takes it (read-only, without bw2data):

    ``{"database", "code", "editable", "multifunctional", "process": {"name", "location", "comment",
    "documentation"}, "products": [{"code", "name", "amount", "unit", "allocation", "comment", "used"}],
    "exchanges": [{"section", "node", "amount", "comment", "sd2"}], "used_by": [{"database", "code",
    "name", "uses"}], "warnings": [...]}``.

    ``code`` can be any process; for one allocated from a multi-output process, the dataset is its
    parent's. Any dataset can be written again in place, ecoinvent's included: each exchange and
    product carries its ``id``, so that what the form cannot show (pedigree, other uncertainties,
    formulas) is left as it is. ``used_by`` (the first ones) and ``used`` tell what keeps a product,
    or the whole dataset, from being removed. Negative inputs from the technosphere are waste to
    treatment when what supplies them treats waste, else avoided products; uncertainties other than
    lognormal are not copied.
    """
    path = project_dir(base, project) / "lci" / "databases.db"
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
        parts = dataset_parts(con, database, code)
        node, data, key = parts["node"], parts["data"], parts["key"]
        warnings: list[str] = []

        products = []
        for e in parts["functional"]:
            if e["input"] == key:
                name, unit, product_code = node["reference_product"] or node["name"], node["unit"], key[1]
            else:
                row = _fetch(con, e["input"])
                if row is None:
                    warnings.append(f"A product of the dataset is missing from {key[0]}")
                    continue
                product = _node(row)
                name, unit, product_code = product["name"], product["unit"], product["code"]
            shares = (e.get("properties") or {}).get("manual_allocation")
            if shares is None and e.get("mf_allocation_factor") is not None:
                shares = 100 * e["mf_allocation_factor"]
            products.append({"code": product_code, "name": name, "amount": e.get("amount"), "unit": unit or node["unit"] or "unit",
                             "allocation": shares, "comment": e.get("comment") or "", "id": e["id"],
                             "process_code": e.get("mf_allocated_process_code") or key[1]})
        if not products:
            raise ValueError(f"{node['name']} has no product")
        if len(products) == 1:
            products[0]["allocation"] = 100
        elif any(p["allocation"] is None for p in products):
            for p in products:
                p["allocation"] = round(100 / len(products), 6)

        exchanges = []
        for e in parts["exchanges"]:
            if e in parts["functional"] or (e["type"] == "production" and e["input"] == key):
                continue
            kind, amount = e["type"], e.get("amount")
            row = _fetch(con, e["input"])
            if row is None or amount is None:
                warnings.append(f"An exchange whose input is missing ({e['input'][1]}) was left out")
                continue
            target = _node(row)
            if kind == "biosphere":
                section = "resource" if _resource(target) else "emission"
            elif kind == "substitution":
                section = "avoided"
            elif kind == "production":  # SimaPro's "waste to treatment", produced in a non-functional exchange
                section = "waste"
            elif kind == "technosphere" and amount < 0:
                section, amount = ("waste" if _treats_waste(con, e["input"]) else "avoided"), -amount
            elif kind == "technosphere":
                section = "technosphere"
            else:
                warnings.append(f"An exchange of type {kind!r} ({target['name']}) was not copied")
                continue
            if e.get("uncertainty type") not in (None, 0, 2):
                warnings.append(f"The uncertainty of {target['name']} is not lognormal: it was not copied")
            exchanges.append({"section": section, "node": target, "amount": amount, "comment": e.get("comment") or "",
                              "sd2": _sd2(e), "id": e["id"]})

        used_by = _users(con, parts)
        used = {tuple(u["uses"]) for u in used_by}
        for p in products:
            p["used"] = (key[0], p["code"]) in used
    kept = (data.get("lca_viewer") or {}).get("comment")  # the comment as typed, without the documentation after it
    return {
        "database": key[0], "code": key[1], "editable": True,
        "multifunctional": node["type"] == "multifunctional" or any(p["code"] != key[1] for p in products),
        "process": {"name": node["name"], "location": node["location"] or "",
                    "comment": data.get("comment") or "" if kept is None else kept,
                    "documentation": dict(data.get("documentation") or {})},
        "products": products, "exchanges": exchanges, "used_by": used_by, "warnings": sorted(set(warnings)),
    }
