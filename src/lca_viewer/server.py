"""Local web server: a stateless JSON API over :class:`View`, plus the UI.

The client owns the view state (expanded rows, group rules, cut-off) and
sends it with each request; the server only holds the model.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analysis import direct_contribution, first_match_contribution, group_contributors, main_path, matched
from .model import LCAModel
from .query import derived_activities, derived_flows, search, suggest_values
from .tree import Tree
from .view import View

STATIC = Path(__file__).parent / "static"

QUERY_FIELDS = [
    "name", "reference_product", "location", "unit", "activity_type",
    "isic", "isic_division", "cpc", "cpc_section", "flow_type", "database",
]
GROUP_BY_FIELDS = ["location", "activity_type", "flow_type", "isic_division", "cpc_section", "reference_product", "unit", "database"]


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
    group_products: bool = False
    methods: list[int] = []
    """Methods whose impacts are returned, in this order (default: ``method`` only)."""


class SearchRequest(BaseModel):
    text: str = ""
    predicate: dict | None = None
    limit: int = 50


class DirectRequest(ViewState):
    row: str
    limit: int = 25


class GlobalRequest(BaseModel):
    root: int
    amount: float = 1.0
    groups: dict[str, dict]
    rule: str = "first_match"


class ContributorsRequest(GlobalRequest):
    group: str
    method: int = 0
    limit: int = 50


class LocateRequest(ViewState):
    product: int
    groups: dict[str, dict] = {}
    rule: str = "direct"
    """With "first_match", the path avoids the supply chains of activities matched by a group."""


def _floats(values: np.ndarray) -> list[float]:
    return [float(v) for v in values]


def _find(activities, name: str, location: str) -> int | None:
    match = activities.index[(activities["name"] == name) & (activities["location"] == location)]
    return int(match[0]) if len(match) else None


def create_app(model: LCAModel, title: str = "", default_root: str | None = None) -> FastAPI:
    """``default_root`` is ``"activity name|location"``."""
    app = FastAPI(title="lca_viewer")
    model.G  # factorise at start-up rather than on the first request
    activities = derived_activities(model)
    flows = derived_flows(model)
    root = _find(activities, *default_root.split("|", 1)) if default_root else None
    try:
        default_method = model.method_index(("EF v3.1", "climate change", "global warming potential (GWP100)"))
    except KeyError:
        default_method = 0

    META_FIELDS = ["name", "reference_product", "location", "unit", "activity_type", "isic", "cpc", "database"]
    rows_by_product = model.A.tocsr()  # row j: the activities using product j
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
        return {"index": int(i), **{f: (None if v is None or pd.isna(v) else str(v)) for f, v in values.items()}}

    def describe(i: int) -> dict:
        r = activities.iloc[i]
        return {"index": int(i), "name": r["name"], "reference_product": r["reference_product"],
                "location": r["location"], "unit": r["unit"]}

    @app.get("/api/info")
    def info():
        return {
            "title": title,
            "activities": model.size,
            "methods": [{"name": list(m.name), "unit": m.unit} for m in model.methods],
            "query_fields": QUERY_FIELDS,
            "group_by_fields": GROUP_BY_FIELDS,
            "default_root": describe(root) if root is not None else None,
            "default_method": default_method,
        }

    @app.post("/api/search")
    def search_activities(req: SearchRequest):
        try:
            found = search(activities, req.text, req.predicate)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, f"Invalid filter: {e}")
        return {"total": int(len(found)), "results": [describe(i) for i in found[: req.limit]]}

    @app.get("/api/values/{field}")
    def values(field: str, q: str = "", limit: int = 100):
        if field not in QUERY_FIELDS:
            raise HTTPException(404, f"Unknown field {field}")
        return suggest_values(activities, field, q, limit)

    @app.post("/api/rows")
    def rows(state: ViewState):
        try:
            view = View.from_dict(model, state.model_dump())
            rendered = view.rows()
        except (KeyError, IndexError, ValueError) as e:
            raise HTTPException(400, f"Invalid view: {e}")
        methods = [state.method] + [m for m in state.methods if m != state.method]

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
            elif r.kind == "direct" and len(r.node.path) > 1 and isinstance(r.node.path[-2], int):
                out["meta"] = meta(r.node.path[-2])  # the process emitting
            elif r.kind == "merged":
                out["products"] = [int(m.product) for m in r.merged]
                out["metas"] = [meta(m.product) for m in r.merged]
            elif r.kind == "flow":
                f = flows.iloc[r.node.flow]
                out["flow"] = {"index": int(r.node.flow), "name": f["name"], "compartment": f["compartment"],
                               "subcompartment": f["subcompartment"]}
            if r.kind != "direct" and r.sources is not None:
                out["emitters"] = [meta(j) for j, _ in r.sources]  # processes whose direct emissions these are
            if r.members is not None:
                out["members"] = [serialise(m) for m in r.members]
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

    @app.post("/api/global")
    def global_contribution(req: GlobalRequest):
        fn = first_match_contribution if req.rule == "first_match" else direct_contribution
        result = fn(model, req.root, req.groups, req.amount)
        return {name: _floats(v) for name, v in result.items()}

    @app.post("/api/global/contributors")
    def global_contributors(req: ContributorsRequest):
        """Activities making up one group of the whole-supply-chain analysis, largest first."""
        try:
            members, values = group_contributors(model, req.root, req.groups, req.group, req.rule, req.amount)
        except ValueError as e:
            raise HTTPException(400, f"Unknown group {req.group!r}") from e
        impacts = values[:, req.method]
        order = np.argsort(-np.abs(impacts))
        shown, rest = order[: req.limit], order[req.limit:]
        return {
            "group": req.group,
            "total": float(impacts.sum()),
            "count": int(len(members)),
            # Under "first match", matched activities bring their supply chain; others only their own emissions
            "cumulative": req.rule == "first_match" and req.group != "other",
            "contributors": [{**meta(int(members[i])), "impact": float(impacts[i])} for i in shown],
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


def create_multi_app(entries: list[dict]) -> FastAPI:
    """Several models in one server, each under ``/db/<key>/`` with the API of :func:`create_app`.

    ``entries`` are ``{"key", "model", "title", "default_root"}``. ``/`` opens the first one and
    ``/api/databases`` lists them for the database picker; switching database reloads the page.
    """
    from fastapi.responses import RedirectResponse

    app = FastAPI(title="lca_viewer")
    listing = [{"key": e["key"], "title": e.get("title") or e["key"], "activities": e["model"].size} for e in entries]

    @app.get("/api/databases")
    def databases():
        return listing

    @app.get("/")
    def index():
        return RedirectResponse(f"/db/{entries[0]['key']}/")

    for e in entries:
        sub = create_app(e["model"], title=e.get("title") or e["key"], default_root=e.get("default_root"))
        app.mount(f"/db/{e['key']}", sub)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def model_key(path) -> str:
    """URL-safe name of a model directory, used in ``/db/<key>/``."""
    import re

    return re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(path).name).strip("-") or "model"


def app_for(models: list[dict]) -> FastAPI:
    """App for one or several ``{"path", "title", "default_root"}`` models (loaded here)."""
    loaded = [{**m, "key": model_key(m["path"]), "model": LCAModel.load(m["path"])} for m in models]
    if len(loaded) == 1:
        m = loaded[0]
        return create_app(m["model"], title=m.get("title") or "", default_root=m.get("default_root"))
    keys = [m["key"] for m in loaded]
    if len(set(keys)) != len(keys):
        raise ValueError(f"Model directories must have different names, got {keys}")
    return create_multi_app(loaded)


def app_from_env() -> FastAPI:
    """App factory for ``uvicorn --reload``: settings come from environment variables."""
    import json
    import os

    if "LCA_VIEWER_MODELS" in os.environ:
        return app_for(json.loads(os.environ["LCA_VIEWER_MODELS"]))
    model = LCAModel.load(os.environ["LCA_VIEWER_MODEL"])
    return create_app(
        model,
        title=os.environ.get("LCA_VIEWER_TITLE", ""),
        default_root=os.environ.get("LCA_VIEWER_DEFAULT_ROOT") or None,
    )
