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


def brightway_projects(base) -> list[dict]:
    """Projects of a Brightway data folder and their databases, read-only.

    Each project: ``{"name", "twofive", "methods", "methods_stamp", "databases": [{"name",
    "processes", "flows", "depends", "modified", "backend"}]}``. ``twofive`` is False for projects
    made with Brightway 2 (bw2data 3), which lca_viewer (Brightway 2.5) cannot read without
    upgrading them. ``methods_stamp`` changes when impact methods are added, removed or written.
    """
    base = Path(base).expanduser()
    projects_db = base / "projects.db"
    if not projects_db.is_file():
        raise FileNotFoundError(f"No Brightway projects in {base} (no projects.db)")
    with closing(sqlite3.connect(f"file:{projects_db.as_posix()}?mode=ro", uri=True)) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(projectdataset)")}
        full = "full_hash" if "full_hash" in columns else "0"
        rows = con.execute(f"SELECT name, data, {full} FROM projectdataset ORDER BY name COLLATE NOCASE").fetchall()
    out = []
    for name, blob, full_hash in rows:
        project_dir = base / _safe_filename(name, bool(full_hash))
        project = {"name": name, "twofive": bool(_project_data(blob).get("25")), "methods": 0, "methods_stamp": None,
                   "databases": []}
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
                "processes": sum(n for t, n in types.items() if t not in NON_PROCESS_TYPES),
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
