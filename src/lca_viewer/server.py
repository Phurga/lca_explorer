"""Local web server: a stateless JSON API over :class:`View`, plus the UI.

The client owns the view state (expanded rows, group rules, cut-off) and
sends it with each request; the server only holds the model.
"""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analysis import (OTHER, flow_contribution, flow_contributors, group_contributors, main_path, matched,
                       supply_chain_contribution)
from .model import LCAModel
from .query import derived_activities, derived_flows, evaluate, field_options, search, suggest_values
from .tree import Tree
from .view import View

STATIC = Path(__file__).parent / "static"

# Static files get their Content-Type from ``mimetypes``, which on Windows reads the
# registry: some machines map .js to text/plain and do not know .mjs, and browsers
# then refuse to run the UI's ES modules. Set the types the UI needs explicitly.
for _type, _extensions in {"text/javascript": [".js", ".mjs"], "text/css": [".css"],
                           "application/json": [".json"], "image/svg+xml": [".svg"]}.items():
    for _extension in _extensions:
        mimetypes.add_type(_type, _extension)

QUERY_FIELDS = [
    "name", "reference_product", "location", "unit", "activity_type",
    "isic", "isic_division", "cpc", "cpc_section", "flow_type", "database",
]
GROUP_BY_FIELDS = ["location", "activity_type", "flow_type", "isic_division", "cpc_section", "reference_product", "unit", "database"]
# Elementary flows (rules with ``target="flows"``)
FLOW_FIELDS = ["name", "compartment", "subcompartment", "unit"]
# Recorded data of the exchange supplying a row that the list can show as columns (comments are sent apart)
EXCHANGE_COLUMNS = ["uncertainty type", "loc", "scale", "scale without pedigree", "shape", "minimum", "maximum",
                    "pedigree", "formula", "variable name"]


class ViewState(BaseModel):
    root: list[int]
    amount: float = 1.0
    expanded: list[str] = []
    rules: list[dict] = []
    cutoff: float = 0.0
    cutoff_basis: str = "parent"
    method: int = 0
    pinned: list[str] = []
    others: list[str] = []
    merges: list[dict] = []
    ungrouped: list[str] = []
    substitutions: dict[str, int] = {}
    group_products: bool = False
    methods: list[int] = []
    """Methods whose impacts are returned, in this order (default: ``method`` only)."""
    labels: dict[str, str] = {}
    """Custom names, as in saved views: ``p:<activity>``, ``f:<flow>``, ``r:<row id>``; the rows
    they apply to carry the name as ``custom``. The page applies them itself and does not send them."""


class SearchRequest(BaseModel):
    text: str = ""
    predicate: dict | None = None
    limit: int = 50
    target: str | None = None
    """"flows": the predicate matches elementary flows (``text`` is ignored)."""


class DirectRequest(ViewState):
    row: str
    limit: int = 25


class GlobalRequest(BaseModel):
    root: int
    amount: float = 1.0
    groups: dict[str, dict]
    rule: str = "first_match"
    flow_groups: dict[str, dict] = {}
    """Groups of elementary flows."""
    order: list[str] = []
    """The names of the groups and flow groups in the order of the rules: a flow group listed before the group
    of an activity takes its flows (see ``analysis._attribute``). Activity groups come first when omitted."""
    method: int | None = None
    """Only this method (faster with flow groups); all methods when omitted."""


class CompareEntry(BaseModel):
    root: int
    amount: float = 1.0


class CompareRequest(BaseModel):
    entries: list[CompareEntry]
    groups: dict[str, dict] = {}
    flow_groups: dict[str, dict] = {}
    order: list[str] = []
    """The names of the groups and flow groups in the order of the rules, as in ``GlobalRequest``."""
    rule: str = "first_match"
    method: int
    """The method the groups are split for."""
    methods: list[int] = []
    """Methods whose total score is returned for every entry (the heat map)."""
    basis: str = "groups"
    """"groups": the groups of ``groups`` / ``flow_groups`` and *other*; "flows": the elementary flows (one
    per substance), the ``limit`` weighing most over all entries and *other* for the rest."""
    limit: int = 15


class ContributorsRequest(GlobalRequest):
    group: str
    method: int = 0
    limit: int = 50


class FlowsRequest(BaseModel):
    root: int
    amount: float = 1.0
    method: int = 0
    by: str = "substance"
    """"substance": compartments summed (one row per name and unit); "flow": one row per flow."""
    limit: int = 25
    flow_groups: dict[str, dict] = {}
    """Named groups of flows: each item says which one it falls in (the first, in order)."""


class FlowContributorsRequest(BaseModel):
    root: int
    amount: float = 1.0
    method: int = 0
    flows: list[int]
    limit: int = 50


class LocateRequest(ViewState):
    product: int
    groups: dict[str, dict] = {}
    rule: str = "direct"
    """With "first_match", the path avoids the supply chains of activities matched by a group."""


def _floats(values: np.ndarray) -> list[float]:
    return [float(v) for v in values]


def label_key(row, serialised: dict) -> str | None:
    """Key of a row's custom name (``labelKey`` of the page): a process or a flow is renamed
    wherever it appears, a merged or group row where it is; None: cannot be renamed."""
    if row.kind == "process" and "meta" in serialised:
        return f"p:{serialised['meta']['index']}"
    if row.kind == "flow":
        return f"f:{serialised['flow']['index']}"
    if row.kind in ("merged", "group"):
        return f"r:{row.id}"
    return None


def _substances(model: LCAModel, flows: pd.DataFrame, root: int, method: int, amount: float) -> dict[str, float]:
    """Impact of each elementary flow of a functional unit's whole inventory, the compartments of a
    substance summed (flows without impact left out)."""
    _, impacts = flow_contribution(model, root, method, amount)
    by_name = pd.Series(impacts).groupby(flows["name"].to_numpy()).sum()
    return {str(name): float(v) for name, v in by_name.items() if v != 0}


def _find(activities, name: str, location: str) -> int | None:
    match = activities.index[(activities["name"] == name) & (activities["location"] == location)]
    return int(match[0]) if len(match) else None


def _default_method(model: LCAModel) -> int:
    """Climate change (GWP100) when the model has it: EF v3.1's (also when the method names start
    with the ecoinvent release, as ecoinvent_interface writes them), else the first one found.
    Methods with no factor on the model's flows are passed over: they score nothing (those of
    another biosphere of the project, as ecoinvent's on a BAFU added to the project)."""
    names = [tuple(str(part).lower() for part in m.name) if m.cfs.any() else () for m in model.methods]
    ef = ("ef v3.1", "climate change", "global warming potential (gwp100)")
    tests = [
        lambda n: n[-3:] == ef,
        lambda n: "climate change" in n and "gwp100" in n[-1] and "no lt" not in n[-1],
        lambda n: "climate change" in " ".join(n) and "gwp100" in n[-1] and "no lt" not in " ".join(n),
        lambda n: "climate change" in " ".join(n),
        lambda n: "global warming" in " ".join(n),  # e.g. Ecological Scarcity 2021 (BAFU)
    ]
    for test in tests:
        found = next((k for k, name in enumerate(names) if test(name)), None)
        if found is not None:
            return found
    return next((k for k, name in enumerate(names) if name), 0)


def _layout(model: LCAModel) -> str:
    """What the positions of activities, flows and methods stand for: a saved view, which refers
    to them by position, applies as it is only to a model with the same layout (not to one
    rebuilt after its Brightway project changed, where they may have moved)."""
    import hashlib

    digest = hashlib.sha1()
    for frame, columns in [(model.activities, ["database", "code", "name", "reference_product", "location"]),
                           (model.flows, ["name", "categories", "unit"])]:
        present = [c for c in columns if c in frame.columns]
        digest.update(pd.util.hash_pandas_object(frame[present].astype(str), index=False).values.tobytes())
    digest.update(repr([tuple(m.name) for m in model.methods]).encode())
    return digest.hexdigest()[:16]


def create_app(model: LCAModel, title: str = "", default_root: str | None = None) -> FastAPI:
    """``default_root`` is ``"activity name|location"``."""
    app = FastAPI(title="lca_viewer")
    model.G  # factorise at start-up rather than on the first request
    activities = derived_activities(model)
    flows = derived_flows(model)
    root = _find(activities, *default_root.split("|", 1)) if default_root else None
    default_method = _default_method(model)
    layout = _layout(model)

    META_FIELDS = ["name", "reference_product", "location", "unit", "activity_type", "isic", "cpc", "database", "code"]
    rows_by_product = model.A.tocsr()  # row j: the activities using product j
    products = activities["reference_product"].astype(str).to_numpy()
    units = activities["unit"].astype(str).to_numpy()
    supplies: dict[tuple[int, float], np.ndarray] = {}

    def supply(product: int, amount: float) -> np.ndarray:
        """Operation levels for a functional unit, cached (one solve each)."""
        key = (product, amount)
        if key not in supplies:
            if len(supplies) >= 8:
                supplies.pop(next(iter(supplies)))
            supplies[key] = model.supply(product, amount)
        return supplies[key]

    def meta(i: int) -> dict:
        """Metadata of activity ``i`` used to name chart rows."""
        r = activities.iloc[i]
        values = {f: r.get(f) for f in META_FIELDS}  # importers may not provide every field
        # models built before the column existed have none: not multifunctional as far as they know
        return {"index": int(i), **{f: (None if v is None or pd.isna(v) else str(v)) for f, v in values.items()},
                "multifunctional": bool(r.get("multifunctional", False))}

    def describe(i: int) -> dict:
        r = activities.iloc[i]
        return {"index": int(i), "name": r["name"], "reference_product": r["reference_product"],
                "location": r["location"], "unit": r["unit"]}

    @app.get("/api/info")
    def info():
        return {
            "title": title,
            "activities": model.size,
            "layout": layout,
            "methods": [{"name": list(m.name), "unit": m.unit} for m in model.methods],
            "query_fields": QUERY_FIELDS,
            "group_by_fields": GROUP_BY_FIELDS,
            "flow_query_fields": FLOW_FIELDS,
            "default_root": describe(root) if root is not None else None,
            "default_method": default_method,
        }

    @app.post("/api/search")
    def search_activities(req: SearchRequest):
        try:
            if req.target == "flows":
                found = np.flatnonzero(evaluate(req.predicate, flows)) if req.predicate else np.arange(len(flows))
                return {"total": int(len(found)), "results": [
                    {"index": int(i), "name": flows["name"].iat[i], "location": flows["compartment"].iat[i]}
                    for i in found[: req.limit]]}
            found = search(activities, req.text, req.predicate)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, f"Invalid filter: {e}")
        return {"total": int(len(found)), "results": [describe(i) for i in found[: req.limit]]}

    @app.get("/api/lookup")
    def lookup(name: str, location: str | None = None, limit: int = 25):
        """Whether an activity exists by name and location, and what is near it: the activities
        of that name elsewhere (``same_name``), or, when no activity has the name, the closest
        by words (``similar``). Without ``location``, every activity of that name matches."""
        same = np.flatnonzero((activities["name"] == name).to_numpy())
        matches = [i for i in same if location is None or activities["location"].iat[i] == location]
        out = {
            "matches": [describe(int(i)) for i in matches[:limit]],
            "same_name": [describe(int(i)) for i in same if i not in matches][:limit],
            "similar": [],
        }
        if not len(same):
            out["similar"] = [describe(int(i)) for i in search(activities, name)[:limit]]
        return out

    @app.get("/api/values/{field}")
    def values(field: str, q: str = "", limit: int = 100, target: str | None = None):
        if field not in (FLOW_FIELDS if target == "flows" else QUERY_FIELDS):
            raise HTTPException(404, f"Unknown field {field}")
        return suggest_values(flows if target == "flows" else activities, field, q, limit)

    @app.get("/api/options/{field}")
    def options(field: str, q: str = "", limit: int = 200, target: str | None = None):
        """The values of a filter field with how many activities (flows) have each and, for codes
        (location, ISIC, CPC), what they stand for: the helper of the filter builder."""
        if field not in (FLOW_FIELDS if target == "flows" else QUERY_FIELDS):
            raise HTTPException(404, f"Unknown field {field}")
        return field_options(flows if target == "flows" else activities, field, q, limit)

    @app.post("/api/rows")
    def rows(state: ViewState):
        try:
            view = View.from_dict(model, state.model_dump())
            rendered = view.rows()
        except (KeyError, IndexError, ValueError) as e:
            raise HTTPException(400, f"Invalid view: {e}")
        methods = [state.method] + [m for m in state.methods if m != state.method]

        def part(node) -> dict:
            """A member of a merged or group row, with its own amount and impacts (members panel)."""
            common = {"amount": float(node.amount), "impacts": _floats(node.impacts[methods])}
            if node.flow is not None:
                f = flows.iloc[node.flow]
                compartment = ", ".join(c for c in (f["compartment"], f["subcompartment"]) if c)
                return {"flow": int(node.flow), "name": f["name"], "location": compartment, "unit": f["unit"],
                        "waste": False, **common}
            return {**meta(node.product), "unit": activities["unit"].iat[node.product],
                    "waste": bool(model.diagonal[node.product] < 0), **common}

        rendered_by_id = {r.id: r for r in rendered}
        datasets: dict[int, list[dict]] = {}  # exchanges of the processes read for the comments

        def supplying(r) -> list[dict]:
            """The exchange(s) supplying a process row, in the dataset(s) of the process above it (past
            group and others rows); none for a row without such a process."""
            above = rendered_by_id.get(r.parent)
            while above is not None and above.kind in ("group", "rest"):
                above = rendered_by_id.get(above.parent)
            if above is None or above.kind not in ("process", "merged"):
                return []
            found: list[dict] = []
            for j in [above.node.product] if above.kind == "process" else [m.product for m in above.merged]:
                if j not in datasets:
                    datasets[j] = model.exchange_details(int(j))
                found += [e for e in datasets[j] if e["type"] != "biosphere" and e["row"] == r.node.product]
            return found

        def serialise(r) -> dict:
            out = {
                "id": r.id,
                "parent": r.parent,
                "depth": r.depth,
                "kind": r.kind,
                "label": r.label,
                "location": activities["location"].iat[r.node.product] if r.kind == "process" else None,
                "amount": r.amount,
                "unit": r.unit,
                "waste": bool(r.kind == "process" and model.diagonal[r.node.product] < 0),
                "impacts": _floats(r.impacts[methods]),
                "expandable": r.expandable,
                "expanded": r.expanded,
                "pinned": r.pinned,
                "moved": r.moved,
            }
            if r.kind == "process":
                out["meta"] = meta(r.node.product)
                out["replaced"] = r.replaced
                supplied = supplying(r)
                comments: list[str] = []
                for e in supplied:
                    text = (e.get("comment") or "").strip()
                    if text and text not in comments:
                        comments.append(text)
                if comments:
                    out["comment"] = "\n".join(comments)
                # What the exchange columns show (uncertainty, pedigree, formula), one entry per exchange
                recorded = [{k: e[k] for k in EXCHANGE_COLUMNS if e.get(k) not in (None, "")} for e in supplied]
                if any(recorded):
                    out["exchanges"] = recorded
            elif r.kind == "direct" and len(r.node.path) > 1 and isinstance(r.node.path[-2], int):
                out["meta"] = meta(r.node.path[-2])  # the process emitting
            elif r.kind == "merged":
                out["products"] = [int(m.product) for m in r.merged]
                out["metas"] = [meta(m.product) for m in r.merged]
                out["parts"] = [part(m) for m in r.merged]
                out["by_rule"] = r.by_rule
            elif r.kind == "group":
                out["parts"] = [part(m) for m in r.node_members]
                out["by_product"] = r.by_product
                if r.sources is None:
                    out["products"] = [int(m.product) for m in r.node_members]  # a group of processes can be merged
            elif r.kind == "flow":
                f = flows.iloc[r.node.flow]
                out["flow"] = {"index": int(r.node.flow), "name": f["name"], "compartment": f["compartment"],
                               "subcompartment": f["subcompartment"]}
            if r.regroup:
                out["regroup"] = r.regroup
            if r.kind != "direct" and r.sources is not None:
                out["emitters"] = [meta(j) for j, _ in r.sources]  # processes whose direct emissions these are
            if r.members is not None:
                out["members"] = [serialise(m) for m in r.members]
            custom = state.labels.get(label_key(r, out))
            if custom:
                out["custom"] = custom
            return out

        rows = [serialise(r) for r in rendered]
        return {"methods": methods, "rows": rows}

    @app.post("/api/direct")
    def direct_breakdown(req: DirectRequest):
        """Elementary flows behind a "direct emissions" row, for the current method."""
        try:
            view = View.from_dict(model, req.model_dump())
            row = view.find(req.row)
        except (KeyError, IndexError, ValueError) as e:
            raise HTTPException(400, f"Invalid request: {e}")
        if row.kind != "direct":
            raise HTTPException(400, f"Row {req.row} is not a direct emissions row")
        sources = view.direct_sources(row)
        amounts = sum(operation * model.B[:, j].toarray().ravel() for j, operation in sources)
        impacts = amounts * model.methods[req.method].cfs
        order = [f for f in np.argsort(-np.abs(impacts)) if impacts[f] != 0]
        shown, rest = order[: req.limit], order[req.limit:]
        categories = model.flows["categories"] if "categories" in model.flows else None
        units = model.flows["unit"] if "unit" in model.flows else None
        return {
            "row": req.row,
            "sources": [meta(j) for j, _ in sources],
            "total": float(impacts.sum()),
            "flows": [
                {"name": model.flows["name"].iat[f],
                 "categories": list(categories.iat[f]) if categories is not None else [],
                 "unit": units.iat[f] if units is not None else "",
                 "amount": float(amounts[f]), "impact": float(impacts[f])}
                for f in shown
            ],
            "others": {"count": len(rest), "impact": float(impacts[rest].sum()) if rest else 0.0},
        }

    @app.get("/api/activity/{index}")
    def activity(index: int, method: int | None = None, flows: int = 8):
        """Metadata, description and per-unit results of one activity, for the info pop-up."""
        if not 0 <= index < model.size:
            raise HTTPException(404, f"No activity {index}")
        k = default_method if method is None else method
        per_unit = 1 / model.diagonal[index]  # per unit of reference product
        column = model.B[:, index].toarray().ravel() * model.methods[k].cfs * per_unit
        top = np.argsort(-np.abs(column))[:flows]
        categories = model.flows["categories"] if "categories" in model.flows else None
        return {
            **meta(index),
            "details": model.activity_details(index),
            "method": k,
            "cumulative_per_unit": float(model.G[index, k]),
            "direct_per_unit": float(model.U[k, index] * per_unit),
            "direct_flows": [
                {"name": model.flows["name"].iat[f],
                 "categories": list(categories.iat[f]) if categories is not None else [],
                 "impact": float(column[f])}
                for f in top if column[f] != 0
            ],
        }

    @app.get("/api/activity/{index}/exchanges")
    def activity_exchanges(index: int, method: int | None = None, product: int | None = None):
        """Exchanges of one activity as recorded in its dataset (amount, comment, uncertainty,
        pedigree, …), with their impact per unit of reference product, largest first.
        With ``product``, only the inputs of that product."""
        if not 0 <= index < model.size:
            raise HTTPException(404, f"No activity {index}")
        k = default_method if method is None else method
        per_unit = 1 / model.diagonal[index]
        categories = model.flows["categories"] if "categories" in model.flows else None
        production, inputs, emissions = [], [], []
        for e in model.exchange_details(index):
            kind, row = e.pop("type"), e.pop("row")
            amount = float(e.get("amount", 0.0))
            positive = kind in ("production", "substitution", "generic production")  # signs in A
            if product is not None and (kind == "biosphere" or row != product or row == index):
                continue
            if kind == "biosphere":
                emissions.append({
                    "index": row, "name": model.flows["name"].iat[row],
                    "categories": list(categories.iat[row]) if categories is not None else [],
                    **e, "impact": amount * float(model.methods[k].cfs[row]) * per_unit,
                })
            elif positive and row == index:
                production.append(e)
            else:
                impact = (-1 if positive else 1) * amount * float(model.G[row, k]) * per_unit
                inputs.append({**meta(row), "type": kind, **e, "impact": impact})
        inputs.sort(key=lambda x: -abs(x["impact"]))
        emissions.sort(key=lambda x: -abs(x["impact"]))
        return {
            "available": model.has_exchanges, "method": k,
            "production": production[0] if production else None, "inputs": inputs, "flows": emissions,
        }

    @app.get("/api/flow/{index}")
    def flow_info(index: int, method: int | None = None, activities: str = ""):
        """An elementary flow: metadata, its characterisation factor in every method where it
        has one and, for ``activities`` (comma-separated), the exchanges of this flow in their
        datasets (amount, comment, uncertainty)."""
        if not 0 <= index < len(flows):
            raise HTTPException(404, f"No flow {index}")
        k = default_method if method is None else method
        f = flows.iloc[index]
        factors = [{"method": i, "cf": float(m.cfs[index])} for i, m in enumerate(model.methods) if m.cfs[index] != 0]
        exchanges = []
        for j in (int(a) for a in activities.split(",") if a.strip()):
            if not 0 <= j < model.size:
                raise HTTPException(404, f"No activity {j}")
            records = [{k_: v for k_, v in e.items() if k_ not in ("type", "row")}
                       for e in model.exchange_details(j) if e.get("type") == "biosphere" and e.get("row") == index]
            exchanges.append({"activity": meta(j), "exchanges": records})
        return {
            "index": index, "name": f["name"], "compartment": f["compartment"], "subcompartment": f["subcompartment"],
            "unit": f["unit"], "id": None if "id" not in flows or pd.isna(f["id"]) else str(f["id"]),
            "method": k, "cf": float(model.methods[k].cfs[index]), "factors": factors,
            "exchanges_available": model.has_exchanges, "exchanges": exchanges,
        }

    @app.get("/api/activity/{index}/users")
    def activity_users(index: int, root: int | None = None, amount: float = 1.0, method: int | None = None, limit: int = 15):
        """Activities using the product of ``index`` ("used by"); with ``root``, how much of
        it each one takes in that functional unit's supply chain, largest first."""
        if not 0 <= index < model.size or (root is not None and not 0 <= root < model.size):
            raise HTTPException(404, f"No activity {index if not 0 <= index < model.size else root}")
        k = default_method if method is None else method
        start, end = rows_by_product.indptr[index], rows_by_product.indptr[index + 1]
        users, values = rows_by_product.indices[start:end], rows_by_product.data[start:end]
        keep = (users != index) & (values != 0)
        users, values = users[keep], values[keep]
        # Product supplied per unit of the user's product (negative for waste sent to treatment)
        per_unit = -values / model.diagonal[users]
        if root is not None:
            s = supply(root, amount)
            flow = -values * s[users]  # product of ``index`` going to each user in this supply chain
            produced = s[index] * model.diagonal[index]
            order = np.lexsort((-np.abs(per_unit), -np.abs(flow)))
        else:
            flow, produced = np.zeros(len(users)), 0.0
            order = np.argsort(-np.abs(per_unit))
        shown = order[:limit]
        return {
            "waste": bool(model.diagonal[index] < 0),
            "count": int(len(users)),
            "in_supply_chain": int(np.count_nonzero(flow)),
            "users": [
                {**meta(int(users[i])), "per_unit": float(per_unit[i]),
                 "flow": float(flow[i]), "share": float(flow[i] / produced) if produced else 0.0,
                 "impact": float(flow[i] * model.G[index, k])}
                for i in shown
            ],
        }

    @app.get("/api/activity/{index}/alternatives")
    def activity_alternatives(index: int, method: int | None = None, limit: int = 50):
        """Other activities producing the same product in the same unit ("alternative
        suppliers"), with their cumulative impact per unit of product, lowest first."""
        if not 0 <= index < model.size:
            raise HTTPException(404, f"No activity {index}")
        k = default_method if method is None else method
        same = np.flatnonzero((products == products[index]) & (units == units[index]) & (model.diagonal > 0))
        same = same[same != index]
        per_unit = model.G[same, k]
        current = float(model.G[index, k])
        shown = same[np.argsort(per_unit)][:limit]
        return {
            "product": products[index], "unit": units[index],
            "current": {**meta(index), "per_unit": current},
            "count": int(len(same)),
            "alternatives": [
                {**meta(int(j)), "per_unit": float(model.G[j, k]),
                 "relative": float(model.G[j, k] / current - 1) if current else None}
                for j in shown
            ],
        }

    @app.post("/api/global")
    def global_contribution(req: GlobalRequest):
        methods = slice(None) if req.method is None else [req.method]
        result = supply_chain_contribution(model, req.root, req.groups, req.rule, req.amount, req.flow_groups, methods,
                                           req.order or None)
        return {name: _floats(v) for name, v in result.items()}

    @app.post("/api/compare")
    def compare(req: CompareRequest):
        """Several functional units under the same groups, for the comparison mode: per entry its
        groups (and *other*) for ``method``, which add up to its ``total``, and the total score of
        each of ``methods``."""
        if not req.entries:
            raise HTTPException(400, "Nothing to compare")
        if req.rule not in ("direct", "first_match"):
            raise HTTPException(400, f"Unknown rule {req.rule!r}")
        if req.basis not in ("groups", "flows"):
            raise HTTPException(400, f"Unknown basis {req.basis!r}")
        for k in [req.method, *req.methods]:
            if not 0 <= k < len(model.methods):
                raise HTTPException(404, f"No method {k}")
        for entry in req.entries:
            if not 0 <= entry.root < model.size:
                raise HTTPException(404, f"No activity {entry.root}")
        if req.basis == "flows":
            split = [_substances(model, flows, e.root, req.method, e.amount) for e in req.entries]
            weight: dict[str, float] = {}
            for parts in split:
                for name, impact in parts.items():
                    weight[name] = weight.get(name, 0.0) + abs(impact)
            shown = set(sorted(weight, key=lambda n: -weight[n])[: max(req.limit, 0)])
            split = [{**{n: v for n, v in parts.items() if n in shown},
                      **({OTHER: rest} if (rest := sum(v for n, v in parts.items() if n not in shown)) else {})}
                     for parts in split]
        else:
            split = [{name: float(v[0]) for name, v in supply_chain_contribution(
                model, e.root, req.groups, req.rule, e.amount, req.flow_groups, [req.method], req.order or None).items()}
                     for e in req.entries]
        return {"entries": [{
            "groups": parts,
            "total": sum(parts.values()),
            "totals": _floats(model.score(e.root, e.amount)[req.methods]),
        } for e, parts in zip(req.entries, split)]}

    @app.post("/api/global/contributors")
    def global_contributors(req: ContributorsRequest):
        """Activities making up one group of the whole-supply-chain analysis, largest first."""
        try:
            members, values = group_contributors(model, req.root, req.groups, req.group, req.rule, req.amount,
                                                 req.flow_groups, [req.method], req.order or None)
        except ValueError as e:
            raise HTTPException(400, f"Unknown group {req.group!r}") from e
        impacts = values[:, 0]
        flow_group = req.group in req.flow_groups and req.group not in req.groups
        order = np.argsort(-np.abs(impacts))
        shown, rest = order[: req.limit], order[req.limit:]
        return {
            "group": req.group,
            "total": float(impacts.sum()),
            "count": int(len(members)),
            # Under "first match", matched activities bring their supply chain; others only their own emissions
            "cumulative": req.rule == "first_match" and req.group != "other" and not flow_group,
            "flow_group": flow_group,
            "contributors": [{**meta(int(members[i])), "impact": float(impacts[i])} for i in shown],
            "others": {"count": int(len(rest)), "impact": float(impacts[rest].sum()) if len(rest) else 0.0},
        }

    @app.post("/api/flows")
    def flows_contribution(req: FlowsRequest):
        """Contribution of elementary flows to the score: the whole inventory of the
        functional unit, characterised, largest |impact| first."""
        if req.by not in ("substance", "flow"):
            raise HTTPException(400, f"Unknown grouping {req.by!r}")
        amounts, impacts = flow_contribution(model, req.root, req.method, req.amount)
        table = pd.DataFrame({"name": flows["name"], "compartment": flows["compartment"],
                              "subcompartment": flows["subcompartment"], "unit": flows["unit"],
                              "amount": amounts, "impact": impacts})
        table = table[table["impact"] != 0]
        if req.by == "substance":
            keys = ["name", "unit"]
            items = [{"name": name, "compartment": f"{len(g)} compartments" if len(g) > 1
                      else ", ".join(c for c in (g["compartment"].iat[0], g["subcompartment"].iat[0]) if c),
                      "unit": unit, "amount": float(g["amount"].sum()), "impact": float(g["impact"].sum()),
                      "flows": [int(i) for i in g.index],
                      "compartments": [", ".join(c for c in cs if c) for cs in zip(g["compartment"], g["subcompartment"])]}
                     for (name, unit), g in ((k, g.reindex(g["impact"].abs().sort_values(ascending=False).index))
                                             for k, g in table.groupby(keys, sort=False))]
        else:
            items = [{"name": r.name, "compartment": ", ".join(c for c in (r.compartment, r.subcompartment) if c),
                      "unit": r.unit, "amount": float(r.amount), "impact": float(r.impact), "flows": [int(i)]}
                     for i, r in zip(table.index, table.itertuples(index=False))]
        items.sort(key=lambda it: -abs(it["impact"]))
        shown, rest = items[: req.limit], items[req.limit:]
        try:
            masks = {name: evaluate(predicate, flows) for name, predicate in req.flow_groups.items()}
        except (KeyError, ValueError) as e:
            raise HTTPException(400, f"Invalid flow group: {e}")
        for it in shown:
            it["group"] = next((name for name, mask in masks.items() if mask[it["flows"]].any()), None)
        return {
            "total": float(impacts.sum()),
            "count": len(items),
            "items": shown,
            "others": {"count": len(rest), "impact": sum(it["impact"] for it in rest)},
        }

    @app.post("/api/flows/contributors")
    def flows_contributors(req: FlowContributorsRequest):
        """Activities emitting some elementary flows in the supply chain, largest first."""
        try:
            members, amounts, impacts, main = flow_contributors(model, req.root, req.flows, req.method, req.amount)
        except IndexError as e:
            raise HTTPException(400, f"Unknown flow: {e}") from e
        order = np.argsort(-np.abs(impacts))
        shown, rest = order[: req.limit], order[req.limit:]
        return {
            "total": float(impacts.sum()),
            "count": int(len(members)),
            "contributors": [{**meta(int(members[i])), "amount": float(amounts[i]), "impact": float(impacts[i]),
                              "flow": int(main[i])} for i in shown],
            "others": {"count": int(len(rest)), "impact": float(impacts[rest].sum()) if len(rest) else 0.0},
        }

    @app.post("/api/locate")
    def locate(req: LocateRequest):
        """View changes that show ``product`` in the tree, along its most important supply path."""
        if not 0 <= req.product < model.size:
            raise HTTPException(404, f"No activity {req.product}")
        start = req.root[0]
        path = None
        if req.rule == "first_match" and req.groups:
            path = main_path(model, start, req.product, req.method, blocked=matched(model, req.groups))
        path = path or main_path(model, start, req.product, req.method)
        if path is None:
            raise HTTPException(404, f"{describe(req.product)['name']} is not in the supply chain of the functional unit")
        try:
            view = View.from_dict(model, req.model_dump())
            target = view.reveal("/".join(map(str, path)))
        except (KeyError, IndexError, ValueError) as e:
            raise HTTPException(400, f"Invalid view: {e}")
        return {
            "target": target,
            "found": target == "/".join(map(str, path)),
            "path": [describe(i) for i in path],
            "expanded": sorted(view.expanded),
            "pinned": sorted(view.pinned),
            "others": sorted(view.others),
        }

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


class LoadRequest(BaseModel):
    path: str | None = None
    """A model listed in ``data/models.json``."""
    brightway: str | None = None
    """Or a Brightway data folder, with ``project`` and ``database``: built first if it never was."""
    project: str | None = None
    database: str | None = None
    rebuild: bool = False
    """Build it again (after its project changed); refused while it is open, unless ``reopen``."""
    reopen: bool = False
    """With ``rebuild`` of a database that is open: it keeps being served until the new build is
    ready, then it is replaced by it (the page is reloaded for it)."""


class UnloadRequest(BaseModel):
    key: str


class ProjectRequest(BaseModel):
    folder: str
    """A Brightway data folder (created if needed)."""
    name: str
    setup: bool = False
    """Install the biosphere and impact methods bundled with Brightway (ecoinvent 3.9)."""


class DeleteProjectRequest(BaseModel):
    folder: str
    project: str
    confirm: str
    """The project's name again, typed by the user."""


class DeleteDatabaseRequest(BaseModel):
    folder: str
    project: str
    database: str


class DefaultsRequest(BaseModel):
    folder: str
    project: str
    version: str
    """An ecoinvent version of files.brightway.dev, or "bundled" (biosphere3 and the ecoinvent 3.9
    methods of bw2io)."""


class AddRemoteRequest(BaseModel):
    folder: str
    project: str
    remote: str
    """A project of files.brightway.dev (e.g. BAFU-2025-v2)."""


class RestoreRequest(BaseModel):
    folder: str
    file: str | None = None
    """A backup archive (.tar.gz) on this computer, or ``remote``: a project of files.brightway.dev."""
    remote: str | None = None
    name: str | None = None


class ImportRequest(BaseModel):
    folder: str
    project: str
    format: str
    """"ecospold2" (a .7z release or the folder it was unpacked to) or "simapro-csv"."""
    path: str
    database: str
    biosphere: str = "biosphere3"
    defaults: str | None = None
    """Default biosphere and impact methods installed first and linked to (``biosphere`` is then
    ignored): an ecoinvent version of files.brightway.dev, or "bundled" (biosphere3 and the
    ecoinvent 3.9 methods of bw2io). None: link to ``biosphere``, installing nothing."""
    link_to: list[str] = []
    migrations: list[str] | None = None
    """randonneur_data transformations (SimaPro CSV), or paths of adapter files (randonneur datapackages in JSON);
    default: chosen from ``link_to``."""
    unlinked: str = "fail"


class EcoinventLogin(BaseModel):
    username: str
    password: str
    remember: bool = True
    """Keep them in the system's credential store; else in the server's memory until it stops."""


class EcoinventImport(BaseModel):
    folder: str
    project: str
    version: str
    system_model: str = "cutoff"
    biosphere: str | None = None
    """A biosphere database of the project to link to, without impact methods; default: the
    release's own biosphere and its impact methods."""


class DatasetRequest(BaseModel):
    folder: str
    project: str
    spec: dict
    """See :func:`lca_viewer.editing.write_dataset`."""


class StageRequest(BaseModel):
    folder: str
    project: str
    change: dict
    """``{"id"?, "kind": "create" | "edit" | "delete", "title", "database", "code"?, "spec"?, "form"?}``:
    ``spec`` as :func:`lca_viewer.editing.write_dataset` takes it; ``form`` is what the page needs to
    open the change again."""


class PendingRequest(BaseModel):
    folder: str
    project: str
    id: str | None = None
    """The change to take back; all of them when omitted."""


class ApplyRequest(BaseModel):
    folder: str
    project: str
    database: str | None = None
    """The database whose model is built again and opened once the changes are made."""
    rebuild: bool = True
    """False: only make the changes. The models of the databases they affect then have to be built again."""


class LockRequest(BaseModel):
    folder: str
    project: str
    database: str
    locked: bool


class DeleteDatasetRequest(BaseModel):
    folder: str
    project: str
    database: str
    code: str
    """A process of the dataset to delete."""


def model_key(path) -> str:
    """URL-safe name of a model directory, used in ``/db/<key>/``."""
    from .sources import slug

    return slug(Path(path).name)


def create_library(data=None, preload: list[dict] = ()) -> FastAPI:
    """The app: databases are loaded while it runs, each served under ``/db/<key>/`` with the
    API of :func:`create_app`.

    ``data``: lca_viewer's data folder (``models.json``, ``cache/``, ``uploads/``); ``preload``:
    ``{"path", "title", "default_root"}`` models loaded before the server starts. ``/`` opens
    the first loaded database, or the page that asks which one to load.
    """
    import gc
    import shutil
    import subprocess
    import sys
    import threading
    import time
    import uuid

    from fastapi.responses import RedirectResponse
    from starlette.routing import Mount

    from . import sources

    data = Path(data or "data").resolve()
    app = FastAPI(title="lca_viewer")
    loaded: dict[str, dict] = {}  # key -> {"key", "title", "path", "activities", "route"}
    jobs: dict[str, dict] = {}
    lock = threading.Lock()

    def listing() -> list[dict]:
        """The loaded databases; ``brightway``: the data folder, project and database a model was built from."""
        entries = {Path(c["path"]).resolve(): c for c in sources.catalog(data)}
        out = []
        for m in loaded.values():
            entry = entries.get(m["path"])
            origin = entry and built_from(entry)
            out.append({"key": m["key"], "title": m["title"], "activities": m["activities"],
                        "brightway": origin and dict(zip(("folder", "project", "database"), origin)),
                        "outdated": origin and outdated(entry, *origin)})
        return out

    def outdated(entry: dict, folder: str, project: str, database: str) -> str | None:
        """What changed in the project since the model of a Brightway database was built (a rebuild is
        required), or None."""
        try:
            p = next(p for p in sources.brightway_projects(folder) if p["name"] == project)
            return sources.changes(entry["source"], sources.database_state(p, database))
        except (StopIteration, KeyError, FileNotFoundError, ValueError):
            return None

    def loaded_key(path) -> str | None:
        path = Path(path).resolve()
        return next((k for k, m in loaded.items() if m["path"] == path), None)

    def mount(path, title: str, default_root: str | None) -> str:
        """Load, factorise and serve a model; returns its key."""
        path = Path(path).resolve()
        model = LCAModel.load(path)
        sub = create_app(model, title=title, default_root=default_root)  # factorises
        with lock:
            key = loaded_key(path)
            if key:
                return key
            key, n = model_key(path), 2
            while key in loaded:
                key, n = f"{model_key(path)}-{n}", n + 1
            route = Mount(f"/db/{key}", app=sub)
            app.router.routes.insert(0, route)  # before the "not loaded" page of /db/<key>/
            loaded[key] = {"key": key, "title": title, "path": path, "activities": model.size, "route": route}
        return key

    for m in preload:
        mount(m["path"], m.get("title") or Path(m["path"]).name, m.get("default_root"))

    def built_from(entry: dict) -> tuple | None:
        """(Brightway folder, project, database) a catalog model was built from."""
        source = entry.get("source") or {}
        if not source.get("brightway"):
            return None
        return (str(Path(source["brightway"]).resolve()), source.get("project"), source.get("database"))

    def find_database(folder: str, project: str, database: str) -> tuple[dict, dict]:
        """(project, database) as listed by :func:`sources.brightway_projects`, if a model can be built from it."""
        try:
            projects = sources.brightway_projects(folder)
        except FileNotFoundError as e:
            raise ValueError(str(e)) from None
        p = next((p for p in projects if p["name"] == project), None)
        if p is None:
            raise ValueError(f"No project {project!r} in {folder}")
        if not p["twofive"]:
            raise ValueError(f"Project {project!r} was made with Brightway 2, and lca_viewer reads Brightway 2.5 "
                             f"projects. Upgrade it with Brightway 2.5 (projects.migrate_project_25()) or "
                             f"Activity Browser 3, or import its databases again into a 2.5 project.")
        d = next((d for d in p["databases"] if d["name"] == database), None)
        if d is None:
            raise ValueError(f"No database {database!r} in project {project!r}")
        if not d["processes"]:
            raise ValueError(f"{database} has no processes (it is a biosphere database): load the database that uses it")
        if not p["methods"]:
            raise ValueError(f"Project {project!r} has no impact methods: install some first "
                             f"(e.g. with bw2io's bw2setup or ecoinvent_interface)")
        return p, d

    def cli(job: dict, folder: str, args: list[str], message=None, env: dict | None = None) -> list[str]:
        """Run ``lca-viewer <args>`` on the Brightway data folder ``folder`` in a separate process
        (bw2data's folder is fixed when it is imported), its output going to the job's log;
        ``message(line)`` gives the job's message for a line, or a false value to keep it.
        ``env``: more environment variables (secrets go there, never in ``args``)."""
        command = [sys.executable, "-m", "lca_viewer.cli", *args]
        env = {**os.environ, **(env or {}), "BRIGHTWAY2_DIR": folder, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        Path(folder).mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                   text=True, encoding="utf-8", errors="replace")
        lines = []
        for line in process.stdout:
            line = line.rstrip()
            if line:
                lines.append(line)
                job["log"].append(line)
                shown = message(line) if message else None
                if shown:
                    job["message"] = shown
        if process.wait() != 0:
            raise ValueError(lines[-1] if lines else "no output")
        return lines

    def build(job: dict, folder: str, project: str, database: str, rebuild: bool, reopen: bool = False) -> dict:
        """The catalog entry of a Brightway database's model, built (in a separate process) if
        there is none yet, or again with ``rebuild`` (the page asks for it when the project
        changed since). A database that is open is not rebuilt, unless ``reopen``: it would mix its
        old matrices with the new metadata files. With ``reopen`` it keeps being served while the
        new build is made, then is replaced by it (the caller opens it again)."""
        import importlib.util

        folder = str(Path(folder).expanduser().resolve())
        p, _ = find_database(folder, project, database)
        state = sources.database_state(p, database)
        entry = next((m for m in sources.catalog(data) if built_from(m) == (folder, project, database)), None)
        output = Path(entry["path"]) if entry else sources.cache_dir(data, folder, project, database).resolve()
        if entry and not rebuild and (output / "A.npz").exists():
            return entry
        if loaded_key(output) and not reopen:
            raise ValueError(f"{database} is open: close it first (Close, in the list of loaded databases), then rebuild it")
        if importlib.util.find_spec("bw2data") is None:
            raise ValueError("Brightway is not installed in the Python environment running lca_viewer: "
                             "run pip install -e \".[brightway]\" in it, then restart the app")
        building = f"Building the model of {database} (once; one or two minutes for ecoinvent)"
        job["message"] = building
        part = output.with_name(output.name + ".part")
        shutil.rmtree(part, ignore_errors=True)
        try:
            # The steps of from_brightway, not Brightway's own messages
            cli(job, folder, ["build", "--project", project, "--database", database, "--output", str(part)],
                lambda line: line.startswith("Reading") and f"{building}: {line[0].lower()}{line[1:]}")
        except ValueError as e:
            shutil.rmtree(part, ignore_errors=True)
            raise ValueError(f"The build failed: {e}") from None
        old = output.with_name(output.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        closed, failure = None, None
        with lock:  # not while the database is being opened
            key = loaded_key(output)
            if key and not reopen:
                shutil.rmtree(part, ignore_errors=True)
                raise ValueError(f"{database} was opened during the build: close it, then rebuild it")
            if key:  # served until now: its files are replaced
                closed = loaded.pop(key)
                app.router.routes.remove(closed["route"])
                gc.collect()
            try:
                if output.exists():
                    output.rename(old)  # fails, leaving the previous build whole, when a file in it is in use
                part.rename(output)
            except OSError as e:
                if old.exists() and not output.exists():
                    old.rename(output)
                shutil.rmtree(part, ignore_errors=True)
                failure = f"Could not replace the previous build in {output}: {e}"
        if failure:
            if closed:
                mount(output, closed["title"], None)  # the previous build keeps being served
            raise ValueError(failure)
        shutil.rmtree(old, ignore_errors=True)
        title = f"{database} ({project})"
        source = {"brightway": folder, "project": project, "database": database, **state}
        sources.register(data, output, title, None, source)
        return {"path": str(output), "title": title, "default_root": None}

    def run(job: dict, req: LoadRequest) -> dict:
        if req.brightway:
            entry = build(job, req.brightway, req.project, req.database, req.rebuild, req.reopen)
        else:
            entry = next((m for m in sources.catalog(data) if Path(m["path"]) == Path(req.path).resolve()), None)
            if entry is None:
                raise ValueError(f"{req.path} is not in {data / 'models.json'}")
        key = loaded_key(entry["path"])
        if key is None:
            if not (Path(entry["path"]) / "A.npz").exists():
                raise ValueError(f"No model in {entry['path']} (no A.npz): build it again")
            job["message"] = f"Loading {entry['title']} and factorising its matrix"
            key = mount(entry["path"], entry["title"], entry.get("default_root"))
        return {"key": key, "message": f"{entry['title']} is ready"}

    def public(job: dict) -> dict:
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def start(title: str, target: tuple, work, *args) -> dict:
        """Run ``work(job, *args)`` in the background, one job per ``target`` at a time; what it
        returns is added to the job, which the page follows with ``/api/jobs/<id>``."""
        with lock:
            for job in jobs.values():  # already under way
                if job["status"] == "running" and job["_target"] == target:
                    return public(job)
            job = {"id": uuid.uuid4().hex[:12], "status": "running", "message": "Starting", "log": [],
                   "title": title, "_target": target, "_start": time.perf_counter()}
            jobs[job["id"]] = job

        def go():
            try:
                job.update(work(job, *args) or {})
                job["status"] = "done"
            except Exception as e:  # reported to the page
                job.update(status="failed", error=str(e) or type(e).__name__)
            finally:
                job["seconds"] = round(time.perf_counter() - job["_start"], 1)

        threading.Thread(target=go, daemon=True).start()
        return public(job)

    @app.get("/api/databases")
    def databases():
        return listing()

    @app.get("/api/sources")
    def sources_():
        """What can be loaded (built models, Brightway data folders), and the loads under way."""
        models = []
        for m in sources.catalog(data):
            path = Path(m["path"])
            models.append({"path": str(path), "title": m["title"], "key": loaded_key(path),
                           "built": (path / "A.npz").exists(), "source": m.get("source")})
        return {
            "data": str(data),
            "loaded": listing(),
            "models": models,
            "brightway": sources.brightway_dirs(),
            "jobs": [public(j) for j in jobs.values() if j["status"] == "running"],
        }

    @app.get("/api/brightway")
    def brightway(folder: str):
        """Projects and databases of a Brightway data folder, with the model built from each, if any."""
        try:
            projects = sources.brightway_projects(folder)
        except FileNotFoundError as e:
            if not Path(folder).expanduser().is_dir():
                raise HTTPException(404, str(e))
            projects = []  # a folder without projects yet: New project creates the first one
        folder = str(Path(folder).expanduser().resolve())
        built = {}
        for m in sources.catalog(data):
            origin = built_from(m)
            if origin and origin[0] == folder and (Path(m["path"]) / "A.npz").exists():
                built[origin[1:]] = {"key": loaded_key(m["path"]), "source": m["source"]}
        for p in projects:
            for d in p["databases"]:
                d["locked"] = d["name"] in locked_read(folder, p["name"])
                b = built.get((p["name"], d["name"]))
                # outdated: what changed in the project since the build (None: nothing)
                d["model"] = b and {"key": b["key"], "outdated": sources.changes(b["source"], sources.database_state(p, d["name"]))}
        return {"folder": folder, "projects": projects}

    @app.post("/api/load")
    def load(req: LoadRequest):
        """Load a database in the background; follow it with ``/api/jobs/<id>``."""
        if not req.path and not (req.brightway and req.project and req.database):
            raise HTTPException(400, "Give a model path, or a Brightway folder, project and database")
        target = ("load", req.path and str(Path(req.path).resolve()),
                  req.brightway and str(Path(req.brightway).expanduser().resolve()), req.project, req.database)
        return start(req.database or Path(req.path).name, target, run, req)

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        if job_id not in jobs:
            raise HTTPException(404, f"No job {job_id}")
        return public(jobs[job_id])

    @app.post("/api/unload")
    def unload(req: UnloadRequest):
        """Stop serving a database, to free its memory."""
        with lock:
            m = loaded.pop(req.key, None)
            if m is None:
                raise HTTPException(404, f"{req.key} is not loaded")
            app.router.routes.remove(m["route"])
        gc.collect()
        return listing()

    # ------------------------------------------------ Brightway projects: changes, in jobs

    def folder_of(path: str) -> str:
        return str(Path(path.strip().strip("'\"")).expanduser().resolve())

    def project_names(folder: str) -> list[str]:
        try:
            return [p["name"] for p in sources.brightway_projects(folder)]
        except FileNotFoundError:
            return []

    def ours(line: str):
        """The progress lines of lca_viewer.editing, not Brightway's own messages."""
        return line if line.startswith(("Created project", "Installing", "Deleted", "Restor", "Downloading", "Writing")) \
            or line.endswith("impact methods installed") else None

    def uploaded(path: Path) -> None:
        """Delete a file the page uploaded (``/api/upload``) once it has been used."""
        if Path(path).resolve().parent == (data / "uploads").resolve():
            Path(path).unlink(missing_ok=True)

    @app.post("/api/brightway/projects")
    def create_project(req: ProjectRequest):
        folder = folder_of(req.folder)
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "Give the project a name")
        if name in project_names(folder):
            raise HTTPException(400, f"Project {name!r} already exists in {folder}")

        def work(job):
            cli(job, folder, ["project", "create", name, *(["--setup"] if req.setup else [])], ours)
            return {"message": f"Project {name} is ready" + (" (with biosphere and impact methods)" if req.setup else ""),
                    "project": name}

        return start(f"project {name}", ("create", folder, name), work)

    @app.post("/api/brightway/projects/delete")
    def delete_project(req: DeleteProjectRequest):
        folder = folder_of(req.folder)
        if req.confirm != req.project:
            raise HTTPException(400, "Type the project's name to confirm")
        if req.project not in project_names(folder):
            raise HTTPException(404, f"No project {req.project!r} in {folder}")

        def work(job):
            cli(job, folder, ["project", "delete", req.project], ours)
            return {"message": f"Project {req.project} deleted"}

        return start(f"project {req.project}", ("delete", folder, req.project), work)

    @app.post("/api/brightway/databases/delete")
    def delete_database(req: DeleteDatabaseRequest):
        """Delete a database from a project, unless a database linking to it, or its open model, needs it."""
        folder = folder_of(req.folder)
        try:
            p = next((p for p in sources.brightway_projects(folder) if p["name"] == req.project), None)
        except FileNotFoundError:
            p = None
        if p is None:
            raise HTTPException(404, f"No project {req.project!r} in {folder}")
        if not p["twofive"]:
            raise HTTPException(400, f"Project {req.project!r} was made with Brightway 2: upgrade it to Brightway 2.5 first")
        if req.database not in [d["name"] for d in p["databases"]]:
            raise HTTPException(404, f"No database {req.database!r} in project {req.project!r}")
        users = [d["name"] for d in p["databases"] if req.database in d["depends"]]
        if users:
            raise HTTPException(400, f"{req.database} is used by {', '.join(users)}: remove "
                                     f"{'it' if len(users) == 1 else 'them'} first")
        if any(built_from(m) == (folder, req.project, req.database) and loaded_key(m["path"]) for m in sources.catalog(data)):
            raise HTTPException(400, f"{req.database} is open: close it first (Close, in the list of loaded databases)")

        def work(job):
            cli(job, folder, ["database", "delete", req.database, "--project", req.project], ours)
            return {"message": f"{req.database} removed from {req.project}"}

        return start(f"{req.database} ({req.project})", ("delete-database", folder, req.project, req.database), work)

    @app.post("/api/brightway/defaults/install")
    def install_defaults_(req: DefaultsRequest):
        """Add the biosphere and impact methods of an ecoinvent version to a project (``lca-viewer defaults``)."""
        folder = folder_of(req.folder)
        if not re.fullmatch(r"bundled|\d+(\.\d+)+", req.version):
            raise HTTPException(400, f"Unknown default biosphere {req.version!r}")
        if req.project not in project_names(folder):
            raise HTTPException(404, f"No project {req.project!r} in {folder}")

        def work(job):
            cli(job, folder, ["defaults", req.version, "--project", req.project, "--data", str(data)], ours)
            return {"message": f"Biosphere and impact methods of ecoinvent {req.version.replace('bundled', '3.9')} "
                               f"are in {req.project}"}

        return start(f"biosphere ({req.project})", ("defaults", folder, req.project, req.version), work)

    @app.get("/api/brightway/remote")
    def remote():
        """Brightway projects published on files.brightway.dev (BAFU, USEEIO, …)."""
        return [{"name": k, "file": v} for k, v in sources.remote_projects().items()]

    @app.post("/api/brightway/remote/add")
    def add_remote(req: AddRemoteRequest):
        """Add a project of files.brightway.dev to a project (``lca-viewer add-remote``)."""
        folder = folder_of(req.folder)
        if req.remote not in sources.remote_projects():
            raise HTTPException(404, f"No project {req.remote!r} on files.brightway.dev")
        if req.project not in project_names(folder):
            raise HTTPException(404, f"No project {req.project!r} in {folder}")

        def work(job):
            cli(job, folder, ["add-remote", req.remote, "--project", req.project, "--data", str(data)], ours)
            return {"message": f"{req.remote} is in {req.project}"}

        return start(f"{req.remote} ({req.project})", ("add-remote", folder, req.project, req.remote), work)

    @app.post("/api/brightway/restore")
    def restore(req: RestoreRequest):
        """A project from a backup archive: a file on this computer, or downloaded from files.brightway.dev."""
        import urllib.request

        folder = folder_of(req.folder)
        name = (req.name or "").strip() or None
        if name and name in project_names(folder):
            raise HTTPException(400, f"Project {name!r} already exists in {folder}")
        if not req.file and not req.remote:
            raise HTTPException(400, "Give a backup file, or a project of files.brightway.dev")
        if req.file and not Path(folder_of(req.file)).is_file():
            raise HTTPException(404, f"No file {req.file}")

        def work(job):
            path = Path(folder_of(req.file)) if req.file else None
            if req.remote:
                archive = sources.remote_projects().get(req.remote)
                if not archive:
                    raise ValueError(f"No project {req.remote!r} on files.brightway.dev")
                path = data / "downloads" / archive
                path.parent.mkdir(parents=True, exist_ok=True)
                part = path.with_name(path.name + ".part")
                with urllib.request.urlopen(sources.REMOTE + archive, timeout=60) as response, open(part, "wb") as out:
                    size, done = int(response.headers.get("Content-Length") or 0), 0
                    while chunk := response.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        job["message"] = (f"Downloading {archive}: {done >> 20} of {size >> 20} MB" if size
                                          else f"Downloading {archive}: {done >> 20} MB")
                part.replace(path)
            lines = cli(job, folder, ["project", "restore", *([name] if name else []), "--file", str(path)],
                        ours)
            if req.remote:
                path.unlink(missing_ok=True)
            else:
                uploaded(path)
            return {"message": lines[-1] if lines else "Restored"}

        return start(f"project {name or req.remote or Path(req.file).name}", ("restore", folder, name, req.file, req.remote), work)

    @app.post("/api/upload")
    async def upload(request: Request, name: str):
        """A file chosen in the page (an archive, a SimaPro CSV), kept in ``data/uploads`` until used."""
        target = data / "uploads" / sources.slug(Path(name).name)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        with open(part, "wb") as out:
            async for chunk in request.stream():
                out.write(chunk)
        part.replace(target)
        return {"path": str(target), "size": target.stat().st_size}

    @app.get("/api/adapters")
    def adapters(link_to: list[str] = Query([])):
        """randonneur_data transformations for SimaPro CSV imports, and those chosen by default
        for the background databases ``link_to``."""
        from .importing import default_migrations

        return {"adapters": sources.adapters(), "default": default_migrations(link_to)}

    @app.get("/api/adapter-file")
    def adapter_file_info(path: str):
        """What an adapter file of the user's holds (a randonneur datapackage in JSON): ``{"name", "path",
        "transformations": {verb: count}}``; 400 when it cannot be used."""
        from .importing import adapter_file

        file = Path(path.strip().strip("'\"")).expanduser().resolve()
        try:
            found = adapter_file(file)
        except SystemExit as e:
            raise HTTPException(400, str(e))
        verbs = {v: len(found[v]) for v in ("replace", "update", "disaggregate", "delete", "create") if isinstance(found.get(v), list)}
        return {"name": file.name, "path": str(file), "transformations": verbs}

    @app.get("/api/brightway/defaults")
    def defaults():
        """ecoinvent versions whose default biosphere and impact methods can be installed in a project."""
        from .importing import default_versions

        return {"versions": default_versions()}

    @app.post("/api/brightway/import")
    def import_database(req: ImportRequest):
        """Import an ecoSpold2 release or a SimaPro CSV into a project (``lca-viewer import``)."""
        folder = folder_of(req.folder)
        path = Path(folder_of(req.path))
        database = req.database.strip()
        if req.format not in ("ecospold2", "simapro-csv"):
            raise HTTPException(400, f"Unknown format {req.format!r}")
        if req.defaults and not re.fullmatch(r"bundled|\d+(\.\d+)+", req.defaults):
            raise HTTPException(400, f"Unknown default biosphere {req.defaults!r}")
        if not path.exists():
            raise HTTPException(404, f"Nothing at {path}")
        if not database:
            raise HTTPException(400, "Give the new database a name")
        if req.project not in project_names(folder):
            raise HTTPException(404, f"No project {req.project!r} in {folder}")
        report = data / f"import-{sources.slug(database)}-unlinked.csv"
        # Adapters: labels of randonneur_data's, or files of the user's (randonneur datapackages in JSON)
        from .importing import is_adapter_file

        migrations = None if req.migrations is None else [folder_of(m) if is_adapter_file(m) else m for m in req.migrations]
        for m in migrations or []:
            if is_adapter_file(m) and not Path(m).is_file():
                raise HTTPException(404, f"Adapter file {m} not found")

        def work(job):
            import csv

            args = ["import", req.format, str(path), "--project", req.project, "--database", database,
                    "--biosphere", req.biosphere, "--data", str(data),
                    *(["--defaults", req.defaults] if req.defaults else ["--no-setup"])]
            if req.format == "simapro-csv":
                args += ["--unlinked", req.unlinked, "--report", str(report)]
                if req.link_to:
                    args += ["--link-to", *req.link_to]
                if migrations is not None:
                    args += ["--migration", *migrations]
            report.unlink(missing_ok=True)

            def unlinked_rows():
                # Exchanges left unlinked: the page lists them (and, after a stop, offers to drop them)
                if report.exists():
                    with open(report, encoding="utf-8") as f:
                        job["report"] = list(csv.DictReader(f))[:300]
                    job["report_path"] = str(report)

            try:
                lines = cli(job, folder, args, lambda line: line if not line.startswith((" ", "\t", "[")) else None)
            except ValueError as e:
                unlinked_rows()
                raise ValueError(str(e)) from None
            unlinked_rows()  # dropped ones, with --unlinked drop
            uploaded(path)
            for m in migrations or []:
                if is_adapter_file(m):
                    uploaded(Path(m))
            return {"message": lines[-1] if lines else f"{database} imported", "database": database}

        return start(f"{database} ({req.project})", ("import", folder, req.project, database), work)

    # ------------------------------------------------ ecoinvent releases, with the user's account

    session_login: dict = {}  # {"username", "password"} given without "remember": memory only

    def local_only(request: Request) -> None:
        """Passwords travel to the server unencrypted: only from this computer."""
        if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(403, "ecoinvent credentials can only be given on the computer running lca_viewer")

    def account() -> tuple[str, str] | None:
        from . import credentials

        if session_login:
            return session_login["username"], session_login["password"]
        return credentials.load()

    def account_status() -> dict:
        import importlib.util

        from . import credentials

        found = account()
        return {
            "installed": importlib.util.find_spec("ecoinvent_interface") is not None,
            "store": credentials.store(),
            "username": found and found[0],
            "remembered": bool(found) and not session_login,
        }

    @app.get("/api/ecoinvent")
    def ecoinvent_status(request: Request):
        """Whether ecoinvent credentials are known (the user name only, never the password)."""
        local_only(request)
        return account_status()

    @app.post("/api/ecoinvent/login")
    def ecoinvent_login(req: EcoinventLogin, request: Request):
        """Check the credentials with ecoinvent, then keep them (credential store, or memory)."""
        from . import credentials

        local_only(request)
        username = req.username.strip()
        if not username or not req.password:
            raise HTTPException(400, "Give your ecoinvent user name and password")
        try:
            versions = credentials.check(username, req.password)
            session_login.clear()
            if req.remember:
                credentials.save(username, req.password)
            else:
                session_login.update(username=username, password=req.password)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {**account_status(), "versions": versions}

    @app.post("/api/ecoinvent/logout")
    def ecoinvent_logout(request: Request):
        from . import credentials

        local_only(request)
        session_login.clear()
        credentials.forget()
        return account_status()

    @app.get("/api/ecoinvent/releases")
    def ecoinvent_releases(request: Request, version: str | None = None):
        """The versions this account can download, or the system models of ``version``."""
        import ecoinvent_interface as ei

        from . import credentials

        local_only(request)
        found = account()
        if not found:
            raise HTTPException(401, "Log in to ecoinvent first")
        try:
            settings = ei.Settings(username=found[0], password=found[1])
            if version:
                return {"system_models": ei.EcoinventRelease(settings).list_system_models(version)}
            return {"versions": credentials.releases(settings)}
        except Exception as e:  # network, expired account…
            raise HTTPException(502, f"ecoinvent: {e}") from None

    @app.post("/api/ecoinvent/import")
    def ecoinvent_import(req: EcoinventImport, request: Request):
        """Download a release and import it into a project (``lca-viewer ecoinvent import``)."""
        from . import credentials

        local_only(request)
        folder = folder_of(req.folder)
        project = req.project
        if project not in project_names(folder):
            raise HTTPException(404, f"No project {project!r} in {folder}")
        found = account()
        if not found:
            raise HTTPException(401, "Log in to ecoinvent first")
        secrets = credentials.environment(*found)

        def work(job):
            lines = cli(job, folder, ["ecoinvent", "import", "--project", project, "--version", req.version,
                                      "--system-model", req.system_model,
                                      *(["--biosphere", req.biosphere] if req.biosphere else [])],
                        lambda line: line if line.startswith(("Logging in", "Downloading", "Importing")) else None,
                        env=secrets)
            return {"message": lines[-1] if lines else f"ecoinvent {req.version} imported into {project}"}

        return start(f"ecoinvent {req.version} {req.system_model}", ("ecoinvent", folder, project), work)

    @app.get("/api/brightway/search")
    def search_nodes(folder: str, project: str, q: str = "", kind: str = "process", database: str | None = None,
                     limit: int = 40, filters: str | None = None):
        """Processes, resources or emissions of a project, for the exchanges of a new dataset. ``filters``:
        a predicate as in ``query`` (JSON) on name, reference_product, location and database of processes;
        name, compartment, subcompartment, unit and database of flows."""
        import json

        if kind not in ("process", "resource", "emission"):
            raise HTTPException(400, f"Unknown kind {kind!r}")
        try:
            predicate = json.loads(filters) if filters else None
        except ValueError as e:
            raise HTTPException(400, f"Invalid filters: {e}")
        try:
            return sources.search_nodes(folder_of(folder), project, q, kind, database, limit, predicate)
        except KeyError as e:
            raise HTTPException(400, f"Invalid filters: unknown field {e}")
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(404, str(e))

    @app.get("/api/brightway/options")
    def node_options(folder: str, project: str, field: str, kind: str = "process", q: str = "", limit: int = 200,
                     database: str | None = None):
        """The values of a filter field of ``/api/brightway/search``, with counts and names of codes."""
        if kind not in ("process", "resource", "emission"):
            raise HTTPException(400, f"Unknown kind {kind!r}")
        try:
            return sources.node_options(folder_of(folder), project, kind, field, q, limit, database)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ------------------------------------------------ staged changes: made together by "apply"

    def pending_file(folder: str, project: str) -> Path:
        import hashlib

        digest = hashlib.sha1(f"{folder}\n{project}".encode("utf-8")).hexdigest()[:12]
        return data / "pending" / f"{sources.slug(project)}-{digest}.json"

    def pending_read(folder: str, project: str) -> list[dict]:
        import json

        path = pending_file(folder, project)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def pending_write(folder: str, project: str, changes: list[dict]) -> list[dict]:
        import json

        path = pending_file(folder, project)
        if not changes:
            path.unlink(missing_ok=True)
            return []
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_text(json.dumps(changes), encoding="utf-8")
        part.replace(path)
        return changes

    # Locked databases: what they contain cannot be changed, whichever database is open when it is reached.
    # Kept here (not in the Brightway project): the server is what stages and applies the changes.

    def locks_file() -> Path:
        return data / "locks.json"

    def locked_all() -> dict[str, list[str]]:
        import json

        try:
            return json.loads(locks_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def locked_read(folder: str, project: str) -> list[str]:
        return locked_all().get(f"{folder}\n{project}", [])

    def check_unlocked(folder: str, project: str, databases) -> None:
        hit = sorted(set(databases) & set(locked_read(folder, project)))
        if hit:
            raise HTTPException(400, f"{', '.join(hit)} {'is' if len(hit) == 1 else 'are'} locked: unlock "
                                     f"{'it' if len(hit) == 1 else 'them'} in Load database to change what it contains")

    def touched_by(change: dict) -> set[str]:
        return {d for d in (change.get("database"), (change.get("spec") or {}).get("database")) if d}

    @app.post("/api/brightway/lock")
    def lock_database(req: LockRequest):
        """Lock or unlock a database of a project: what it contains cannot be edited, deleted or added to."""
        import json

        folder = folder_of(req.folder)
        if req.project not in project_names(folder):
            raise HTTPException(404, f"No project {req.project!r} in {folder}")
        everything = locked_all()
        names = set(everything.get(f"{folder}\n{req.project}", []))
        (names.add if req.locked else names.discard)(req.database)
        if names:
            everything[f"{folder}\n{req.project}"] = sorted(names)
        else:
            everything.pop(f"{folder}\n{req.project}", None)
        locks_file().parent.mkdir(parents=True, exist_ok=True)
        locks_file().write_text(json.dumps(everything, indent=1), encoding="utf-8")
        return {"locked": sorted(names)}

    def pending_view(folder: str, project: str, changes: list[dict] | None = None) -> dict:
        """The staged changes, with the databases they affect (theirs, and those using them: their models
        are out of date once the changes are made) and the locked ones."""
        changes = pending_read(folder, project) if changes is None else changes
        touched = sorted({d for c in changes for d in touched_by(c)})
        try:
            p = next(p for p in sources.brightway_projects(folder) if p["name"] == project)
            affected = sources.affected_databases(p, touched)
        except (StopIteration, FileNotFoundError):
            affected = touched
        return {"changes": changes, "affected": affected, "locked": locked_read(folder, project)}

    @app.get("/api/brightway/pending")
    def pending(folder: str, project: str):
        """The changes staged for a project, in the order they will be made."""
        return pending_view(folder_of(folder), project)

    @app.post("/api/brightway/pending")
    def stage(req: StageRequest):
        """Stage a change of a process (nothing is written to Brightway until it is applied). A
        change of a process already staged takes its place."""
        from . import editing

        folder, change = folder_of(req.folder), dict(req.change)
        if change.get("kind") not in ("create", "edit", "delete"):
            raise HTTPException(400, f"Unknown change {change.get('kind')!r}")
        check_unlocked(folder, req.project, touched_by(change))
        if change["kind"] == "delete" and not (change.get("database") and change.get("code")):
            raise HTTPException(400, "Say which dataset to delete")
        if change["kind"] != "delete":
            try:
                editing.check_spec(change.get("spec") or {})
            except (KeyError, ValueError, TypeError) as e:
                raise HTTPException(400, str(e) if isinstance(e, ValueError) else f"Incomplete change: {e}")
        change["id"] = change.get("id") or uuid.uuid4().hex[:10]
        change["title"] = change.get("title") or change["kind"]
        same = lambda c: c["id"] == change["id"] or (change["kind"] != "create" and c["kind"] != "create"
                                                   and (c.get("database"), c.get("code")) == (change.get("database"), change.get("code")))
        kept = [c for c in pending_read(folder, req.project) if not same(c)]
        return pending_view(folder, req.project, pending_write(folder, req.project, [*kept, change]))

    @app.post("/api/brightway/pending/remove")
    def unstage(req: PendingRequest):
        folder = folder_of(req.folder)
        kept = [c for c in pending_read(folder, req.project) if req.id and c["id"] != req.id]
        return pending_view(folder, req.project, pending_write(folder, req.project, kept))

    @app.post("/api/brightway/apply")
    def apply_pending(req: ApplyRequest):
        """Make the staged changes of a project in one transaction (``lca-viewer apply``), then build
        the model of ``database`` again and open it."""
        import json
        import tempfile

        folder = folder_of(req.folder)
        if req.project not in project_names(folder):
            raise HTTPException(404, f"No project {req.project!r} in {folder}")
        changes = pending_read(folder, req.project)
        if not changes:
            raise HTTPException(400, "No change is staged")
        check_unlocked(folder, req.project, {d for c in changes for d in touched_by(c)})  # locked since they were staged

        def work(job):
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
                json.dump(changes, f)
            try:
                job["message"] = f"Making {len(changes)} change{'s' if len(changes) > 1 else ''} in {req.project}"
                cli(job, folder, ["apply", f.name, "--project", req.project], lambda line: None)
            finally:
                Path(f.name).unlink(missing_ok=True)
            pending_write(folder, req.project, [c for c in pending_read(folder, req.project) if c not in changes])
            done = {"applied": len(changes), "message": f"{len(changes)} change{'s' if len(changes) > 1 else ''} made"}
            affected = pending_view(folder, req.project, changes)["affected"]
            done["affected"] = affected
            if req.database and req.database in affected and not req.rebuild:
                done["message"] += f"; {req.database} has to be built again to show them"
            if req.database and req.database in affected and req.rebuild:
                try:
                    done.update(run(job, LoadRequest(brightway=folder, project=req.project, database=req.database,
                                                     rebuild=True, reopen=True)))
                except ValueError as e:
                    raise ValueError(f"The changes were made, but building {req.database} failed: {e}") from None
            return done

        return start(f"{req.project}", ("apply", folder, req.project), work)

    @app.get("/api/brightway/dataset")
    def read_dataset(folder: str, project: str, database: str, code: str):
        """A process as the form takes it, to copy it or edit it (with what uses it); ``locked``: its
        database is locked, so it cannot be changed."""
        folder = folder_of(folder)
        try:
            read = sources.read_dataset(folder, project, database, code)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(404, str(e))
        return {**read, "locked": read["database"] in locked_read(folder, project)}

    @app.post("/api/brightway/dataset/delete")
    def delete_dataset(req: DeleteDatasetRequest):
        """Delete a dataset written by this app, if nothing uses it (``lca-viewer delete-dataset``)."""
        import json

        folder = folder_of(req.folder)
        if req.project not in project_names(folder):
            raise HTTPException(404, f"No project {req.project!r} in {folder}")
        check_unlocked(folder, req.project, {req.database})
        try:
            sources.read_dataset(folder, req.project, req.database, req.code)
        except ValueError as e:
            raise HTTPException(404, str(e))

        def work(job):
            lines = cli(job, folder, ["delete-dataset", "--project", req.project, "--database", req.database, "--code", req.code],
                        lambda line: None)
            result = next((json.loads(line[7:]) for line in reversed(lines) if line.startswith("RESULT ")), {})
            return {"result": result, "message": f"{result.get('deleted', 'The dataset')} deleted from {req.database}"}

        return start(f"{req.database} / {req.code}", ("delete-dataset", folder, req.project, req.database, req.code), work)

    @app.post("/api/brightway/dataset")
    def write_dataset(req: DatasetRequest):
        """Write a new dataset into a project, or write one again in place when ``spec`` has its
        ``code`` (``lca-viewer dataset``)."""
        import json
        import tempfile

        folder = folder_of(req.folder)
        if req.project not in project_names(folder):
            raise HTTPException(404, f"No project {req.project!r} in {folder}")
        check_unlocked(folder, req.project, touched_by({"spec": req.spec}))

        def work(job):
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
                json.dump(req.spec, f)
            try:
                lines = cli(job, folder, ["dataset", f.name, "--project", req.project], lambda line: None)
            finally:
                Path(f.name).unlink(missing_ok=True)
            result = next((json.loads(line[7:]) for line in reversed(lines) if line.startswith("RESULT ")), {})
            return {"result": result, "message": f"{req.spec['process']['name']} saved in {result.get('database')}"}

        name = (req.spec.get("process") or {}).get("name") or "dataset"
        return start(name, ("dataset", folder, req.project, name, uuid.uuid4().hex), work)

    page_headers = {"Cache-Control": "no-cache"}  # what "/" shows changes with the databases loaded

    @app.get("/")
    def index():
        if loaded:
            return RedirectResponse(f"/db/{next(iter(loaded))}/", headers=page_headers)
        return FileResponse(STATIC / "index.html", headers=page_headers)

    @app.get("/db/{key}/")
    def not_loaded(key: str):
        """A database that is not loaded (any more), e.g. after a restart: the page offers to load one."""
        return FileResponse(STATIC / "index.html", headers=page_headers)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def app_for(models: list[dict], data=None) -> FastAPI:
    """App serving ``{"path", "title", "default_root"}`` models (loaded here), able to load others."""
    return create_library(data, models)


def app_from_env() -> FastAPI:
    """App factory for ``uvicorn --reload``: settings come from environment variables."""
    import json

    if "LCA_VIEWER_MODEL" in os.environ:
        model = LCAModel.load(os.environ["LCA_VIEWER_MODEL"])
        return create_app(
            model,
            title=os.environ.get("LCA_VIEWER_TITLE", ""),
            default_root=os.environ.get("LCA_VIEWER_DEFAULT_ROOT") or None,
        )
    return app_for(json.loads(os.environ.get("LCA_VIEWER_MODELS", "[]")), os.environ.get("LCA_VIEWER_DATA"))
