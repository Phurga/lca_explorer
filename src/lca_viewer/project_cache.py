# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cache of a Brightway project: one segment per database, or per group of databases that use each other.

See ``docs/project-cache.md``. A segment holds its own columns of ``A`` and ``B`` and its rows of ``G``. Ordering
the products with those of the segments it uses first (*u*), then its own (*s*), ``A`` is block triangular, so

    A_ss^T G_s = U_s^T - A_us^T G_u

gives its rows of ``G`` from those of the segments it uses, which do not depend on it. Opening a database puts its
segment and those it uses, directly or not, together into one :class:`LCAModel`.

Files (``data/cache/project-<hash>/``)::

    project.json      format, Brightway origin, methods, segments (any order: ``uses`` orders them), retired folders
    flows.pkl         elementary flows of the segments built so far (appended to)
    methods.pkl       the methods' factors on those flows
    unlinked.pkl      their factors on flows outside them
    segments/<slug>-<version>/
        segment.json  its databases, what it uses, the state of its databases when built
        A.npz         its own block (products x activities, by position), the node id of its products, and its
                      inputs from other segments (product node id, column, amount)
        B.npz         its elementary flows (flow node id, column, amount)
        G.npy G.json  its rows of G (its products x the methods) and what they were solved from
        activities.pkl details.pkl exchanges.sqlite (rows: node ids)

A segment rebuilt goes to a new folder (a new version): an open model keeps reading the old one, which is deleted
when no model uses it (``retired``).

Versions and bases: a segment's ``version`` changes when it is built again from Brightway; its ``basis`` when its
rows of ``G`` change (its version, or the basis of a segment it uses, changed). The columns of ``G`` are named by
method tokens, so that methods added or removed only solve or drop their columns.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp

from .model import G_CHUNK, LCAModel, Method

FORMAT = 1


def _token() -> str:
    return uuid.uuid4().hex[:10]


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")[:40] or "segment"


def segment_id(databases) -> str:
    """A segment's name: its databases (sorted), as a slug and a hash."""
    databases = sorted(databases)
    digest = hashlib.sha1("\n".join(databases).encode("utf-8")).hexdigest()[:8]
    return f"{_slug(databases[0])}{'-and-' + str(len(databases) - 1) if len(databases) > 1 else ''}-{digest}"


def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _replace_pickle(path: Path, value) -> None:
    tmp = path.with_name(path.name + ".tmp")
    if isinstance(value, pd.DataFrame):
        value.to_pickle(tmp)
    else:
        with open(tmp, "wb") as f:
            pickle.dump(value, f)
    os.replace(tmp, path)


def read_project(cache) -> dict | None:
    """``project.json``, or None when there is none or it is of another format (an obsolete cache, never opened)."""
    try:
        info = json.loads((Path(cache) / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if info.get("format") == FORMAT else None


def method_names(cache) -> list[list[str]]:
    """The impact methods of the project cache ([] when there is none)."""
    info = read_project(cache)
    return [m["name"] for m in info["methods"]] if info else []


def folder(cache, record: dict) -> Path:
    return Path(cache) / "segments" / record["folder"]


def ordered(info: dict, ids=None) -> list[dict]:
    """The segments ``ids`` (all by default), each after the segments it uses (those not in ``ids`` left out)."""
    by_id = {r["id"]: r for r in info["segments"]}
    ids = list(by_id) if ids is None else [i for i in ids if i in by_id]
    wanted, out, placed = set(ids), [], set()

    def place(i, path=()):
        if i in placed or i not in wanted:
            return
        if i in path:
            raise ValueError(f"Segments that use each other: {', '.join(path)}")
        for u in by_id[i]["uses"]:
            place(u, (*path, i))
        placed.add(i)
        out.append(by_id[i])

    for i in ids:
        place(i)
    return out


def closure(info: dict, database: str) -> list[dict]:
    """The segment of ``database`` and those it uses, directly or not, used first; [] when it has none."""
    by_id = {r["id"]: r for r in info["segments"]}
    start = next((r["id"] for r in info["segments"] if database in r["databases"]), None)
    if start is None:
        return []
    found, todo = set(), [start]
    while todo:
        i = todo.pop()
        if i in found:
            continue
        found.add(i)
        todo.extend(u for u in by_id.get(i, {"uses": []})["uses"])
    return ordered(info, found) if all(i in by_id for i in found) else []


def _g_meta(cache, record: dict) -> dict | None:
    try:
        return json.loads((folder(cache, record) / "G.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _g_current(info: dict, record: dict) -> bool:
    """Whether the segment's ``basis`` still holds: built from its version, and from the bases of those it uses."""
    by_id = {r["id"]: r for r in info["segments"]}
    return (record.get("g_version") == record["version"] and all(u in by_id for u in record["uses"])
            and record.get("g_uses") == {u: by_id[u].get("basis") for u in record["uses"]})


def _g_ready(cache, info: dict, record: dict) -> bool:
    """Whether the segment's ``G.npy`` has the columns of every method of the project, for its current basis."""
    meta = _g_meta(cache, record)
    tokens = [m["token"] for m in info["methods"]]
    return (_g_current(info, record) and meta is not None and meta.get("basis") == record.get("basis")
            and set(tokens) <= set(meta.get("methods", ())))


def status(cache, database: str, project: dict | None = None) -> dict:
    """``{"built", "outdated", "solve", "exchanges"}`` of ``database`` in the project cache: whether its segment and
    those it uses are there; what changed in the Brightway project since (``project`` as
    :func:`sources.brightway_projects` lists it; None: not checked), or None; whether results are to be computed again
    (no reading from Brightway); whether the exchanges of its segments are read (a build stopped before reading them
    leaves a database that opens without them: the next build reads them alone)."""
    info = read_project(cache)
    segments = closure(info, database) if info else []
    if not segments or not all((folder(cache, r) / "A.npz").exists() for r in segments):
        return {"built": False, "outdated": None, "solve": False, "exchanges": False}
    outdated = None
    if project is not None:
        modified = {d["name"]: d.get("modified") for d in project["databases"]}
        for r in segments:
            for d in r["databases"]:
                if d not in modified:
                    outdated = f"{d} no longer in the project"
                elif modified[d] != r["state"].get(d):
                    outdated = ("changed since the model was built" if d == database
                                else f"{d} (linked) changed since the model was built")
                if outdated:
                    break
            if outdated:
                break
        if outdated is None and project.get("methods_stamp") and project["methods_stamp"] != info.get("methods_stamp"):
            outdated = "impact methods changed since the model was built"
    solve = not all(_g_ready(cache, info, r) for r in segments)
    exchanges = all(exchange_parts(folder(cache, r), 0) is not None for r in segments)
    return {"built": True, "outdated": outdated or ("results to compute again" if solve else None), "solve": solve,
            "exchanges": exchanges}


def users(info: dict, ids) -> list[dict]:
    """The segments that use those of ``ids``, directly or not (``ids`` left out)."""
    out, grew = set(ids), True
    while grew:
        grew = False
        for r in info["segments"]:
            if r["id"] not in out and out & set(r["uses"]):
                out.add(r["id"])
                grew = True
    return [r for r in info["segments"] if r["id"] in out and r["id"] not in set(ids)]


# ---------------------------------------------------------------- opening (no Brightway needed)

def _read_segment(path: Path) -> dict:
    with np.load(path / "A.npz") as a, np.load(path / "B.npz") as b:
        n = int(a["n"])
        return {
            "n": n, "products": a["products"],
            "A": sp.csc_matrix((a["data"], a["indices"], a["indptr"]), shape=(n, n)),
            "link_node": a["link_node"], "link_col": a["link_col"], "link_value": a["link_value"],
            "flow": b["flow"], "flow_col": b["col"], "flow_value": b["value"],
        }


def _lookup(ids: np.ndarray, values: np.ndarray):
    """``find(keys)``: the value of each key (node ids) in ``ids``, and which keys are there."""
    order = np.argsort(ids, kind="stable")
    sorted_ids, sorted_values = ids[order], values[order]

    def find(keys):
        keys = np.asarray(keys, dtype=np.int64)
        at = np.minimum(np.searchsorted(sorted_ids, keys), max(len(sorted_ids) - 1, 0))
        found = (sorted_ids[at] == keys) if len(sorted_ids) else np.zeros(len(keys), bool)
        return sorted_values[at] if len(sorted_ids) else np.zeros(len(keys), values.dtype), found

    return find, sorted_ids, sorted_values


def _project_files(cache: Path):
    flows = pd.read_pickle(cache / "flows.pkl")
    with open(cache / "methods.pkl", "rb") as f:
        methods = pickle.load(f)
    unlinked = pd.read_pickle(cache / "unlinked.pkl") if (cache / "unlinked.pkl").exists() else None
    return flows, methods, unlinked


def _model_unlinked(project_flows: pd.DataFrame, methods: list[Method], unlinked: pd.DataFrame | None,
                    kept: np.ndarray) -> pd.DataFrame | None:
    """The factors of the methods on flows outside a model whose flows are the project's ``kept``: those outside the
    project (``unlinked``), and those on the project's flows the model does not have."""
    if unlinked is None:
        return None
    outside = np.ones(len(project_flows), bool)
    outside[kept] = False
    rows = []
    for k, m in enumerate(methods):
        for f in np.flatnonzero(outside & (m.cfs != 0)):
            rows.append((k, f, m.cfs[f]))
    if not rows:
        return unlinked
    k, f, cf = map(np.array, zip(*rows))
    extra = pd.DataFrame({"method": k.astype("int32"), "id": project_flows["id"].to_numpy()[f].astype("int64"),
                          "name": project_flows["name"].to_numpy()[f], "categories": project_flows["categories"].to_numpy()[f],
                          "unit": project_flows["unit"].to_numpy()[f], "cf": cf.astype("float64")})
    return pd.concat([unlinked, extra], ignore_index=True)


def load(cache, database: str) -> LCAModel:
    """The model of ``database``: its segment and those it uses, read into place (no copies of ``G``)."""
    cache = Path(cache)
    info = read_project(cache)
    segments = closure(info, database) if info else []
    if not segments:
        raise ValueError(f"{database} is not built in {cache}: build it again")
    for r in segments:
        if not _g_ready(cache, info, r):
            raise ValueError(f"The results of {', '.join(r['databases'])} are to compute again: build it again")
    parts = [_read_segment(folder(cache, r)) for r in segments]
    sizes = [p["n"] for p in parts]
    starts = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    n = int(starts[-1])
    products = np.concatenate([p["products"] for p in parts]).astype(np.int64)
    if len(np.unique(products)) != n:
        raise ValueError(f"{database}: products made by processes of several segments: build it again")
    position, _, _ = _lookup(products, np.arange(n, dtype=np.int64))

    rows, cols, data = [], [], []
    for p, start in zip(parts, starts):
        A = p["A"].tocoo()
        rows.append(A.row.astype(np.int64) + start)
        cols.append(A.col.astype(np.int64) + start)
        data.append(A.data)
        at, found = position(p["link_node"])
        if not found.all():
            raise ValueError(f"{database}: inputs of products no longer in the databases it uses: build it again")
        rows.append(at)
        cols.append(p["link_col"].astype(np.int64) + start)
        data.append(p["link_value"])
    A = sp.csc_matrix((np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))

    project_flows, project_methods, project_unlinked = _project_files(cache)
    flow_position, _, _ = _lookup(project_flows["id"].to_numpy(np.int64), np.arange(len(project_flows), dtype=np.int64))
    used = [flow_position(p["flow"]) for p in parts]
    if not all(found.all() for _, found in used):
        raise ValueError(f"{database}: elementary flows missing from the project cache: build it again")
    kept = np.unique(np.concatenate([at for at, _ in used])) if used else np.zeros(0, np.int64)
    row_of = np.full(len(project_flows), -1, dtype=np.int64)
    row_of[kept] = np.arange(len(kept))
    B = sp.csc_matrix((np.concatenate([p["flow_value"] for p in parts]),
                       (np.concatenate([row_of[at] for at, _ in used]),
                        np.concatenate([p["flow_col"].astype(np.int64) + s for p, s in zip(parts, starts)]))),
                      shape=(len(kept), n))
    flows = project_flows.iloc[kept].reset_index(drop=True)
    methods = [Method(m.name, m.unit, m.cfs[kept]) for m in project_methods]

    tokens = [m["token"] for m in info["methods"]]
    G = np.empty((n, len(tokens)))
    for r, start, size in zip(segments, starts, sizes):
        have = _g_meta(cache, r)["methods"]
        stored = np.load(folder(cache, r) / "G.npy", mmap_mode="r")
        columns = [have.index(t) for t in tokens]
        if columns == list(range(stored.shape[1])):
            G[start:start + size] = stored
        else:  # methods removed since it was solved: their columns are left out
            G[start:start + size] = stored[:, columns]
        del stored

    activities = pd.concat([pd.read_pickle(folder(cache, r) / "activities.pkl") for r in segments], ignore_index=True)
    model = LCAModel(A=A, B=B, activities=activities, flows=flows, methods=methods,
                     unlinked=_model_unlinked(project_flows, project_methods, project_unlinked, kept))
    model._G = G
    model._details_paths = [folder(cache, r) / "details.pkl" for r in segments if (folder(cache, r) / "details.pkl").exists()]
    _, product_ids, product_rows = _lookup(products, np.arange(n, dtype=np.int64))
    flow_ids = project_flows["id"].to_numpy(np.int64)[kept]
    _, flow_ids_sorted, flow_rows = _lookup(flow_ids, np.arange(len(kept), dtype=np.int64))
    model._node_rows = (product_ids, product_rows, flow_ids_sorted, flow_rows)
    # What the model was put together from: the server tells from it when a segment was rebuilt since
    model.project_cache = {"cache": str(cache), "database": database,
                           "segments": {r["id"]: [r["version"], r.get("basis")] for r in segments},
                           "folders": [str(folder(cache, r)) for r in segments],
                           "starts": [int(s) for s in starts]}
    attach_exchanges(model)
    return model


def attach_exchanges(model: LCAModel) -> bool:
    """Give a model opened from a project cache the exchanges of its segments read so far (a model opened while its
    build reads them gets them when it is done); returns whether it has those of all of them."""
    origin = model.project_cache
    parts, complete = [], True
    for path, start, end in zip(origin["folders"], origin["starts"], [*origin["starts"][1:], model.size]):
        found = exchange_parts(Path(path), end - start)
        if found is None:
            complete = False
            continue
        parts += [(start + a, start + b, file) for a, b, file in found]
    model._exchange_parts = parts or None
    return complete


def method_columns(model: LCAModel, names: list[list[str]]) -> tuple[list[Method], np.ndarray, pd.DataFrame | None]:
    """For a model opened from a project cache: the methods ``names`` (as the cache has them now, on the model's
    flows), their columns of ``G`` and the factors of all the model's methods on flows outside it."""
    origin = model.project_cache
    cache = Path(origin["cache"])
    info = read_project(cache)
    segments = closure(info, origin["database"]) if info else []
    if {r["id"]: [r["version"], r.get("basis")] for r in segments} != origin["segments"]:
        raise ValueError("The project cache was built again since this database was opened: open it again")
    project_flows, project_methods, project_unlinked = _project_files(cache)
    flow_position, _, _ = _lookup(project_flows["id"].to_numpy(np.int64), np.arange(len(project_flows), dtype=np.int64))
    kept, _ = flow_position(model.flows["id"].to_numpy(np.int64))
    wanted = [tuple(n) for n in names]
    chosen = [k for k, m in enumerate(project_methods) if tuple(m.name) in wanted]
    tokens = [info["methods"][k]["token"] for k in chosen]
    columns = np.empty((model.size, len(chosen)))
    start = 0
    for r in segments:
        have = _g_meta(cache, r)["methods"]
        stored = np.load(folder(cache, r) / "G.npy", mmap_mode="r")
        columns[start:start + stored.shape[0]] = stored[:, [have.index(t) for t in tokens]]
        start += stored.shape[0]
        del stored
    methods = [Method(project_methods[k].name, project_methods[k].unit, project_methods[k].cfs[kept]) for k in chosen]
    unlinked = None
    if project_unlinked is not None:  # ``method``: position in ``methods``, as LCAModel.add_methods takes them
        whole = _model_unlinked(project_flows, project_methods, project_unlinked, kept)
        position = {k: i for i, k in enumerate(chosen)}
        unlinked = whole[whole["method"].isin(chosen)].copy()
        unlinked["method"] = unlinked["method"].map(position).astype("int32")
        unlinked = unlinked.reset_index(drop=True)
    return methods, columns, unlinked


def remove_retired(cache, in_use) -> None:
    """Delete the folders of rebuilt or deleted segments that no open model reads (``in_use``: their folders)."""
    cache = Path(cache)
    info = read_project(cache)
    if not info or not info.get("retired"):
        return
    in_use = {str(Path(p).resolve()) for p in in_use}
    left = []
    for name in info["retired"]:
        path = cache / "segments" / name
        if str(path.resolve()) in in_use:
            left.append(name)
            continue
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            left.append(name)
    info["retired"] = left
    _write_json(cache / "project.json", info)


def delete_segments(cache, ids) -> list[str]:
    """Delete segments and those that use them (their folders retired); returns the ids deleted."""
    cache = Path(cache)
    info = read_project(cache)
    if not info:
        return []
    gone = set(ids) | {r["id"] for r in users(info, ids)}
    for r in info["segments"]:
        if r["id"] in gone:
            info["retired"].append(r["folder"])
    info["segments"] = [r for r in info["segments"] if r["id"] not in gone]
    _write_json(cache / "project.json", info)
    return sorted(gone)


def drop_results(cache, ids) -> None:
    """Delete the ``G`` of segments: solved again by the next build (their basis is kept: the same numbers)."""
    info = read_project(cache)
    for r in info["segments"] if info else []:
        if r["id"] in ids:
            for name in ("G.npy", "G.json"):
                (folder(cache, r) / name).unlink(missing_ok=True)


# ---------------------------------------------------------------- building (in the build process, with Brightway)

def _database_graph(project_dir: Path, database: str, links: dict[str, set[str]]) -> dict[str, set[str]]:
    """database -> the databases with processes it takes inputs from, for ``database`` and those it uses, directly or
    not. ``links``: those found in the matrix of a database when its segment was built (and unchanged since);
    for the others, Brightway's ``depends``, checked against the matrix when it is read (a query of the exchanges
    here would take 20 s for Agribalyse's 5M)."""
    import sqlite3
    from contextlib import closing

    import bw2data as bd

    from .sources import NON_PROCESS_TYPES

    path = project_dir / "lci" / "databases.db"
    types = sorted(NON_PROCESS_TYPES)
    marks = ", ".join("?" * len(types))
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
        known: dict[str, bool] = {}

        def with_processes(d: str) -> bool:
            # The index on (database, code) finds a process at once: no scan of the whole table (5 s on a 5 GB project)
            if d not in known:
                known[d] = bool(con.execute(
                    f"SELECT EXISTS (SELECT 1 FROM activitydataset WHERE database = ? AND (type IS NULL OR type NOT IN "
                    f"({marks})))", [d, *types]).fetchone()[0])
            return known[d]

        uses: dict[str, set[str]] = {}
        todo = [database]
        while todo:
            d = todo.pop()
            if d in uses:
                continue
            found = links.get(d)
            if found is None:
                found = set((bd.databases[d] or {}).get("depends", [])) if d in bd.databases else set()
            uses[d] = {u for u in found if u != d and with_processes(u)}
            todo.extend(uses[d])
    return uses


def _components(uses: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """The groups of databases that use each other (strongly connected components), each a sorted tuple."""
    from scipy.sparse.csgraph import connected_components

    names = sorted(uses)
    index = {d: i for i, d in enumerate(names)}
    pairs = [(index[a], index[b]) for a in names for b in uses[a]]
    graph = sp.csr_matrix((np.ones(len(pairs)), ([a for a, _ in pairs], [b for _, b in pairs])),
                          shape=(len(names), len(names)))
    _, label = connected_components(graph, directed=True, connection="strong")
    groups: dict[int, list[str]] = {}
    for d, k in zip(names, label):
        groups.setdefault(int(k), []).append(d)
    return [tuple(sorted(g)) for g in groups.values()]


# Processes that read the exchanges of a build: None, as many as the machine has cores but two (the build process and
# the app keep theirs), at most 16; 0: read in the build process. LCA_VIEWER_WORKERS (environment) overrides it.
# Agribalyse's 5M exchanges: 30 s with 4, 23 s with 8, 17 s with 16 (reading SQLite: it does not scale further).
EXCHANGE_WORKERS = None
PARALLEL_EXCHANGES = 50_000  # a segment with fewer exchanges is read in the build process (a process takes 1 s to start)
EXCHANGES = "exchanges.json"  # in a segment's folder: its exchanges are read (the runs of activities and their files)
READY = "Results ready"  # the line of the build's log after which the database can be opened (its exchanges follow)


def exchange_workers() -> int:
    """How many processes read the exchanges of a build (0: the build process reads them)."""
    chosen = os.environ.get("LCA_VIEWER_WORKERS", "").strip()
    if chosen:
        return max(0, int(chosen))
    if EXCHANGE_WORKERS is not None:
        return EXCHANGE_WORKERS
    return max(0, min(16, (os.cpu_count() or 1) - 2))


def _save_npz(path: Path, **arrays) -> None:
    """``np.savez_compressed`` at the fastest level of deflate: Agribalyse's B.npz in 0.7 s and 24 MB, not 2 s and
    16 MB (read by ``np.load`` as any .npz)."""
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for name, value in arrays.items():
            with z.open(f"{name}.npy", "w", force_zip64=True) as out:
                np.lib.format.write_array(out, np.asanyarray(value), allow_pickle=False)


def exchange_parts(path: Path, n: int) -> list[tuple[int, int, Path]] | None:
    """The files of the exchanges of the segment in ``path`` (``n`` activities): ``(first activity, end, file)``, or
    None when they are not read (yet)."""
    try:
        parts = json.loads((path / EXCHANGES).read_text(encoding="utf-8"))["parts"]
        return [(int(a), int(b), path / name) for a, b, name in parts]
    except (OSError, ValueError, KeyError):
        pass
    if (path / "exchanges.sqlite").exists():  # one file, as a segment built before they were read by runs
        return [(0, n, path / "exchanges.sqlite")]
    return None


class _ExchangeReader:
    """Reads the exchanges of the segments a build reads, by runs of activities in processes of their own, while the
    build goes on with the results (:func:`exchange_store.read_part`). They are what the activity pop-up and the
    Exchanges panel show (comments, uncertainty, pedigree), not what the results are computed from: the model can be
    opened before they are read."""

    def __init__(self, project_dir: Path, log):
        self.source = project_dir / "lci" / "databases.db"
        self.log = log
        self.pool = None
        self.jobs: list[tuple[Path, list[tuple[int, int, str]], list | None, list]] = []

    def start(self, path: Path) -> None:
        from . import exchange_store

        activities = pd.read_pickle(path / "activities.pkl")
        keys = list(zip(activities["database"].tolist(), activities["code"].tolist()))
        counts = exchange_store.counts(self.source, sorted(set(activities["database"])))
        sizes = np.array([counts.get(k, 0) for k in keys], dtype=np.int64)
        total, workers = int(sizes.sum()), exchange_workers()
        k = 1 if not workers or total < PARALLEL_EXCHANGES else max(1, min(workers, len(keys)))
        # ``k`` runs of consecutive activities with about as many exchanges each
        cuts = np.searchsorted(np.cumsum(sizes), total * np.arange(1, k) / k, side="left") + 1
        bounds = sorted({0, len(keys), *np.clip(cuts, 0, len(keys)).tolist()})
        parts = [(a, b, f"exchanges-{i}.sqlite") for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:]))]
        work = [keys[a:b] for a, b, _ in parts]
        if k > 1:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor

            if self.pool is None:
                self.pool = ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn"),
                                                initializer=exchange_store.lower_priority)
            futures = [self.pool.submit(exchange_store.read_part, self.source, path / name, w)
                       for (_, _, name), w in zip(parts, work)]
            self.log(f"Reading the {total} exchanges of {len(keys)} activities in {len(parts)} processes")
        else:
            futures = None  # read in this process at the end, once the results are solved
        self.jobs.append((path, parts, futures, work))

    def started(self, path: Path) -> bool:
        return any(p == path for p, *_ in self.jobs)

    def finish(self, current: set[str]) -> None:
        """Wait for the exchanges, and mark them read in the folders of ``current`` (segments retired meanwhile: no)."""
        from . import exchange_store

        try:
            for path, parts, futures, work in self.jobs:
                if futures is None:
                    self.log(f"Reading the exchanges of {sum(len(w) for w in work)} activities")
                    for (_, _, name), w in zip(parts, work):
                        exchange_store.read_part(self.source, path / name, w)
                else:
                    for f in futures:
                        f.result()
                if path.name in current:
                    _write_json(path / EXCHANGES, {"parts": [list(p) for p in parts]})
        finally:
            if self.pool is not None:
                self.pool.shutdown(cancel_futures=True)
                self.pool = None


def _read_matrices(databases, uses_databases, path: Path, method, log) -> tuple[np.ndarray, dict]:
    """Read the matrices and activities of the segment of ``databases`` from Brightway into ``path`` (its exchanges
    are read apart: :class:`_ExchangeReader`); returns the node ids of its flows, and the databases each of its
    databases takes inputs from, as its matrix says."""
    import bw2calc as bc
    import bw2data as bd
    from bw2data.backends import ActivityDataset as AD

    from .brightway import _chunks, _details, _product_rows, activity_frame

    log(f"Reading the matrices of {', '.join(databases)}")
    # One node of each database, so that Brightway loads all of them (not only those ``depends`` lists)
    nodes = {}
    for d in [*databases, *uses_databases]:
        node = next(iter(bd.Database(d)), None)
        if node is not None:
            nodes[node] = 1
    lca = bc.LCA(nodes, method=method)
    lca.load_lci_data()
    # int64: Brightway 2.5 node ids are 64-bit, and dtype=int is 32-bit on Windows
    activity_ids = np.empty(len(lca.dicts.activity), dtype=np.int64)
    for node_id, col in lca.dicts.activity.items():
        activity_ids[col] = node_id
    own_ids = np.array([i for d in databases for i, in AD.select(AD.id).where(AD.database == d).tuples()], dtype=np.int64)
    own_cols = np.flatnonzero(np.isin(activity_ids, own_ids))
    if not len(own_cols):
        raise ValueError(f"{', '.join(databases)} has no processes")
    own_rows = _product_rows(lca, activity_ids[own_cols])
    product_node = np.empty(len(lca.dicts.product), dtype=np.int64)
    for node_id, row in lca.dicts.product.items():
        product_node[row] = node_id
    n = len(own_cols)
    local = np.full(len(product_node), -1, dtype=np.int64)
    local[own_rows] = np.arange(n)
    T = sp.csc_matrix(lca.technosphere_matrix)[:, own_cols].tocoo()
    mine = local[T.row] >= 0
    A = sp.csc_matrix((T.data[mine], (local[T.row[mine]], T.col[mine])), shape=(n, n))
    A.sum_duplicates()
    flow_node = np.empty(len(lca.dicts.biosphere), dtype=np.int64)
    for node_id, row in lca.dicts.biosphere.items():
        flow_node[row] = node_id
    Bt = sp.csc_matrix(lca.biosphere_matrix)[:, own_cols].tocoo()

    path.mkdir(parents=True, exist_ok=True)
    _save_npz(path / "A.npz", n=n, indptr=A.indptr, indices=A.indices, data=A.data,
                        products=product_node[own_rows], link_node=product_node[T.row[~mine]],
                        link_col=T.col[~mine].astype(np.int32), link_value=T.data[~mine])
    _save_npz(path / "B.npz", flow=flow_node[Bt.row], col=Bt.col.astype(np.int32), value=Bt.data)
    own_activities = activity_ids[own_cols]
    flows = np.unique(flow_node[Bt.row])
    rows, cols = T.row, T.col
    del lca, Bt

    log(f"Reading {n} activities")
    activities, records = activity_frame(own_activities)
    activities.to_pickle(path / "activities.pkl")
    with open(path / "details.pkl", "wb") as f:
        pickle.dump([_details(records[i]) for i in own_activities], f)
    del records
    # The databases each of its databases takes inputs from: those of the products in its columns of the matrix, the
    # inputs its results are computed from (``depends`` can be out of date: then the segments are made again)
    used, at = np.unique(product_node[rows], return_inverse=True)
    database_of = {i: d for chunk in _chunks(used.tolist())
                   for i, d in AD.select(AD.id, AD.database).where(AD.id.in_(chunk)).tuples()}
    pairs = pd.DataFrame({"user": activities["database"].to_numpy()[cols],
                          "used": np.array([database_of.get(int(i)) for i in used], dtype=object)[at]}).drop_duplicates()
    seen = {d: set() for d in databases}
    for user, by in zip(pairs["user"], pairs["used"]):
        if by is not None and by != user:
            seen.setdefault(user, set()).add(by)
    return flows, seen


def _solve_segment(cache: Path, info: dict, record: dict, methods: list[Method], flow_position, log) -> None:
    """Write the segment's rows of ``G`` for the project's methods: the columns it has kept, the others solved by
    chunks of methods (``A_ss^T G_s = U_s^T - A_us^T G_u``) into a memory-mapped file."""
    from .solver import Solver

    path = folder(cache, record)
    tokens = [m["token"] for m in info["methods"]]
    meta = _g_meta(cache, record)
    have = meta["methods"] if meta and meta.get("basis") == record["basis"] and (path / "G.npy").exists() else []
    if have == tokens:
        return
    missing = [k for k, t in enumerate(tokens) if t not in have]
    seg = _read_segment(path)
    n = seg["n"]
    log(f"Solving the results of {', '.join(record['databases'])} "
        f"({n} products, {len(missing)} of {len(tokens)} methods)")
    by_id = {r["id"]: r for r in info["segments"]}
    tmp = path / "G.tmp.npy"
    out = np.lib.format.open_memmap(tmp, mode="w+", dtype=np.float64, shape=(n, len(tokens)))
    if have:
        stored = np.load(path / "G.npy", mmap_mode="r")
        for k, t in enumerate(tokens):
            if t in have:
                out[:, k] = stored[:, have.index(t)]
        del stored
    if missing:
        solver = Solver(seg["A"])
        flows, inverse = np.unique(seg["flow"], return_inverse=True)
        at, found = flow_position(flows)
        if not found.all():
            raise ValueError(f"{record['databases']}: flows missing from flows.pkl")
        Bs = sp.csr_matrix((seg["flow_value"], (inverse, seg["flow_col"])), shape=(len(flows), n))
        CF = sp.csr_matrix(np.vstack([methods[k].cfs[at] for k in missing]))
        links = None
        if len(seg["link_node"]):
            nodes, link_row = np.unique(seg["link_node"], return_inverse=True)
            A_us = sp.csr_matrix((seg["link_value"], (link_row, seg["link_col"])), shape=(len(nodes), n))
            G_u = np.empty((len(nodes), len(missing)))
            placed = np.zeros(len(nodes), bool)
            for u in record["uses"]:
                used = by_id[u]
                upath = folder(cache, used)
                with np.load(upath / "A.npz") as a:
                    their = a["products"]
                position, _, _ = _lookup(their, np.arange(len(their), dtype=np.int64))
                rows, found = position(nodes)
                if found.any():
                    columns = [_g_meta(cache, used)["methods"].index(tokens[k]) for k in missing]
                    stored = np.load(upath / "G.npy", mmap_mode="r")
                    G_u[found] = stored[rows[found]][:, columns]
                    del stored
                    placed |= found
            if not placed.all():
                raise LookupError("inputs of products no longer in the databases it uses")
            links = (A_us, G_u)
        step = max(1, G_CHUNK // max(n, 1))
        for a in range(0, len(missing), step):
            rhs = np.asarray((CF[a:a + step] @ Bs).todense()).T
            if links:
                rhs -= links[0].T @ links[1][:, a:a + step]
            out[:, missing[a:a + step]] = solver.solve_transposed(rhs).reshape(n, -1)
    out.flush()
    del out
    os.replace(tmp, path / "G.npy")
    _write_json(path / "G.json", {"basis": record["basis"], "methods": tokens, "shape": [n, len(tokens)]})


def _segments(info: dict, graph: dict[str, set[str]], log) -> list[dict]:
    """The records of the segments ``graph`` needs (added to ``info`` when new), used first. Segments of other
    databases that the groups now take in are retired."""
    groups = _components(graph)
    segment_of = {d: g for g in groups for d in g}
    for g in groups:
        if any(tuple(r["databases"]) == g for r in info["segments"]):
            continue
        if len(g) > 1:
            log(f"{', '.join(g)} use products of each other: they are built together")
        for r in [r for r in info["segments"] if set(r["databases"]) & set(g)]:  # replaced by a segment of others
            if r.get("folder"):
                info["retired"].append(r["folder"])
            info["segments"].remove(r)
    by_databases = {tuple(r["databases"]): r for r in info["segments"]}
    needed = []
    for g in groups:
        record = by_databases.get(g)
        if record is None:
            record = {"id": segment_id(g), "databases": list(g), "uses": [], "state": {}, "version": None}
            info["segments"].append(record)
        record["uses"] = sorted({segment_id(segment_of[u]) for d in g for u in graph[d] if segment_of[u] != g})
        needed.append(record)
    return ordered(info, [r["id"] for r in needed])


def build(project: str, database: str, methods: list | None, cache, rebuild: bool = False, log=None) -> dict:
    """Build what ``database`` needs in the project cache ``cache``: the segments missing or whose databases changed
    (read from Brightway), then the rows of ``G`` of every segment whose results changed (its own, or those of a
    segment it uses), the others kept. ``methods``: the impact methods of the project cache (None: those it has,
    else all the project's); ``rebuild``: read the segment of ``database`` again even if unchanged.
    The results come first: once those of ``database`` are solved, the build logs :data:`READY` (the database can be
    opened), then waits for the exchanges of the segments it read, read meanwhile by other processes.
    Returns ``project.json``."""
    import bw2data as bd

    from .methods_library import registered

    try:
        import multifunctional  # noqa: F401  (backend of databases with allocated multi-output processes)
    except ImportError:
        pass

    log = log or (lambda message: None)
    cache = Path(cache)
    bd.projects.set_current(project)
    project_dir = Path(bd.projects.dir)
    info = read_project(cache)
    if info is None:  # none yet, or of another format
        if cache.exists():
            shutil.rmtree(cache)
        info = {"format": FORMAT, "brightway": str(Path(getattr(bd.projects, "_base_data_dir", project_dir.parent))),
                "project": project, "methods_stamp": None, "methods": [], "segments": [], "retired": []}
    (cache / "segments").mkdir(parents=True, exist_ok=True)
    names = [tuple(m) for m in (methods if methods is not None else [m["name"] for m in info["methods"]] or list(bd.methods))]
    names = [m for m in names if m in bd.methods or registered(m)]  # a method of the library: given by the server
    if not names:
        raise ValueError(f"Project {project!r} has no impact methods: install some first (e.g. with "
                         f"bw2io's bw2setup or ecoinvent_interface)")

    # The segments database needs, from the matrices
    if database not in bd.databases:
        raise ValueError(f"No database {database!r} in project {project!r}")
    modified = {d: (bd.databases[d] or {}).get("modified") for d in bd.databases}
    links = {d: set(used) for r in info["segments"] for d, used in (r.get("links") or {}).items()
             if r["state"].get(d) == modified.get(d)}
    flows = pd.read_pickle(cache / "flows.pkl") if (cache / "flows.pkl").exists() else None
    known = set(flows["id"].tolist()) if flows is not None else set()
    new_flows: set[int] = set()
    reader = _ExchangeReader(project_dir, log)
    try:
        info = _build(cache, info, project_dir, database, names, rebuild, modified, links, flows, known, new_flows,
                      reader, log)
        if reader.jobs:
            reader.finish({r["folder"] for r in info["segments"] if r.get("folder")})
            log("Exchanges read")
    finally:
        if reader.pool is not None:
            reader.pool.shutdown(cancel_futures=True)
    return info


def _build(cache: Path, info: dict, project_dir: Path, database: str, names: list, rebuild: bool, modified: dict,
           links: dict, flows, known: set, new_flows: set, reader: _ExchangeReader, log) -> dict:
    """:func:`build`, the exchanges left to ``reader``."""
    import bw2data as bd

    from .brightway import _unlinked_factors, flow_frame, method_factors
    from .methods_library import registered
    from .sources import _methods_stamp

    for attempt in range(4):
        graph = _database_graph(project_dir, database, links)
        needed = _segments(info, graph, log)
        # Read from Brightway the segments missing or changed
        again = False
        for record in needed:
            state = {d: modified.get(d) for d in record["databases"]}
            if (record["version"] and record["state"] == state and (folder(cache, record) / "A.npz").exists()
                    and not (rebuild and database in record["databases"])):
                continue
            version = _token()
            name = f"{_slug(record['databases'][0])}-{version}"
            path = cache / "segments" / name
            uses_databases = sorted({d for r in closure(info, record["databases"][0]) for d in r["databases"]}
                                    - set(record["databases"]))
            try:
                ids, seen = _read_matrices(record["databases"], uses_databases, path,
                                           next((m for m in names if m in bd.methods), None), log)
            except BaseException:
                shutil.rmtree(path, ignore_errors=True)
                raise
            _write_json(path / "segment.json", {"databases": record["databases"], "uses": record["uses"],
                                                "state": state, "version": version})
            if record.get("folder"):
                info["retired"].append(record["folder"])
            record.update(folder=name, version=version, state=state,
                          links={d: sorted(seen[d]) for d in record["databases"]})
            new_flows |= set(int(i) for i in ids) - known
            found = {d: {u for u in seen[d] if u in modified} for d in record["databases"]}
            if any(found[d] != graph[d] for d in record["databases"]):
                # ``depends`` did not list what the exchanges use: the segments are made again from the matrices
                log(f"{', '.join(record['databases'])}: its exchanges link to other databases than its metadata "
                    f"lists: built again")
                links.update(found)
                record["version"] = None
                again = True
                break
            reader.start(path)  # its exchanges, read while the build goes on
        if not again:
            break
    else:
        raise ValueError(f"The links between the databases {database} uses keep changing: build it again")
    for record in needed:  # built before, their exchanges not read (a build stopped before it read them)
        path = folder(cache, record)
        if not reader.started(path) and exchange_parts(path, 0) is None and (path / "activities.pkl").exists():
            reader.start(path)

    # Flows and methods of the project
    stamp = _methods_stamp(project_dir)
    old = {}
    if (cache / "methods.pkl").exists():
        with open(cache / "methods.pkl", "rb") as f:
            old = {tuple(m.name): (m, t["token"]) for m, t in zip(pickle.load(f), info["methods"])}
    old_digests = {tuple(t["name"]): t.get("library") for t in info["methods"]}
    if new_flows:
        log(f"Reading {len(new_flows)} elementary flows")
        added = flow_frame(np.array(sorted(new_flows), dtype=np.int64))
        flows = added if flows is None else pd.concat([flows, added], ignore_index=True)
    if flows is None:
        flows = flow_frame(np.zeros(0, dtype=np.int64))
    flow_ids = flows["id"].to_numpy(np.int64)
    changed = stamp != info["methods_stamp"]
    # Kept in the order they had, those added after: the columns of the methods already there do not move
    order = [n for n in old if n in names] + [n for n in names if n not in old]
    library = [n for n in order if registered(n)]
    if changed or new_flows or order != list(old) or any(registered(n)["digest"] != old_digests.get(n) for n in library):
        # Read again: all of them when the project's methods were written since, else those added (and those of
        # the library, which may have been edited: their columns of G are kept if their factors are the same)
        fresh_names = order if changed else [n for n in order if n not in old or n in library]
        if fresh_names:
            log(f"Reading {len(fresh_names)} impact methods")
        fresh = {tuple(m.name): m for m in method_factors(fresh_names, flow_ids)} if fresh_names else {}
        others = [n for n in order if n not in fresh]
        n_old = len(flow_ids) - len(new_flows)
        extra = ({tuple(m.name): m.cfs for m in method_factors(others, flow_ids[n_old:])}
                 if new_flows and others else {})
        loaded, entries = [], []
        for n in order:
            before = old.get(n)
            if n in fresh:
                m = fresh[n]
                # Factors unchanged on the flows of the segments solved so far: its columns of G are kept
                same = (before is not None and before[0].unit == m.unit
                        and np.array_equal(before[0].cfs, m.cfs[:len(before[0].cfs)]))
                token = before[1] if same else _token()
            else:
                m, token = before
                m = Method(m.name, m.unit, np.concatenate([m.cfs, extra.get(n, np.zeros(len(flow_ids) - len(m.cfs)))]))
            loaded.append(m)
            entries.append({"name": list(n), "unit": m.unit, "token": token,
                            **({"library": registered(n)["digest"]} if registered(n) else {})})
        log("Reading the factors of the methods on flows outside the project cache")
        unlinked = _unlinked_factors(order, flow_ids)
        _replace_pickle(cache / "flows.pkl", flows)
        _replace_pickle(cache / "methods.pkl", loaded)
        _replace_pickle(cache / "unlinked.pkl", unlinked)
        info["methods"], info["methods_stamp"] = entries, stamp
    else:
        loaded = [old[n][0] for n in order]
    _write_json(cache / "project.json", info)

    # Results: every segment whose basis is still that of its databases, in order (those of ``database`` must be)
    position, _, _ = _lookup(flow_ids, np.arange(len(flow_ids), dtype=np.int64))
    needed_ids = {r["id"] for r in needed}
    skipped: set[str] = set()
    ready = [False]

    def say_ready():
        if not ready[0] and all(_g_ready(cache, info, r) for r in closure(info, database)):
            ready[0] = True
            log(f"{READY}: {database} can be opened" + (" (its exchanges are being read)" if reader.jobs else ""))

    say_ready()
    for record in ordered(info):
        state = {d: (bd.databases[d] or {}).get("modified") if d in bd.databases else None for d in record["databases"]}
        by_id = {r["id"]: r for r in info["segments"]}
        if record["id"] not in needed_ids and (record["state"] != state or set(record["uses"]) & skipped
                                               or not all(u in by_id for u in record["uses"])
                                               or not (folder(cache, record) / "A.npz").exists()):
            skipped.add(record["id"])  # its database changed too: left for its own build
            continue
        if not _g_current(info, record):
            record.update(basis=_token(), g_version=record["version"],
                          g_uses={u: by_id[u].get("basis") for u in record["uses"]})
        try:
            _solve_segment(cache, info, record, loaded, position, log)
        except LookupError:
            if record["id"] not in needed_ids:
                skipped.add(record["id"])
                continue
            raise ValueError(f"{', '.join(record['databases'])} takes inputs of products that are no longer in "
                             f"the databases it uses: build it again") from None
        _write_json(cache / "project.json", info)
        say_ready()
    return info


def add_methods(project: str, database: str, cache, names: list, log=None) -> dict:
    """Add impact methods to the project cache (built for ``database``): their factors are read, and only their
    columns of ``G`` solved, in every segment."""
    have = method_names(cache)
    if not have:
        raise ValueError(f"No project cache in {cache}")
    return build(project, database, have + [list(n) for n in names if list(n) not in have], cache, log=log)
