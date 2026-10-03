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

from .analysis import direct_contribution, first_match_contribution
from .model import LCAModel
from .query import derived_activities, search, suggest_values
from .tree import Tree
from .view import View

STATIC = Path(__file__).parent / "static"

QUERY_FIELDS = [
    "name", "reference_product", "location", "unit", "activity_type",
    "isic", "isic_division", "cpc", "cpc_section", "database",
]
GROUP_BY_FIELDS = ["location", "activity_type", "isic_division", "cpc_section", "reference_product", "unit", "database"]


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
    methods: list[int] = []
    """Methods whose impacts are returned, in this order (default: ``method`` only)."""


class SearchRequest(BaseModel):
    text: str = ""
    predicate: dict | None = None
    limit: int = 50


class GlobalRequest(BaseModel):
    root: int
    amount: float = 1.0
    groups: dict[str, dict]
    rule: str = "first_match"


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
    root = _find(activities, *default_root.split("|", 1)) if default_root else None
    try:
        default_method = model.method_index(("EF v3.1", "climate change", "global warming potential (GWP100)"))
    except KeyError:
        default_method = 0

    META_FIELDS = ["name", "reference_product", "location", "unit", "activity_type", "isic", "cpc", "database"]

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
            elif r.kind == "direct" and len(r.node.path) > 1:
                out["meta"] = meta(r.node.path[-2])  # the process emitting
            if r.members is not None:
                out["members"] = [serialise(m) for m in r.members]
            return out

        rows = [serialise(r) for r in rendered]
        return {"methods": methods, "rows": rows}

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

    @app.post("/api/global")
    def global_contribution(req: GlobalRequest):
        fn = first_match_contribution if req.rule == "first_match" else direct_contribution
        result = fn(model, req.root, req.groups, req.amount)
        return {name: _floats(v) for name, v in result.items()}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


def app_from_env() -> FastAPI:
    """App factory for ``uvicorn --reload``: settings come from environment variables."""
    import os

    model = LCAModel.load(os.environ["LCA_VIEWER_MODEL"])
    return create_app(
        model,
        title=os.environ.get("LCA_VIEWER_TITLE", ""),
        default_root=os.environ.get("LCA_VIEWER_DEFAULT_ROOT") or None,
    )
