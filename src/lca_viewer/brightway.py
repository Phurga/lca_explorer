# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build an :class:`LCAModel` from a Brightway 2.5 project."""

from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.sparse as sp

from .model import LCAModel, Method


# SQLite caps the parameters of one statement (999 before 3.32, 32766 after), so a lookup
# on a large database (hundreds of thousands of processes) goes in chunks under that cap.
SQL_CHUNK = 900


def _chunks(values) -> list[list]:
    values = list(values)
    return [values[i:i + SQL_CHUNK] for i in range(0, len(values), SQL_CHUNK)]


DETAIL_FIELDS = [
    "comment", "product_information", "included_activities_start", "included_activities_end",
    "start_date", "end_date", "activity type", "synonyms", "production amount", "filename",
]


def _details(data: dict) -> dict:
    """Descriptive metadata shown in the activity pop-up."""
    out = {k: data[k] for k in DETAIL_FIELDS if data.get(k) not in (None, "", [])}
    if data.get("classifications"):
        out["classifications"] = [list(c) for c in data["classifications"]]
    authors = data.get("authors") or {}
    if isinstance(authors, dict):
        names = {role: a.get("name") for role, a in authors.items() if isinstance(a, dict) and a.get("name")}
        if names:
            out["authors"] = names
    return out


EXCHANGE_FIELDS = [
    "amount", "unit", "comment", "formula", "variable name", "uncertainty type", "loc", "scale",
    "scale without pedigree", "shape", "minimum", "maximum", "pedigree",
]


def _exchange_data(data: dict) -> dict:
    """Metadata of one exchange shown in the activity pop-up; the product properties (price,
    carbon content, …) and production volume are kept for production exchanges only."""
    fields = EXCHANGE_FIELDS + (["production volume", "properties"] if data.get("type") == "production" else [])
    out = {}
    for k in fields:
        v = data.get(k)
        if isinstance(v, np.ndarray):  # some imports (e.g. BAFU) store numpy values
            v = v.tolist()
        elif isinstance(v, np.generic):
            v = v.item()
        if v is None or (isinstance(v, (str, list, tuple, dict)) and not v) or (isinstance(v, float) and v != v):
            continue  # empty, or NaN (not valid JSON)
        out[k] = v
    return out


def _exchanges(lca, rows: np.ndarray, activity_ids: np.ndarray) -> list[tuple]:
    """``(activity, type, row, data)`` for every exchange of the model's activities, with
    ``row`` in the model's numbering (products of ``A``, flows of ``B``)."""
    from bw2data.backends import ActivityDataset as AD
    from bw2data.backends import ExchangeDataset as ED

    product_of_row = np.empty(len(rows), dtype=int)
    product_of_row[rows] = np.arange(len(rows))  # technosphere row -> model product
    databases = {db for chunk in _chunks(activity_ids.tolist())
                 for db, in AD.select(AD.database).where(AD.id.in_(chunk)).distinct().tuples()}
    node_id = {(db, code): i for i, db, code in AD.select(AD.id, AD.database, AD.code).tuples()}
    out = []
    query = ED.select(ED.type, ED.input_database, ED.input_code, ED.output_database, ED.output_code, ED.data)
    for kind, in_db, in_code, out_db, out_code, data in query.where(ED.output_database.in_(databases)).tuples().iterator():
        activity = lca.dicts.activity.get(node_id.get((out_db, out_code)))
        source = node_id.get((in_db, in_code))
        if kind == "biosphere":
            row = lca.dicts.biosphere.get(source)
        else:
            row = lca.dicts.product.get(source)
            row = None if row is None else int(product_of_row[row])
        if activity is not None and row is not None:
            # ``input``: what the exchange is linked to, to find it again in the project when it is edited
            out.append((activity, kind, row, {**_exchange_data(data), "input": [in_db, in_code]}))
    return out


def _classification(classifications, prefix: str) -> str | None:
    for system, value in classifications or []:
        if system.startswith(prefix):
            return value
    return None


def _product_rows(lca, activity_ids: np.ndarray) -> np.ndarray:
    """Technosphere row of the reference product of each activity (column).

    A process is usually its own product (ecoinvent imports); SimaPro imports can instead
    give each process a separate product node, linked by its functional exchange: a production
    one, or a technosphere one for a waste treatment. Other production exchanges are not its
    product: bw_simapro_csv writes SimaPro's "waste to treatment" as non-functional production
    exchanges to the waste's product.
    """
    from bw2data.backends import ActivityDataset as AD
    from bw2data.backends import ExchangeDataset as ED

    rows = np.array([lca.dicts.product.get(i, -1) for i in activity_ids])
    missing = activity_ids[rows < 0].tolist()
    if not missing:
        return rows
    key_of = {i: (db, code) for chunk in _chunks(missing)
              for i, db, code in AD.select(AD.id, AD.database, AD.code).where(AD.id.in_(chunk)).tuples()}
    product_key, rank = {}, {}
    for db, chunk in ((db, chunk) for db in {db for db, _ in key_of.values()}
                      for chunk in _chunks(code for d, code in key_of.values() if d == db)):
        query = ED.select(ED.output_code, ED.input_database, ED.input_code, ED.type, ED.data).where(
            ED.type.in_(["production", "substitution", "technosphere"]), ED.output_database == db,
            ED.output_code.in_(chunk),
        )
        for output_code, input_db, input_code, kind, data in query.tuples():
            # Best candidate first: functional, then a production exchange with an amount
            if data.get("functional"):
                r = 0
            elif kind == "production" and data.get("functional") is None and data.get("amount"):
                r = 1
            else:
                continue
            key = (db, output_code)
            if r < rank.get(key, 2):
                product_key[key], rank[key] = (input_db, input_code), r
    id_of = {
        (db, code): i
        for db in {db for db, _ in product_key.values()}
        for chunk in _chunks({c for d, c in product_key.values() if d == db})
        for i, code in AD.select(AD.id, AD.code).where(AD.database == db, AD.code.in_(chunk)).tuples()
    }
    for j in np.flatnonzero(rows < 0):
        key = product_key.get(key_of[activity_ids[j]])
        if key is None or id_of.get(key) not in lca.dicts.product:
            raise ValueError(f"Activity {key_of[activity_ids[j]]} has no reference product in the technosphere")
        rows[j] = lca.dicts.product[id_of[key]]
    if len(set(rows.tolist())) != len(rows):
        counts = np.bincount(rows)
        shared = [activity_ids[j] for j in range(len(rows)) if counts[rows[j]] > 1][:4]
        names = {i: n for i, n in AD.select(AD.id, AD.name).where(AD.id.in_([int(i) for i in shared])).tuples()}
        raise ValueError("Several activities produce the same product, so the technosphere is not square, e.g. "
                         + "; ".join(names.get(int(i), str(i)) for i in shared))
    return rows


def method_factors(names: list[tuple], flow_ids) -> list[Method]:
    """The impact methods ``names`` of the current project, their factors on the elementary flows
    ``flow_ids`` (node ids, the rows of a model's ``B``), as Brightway's LCA reads them: in float32
    (its datapackages keep them so), the factors of one flow added up (a method may give it several,
    by location), flows outside ``flow_ids`` left out."""
    import bw2data as bd

    row_of = {int(i): r for r, i in enumerate(flow_ids)}
    out = []
    for name in names:
        method = bd.Method(tuple(name))
        cfs = np.zeros(len(row_of))
        for cf in method.load():
            flow, value = cf[0], cf[1]
            if isinstance(flow, (tuple, list)):  # a key, in data written before node ids
                flow = bd.get_id(tuple(flow))
            if isinstance(value, dict):
                value = value.get("amount", 0.0)
            r = row_of.get(int(flow))
            if r is not None:
                cfs[r] += float(np.float32(value))
        out.append(Method(tuple(name), method.metadata.get("unit", ""), cfs))
    return out


def _cf_value(cf) -> float:
    """A characterisation factor as Brightway stores it: a number, or an uncertainty dict with its ``amount``."""
    if isinstance(cf, dict):
        cf = cf.get("amount", 0.0)
    try:
        return float(cf)
    except (TypeError, ValueError):
        return 0.0


def _unlinked_factors(methods: list[tuple], flow_ids: np.ndarray) -> pd.DataFrame:
    """The characterisation factors of ``methods`` on flows that are not rows of the model's ``B`` (flows that no
    activity of the model exchanges, or of another biosphere database): what the LCA drops without a word. One row
    per factor: ``method`` (position in ``methods``), ``id`` (Brightway node id, -1 when the flow is not in the
    project), ``name``, ``categories``, ``unit``, ``cf``. Methods whose factors cannot be read are left out."""
    import bw2data as bd
    from bw2data.backends import ActivityDataset as AD

    linked = set(int(i) for i in flow_ids)
    found: list[tuple[int, object, float]] = []  # (method, flow reference, cf)
    for k, name in enumerate(methods):
        try:
            data = bd.Method(name).load()
        except Exception:  # noqa: BLE001  (a method without data: nothing to say about it)
            continue
        for row in data:
            if len(row) < 2:
                continue
            ref, cf = row[0], _cf_value(row[1])
            if cf == 0 or (isinstance(ref, (int, np.integer)) and int(ref) in linked):
                continue
            found.append((k, ref, cf))
    # Flows referred to by key (database, code) in older projects: their ids, in one query per database
    keys = {tuple(ref) for _, ref, _ in found if isinstance(ref, (tuple, list))}
    key_ids: dict[tuple, int] = {}
    for db in {d for d, _ in keys}:
        codes = [c for d, c in keys if d == db]
        for start in range(0, len(codes), 500):
            for ds in AD.select(AD.id, AD.code).where((AD.database == db) & AD.code.in_(codes[start:start + 500])):
                key_ids[(db, ds.code)] = ds.id
    rows = []
    for k, ref, cf in found:
        node = key_ids.get(tuple(ref), -1) if isinstance(ref, (tuple, list)) else int(ref)
        if node in linked:
            continue
        rows.append((k, node, ref, cf))
    ids = sorted({node for _, node, _, _ in rows if node >= 0})
    records: dict[int, dict] = {}
    for start in range(0, len(ids), 500):
        for ds in AD.select(AD.id, AD.data).where(AD.id.in_(ids[start:start + 500])):
            records[ds.id] = ds.data
    out = {"method": [], "id": [], "name": [], "categories": [], "unit": [], "cf": []}
    for k, node, ref, cf in rows:
        data = records.get(node, {})
        out["method"].append(k)
        out["id"].append(node)
        out["name"].append(data.get("name") or (str(ref[1]) if isinstance(ref, (tuple, list)) else str(ref)))
        out["categories"].append(tuple(data.get("categories") or ()))
        out["unit"].append(data.get("unit") or "")
        out["cf"].append(cf)
    return pd.DataFrame(out).astype({"method": "int32", "id": "int64", "cf": "float64"})


def activity_frame(activity_ids: np.ndarray) -> tuple[pd.DataFrame, dict]:
    """The table of the activities ``activity_ids`` (node ids, in the model's order), and their records by id."""
    from bw2data.backends import ActivityDataset as AD

    records = {
        ds.id: ds.data
        for chunk in _chunks(activity_ids.tolist())
        for ds in AD.select(AD.id, AD.data).where(AD.id.in_(chunk))
    }
    activities = pd.DataFrame(
        {
            "id": activity_ids,
            "database": [records[i].get("database") for i in activity_ids],
            "code": [records[i].get("code") for i in activity_ids],
            "name": [records[i].get("name") for i in activity_ids],
            "reference_product": [records[i].get("reference product") for i in activity_ids],
            "location": [records[i].get("location") for i in activity_ids],
            "unit": [records[i].get("unit") for i in activity_ids],
            "isic": [_classification(records[i].get("classifications"), "ISIC") for i in activity_ids],
            "cpc": [_classification(records[i].get("classifications"), "CPC") for i in activity_ids],
            # allocated from a multi-output process (a co-product), as multifunctional's allocation makes them
            "multifunctional": [bool(records[i].get("mf_parent_key")) for i in activity_ids],
        }
    )
    return activities, records


def flow_frame(flow_ids: np.ndarray) -> pd.DataFrame:
    """The table of the elementary flows ``flow_ids`` (node ids, the rows of ``B``)."""
    from bw2data.backends import ActivityDataset as AD

    flow_records = {
        ds.id: ds.data
        for chunk in _chunks(flow_ids.tolist())
        for ds in AD.select(AD.id, AD.data).where(AD.id.in_(chunk))
    }
    return pd.DataFrame(
        {
            "id": flow_ids,
            "name": [flow_records[i].get("name") for i in flow_ids],
            "categories": [tuple(flow_records[i].get("categories") or ()) for i in flow_ids],
            "unit": [flow_records[i].get("unit") for i in flow_ids],
            # the metadata of a flow (its pop-up): what the biosphere record has, if anything
            "location": [flow_records[i].get("location") for i in flow_ids],
            "cas": [flow_records[i].get("CAS number") for i in flow_ids],
            "formula": [flow_records[i].get("formula") or flow_records[i].get("chemical formula") for i in flow_ids],
            "synonyms": [tuple(flow_records[i].get("synonyms") or ()) for i in flow_ids],
            "comment": [flow_records[i].get("comment") for i in flow_ids],
        }
    )


def from_brightway(project: str, database: str, methods: list[tuple], log=None) -> LCAModel:
    """Load the technosphere of ``database`` and of the databases it links to, with ``methods``.

    The LCA is built from one activity of ``database``: Brightway then includes every database
    it depends on (e.g. ecoinvent and the biosphere for Agribalyse), so the model is complete.
    ``log(message)`` is called at each step.
    """
    import bw2calc as bc
    import bw2data as bd

    try:
        import multifunctional  # noqa: F401  (backend of databases with allocated multi-output processes)
    except ImportError:
        pass

    log = log or (lambda message: None)
    if not methods:
        raise ValueError(f"Project {project!r} has no impact methods: install some first (e.g. with "
                         f"bw2io's bw2setup or ecoinvent_interface)")
    bd.projects.set_current(project)
    log(f"Reading the matrices of {database} and the databases it uses")
    any_node = next(iter(bd.Database(database)))
    lca = bc.LCA({any_node: 1}, method=methods[0])
    lca.load_lci_data()  # matrices only: the demand just selects the databases

    # Reorder rows so that column j produces row j
    # int64: Brightway 2.5 node ids are 64-bit, and dtype=int is 32-bit on Windows
    activity_ids = np.empty(len(lca.dicts.activity), dtype=np.int64)
    for node_id, col in lca.dicts.activity.items():
        activity_ids[col] = node_id
    rows = _product_rows(lca, activity_ids)
    A = sp.csr_matrix(lca.technosphere_matrix)[rows, :].tocsc()
    B = sp.csc_matrix(lca.biosphere_matrix)

    log(f"Reading {len(activity_ids)} activities")
    activities, records = activity_frame(activity_ids)
    flow_ids = np.empty(len(lca.dicts.biosphere), dtype=np.int64)
    for node_id, row in lca.dicts.biosphere.items():
        flow_ids[row] = node_id
    flows = flow_frame(flow_ids)

    log(f"Reading {len(methods)} impact methods")
    loaded = method_factors(methods, flow_ids)
    log("Reading the factors of the methods on flows outside the model")
    unlinked = _unlinked_factors(methods, flow_ids)

    details = [_details(records[i]) for i in activity_ids]
    log("Reading the exchanges")
    exchanges = _exchanges(lca, rows, activity_ids)
    return LCAModel(A=A, B=B, activities=activities, flows=flows, methods=loaded, details=details, exchanges=exchanges,
                    unlinked=unlinked)
