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
``["library", *name]``, or ``["library/<source>", *name]`` when it has a source of its own (``"source"``, e.g. the
release of the method it was copied from): *library* is its source, as the release is the source of the methods that
come with ecoinvent (``_method_family`` of the server, ``methodPath`` of the page). ``former_names`` holds the names in
a model it had before (older files: names in the library, of no source). The server reads the projects' files directly (no
bw2data); a build gets the library methods it needs as a file of factors on node ids (:func:`build_entries`), which
:func:`register_file` makes known to :func:`lca_viewer.brightway.method_factors` in the build process.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
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

def model_name(name, source: str = "") -> list[str]:
    """The name of a library method in a model: its source first, "library" or "library/<source>"."""
    return [f"{SOURCE}/{source}" if source else SOURCE, *[str(part) for part in name]]


def model_name_of(method: dict) -> list[str]:
    return model_name(method["name"], method.get("source") or "")


def is_library(name) -> bool:
    """Whether a method name (in a model, a build) is that of a library method."""
    return len(name) >= 2 and (str(name[0]) == SOURCE or str(name[0]).startswith(SOURCE + "/"))


def split_name(name) -> tuple[str, list[str]]:
    """A method name read as the method lists read it (``methodPath``): a first part "<name>-<version>" (an ecoinvent
    release) in a name of four parts or more is its source; the rest is family, category, indicator…"""
    name = [str(part) for part in name]
    if len(name) >= 4 and _RELEASE.match(name[0]):
        return name[0], name[1:]
    return "", name


def _former(method: dict) -> list[list[str]]:
    """The names in a model a method had before (older files kept its names in the library, of no source)."""
    return [list(n) if is_library(n) else model_name(n) for n in method.get("former_names", [])]


def clean_name(name) -> list[str]:
    """A name given by the page: its parts stripped, the empty ones dropped; at least one."""
    if isinstance(name, str):
        name = [name]
    parts = [re.sub(r"\s+", " ", str(part)).strip() for part in (name or [])]
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError("Give the method a name")
    if parts[0] == SOURCE or parts[0].startswith(SOURCE + "/"):
        raise ValueError(f"A name cannot start with {SOURCE!r}: the source of the library's methods in a model")
    return parts


def clean_source(source) -> str:
    """A source given by the page, spaces tidied ("library" itself, or "library/" before it: left out)."""
    source = re.sub(r"\s+", " ", str(source or "")).strip()
    if source.startswith(SOURCE + "/"):
        source = source[len(SOURCE) + 1:].strip()
    return "" if source == SOURCE else source


def label(name, source: str = "") -> str:
    return " | ".join([*([source] if source else []), *name])


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
        """The method of a name in a model (``["library", ...]``, ``["library/<source>", ...]``) or in the library (of
        no source); also by a name it had before."""
        name = [str(p) for p in name]
        if not is_library(name):
            name = model_name(name)
        methods = self.all()
        return (next((m for m in methods if model_name_of(m) == name), None)
                or next((m for m in methods if name in _former(m)), None))

    def taken(self, but: str | None = None) -> set[tuple]:
        """The names in a model of the library's methods (but one)."""
        return {tuple(model_name_of(m)) for m in self.all() if m["id"] != but}

    def _free(self, name: list[str], source: str = "", but: str | None = None, taken: set | None = None) -> None:
        if tuple(model_name(name, source)) in (self.taken(but) if taken is None else taken):
            raise ValueError(f"The library has a method {label(name, source)!r} already: give another name")

    def create(self, name, unit: str = "", description: str = "", factors=(), origin: dict | None = None,
               source: str = "", taken: set | None = None) -> dict:
        """A new method. ``taken``: the names in use (:meth:`taken`), given (and kept up to date) by a caller adding
        several methods, so that the files are not all read again for each."""
        with self.lock:
            name, source = clean_name(name), clean_source(source)
            self._free(name, source, taken=taken)
            merged: dict[tuple, dict] = {}
            for f in factors:
                f = _factor(f)
                key = (f["database"], f["code"])
                if key in merged:  # one flow, several factors (by location): summed, as Brightway's LCA does
                    merged[key]["cf"] += f["cf"]
                else:
                    merged[key] = f
            method = self._write({"format": FORMAT, "id": uuid.uuid4().hex[:10], "name": name, "source": source,
                                  "unit": str(unit or "").strip(), "description": str(description or ""),
                                  "origin": origin, "former_names": [], "factors": list(merged.values())})
            if taken is not None:
                taken.add(tuple(model_name_of(method)))
            return method

    @staticmethod
    def _rename(method: dict, name: list[str], source: str) -> None:
        """A method renamed: its former name in a model kept, so that a build made with it finds the method again
        (and takes the new name); that build is outdated (:meth:`stale`)."""
        old, new = model_name_of(method), model_name(name, source)
        if old != new:
            method["former_names"] = [*[n for n in _former(method) if n not in (old, new)], old]
        method["name"], method["source"] = name, source

    def update(self, id: str, name=None, unit: str | None = None, description: str | None = None,
               source: str | None = None) -> dict:
        with self.lock:
            method = self.get(id)
            if name is not None or source is not None:
                name = clean_name(name) if name is not None else method["name"]
                source = clean_source(source) if source is not None else method.get("source") or ""
                if model_name(name, source) != model_name_of(method):
                    self._free(name, source, but=id)
                self._rename(method, name, source)
            if unit is not None:
                method["unit"] = str(unit).strip()
            if description is not None:
                method["description"] = str(description)
            return self._write(method)

    def rename_many(self, changes) -> list[dict]:
        """Names (``name``, ``source``) and units of several methods changed at once (``[{"id", "name"?, "source"?,
        "unit"?}]``): all or none, the names checked against the library as it will be (two methods may swap their
        names). Returns the methods changed."""
        with self.lock:
            methods = {m["id"]: m for m in self.all()}
            planned = {}
            for change in changes:
                id = str(change.get("id") or "")
                if id not in methods:
                    raise KeyError(id)
                if id in planned:
                    raise ValueError(f"Method {id} is changed twice")
                m = methods[id]
                name = clean_name(change["name"]) if change.get("name") is not None else m["name"]
                source = clean_source(change["source"]) if change.get("source") is not None else m.get("source") or ""
                unit = str(change["unit"]).strip() if change.get("unit") is not None else m["unit"]
                planned[id] = (name, source, unit)
            named: set[tuple] = set()
            for id, m in methods.items():
                name, source, _ = planned.get(id, (m["name"], m.get("source") or "", None))
                key = tuple(model_name(name, source))
                if key in named:
                    raise ValueError(f"Two methods would be named {label(name, source)!r}: give them other names")
                named.add(key)
            changed = []
            for id, (name, source, unit) in planned.items():
                m = methods[id]
                if (name, source, unit) == (m["name"], m.get("source") or "", m["unit"]):
                    continue
                self._rename(m, name, source)
                m["unit"] = unit
                changed.append(self._write(m))
            return changed

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
                           {"copy_of": method["name"]}, method.get("source") or "")

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
            if model_name_of(method) != entry["name"] or digest(method) != entry["library"]:
                return "methods of the library changed since the model was built"
        return None


def summary(method: dict) -> dict:
    """A method as the library lists it (no factors)."""
    return {"id": method["id"], "name": method["name"], "source": method.get("source") or "",
            "model_name": model_name_of(method), "unit": method["unit"],
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

def _resolve(name: list[str], source: str, taken: set, on_conflict: str, suffix: str) -> list[str] | None:
    """The name a method added takes: its own when free; when the library has it already, None (``on_conflict``
    "skip"), an error ("error"), or the name with ``suffix`` after its last part ("rename"), numbered while taken."""
    if tuple(model_name(name, source)) not in taken:
        return name
    if on_conflict == "skip":
        return None
    if on_conflict != "rename":
        raise ValueError(f"The library has a method {label(name, source)!r} already: give another name")
    suffix = suffix.rstrip()
    for k in itertools.count(1 if suffix else 2):
        last = f"{name[-1]}{suffix}{f' {k}' if suffix and k > 1 else ''}" if suffix else f"{name[-1]} ({k})"
        candidate = [*name[:-1], last.strip()]
        if tuple(model_name(candidate, source)) not in taken:
            return candidate
    raise AssertionError  # pragma: no cover


_TAKEN = "the library has a method of this name"


def project_methods(base, project: str) -> list[tuple[list[str], dict]]:
    """The impact methods of a project, read from its files (``methods.json``): their names and metadata."""
    from .sources import project_dir

    try:
        listed = json.loads((project_dir(base, project) / "methods.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"Project {project!r} has no impact methods") from None
    pairs = listed.items() if isinstance(listed, dict) else [(m[0], m[1]) for m in listed if isinstance(m, list) and len(m) == 2]
    return [([str(p) for p in n], m) for n, m in pairs if isinstance(n, list) and isinstance(m, dict)]


def _project_factors(base, project: str, method: list[str], meta: dict) -> tuple[list[dict], int]:
    """The factors of a project's method on the project's flows (from ``intermediate/<abbreviation>.pickle``), and how
    many are on flows the project does not have."""
    from .sources import _PlainUnpickler, project_dir

    path = project_dir(base, project) / "intermediate" / f"{meta.get('abbreviation', '')}.pickle"
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
    return factors, missing


def copy_methods(library: Library, base, project: str, methods, on_conflict: str = "skip", suffix: str = " (copy)",
                 name=None, source=None) -> dict:
    """Editable copies of methods of a Brightway project, read from its files: their factors on the project's flows
    (those of flows the project does not have are left out, and counted in ``origin``). A copy keeps the method's
    name as it is, its source read as the lists read it (:func:`split_name`: an ecoinvent release); in a model it is
    under "library/<release>", apart from the project's. Its names are curated in the library afterwards
    (:meth:`Library.rename_many`). ``name`` (and ``source``): the name of a single copy.

    Returns ``{"added": [summary, with "wanted": the name taken when it was renamed], "skipped": [{"method",
    "name", "source", "reason", "taken"}]}``."""
    listed = dict((tuple(n), m) for n, m in project_methods(base, project))
    added, skipped = [], []
    with library.lock:
        taken = library.taken()
        for method in methods:
            method = [str(p) for p in method]
            own_source, own_name = split_name(method)
            wanted_source = clean_source(source) if source is not None else own_source
            wanted = clean_name(name) if name is not None else own_name
            meta = listed.get(tuple(method))
            if meta is None:
                if on_conflict == "error":
                    raise ValueError(f"No method {' | '.join(method)!r} in project {project!r}")
                skipped.append({"method": method, "name": wanted, "source": wanted_source,
                                "reason": f"no method of this name in {project}", "taken": False})
                continue
            final = _resolve(wanted, wanted_source, taken, on_conflict, suffix)
            if final is None:
                skipped.append({"method": method, "name": wanted, "source": wanted_source, "reason": _TAKEN, "taken": True})
                continue
            try:
                factors, missing = _project_factors(base, project, method, meta)
            except ValueError as e:
                if on_conflict == "error":
                    raise
                skipped.append({"method": method, "name": wanted, "source": wanted_source, "reason": str(e), "taken": False})
                continue
            origin = {"project": project, "method": method, **({"left_out": missing} if missing else {})}
            made = library.create(final, meta.get("unit", ""), meta.get("description", ""), factors, origin,
                                  wanted_source, taken)
            added.append({**summary(made), **({"wanted": wanted} if final != wanted else {})})
    return {"added": added, "skipped": skipped}


def copy_from_project(library: Library, base, project: str, method, name=None) -> dict:
    """A copy of one method of a project (:func:`copy_methods`), an error when its name is taken."""
    return library.get(copy_methods(library, base, project, [method], "error", name=name)["added"][0]["id"])


_COLUMNS = {
    "name": ("name", "flow", "flow name", "substance", "elementary flow"),
    "compartment": ("compartment", "category", "categories"),
    "subcompartment": ("subcompartment", "sub-compartment", "sub compartment", "subcategory"),
    "unit": ("unit", "flow unit"),
    "cf": ("factor", "cf", "value", "characterisation factor", "characterization factor", "amount"),
    "database": ("database",),
    "code": ("code", "uuid", "key"),
    # The method of each row, in a file of several: its whole name (parts separated by "|"), or its parts
    "method": ("method", "method name", "impact method", "lcia method"),
    "source": ("source", "method source"),
    "family": ("family", "method family"),
    "impact_category": ("impact category", "category"),
    "indicator": ("indicator", "impact indicator"),
    "method_unit": ("method unit", "impact unit", "indicator unit"),
}
_PARTS = ("family", "impact_category", "indicator")


def parse_csv(text: str) -> tuple[list[dict], list[dict]]:
    """The factors of a CSV file (flow name, compartment, subcompartment, unit, factor; a header row naming them is
    read, also with database and code columns, else this order), and the rows that could not be read.

    A file of several methods names the method of each row in a ``method`` column (its parts separated by "|"), or
    in ``source``, ``family``, ``impact category`` and ``indicator`` columns (``method unit``: its unit). Each factor
    then has ``method``: ``(source, [parts])``; else ``None``."""
    text = text.lstrip("﻿")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("The file is empty")
    sample = "\n".join(lines[:20])
    delimiter = max([",", ";", "\t"], key=lambda d: sample.count(d))
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    head = [_text(c) for c in rows[0]]
    columns, used = {}, set()
    for field, names in _COLUMNS.items():  # each column read once, the names of a field in the order above
        at = next((head.index(n) for n in names if n in head and head.index(n) not in used), None)
        if at is not None:
            columns[field] = at
            used.add(at)
    if "name" in columns and "cf" in columns:
        body, first = rows[1:], 2
    else:
        columns, body, first = {"name": 0, "compartment": 1, "subcompartment": 2, "unit": 3, "cf": 4}, rows, 1
        try:  # a header row of other words
            _number(rows[0][4])
        except (IndexError, ValueError):
            body, first = rows[1:], 2
    if "impact_category" in columns and not any(f in columns for f in ("family", "indicator", "method")):
        del columns["impact_category"]  # a "category" column alone: the compartment's other name, read above
    named = "method" in columns or any(f in columns for f in _PARTS)
    factors, bad = [], []
    for line, row in enumerate(body, first):
        cell = lambda field: row[columns[field]].strip() if field in columns and columns[field] < len(row) else ""  # noqa: E731
        item = {"line": line, "name": cell("name"), "compartment": cell("compartment"),
                "subcompartment": cell("subcompartment"), "unit": cell("unit"), "factor": cell("cf"),
                "database": cell("database"), "code": cell("code"), "method": None, "method_unit": cell("method_unit")}
        if named:
            parts = ([p.strip() for p in cell("method").split("|")] if "method" in columns
                     else [cell(f) for f in _PARTS])
            parts = [re.sub(r"\s+", " ", p) for p in parts if p]
            source = cell("source")
            if not source and "method" in columns:
                source, parts = split_name(parts)
            if not parts:
                bad.append({**item, "reason": "no method name"})
                continue
            item["method"] = (source, parts)
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


def _shown(row: dict, file: str) -> dict:
    """A row left out, as the page lists it: its file and method too."""
    method = row.get("method")
    return {**row, "file": file, "method": label(method[1], method[0]) if method else ""}


def import_csv(library: Library, files, base, project: str, name=None, unit: str = "", description: str = "",
               id: str | None = None, file: str = "", on_conflict: str = "skip", suffix: str = " (copy)",
               only=None) -> dict:
    """CSV files of factors matched to the flows of ``project``: one method per method named in a file (a ``method``
    column, or its parts' columns), else one per file, named after the file (or ``name``, for a single file); or
    (``id``) all their factors set in one method of the library. A method's name is taken as the file gives it; it is
    curated in the library afterwards. ``files``: ``[(text, file name)]``, or the text of one file named ``file``.
    ``only``: the names in a model (as read from the files) of the methods to add, the others passed over (to add,
    renamed, those skipped before).

    Returns ``{"methods": [summary, with "wanted" when renamed], "method": the first, "skipped": [{"method", "name",
    "source", "file", "reason", "taken"}], "matched", "unmatched": [rows of no flow, with the reason, file, method]}``."""
    if isinstance(files, str):
        files = [(files, file)]
    single = len(files) == 1
    groups: dict[tuple, dict] = {}  # by the name in a model: the method's factors, from one file or several
    unmatched, matched = [], 0
    for text, filename in files:
        try:
            rows, bad = parse_csv(text)
        except ValueError as e:
            raise ValueError(f"{filename}: {e}" if filename else str(e)) from None
        unmatched += [_shown(r, filename) for r in bad]
        stem = Path(filename).stem.strip() or "Imported"
        for r in rows:
            if r["method"] is None:
                source, parts = ("", clean_name(name)) if name is not None and single else ("", [re.sub(r"\s+", " ", stem)])
            else:
                source, parts = r["method"]
            key = tuple(model_name(parts, source))
            group = groups.setdefault(key, {"source": source, "name": parts, "rows": [], "files": [], "unit": ""})
            group["rows"].append(r)
            if filename not in group["files"]:
                group["files"].append(filename)
            group["unit"] = group["unit"] or r.get("method_unit", "")
    if only is not None:
        wanted = {tuple(str(p) for p in n) for n in only}
        groups = {k: g for k, g in groups.items() if k in wanted}
    if id:
        found, left = match_rows([r for g in groups.values() for r in g["rows"]], base, project)
        method = summary(library.set_factors(id, found))
        return {"method": method, "methods": [method], "skipped": [], "matched": len(found),
                "unmatched": unmatched + [_shown(r, "") for r in left]}
    added, skipped = [], []
    with library.lock:
        taken = library.taken()
        for group in groups.values():
            found, left = match_rows(group["rows"], base, project)
            unmatched += [_shown(r, ", ".join(group["files"])) for r in left]
            entry = {"method": group["name"], "name": group["name"], "source": group["source"],
                     "file": ", ".join(group["files"])}
            if not found:
                skipped.append({**entry, "reason": f"no row matches a flow of {project}"
                                + (f" ({left[0]['reason']}…)" if left else ""), "taken": False})
                continue
            final = _resolve(group["name"], group["source"], taken, on_conflict, suffix)
            if final is None:
                skipped.append({**entry, "reason": _TAKEN, "taken": True})
                continue
            made = library.create(final, group["unit"] or unit, description, found,
                                  {"csv": entry["file"], "project": project}, group["source"], taken)
            matched += len(found)
            added.append({**summary(made), **({"wanted": group["name"]} if final != group["name"] else {})})
    if not added and not any(s["taken"] for s in skipped):
        why = next((r["reason"] for r in unmatched if r.get("reason")), None)
        raise ValueError(f"No row of the file matches a flow of {project}" + (f" ({why}…)" if why else ""))
    return {"methods": added, "method": added[0] if added else None, "skipped": skipped, "matched": matched,
            "unmatched": unmatched}


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
        current = model_name_of(method)
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
    source: str | None = None
    unit: str | None = None
    description: str | None = None

class NameChanges(BaseModel):
    changes: list[dict]
    """``[{"id", "name"?, "source"?, "unit"?}]``, applied all at once (:meth:`Library.rename_many`)."""

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
    method: list[str] | None = None
    """One method: the copy is returned (an error when its name is taken)."""
    methods: list[list[str]] = []
    """Several: ``{"added", "skipped"}`` is returned (:func:`copy_methods`)."""
    name: list[str] | None = None
    """Of ``method``: None, its own name."""
    on_conflict: str = "skip"
    """Of ``methods``, when the library has a method of the name: "skip" it, or "rename" it with ``suffix``."""
    suffix: str = " (copy)"

class ImportRequest(BaseModel):
    folder: str
    project: str
    """The project whose elementary flows the rows are matched to."""
    path: str = ""
    paths: list[str] = []
    """Several files (with ``path``, if given)."""
    names: list[str] = []
    """The names of the files of ``paths`` as chosen in the page (an uploaded file is renamed): a method of a file
    that names none is named after its file."""
    name: list[str] | None = None
    unit: str = ""
    """Of the methods whose file gives no unit (a ``method unit`` column)."""
    description: str = ""
    id: str | None = None
    """Set the factors in this method of the library, instead of making new ones."""
    on_conflict: str = "skip"
    suffix: str = " (copy)"
    only: list[list[str]] | None = None
    """The methods to add (their names in a model, as read from the files), the others passed over."""


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
        """Editable copies of methods of a Brightway project, under their own names (curated afterwards): one
        (``method``, its summary returned) or several (``methods``: ``{"added", "skipped"}``)."""
        folder = folder_of(req.folder)
        try:
            if req.method is not None:
                return summary(copy_from_project(library, folder, req.project, req.method, req.name))
            if req.on_conflict not in ("skip", "rename"):
                raise ValueError(f"on_conflict: skip or rename, not {req.on_conflict!r}")
            return copy_methods(library, folder, req.project, req.methods, req.on_conflict, req.suffix)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @api.post("/import")
    def import_method(req: ImportRequest):
        """CSV files of factors (flow name, compartment, subcompartment, unit, factor; a method column for files of
        several methods), matched to the elementary flows of a project: one new method per method found (or factors
        set in ``id``); the methods skipped and the rows of no flow are listed (:func:`import_csv`)."""
        paths = [p for p in [req.path, *req.paths] if p.strip()]
        if not paths:
            raise HTTPException(400, "Give the CSV file")
        named = ([""] if req.path.strip() else []) + list(req.names)
        files, read = [], []
        for k, given in enumerate(paths):
            path = Path(given.strip().strip("'\"")).expanduser()
            try:
                raw = path.read_bytes()
            except OSError as e:
                raise HTTPException(404, f"Cannot read {path}: {e.strerror or e}") from None
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp1252", errors="replace")  # Excel's CSV on Windows
            files.append((text, (named[k] if k < len(named) else "") or path.name))
            read.append(path)
        if req.id:
            found(req.id)
        try:
            out = checked(import_csv, library, files, folder_of(req.folder), req.project, req.name, req.unit,
                          req.description, req.id, on_conflict=req.on_conflict, suffix=req.suffix, only=req.only)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
        if not any(s["taken"] for s in out["skipped"]):  # kept while methods skipped may be added renamed
            for path in read:
                uploaded(path)
        return out

    @api.post("/names")
    def rename_methods(req: NameChanges):
        """Names (source, parts) and units of several methods changed at once, all or none (*Curate names*).
        Databases built with a method renamed find it under its former name and are shown as to rebuild."""
        changed = checked(library.rename_many, req.changes)
        return {"changed": [summary(m) for m in changed], "methods": [summary(m) for m in library.all()]}

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
        """Rename a method (its name, its source), change its unit or its description."""
        found(id)
        return summary(checked(library.update, id, req.name, req.unit, req.description, req.source))

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
