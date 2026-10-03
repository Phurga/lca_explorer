"""Build an :class:`LCAModel` from a Brightway 2.5 project."""

from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.sparse as sp

from .model import LCAModel, Method


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


def _classification(classifications, prefix: str) -> str | None:
    for system, value in classifications or []:
        if system.startswith(prefix):
            return value
    return None


def from_brightway(project: str, database: str, methods: list[tuple]) -> LCAModel:
    """Load the technosphere of ``database`` (and its dependencies) with ``methods``."""
    import bw2calc as bc
    import bw2data as bd
    from bw2data.backends import ActivityDataset as AD

    bd.projects.set_current(project)
    any_activity = next(iter(bd.Database(database)))
    lca = bc.LCA({any_activity: 1}, method=methods[0])
    lca.lci()

    # Reorder rows so that column j produces row j
    activity_ids = np.empty(len(lca.dicts.activity), dtype=int)
    for node_id, col in lca.dicts.activity.items():
        activity_ids[col] = node_id
    rows = np.array([lca.dicts.product[i] for i in activity_ids])
    A = sp.csr_matrix(lca.technosphere_matrix)[rows, :].tocsc()
    B = sp.csc_matrix(lca.biosphere_matrix)

    records = {
        ds.id: ds.data
        for ds in AD.select(AD.id, AD.data).where(AD.id.in_(activity_ids.tolist()))
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
        }
    )

    flow_ids = np.empty(len(lca.dicts.biosphere), dtype=int)
    for node_id, row in lca.dicts.biosphere.items():
        flow_ids[row] = node_id
    flow_records = {
        ds.id: ds.data
        for ds in AD.select(AD.id, AD.data).where(AD.id.in_(flow_ids.tolist()))
    }
    flows = pd.DataFrame(
        {
            "id": flow_ids,
            "name": [flow_records[i].get("name") for i in flow_ids],
            "categories": [tuple(flow_records[i].get("categories") or ()) for i in flow_ids],
            "unit": [flow_records[i].get("unit") for i in flow_ids],
        }
    )

    loaded = []
    for name in methods:
        lca.switch_method(name)
        cfs = np.asarray(lca.characterization_matrix.diagonal()).ravel()
        loaded.append(Method(tuple(name), bd.methods[name].get("unit", ""), cfs))

    details = [_details(records[i]) for i in activity_ids]
    return LCAModel(A=A, B=B, activities=activities, flows=flows, methods=loaded, details=details)
