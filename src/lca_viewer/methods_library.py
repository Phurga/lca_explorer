# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The methods library: impact methods kept by the app, not by a Brightway project (see ``docs/methods-library.md``).

One JSON file per method in ``data/methods/``::

    {"format": 1, "id": "3f2a9c41d0", "name": ["My EF", "climate change", "GWP100"], "unit": "kg CO2-eq",
     "description": "...", "origin": {...} | null, "former_names": [[...]], "modified": "2026-11-27T10:00:00",
     "factors": [{"database": "biosphere3", "code": "349b29d1-...", "name": "Carbon dioxide, fossil",
                  "categories": ["air"], "unit": "kilogram", "cf": 1.0}, ...]}

A factor is keyed by its flow (database, code); its name, compartments and unit are kept to show it, and to find the
flow in a project whose biosphere has other keys. A library method enters a model under the name
``["library", *name]``: *library* is its source, as the release is the source of the methods that come with ecoinvent
(``_method_family`` of the server, ``methodPath`` of the page). The server reads the projects' files directly (no
bw2data); a build gets the library methods it needs as a file of factors on node ids (:func:`build_entries`), which
:func:`register_file` makes known to :func:`lca_viewer.brightway.method_factors` in the build process.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

SOURCE = "library"
FORMAT = 1
_RELEASE = re.compile(r"^[a-z]\w*-\d+(\.\d+)+$", re.IGNORECASE)  # the source of the methods of an ecoinvent release


# ---------------------------------------------------------------- names

def model_name(name) -> list[str]:
    """The name of a library method in a model: its source first."""
    return [SOURCE, *[str(part) for part in name]]


def is_library(name) -> bool:
    """Whether a method name (in a model, a build) is that of a library method."""
    return len(name) >= 2 and str(name[0]) == SOURCE


def clean_name(name) -> list[str]:
    """A name given by the page: its parts stripped, the empty ones dropped; at least one."""
    if isinstance(name, str):
        name = [name]
    parts = [re.sub(r"\s+", " ", str(part)).strip() for part in (name or [])]
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError("Give the method a name")
    if parts[0] == SOURCE:
        raise ValueError(f"A name cannot start with {SOURCE!r}: the source of the library's methods in a model")
    return parts


def digest(method: dict) -> str:
    """Changes with the unit and the factors of a method (not its name or description)."""
    rows = sorted((f["database"], f["code"], float(f["cf"])) for f in method["factors"])
    return hashlib.sha1(json.dumps([method.get("unit", ""), rows]).encode("utf-8")).hexdigest()[:16]


def _number(value) -> float:
    if isinstance(value, str):
        value = value.strip().replace("−", "-")
        if re.fullmatch(r"-?\d+,\d+([eE][-+]?\d+)?", value):  # a decimal comma
            value = value.replace(",", ".")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{value} is not a number")
    return number


def _factor(f: dict) -> dict:
    """A factor as kept: the flow's key, what shows it, the value."""
    database, code = str(f.get("database") or "").strip(), str(f.get("code") or "").strip()
    if not database or not code:
        raise ValueError("A factor needs the database and the code of its flow")
    categories = f.get("categories")
    if categories is None:
        categories = [c for c in (f.get("compartment"), f.get("subcompartment")) if c]
    return {"database": database, "code": code, "name": str(f.get("name") or ""),
            "categories": [str(c) for c in categories or ()], "unit": str(f.get("unit") or ""), "cf": _number(f["cf"])}


# ---------------------------------------------------------------- the library (files)

class Library:
    """The methods of ``folder`` (``data/methods``), one JSON file each, named by the method's id."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.lock = threading.RLock()

    def _path(self, id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{6,32}", id or ""):
            raise KeyError(id)
        return self.folder / f"{id}.json"

    def _write(self, method: dict) -> dict:
        method["modified"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(method["id"])
        part = path.with_name(path.name + ".part")
        part.write_text(json.dumps(method, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(part, path)
        return method

    def all(self) -> list[dict]:
        out = []
        for path in sorted(self.folder.glob("*.json")):
            try:
                method = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if method.get("format") == FORMAT and method.get("id") == path.stem:
                out.append(method)
        return sorted(out, key=lambda m: [p.lower() for p in m["name"]])

    def get(self, id: str) -> dict:
        try:
            method = json.loads(self._path(id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KeyError(id) from None
        return method

    def find(self, name) -> dict | None:
        """The method of a name in a model (``["library", ...]``) or in the library; also by a name it had before."""
        name = [str(p) for p in name]
        if is_library(name):
            name = name[1:]
        methods = self.all()
        return (next((m for m in methods if m["name"] == name), None)
                or next((m for m in methods if name in m.get("former_names", [])), None))

    def _free(self, name: list[str], but: str | None = None) -> None:
        if any(m["name"] == name and m["id"] != but for m in self.all()):
            raise ValueError(f"The library has a method {' | '.join(name)!r} already: give another name")

    def create(self, name, unit: str = "", description: str = "", factors=(), origin: dict | None = None) -> dict:
        with self.lock:
            name = clean_name(name)
            self._free(name)
            merged: dict[tuple, dict] = {}
            for f in factors:
                f = _factor(f)
                key = (f["database"], f["code"])
                if key in merged:  # one flow, several factors (by location): summed, as Brightway's LCA does
                    merged[key]["cf"] += f["cf"]
                else:
                    merged[key] = f
            return self._write({"format": FORMAT, "id": uuid.uuid4().hex[:10], "name": name, "unit": str(unit or "").strip(),
                                "description": str(description or ""), "origin": origin, "former_names": [],
                                "factors": list(merged.values())})

    def update(self, id: str, name=None, unit: str | None = None, description: str | None = None) -> dict:
        with self.lock:
            method = self.get(id)
            if name is not None:
                name = clean_name(name)
                if name != method["name"]:
                    self._free(name, but=id)
                    # A build made with the former name finds the method again (and takes the new name)
                    former = [n for n in method.get("former_names", []) if n != name]
                    method["former_names"] = [*former, method["name"]]
                    method["name"] = name
            if unit is not None:
                method["unit"] = str(unit).strip()
            if description is not None:
                method["description"] = str(description)
            return self._write(method)

    def set_factors(self, id: str, set_=(), remove=()) -> dict:
        """Change or add the factors ``set_`` (by their flow), then remove the flows ``remove`` ([database, code])."""
        with self.lock:
            method = self.get(id)
            at = {(f["database"], f["code"]): k for k, f in enumerate(method["factors"])}
            for f in set_:
                f = _factor(f)
                key = (f["database"], f["code"])
                if key in at:
                    old = method["factors"][at[key]]
                    method["factors"][at[key]] = {**old, **{k: v for k, v in f.items() if v or k == "cf"}}
                else:
                    at[key] = len(method["factors"])
                    method["factors"].append(f)
            gone = {(str(d), str(c)) for d, c in remove}
            method["factors"] = [f for f in method["factors"] if (f["database"], f["code"]) not in gone]
            return self._write(method)

    def duplicate(self, id: str, name) -> dict:
        method = self.get(id)
        return self.create(name, method["unit"], method.get("description", ""), method["factors"],
                           {"copy_of": method["name"]})

    def delete(self, id: str) -> None:
        with self.lock:
            path = self._path(id)
            if not path.exists():
                raise KeyError(id)
            path.unlink()

    def stale(self, info: dict | None) -> str | None:
        """What changed in the library since a project cache (``project.json``) was built with its methods, or None."""
        if not info:
            return None
        for entry in info.get("methods", []):
            if "library" not in entry:
                continue
            method = self.find(entry["name"])
            if method is None:
                return f"{' | '.join(entry['name'][1:])} is no longer in the methods library"
            if model_name(method["name"]) != entry["name"] or digest(method) != entry["library"]:
                return "methods of the library changed since the model was built"
        return None


def summary(method: dict) -> dict:
    """A method as the library lists it (no factors)."""
    return {"id": method["id"], "name": method["name"], "model_name": model_name(method["name"]), "unit": method["unit"],
            "description": method.get("description", ""), "factors": len(method["factors"]), "origin": method.get("origin"),
            "modified": method.get("modified")}


# ---------------------------------------------------------------- the flows of a project (its files)

_FLOW_TYPES = ("emission", "natural resource", "economic", "inventory indicator", "social")
_flows_cache: dict[str, tuple[list, list[dict]]] = {}


def project_flows(base, project: str) -> list[dict]:
    """The elementary flows of a Brightway project, read from its file: ``{"id", "database", "code", "name",
    "categories", "unit", "type"}``. Cached until the file changes."""
    from .sources import _file_stamp, _project_data, project_dir

    path = project_dir(base, project) / "lci" / "databases.db"
    stamp = _file_stamp(path)
    cached = _flows_cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
        rows = con.execute(f"SELECT id, database, code, name, type, data FROM activitydataset "
                           f"WHERE type IN ({','.join('?' * len(_FLOW_TYPES))})", _FLOW_TYPES).fetchall()
    flows = []
    for id, database, code, name, kind, blob in rows:
        data = _project_data(blob)
        flows.append({"id": int(id), "database": database, "code": code, "name": name or data.get("name") or "",
                      "categories": [str(c) for c in data.get("categories") or ()], "unit": data.get("unit") or "",
                      "type": kind})
    _flows_cache[str(path)] = (stamp, flows)
    return flows


def _text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


_UNSPECIFIED = {"", "unspecified", "(unspecified)", "not specified"}


def _categories(categories) -> tuple[str, ...]:
    """Compartments to compare: lower case, an "unspecified" subcompartment the same as none (SimaPro writes it)."""
    out = [_text(c) for c in categories]
    while len(out) > 1 and out[-1] in _UNSPECIFIED:
        out.pop()
    return tuple(out)


# The units of the same quantity, as files and Brightway write them
_UNITS = {"kg": "kilogram", "g": "gram", "t": "ton", "tonne": "ton", "m3": "cubic meter", "m³": "cubic meter",
          "m2": "square meter", "m²": "square meter", "m2a": "square meter-year", "m2*a": "square meter-year",
          "m2 a": "square meter-year", "mj": "megajoule", "kwh": "kilowatt hour", "kbq": "kilo becquerel",
          "bq": "becquerel", "l": "litre", "liter": "litre", "m": "meter", "km": "kilometer", "p": "unit",
          "unit": "unit", "kg*a": "kilogram-year"}


def same_unit(a: str, b: str) -> bool:
    a, b = _text(a), _text(b)
    return not a or not b or _UNITS.get(a, a) == _UNITS.get(b, b)


class Matcher:
    """Finds a factor's flow in a project: by its key (database, code); else by its code alone (the flows of
    ecoinvent's biosphere keep their codes from one version to the next, under another database name); else by its
    name and compartments (unit checked)."""

    def __init__(self, flows: list[dict]):
        self.by_key = {(f["database"], f["code"]): f for f in flows}
        self.by_code: dict[str, list[dict]] = {}
        self.by_name: dict[tuple, list[dict]] = {}
        for f in flows:
            self.by_code.setdefault(f["code"], []).append(f)
            self.by_name.setdefault((_text(f["name"]), _categories(f["categories"])), []).append(f)

    def find(self, database: str | None, code: str | None, name: str = "", categories=(), unit: str = "") -> tuple[dict | None, str]:
        """The flow and how it was found ("key", "code", "name"), or (None, why not)."""
        if database and code and (database, code) in self.by_key:
            return self.by_key[(database, code)], "key"
        if code and len(self.by_code.get(code, [])) == 1:
            return self.by_code[code][0], "code"
        found = self.by_name.get((_text(name), _categories(categories)), [])
        if not found:
            return None, "no flow of this name in these compartments"
        fitting = [f for f in found if same_unit(f["unit"], unit)]
        if not fitting:
            return None, f"the flow is in {found[0]['unit']}, not {unit}"
        if len({f["unit"] for f in fitting}) > 1 or len({f["database"] for f in fitting}) > 1:
            fitting = [f for f in fitting if _text(f["unit"]) == _text(unit)] or fitting
        return fitting[0], "name"


def coverage(method: dict, base, project: str) -> dict:
    """For each factor, the flow of ``project`` it applies to (its node id) or why none; counts."""
    matcher = Matcher(project_flows(base, project))
    rows = []
    for f in method["factors"]:
        flow, how = matcher.find(f["database"], f["code"], f["name"], f["categories"], f["unit"])
        rows.append({"database": f["database"], "code": f["code"], "id": flow and flow["id"],
                     "how": how if flow else None, "reason": None if flow else how})
    return {"factors": rows, "matched": sum(1 for r in rows if r["id"] is not None), "total": len(rows)}


# ---------------------------------------------------------------- adding methods

def copy_from_project(library: Library, base, project: str, method, name=None) -> dict:
    """An editable copy of a method of a Brightway project, read from its files: its factors on the project's flows
    (those of flows the project does not have are left out, and counted in ``origin``)."""
    from .sources import _PlainUnpickler, project_dir

    method = [str(p) for p in method]
    folder = project_dir(base, project)
    try:
        listed = json.loads((folder / "methods.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"Project {project!r} has no impact methods") from None
    pairs = listed.items() if isinstance(listed, dict) else [(m[0], m[1]) for m in listed if isinstance(m, list) and len(m) == 2]
    meta = next((m for n, m in pairs if [str(p) for p in (n if isinstance(n, list) else [])] == method), None)
    if meta is None:
        raise ValueError(f"No method {' | '.join(method)!r} in project {project!r}")
    path = folder / "intermediate" / f"{meta.get('abbreviation', '')}.pickle"
    try:
        with open(path, "rb") as f:
            rows = _PlainUnpickler(f).load()
    except Exception as e:  # noqa: BLE001  (a file missing, or not plain data)
        raise ValueError(f"The factors of {' | '.join(method)} cannot be read ({path.name}: {e})") from None
    flows = project_flows(base, project)
    by_id = {f["id"]: f for f in flows}
    by_key = {(f["database"], f["code"]): f for f in flows}
    factors, missing = [], 0
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        ref, cf = row[0], row[1]
        if isinstance(cf, dict):
            cf = cf.get("amount", 0.0)
        flow = by_key.get(tuple(ref)) if isinstance(ref, (list, tuple)) else by_id.get(int(ref))
        if flow is None:
            missing += 1
            continue
        factors.append({**{k: flow[k] for k in ("database", "code", "name", "categories", "unit")}, "cf": float(cf)})
    if name is None:  # the family marked as a copy; the release (an ecoinvent source) left out
        rest = method[1:] if len(method) >= 4 and _RELEASE.match(method[0]) else method
        name = [f"{rest[0]} (copy)", *rest[1:]]
    origin = {"project": project, "method": method, **({"left_out": missing} if missing else {})}
    return library.create(name, meta.get("unit", ""), meta.get("description", ""), factors, origin)


_COLUMNS = {
    "name": ("name", "flow", "flow name", "substance", "elementary flow"),
    "compartment": ("compartment", "category", "categories"),
    "subcompartment": ("subcompartment", "sub-compartment", "sub compartment", "subcategory"),
    "unit": ("unit",),
    "cf": ("factor", "cf", "value", "characterisation factor", "characterization factor", "amount"),
    "database": ("database",),
    "code": ("code", "uuid", "key"),
}


def parse_csv(text: str) -> tuple[list[dict], list[dict]]:
    """The factors of a CSV file (flow name, compartment, subcompartment, unit, factor; a header row naming them is
    read, also with database and code columns, else this order), and the rows that could not be read."""
    text = text.lstrip("﻿")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("The file is empty")
    sample = "\n".join(lines[:20])
    delimiter = max([",", ";", "\t"], key=lambda d: sample.count(d))
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    head = [_text(c) for c in rows[0]]
    columns = {}
    for field, names in _COLUMNS.items():
        at = next((k for k, c in enumerate(head) if c in names), None)
        if at is not None:
            columns[field] = at
    if "name" in columns and "cf" in columns:
        body, first = rows[1:], 2
    else:
        columns, body, first = {"name": 0, "compartment": 1, "subcompartment": 2, "unit": 3, "cf": 4}, rows, 1
        try:  # a header row of other words
            _number(rows[0][4])
        except (IndexError, ValueError):
            body, first = rows[1:], 2
    factors, bad = [], []
    for line, row in enumerate(body, first):
        cell = lambda field: row[columns[field]].strip() if field in columns and columns[field] < len(row) else ""  # noqa: E731
        item = {"line": line, "name": cell("name"), "compartment": cell("compartment"),
                "subcompartment": cell("subcompartment"), "unit": cell("unit"), "factor": cell("cf"),
                "database": cell("database"), "code": cell("code")}
        if not item["name"] and not item["code"]:
            bad.append({**item, "reason": "no flow name"})
            continue
        try:
            item["cf"] = _number(item["factor"])
        except ValueError:
            bad.append({**item, "reason": f"the factor {item['factor']!r} is not a number"})
            continue
        factors.append(item)
    return factors, bad


def match_rows(rows: list[dict], base, project: str) -> tuple[list[dict], list[dict]]:
    """CSV rows matched to the flows of a project: the factors found, and the rows left (with the reason)."""
    matcher = Matcher(project_flows(base, project))
    found, left = [], []
    for r in rows:
        compartments = [c for c in (r["compartment"], *re.split(r"\s*[,/]\s*", r["subcompartment"])) if c]
        flow, how = matcher.find(r.get("database"), r.get("code"), r["name"], compartments, r["unit"])
        if flow is None:
            left.append({k: v for k, v in r.items() if k != "cf"} | {"reason": how})
        else:
            found.append({**{k: flow[k] for k in ("database", "code", "name", "categories", "unit")}, "cf": r["cf"]})
    return found, left


def import_csv(library: Library, text: str, base, project: str, name=None, unit: str = "", description: str = "",
               id: str | None = None, file: str = "") -> dict:
    """A CSV file of factors matched to the flows of ``project``: a new method, or (``id``) factors set in one of the
    library. Returns ``{"method", "matched", "unmatched"}`` (the rows of no flow, with the reason)."""
    rows, bad = parse_csv(text)
    found, left = match_rows(rows, base, project)
    if id:
        method = library.set_factors(id, found)
    else:
        if not found:
            raise ValueError(f"No row of the file matches a flow of {project}" + (f" ({left[0]['reason']}…)" if left else ""))
        method = library.create(name, unit, description, found, {"csv": file, "project": project})
    return {"method": summary(method), "matched": len(found), "unmatched": bad + left}


def to_csv(method: dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["name", "compartment", "subcompartment", "unit", "factor", "database", "code"])
    for f in method["factors"]:
        categories = f["categories"] or [""]
        writer.writerow([f["name"], categories[0], ", ".join(categories[1:]), f["unit"], repr(float(f["cf"])),
                         f["database"], f["code"]])
    return out.getvalue()


# ---------------------------------------------------------------- builds

def build_entries(library: Library, names, base, project: str) -> tuple[list[list[str]], list[dict]]:
    """For a build of ``project`` with the methods ``names``: the names, those of the library under their current
    name (one renamed since) and those no longer in it left out, and the library methods as the build process takes
    them (:func:`register_file`): ``{"name", "unit", "digest", "data": [[node id, cf], ...]}``, the factors on flows
    of the project."""
    out_names, entries, flows = [], [], None
    for name in names:
        if not is_library(name):
            out_names.append(list(name))
            continue
        method = library.find(name)
        if method is None:
            continue
        current = model_name(method["name"])
        if current in out_names:
            continue
        if flows is None:
            flows = Matcher(project_flows(base, project))
        data = {}
        for f in method["factors"]:
            flow, _ = flows.find(f["database"], f["code"], f["name"], f["categories"], f["unit"])
            if flow is not None:
                data[flow["id"]] = data.get(flow["id"], 0.0) + float(f["cf"])
        out_names.append(current)
        entries.append({"name": current, "unit": method["unit"], "digest": digest(method),
                        "data": [[k, v] for k, v in data.items()]})
    return out_names, entries


# In the build process: the library methods of the build, by name (see lca_viewer.brightway.method_factors)
_REGISTERED: dict[tuple, dict] = {}


def register_file(path) -> None:
    for entry in json.loads(Path(path).read_text(encoding="utf-8")):
        _REGISTERED[tuple(entry["name"])] = entry


def registered(name) -> dict | None:
    return _REGISTERED.get(tuple(str(p) for p in name))


# ---------------------------------------------------------------- API (/api/library/methods…)

from pydantic import BaseModel  # noqa: E402  (the requests of the API; FastAPI reads them at module level)


class NewMethod(BaseModel):
    name: list[str]
    unit: str = ""
    description: str = ""

class MethodChange(BaseModel):
    name: list[str] | None = None
    unit: str | None = None
    description: str | None = None

class FactorChanges(BaseModel):
    set: list[dict] = []
    """Factors to change or add: ``{"database", "code", "cf"}`` (and ``name``, ``categories``, ``unit``)."""
    remove: list[list[str]] = []
    """Flows whose factor is removed: ``[database, code]``."""

class Duplicate(BaseModel):
    name: list[str]

class CopyRequest(BaseModel):
    folder: str
    project: str
    method: list[str]
    name: list[str] | None = None
    """None: the family of the method, marked as a copy."""

class ImportRequest(BaseModel):
    folder: str
    project: str
    """The project whose elementary flows the rows are matched to."""
    path: str
    name: list[str] | None = None
    unit: str = ""
    description: str = ""
    id: str | None = None
    """Set the factors in this method of the library, instead of making a new one."""



def router(library: Library, folder_of, uploaded=lambda path: None):
    """The endpoints of the library. ``folder_of``: a Brightway data folder as the page gives it, resolved;
    ``uploaded(path)``: delete a file the page uploaded once it is read."""
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import Response

    api = APIRouter(prefix="/api/library/methods")

    def found(id: str) -> dict:
        try:
            return library.get(id)
        except KeyError:
            raise HTTPException(404, f"No method {id} in the library") from None

    def checked(work, *args, **kwargs):
        try:
            return work(*args, **kwargs)
        except KeyError as e:
            raise HTTPException(404, f"No method {e.args[0]} in the library") from None
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e)) from None

    @api.get("")
    def list_methods():
        """The methods of the library (no factors)."""
        return [summary(m) for m in library.all()]

    @api.post("")
    def create_method(req: NewMethod):
        """An empty method: its factors are added in the editor."""
        return summary(checked(library.create, req.name, req.unit, req.description))

    @api.post("/copy")
    def copy_method(req: CopyRequest):
        """An editable copy of a method of a Brightway project."""
        folder = folder_of(req.folder)
        try:
            return summary(copy_from_project(library, folder, req.project, req.method, req.name))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @api.post("/import")
    def import_method(req: ImportRequest):
        """A CSV file of factors (flow name, compartment, subcompartment, unit, factor), matched to the elementary
        flows of a project: a new method (or factors set in ``id``); the rows of no flow are listed."""
        path = Path(req.path.strip().strip("'\"")).expanduser()
        try:
            raw = path.read_bytes()
        except OSError as e:
            raise HTTPException(404, f"Cannot read {path}: {e.strerror or e}") from None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")  # Excel's CSV on Windows
        if req.id:
            found(req.id)
        try:
            out = checked(import_csv, library, text, folder_of(req.folder), req.project, req.name, req.unit,
                          req.description, req.id, path.name)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
        uploaded(path)
        return out

    @api.get("/{id}")
    def get_method(id: str, folder: str | None = None, project: str | None = None):
        """A method with its factors; with ``folder`` and ``project``, each factor's flow in that project
        (``id``: its node id, or ``reason`` why none) and counts (``coverage``)."""
        method = found(id)
        out = {**summary(method), "factors": method["factors"], "former_names": method.get("former_names", [])}
        if folder and project:
            try:
                cover = coverage(method, folder_of(folder), project)
            except (FileNotFoundError, ValueError) as e:
                raise HTTPException(404, str(e)) from None
            for f, c in zip(out["factors"], cover["factors"]):
                f.update(flow=c["id"], reason=c["reason"])
            out["coverage"] = {"matched": cover["matched"], "total": cover["total"], "project": project}
        return out

    @api.post("/{id}")
    def change_method(id: str, req: MethodChange):
        """Rename a method, change its unit or its description."""
        found(id)
        return summary(checked(library.update, id, req.name, req.unit, req.description))

    @api.post("/{id}/factors")
    def change_factors(id: str, req: FactorChanges):
        found(id)
        method = checked(library.set_factors, id, req.set, req.remove)
        return {**summary(method), "factors": method["factors"]}

    @api.post("/{id}/duplicate")
    def duplicate_method(id: str, req: Duplicate):
        found(id)
        return summary(checked(library.duplicate, id, req.name))

    @api.post("/{id}/delete")
    def delete_method(id: str):
        """Delete a method of the library (models built with it keep it until they are built again)."""
        found(id)
        checked(library.delete, id)
        return [summary(m) for m in library.all()]

    @api.get("/{id}/csv")
    def export_method(id: str):
        method = found(id)
        filename = re.sub(r"[^\w.-]+", "_", " ".join(method["name"]))[:80] or "method"
        return Response(to_csv(method), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})

    return api
