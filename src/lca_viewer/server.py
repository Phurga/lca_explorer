# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local web server: a stateless JSON API over :class:`View`, plus the UI.

The client owns the view state (expanded rows, group rules, cut-off) and
sends it with each request; the server only holds the model.
"""

from __future__ import annotations

import mimetypes
import os
import re
import sqlite3
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analysis import (OTHER, flow_contribution, flow_contributors, group_contributors, main_path, matched,
                       method_coverage, supply_chain_contribution, uncharacterised)
from .analysis import compare_methods, direct_emitters, unlinked_factors
from .codes import location_name
from .model import LCAModel
from .query import (EXCHANGE_FIELDS, EXCHANGE_TYPES, FLOW_EXCHANGE_TYPES, derived_activities, derived_flows, evaluate,
                    field_options, search, suggest_values, without_exchange)
from .tree import Tree
from .view import View, below_cutoff

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
GROUP_BY_FIELDS = ["location", "activity_type", "flow_type", "isic_division", "cpc_section", "reference_product", "unit", "database",
                   "exchange_type"]
# Elementary flows (rules with ``target="flows"``)
FLOW_FIELDS = ["name", "compartment", "subcompartment", "unit"]
# The rules of the tree can also test the exchange bringing a row in: its type and its amount (``query.EXCHANGE_FIELDS``)
RULE_QUERY_FIELDS = [*QUERY_FIELDS, *EXCHANGE_FIELDS]
RULE_FLOW_FIELDS = [*FLOW_FIELDS, *EXCHANGE_FIELDS]
# Recorded data of the exchange supplying a row that the list can show as columns (comments are sent apart)
EXCHANGE_COLUMNS = ["uncertainty type", "loc", "scale", "scale without pedigree", "shape", "minimum", "maximum",
                    "pedigree", "formula", "variable name"]


RULES = ("direct", "first_match")
"""Attribution rules (see ``analysis``)."""


class Cut(BaseModel):
    """Which rows of a list are shown, the others summed in one row: as the cut-off of the Custom view (see
    ``view.below_cutoff``): ``top`` the ``value`` largest (all under 1), ``total`` the rows under ``value`` (a fraction)
    of the total go to others, ``cumulative`` the largest until together ``value`` of all the rows."""
    mode: str = "top"
    value: float = 25


class ViewState(BaseModel):
    root: list[int]
    amount: float = 1.0
    expanded: list[str] = []
    rules: list[dict] = []
    attribution: str = "direct"
    """``first_match``: the rules do not group below the first row a rule takes (see ``View.attribution``)."""
    colour_rules: list[dict] = []
    """The rules that colour the rows (each row says which of them matches it, grouped or not), whether or not they group."""
    cutoff: float = 0.0
    cutoff_basis: str = "parent"
    method: int = 0
    pinned: list[str] = []
    others: list[str] = []
    merges: list[dict] = []
    ungrouped: list[str] = []
    substitutions: dict[str, int] = {}
    group_products: bool = False
    collapse_singletons: bool = False
    """A process made of only one process is shown as that process (see ``View.collapse_singletons``)."""
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


class GlobalRequest(BaseModel):
    root: int
    amount: float = 1.0
    groups: dict[str, dict]
    rule: str = "first_match"
    flow_groups: dict[str, dict] = {}
    """Groups of elementary flows."""
    flow_owners: dict[str, dict] = {}
    """For a flow group (by name), a predicate over activities: only the flows these emit are in the group."""
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
    flow_owners: dict[str, dict] = {}
    order: list[str] = []
    """The names of the groups and flow groups in the order of the rules, as in ``GlobalRequest``."""
    rule: str = "first_match"
    method: int
    """The method the groups are split for."""
    methods: list[int] = []
    """Methods whose total score is returned for every entry (the heat map)."""
    basis: str = "groups"
    """"groups": the groups of ``groups`` / ``flow_groups`` and *other*; "flows": the elementary flows (one
    per substance); "activities": what is inside each product. For these two, the rows of all the entries together are
    cut by ``cut`` and *other* takes the rest."""
    cut: Cut = Cut(value=15)


class ContributorsRequest(GlobalRequest):
    group: str
    method: int = 0
    cut: Cut = Cut(value=50)


class FlowsRequest(BaseModel):
    root: int
    amount: float = 1.0
    method: int = 0
    by: str = "substance"
    """"substance": compartments summed (one row per name and unit); "flow": one row per flow."""
    keys: list[str] | None = None
    """Group by these fields instead (``name``, ``compartment``, ``subcompartment``): one row per combination (the unit
    goes with the name)."""
    cut: Cut = Cut()


class FlowContributorsRequest(BaseModel):
    root: int
    amount: float = 1.0
    method: int = 0
    flows: list[int]
    cut: Cut = Cut(value=50)


class UncharacterisedRequest(BaseModel):
    root: int
    amount: float = 1.0
    method: int = 0
    reference: int | None = None
    """A method whose factors are shown next to the flows (e.g. the one the new method replaces)."""
    limit: int = 200
    """At most this many flows per unit (the largest amounts)."""
    query: str = ""
    """Only the flows whose name or compartment has all these words."""


class ProcessFlowsRequest(BaseModel):
    root: int
    amount: float = 1.0
    method: int = 0
    activity: int
    """The process, in the supply chain of ``root``, whose own emissions are split by elementary flow."""
    cut: Cut = Cut()


class CategoryRequest(BaseModel):
    """The flows of an impact category (``method``), alone or against another one (``compare``)."""
    method: int
    compare: int | None = None
    root: int | None = None
    """A functional unit (with ``amount``): the amount of each flow in its inventory and its contribution. None: the
    whole database (factors and emitters only). The pop-up does not use it for now: always the whole database."""
    amount: float = 1.0
    unlinked_limit: int = 300
    """At most this many of the factors of each method on flows outside the model (the largest first)."""


class LocateRequest(ViewState):
    product: int
    groups: dict[str, dict] = {}
    rule: str = "direct"
    """With "first_match", the path avoids the supply chains of activities matched by a group."""


def _floats(values: np.ndarray) -> list[float]:
    return [float(v) for v in values]


def _listed(impacts, cut: Cut, total: float | None = None) -> tuple[list[int], list[int], dict]:
    """The positions of ``impacts`` shown by ``cut``, largest |impact| first, the rest, and the *others* row of the rest
    (``{"count", "impact"}``). ``total``: the score the shares are of (default: the sum). As in the tree, a single row
    under the cut-off is shown as itself."""
    impacts = np.asarray(impacts, dtype=float)
    order = [int(i) for i in np.argsort(-np.abs(impacts), kind="stable")]
    try:
        below = below_cutoff([abs(float(impacts[i])) for i in order], cut.mode, cut.value,
                             abs(float(impacts.sum() if total is None else total)))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if sum(below) == 1:
        below = [False] * len(below)
    shown = [i for i, b in zip(order, below) if not b]
    rest = [i for i, b in zip(order, below) if b]
    return shown, rest, {"count": len(rest), "impact": float(impacts[rest].sum()) if rest else 0.0}


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
    return _default_index([m.name if m.cfs.any() else () for m in model.methods])


_METHOD_SOURCE = re.compile(r"^[a-z]\w*-\d+(\.\d+)+$", re.IGNORECASE)


def _method_family(name) -> tuple[str, str]:
    """The family of a method (its source, its family): Brightway names a method (family, category, indicator...); those
    that come with an ecoinvent release have the release first ("ecoinvent-3.10.1", family, category, indicator), as
    ``methodPath`` of the app reads them. The methods of the app's library have "library" (or "library/<source>") first."""
    from .methods_library import is_library

    name = [str(part) for part in name]
    if (len(name) >= 4 and _METHOD_SOURCE.match(name[0])) or is_library(name):
        return name[0], name[1]
    return "", name[0] if name else ""


def _default_index(names: list) -> int:
    """Of :func:`_default_method`, on the method names (an empty one: a method to pass over)."""
    names = [tuple(str(part).lower() for part in name) for name in names]
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
    # G is read from its cache, or computed (10 s on ecoinvent), before the first tree is asked for. A model loaded from
    # disk does it in a thread, so that the page opens meanwhile; a model in memory (tests) at once.
    if model._directory is not None:
        import threading

        threading.Thread(target=lambda: model.G, daemon=True).start()
    else:
        model.G
    activities = derived_activities(model)
    flows = derived_flows(model)
    root = _find(activities, *default_root.split("|", 1)) if default_root else None
    default_method = _default_method(model)
    layouts = {}  # by number of methods: methods added to the model change it

    def layout_of() -> str:
        n = len(model.methods)
        if n not in layouts:
            layouts[n] = _layout(model)
        return layouts[n]

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

    def check(activity=(), method=(), rule=()) -> None:
        """The activities, methods and attribution rules a request names exist: the same answer at every endpoint
        (a negative index would else silently be one from the end)."""
        for j in activity:
            if j is not None and not 0 <= j < model.size:
                raise HTTPException(404, f"No activity {j}")
        for k in method:
            if k is not None and not 0 <= k < len(model.methods):
                raise HTTPException(404, f"No method {k}")
        for r in rule:
            if r not in RULES:
                raise HTTPException(400, f"Unknown rule {r!r}")

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
            "layout": layout_of(),
            "methods": [{"name": list(m.name), "unit": m.unit} for m in model.methods],
            "query_fields": QUERY_FIELDS,
            "group_by_fields": GROUP_BY_FIELDS,
            "flow_query_fields": FLOW_FIELDS,
            "rule_query_fields": RULE_QUERY_FIELDS,
            "rule_flow_query_fields": RULE_FLOW_FIELDS,
            "default_root": describe(root) if root is not None else None,
            "default_method": default_method,
        }

    @app.post("/api/search")
    def search_activities(req: SearchRequest):
        predicate = without_exchange(req.predicate)  # a rule's preview: what may match, whatever the exchange
        try:
            if req.target == "flows":
                found = np.flatnonzero(evaluate(predicate, flows)) if predicate else np.arange(len(flows))
                return {"total": int(len(found)), "results": [
                    {"index": int(i), "name": flows["name"].iat[i], "location": flows["place"].iat[i]}
                    for i in found[: req.limit]]}
            found = search(activities, req.text, predicate)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, f"Invalid filter: {e}")
        return {"total": int(len(found)), "results": [describe(i) for i in found[: req.limit]]}

    @app.get("/api/values/{field}")
    def values(field: str, q: str = "", limit: int = 100, target: str | None = None):
        if field in EXCHANGE_FIELDS:
            return [t for t in (FLOW_EXCHANGE_TYPES if target == "flows" else EXCHANGE_TYPES)
                    if field == "exchange_type" and q.lower() in t]
        if field not in (FLOW_FIELDS if target == "flows" else QUERY_FIELDS):
            raise HTTPException(404, f"Unknown field {field}")
        return suggest_values(flows if target == "flows" else activities, field, q, limit)

    @app.get("/api/options/{field}")
    def options(field: str, q: str = "", limit: int = 200, target: str | None = None):
        """The values of a filter field with how many activities (flows) have each and, for codes
        (location, ISIC, CPC), what they stand for: the helper of the filter builder."""
        if field in EXCHANGE_FIELDS:  # of an exchange, not of the activities: the types, no count
            types = (FLOW_EXCHANGE_TYPES if target == "flows" else EXCHANGE_TYPES) if field == "exchange_type" else ()
            items = [{"value": t, "count": ""} for t in types if q.lower() in t]
            return {"items": items, "named": False, "total": len(items)}
        if field not in (FLOW_FIELDS if target == "flows" else QUERY_FIELDS):
            raise HTTPException(404, f"Unknown field {field}")
        return field_options(flows if target == "flows" else activities, field, q, limit)

    @app.post("/api/rows")
    def rows(state: ViewState):
        check(activity=state.root, method=[state.method, *state.methods], rule=[state.attribution])
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
                return {"flow": int(node.flow), "name": f["name"], "location": f["place"], "unit": f["unit"],
                        "compartment": f["compartment"], "subcompartment": f["subcompartment"], "waste": False, **common}
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
                found += [e for e in dataset(j) if e["type"] != "biosphere" and e["row"] == r.node.product]
            return found

        def dataset(j) -> list[dict]:
            if j not in datasets:
                datasets[j] = model.exchange_details(int(j))
            return datasets[j]

        def emitted(r) -> list[dict]:
            """The exchange(s) of a flow row: the flow in the dataset(s) of the process(es) emitting it."""
            return [e for j, _ in r.sources or [] for e in dataset(j) if e["type"] == "biosphere" and e["row"] == r.node.flow]

        def exchange_columns(out: dict, recorded: list[dict]) -> None:
            """The comment(s) of the exchange(s) a row stands for, and what the exchange columns show (uncertainty,
            pedigree, formula), one entry per exchange: the same for a process (supplied) and a flow (emitted)."""
            comments: list[str] = []
            for e in recorded:
                text = (e.get("comment") or "").strip()
                if text and text not in comments:
                    comments.append(text)
            if comments:
                out["comment"] = "\n".join(comments)
            shown = [{k: e[k] for k in EXCHANGE_COLUMNS if e.get(k) not in (None, "")} for e in recorded]
            if any(shown):
                out["exchanges"] = shown

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
            matched = r.rule_group if r.kind in ("group", "merged") else (
                view.matching_rule(r) if r.kind in ("process", "flow") else None)
            if matched:
                out["rule"] = matched  # the rule that makes the group or matches the row: its colour, grouped or not
            if r.kind == "process":
                out["meta"] = meta(r.node.product)
                out["replaced"] = r.replaced
                if r.chain:
                    out["chain"] = [meta(n.product) for n in r.chain]  # the processes made of this one alone, first first
                exchange_columns(out, supplying(r))
            elif r.kind == "direct" and len(r.node.path) > 1 and isinstance(r.node.path[-2], int):
                out["meta"] = meta(r.node.path[-2])  # the process emitting
            elif r.kind == "merged":
                out["products"] = [int(m.product) for m in r.merged]
                out["metas"] = [meta(m.product) for m in r.merged]
                out["parts"] = [part(m) for m in r.merged]
            elif r.kind == "group":
                out["parts"] = [part(m) for m in r.node_members]
                out["by_product"] = r.by_product
                if r.sources is None:
                    out["products"] = [int(m.product) for m in r.node_members]  # a group of processes can be merged
            elif r.kind == "flow":
                f = flows.iloc[r.node.flow]
                out["flow"] = {"index": int(r.node.flow), "name": f["name"], "compartment": f["compartment"],
                               "subcompartment": f["subcompartment"]}
                exchange_columns(out, emitted(r))  # comments were those of technosphere exchanges only
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

    @app.get("/api/activity/{index}")
    def activity(index: int, method: int | None = None, flows: int = 8):
        """Metadata, description and per-unit results of one activity, for the info pop-up."""
        check(activity=[index])
        k = default_method if method is None else method
        per_unit = 1 / model.diagonal[index]  # per unit of reference product
        column = model.B[:, index].toarray().ravel() * model.methods[k].cfs * per_unit
        top = np.argsort(-np.abs(column))[:flows]
        categories = model.flows["categories"] if "categories" in model.flows else None
        info = meta(index)
        return {
            **info,
            # what the location code stands for (FR: France, RER: Europe), "" when it reads as it is
            "location_name": location_name(info["location"]) if info.get("location") else "",
            "details": model.activity_details(index),
            "method": k,
            "cumulative_per_unit": float(model.G[index, k]),
            "direct_per_unit": float(model.direct(index)[k] * per_unit),
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
        check(activity=[index])
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
                # waste: the supplier is a waste treatment (its production is negative in A)
                inputs.append({**meta(row), "type": kind, **e, "impact": impact, "waste": bool(model.diagonal[row] < 0)})
        inputs.sort(key=lambda x: -abs(x["impact"]))
        emissions.sort(key=lambda x: -abs(x["impact"]))
        return {
            "available": model.has_exchanges, "method": k,
            "production": production[0] if production else None, "inputs": inputs, "flows": emissions,
        }

    @app.get("/api/flow/{index}")
    def flow_info(index: int, method: int | None = None, activities: str = "", folder: str | None = None,
                  project: str | None = None):
        """An elementary flow: metadata, its characterisation factor in every method where it
        has one and, for ``activities`` (comma-separated), the exchanges of this flow in their
        datasets (amount, comment, uncertainty)."""
        check_flow(index)
        check(method=[method])
        k = default_method if method is None else method
        f = flows.iloc[index]
        # Rank of the flow in each method that characterises it: by |factor| among the flows of the model it characterises
        CF = abs(model.CF)
        CF.eliminate_zeros()
        mine = CF[:, [index]].toarray().ravel()
        characterised = np.flatnonzero(mine)
        theirs = CF[characterised]  # sparse: the factors of the methods that characterise it
        counts = np.diff(theirs.indptr)
        above = theirs.data > np.repeat(mine[characterised], counts)
        ranks = np.bincount(np.repeat(np.arange(len(characterised)), counts), weights=above,
                            minlength=len(characterised)).astype(int) + 1
        factors = [{"method": int(i), "cf": float(model.methods[i].cfs[index]), "rank": int(r), "of": int(n)}
                   for i, r, n in zip(characterised, ranks, counts)]
        exchanges = []
        B = flow_rows()
        for j in (int(a) for a in activities.split(",") if a.strip()):
            check(activity=[j])
            records = [{k_: v for k_, v in e.items() if k_ not in ("type", "row")}
                       for e in model.exchange_details(j) if e.get("type") == "biosphere" and e.get("row") == index]
            exchanges.append({"activity": meta(j), "exchanges": records, "amount": float(B[index, j])})
        description = flow_description(index, folder, project)
        siblings = np.flatnonzero((flows["name"].to_numpy() == f["name"]) & (flows["unit"].to_numpy() == f["unit"]))
        return {
            "index": index, "name": f["name"], "compartment": f["compartment"], "subcompartment": f["subcompartment"],
            "place": f["place"], "unit": f["unit"], "id": None if "id" not in flows or pd.isna(f["id"]) else str(f["id"]),
            "resource": is_resource(index), "method": k, "cf": float(model.methods[k].cfs[index]), "factors": factors,
            "location": description.pop("location", None), "description": description, "name_parts": name_parts(f["name"]),
            "emitters": int(B[index].nnz), "same_substance": int(len(siblings) - 1),
            "exchanges_available": model.has_exchanges, "exchanges": exchanges,
        }

    # ------------------------------------------------ a flow (abstract: no quantity), its panels

    flow_matrix = {}

    def flow_rows() -> sp.csr_matrix:
        """``B`` by rows (row f: the activities with an exchange of flow f), made once."""
        if "B" not in flow_matrix:
            flow_matrix["B"] = model.B.tocsr()
        return flow_matrix["B"]

    def check_flow(index: int) -> None:
        if not 0 <= index < len(flows):
            raise HTTPException(404, f"No flow {index}")

    def is_resource(index: int) -> bool:
        """A flow taken from nature (its compartment says so), else an emission."""
        c = str(flows["compartment"].iat[index]).lower()
        return c.startswith("natural resource") or c in ("resource", "raw", "raw materials", "land")

    def flow_description(index: int, folder: str | None, project: str | None) -> dict:
        """CAS number, formula, synonyms, location and comment of a flow: from the model (built with them), else from
        the record of the Brightway project it was built from (``folder``, ``project``: models built before)."""
        f = flows.iloc[index]
        record = {}
        if folder and project and "id" in flows and not pd.isna(f["id"]):
            try:
                from . import sources

                base = str(Path(folder.strip().strip("'\"")).expanduser().resolve())
                record = sources.flow_record(base, project, int(f["id"]))
            except (OSError, ValueError, sqlite3.Error):
                record = {}
        out = {}
        for field, key in (("cas", "CAS number"), ("formula", "formula"), ("synonyms", "synonyms"),
                           ("location", "location"), ("comment", "comment")):
            v = f.get(field) if field in flows else None
            if v is None or (not isinstance(v, (tuple, list)) and pd.isna(v)) or (isinstance(v, (tuple, list)) and not v):
                v = record.get(key) or (record.get("chemical formula") if field == "formula" else None)
            if isinstance(v, (tuple, list)):
                v = [str(x) for x in v if x] or None
            elif v is not None and str(v).strip() == "":
                v = None
            if v is not None:
                out[field] = v if isinstance(v, list) else str(v)
        return out

    def name_parts(name: str) -> dict:
        """What the name of a flow says by its structure (ecoinvent: "Substance, qualifier, …"): the substance and
        its qualifiers (fossil, biogenic, ion, in ground, …)."""
        parts = [p.strip() for p in str(name).split(", ") if p.strip()]  # ", ": not the commas of "1,4-Butanediol"
        return {"substance": parts[0] if parts else str(name), "qualifiers": parts[1:]}

    def flow_meta(index: int) -> dict:
        return {"flow": int(index), "name": flows["name"].iat[index], "compartment": flows["place"].iat[index],
                "unit": flows["unit"].iat[index]}

    @app.get("/api/flow/{index}/emitters")
    def flow_emitters(index: int, root: int | None = None, amount: float = 1.0, limit: int = 50):
        """The processes with an exchange of this flow (*Emitted by*, *Taken by* for a resource): the amount per unit
        of their product, largest first; with ``root``, also the amount in that functional unit's supply chain
        (the inventory flow: B·s), and they are ranked by it."""
        check_flow(index)
        check(activity=[root])
        row = flow_rows()[index]
        users, values = row.indices, row.data
        keep = values != 0
        users, values = users[keep], values[keep]
        per_unit = values / model.diagonal[users]
        if root is not None:
            s = supply(root, amount)
            scoped = values * s[users]
            order = np.lexsort((-np.abs(per_unit), -np.abs(scoped)))
            total = float(scoped.sum())
        else:
            scoped, total = np.zeros(len(users)), None
            order = np.argsort(-np.abs(per_unit))
        shown = order[:limit]
        return {
            **flow_meta(index), "resource": is_resource(index), "count": int(len(users)),
            "in_supply_chain": int(np.count_nonzero(scoped)) if root is not None else None,
            "total": total,
            "emitters": [{**meta(int(users[i])), "per_unit": float(per_unit[i]),
                          "amount": float(scoped[i]) if root is not None else None,
                          "share": float(scoped[i] / total) if root is not None and total else None}
                         for i in shown],
        }

    @app.get("/api/flow/{index}/same-substance")
    def flow_same_substance(index: int, method: int | None = None):
        """The flows of the same substance (name and unit) in other compartments, with their factor in ``method``
        (default: the default method)."""
        check_flow(index)
        check(method=[method])
        ks = [default_method if method is None else method]
        name, unit = flows["name"].iat[index], flows["unit"].iat[index]
        same = np.flatnonzero((flows["name"].to_numpy() == name) & (flows["unit"].to_numpy() == unit))
        B = flow_rows()
        return {
            **flow_meta(index), "methods": ks,
            "flows": [{**flow_meta(int(g)), "current": int(g) == index, "emitters": int(B[g].nnz),
                       "cfs": [float(model.methods[k].cfs[g]) for k in ks]} for g in same],
        }

    @app.get("/api/activity/{index}/users")
    def activity_users(index: int, root: int | None = None, amount: float = 1.0, method: int | None = None, limit: int = 15):
        """Activities using the product of ``index`` ("used by"); with ``root``, how much of
        it each one takes in that functional unit's supply chain, largest first."""
        check(activity=[index, root])
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
        check(activity=[index])
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

    @app.get("/api/activity/{index}/characterisation")
    def activity_characterisation(index: int, method: int | None = None, limit: int = 500):
        """The *Characterisation* panel of a process's pop-up: its own exchanges with nature per unit of its product,
        split by elementary flow in ``method`` (amount, factor, impact), largest |impact| first, at most ``limit``;
        with its direct and cumulative scores per unit (``U`` and ``G``: one column of ``B``, nothing solved), in
        ``method`` and, under ``totals``, in every method of the model (one value per method, in their order)."""
        check(activity=[index], method=[method])
        k = default_method if method is None else method
        per_unit = 1 / model.diagonal[index]
        amounts = model.B[:, index].toarray().ravel() * per_unit
        cfs = np.asarray(model.methods[k].cfs, dtype=float)
        impacts = amounts * cfs
        exchanged = np.flatnonzero(amounts)
        characterised = exchanged[impacts[exchanged] != 0]
        shown = characterised[np.argsort(-np.abs(impacts[characterised]), kind="stable")][:limit]
        return {
            "method": k, "unit": units[index],
            "direct": float(model.direct(index)[k] * per_unit), "cumulative": float(model.G[index, k]),
            "count": int(len(characterised)), "uncharacterised": int(len(exchanged) - len(characterised)),
            "totals": {"direct": _floats(model.U[:, index] * per_unit), "cumulative": _floats(model.G[index, :])},
            "flows": [{"flow": int(f), "name": flows["name"].iat[f], "compartment": flows["place"].iat[f],
                       "unit": flows["unit"].iat[f], "resource": is_resource(int(f)), "amount": float(amounts[f]),
                       "cf": float(cfs[f]), "impact": float(impacts[f])} for f in shown],
        }

    @app.post("/api/global")
    def global_contribution(req: GlobalRequest):
        check(activity=[req.root], method=[req.method], rule=[req.rule])
        methods = slice(None) if req.method is None else [req.method]
        result = supply_chain_contribution(model, req.root, req.groups, req.rule, req.amount, req.flow_groups, methods,
                                           req.order or None, req.flow_owners)
        return {name: _floats(v) for name, v in result.items()}

    @app.post("/api/compare")
    def compare(req: CompareRequest):
        """Several functional units under the same groups, for the comparison mode: per entry its
        groups (and *other*) for ``method``, which add up to its ``total``, and the total score of
        each of ``methods``."""
        if not req.entries:
            raise HTTPException(400, "Nothing to compare")
        if req.basis not in ("groups", "flows", "activities"):
            raise HTTPException(400, f"Unknown basis {req.basis!r}")
        check(activity=[e.root for e in req.entries], method=[req.method, *req.methods], rule=[req.rule])

        def folded(split: list[dict]) -> list[dict]:
            """The parts of all the entries cut together (``req.cut``, of their totals together); *other* for the rest."""
            keys = list({k: None for parts in split for k in parts})
            weight = [sum(abs(parts.get(k, 0.0)) for parts in split) for k in keys]
            shown, _, _ = _listed(weight, req.cut, sum(abs(sum(parts.values())) for parts in split))
            kept = {keys[i] for i in shown}
            out = []
            for parts in split:
                part = {k: v for k, v in parts.items() if k in kept}
                rest = sum(v for k, v in parts.items() if k not in kept)
                if abs(rest) > 1e-12 * max(abs(sum(parts.values())), 1e-300):
                    part[OTHER] = rest
                out.append(part)
            return out

        if req.basis == "flows":
            split = folded([_substances(model, flows, e.root, req.method, e.amount) for e in req.entries])
        elif req.basis == "activities":
            # what is inside each product, the first tier only: its own emissions and each input with its supply chain
            # (not the processes further down: those are in the input's share)
            contents: list[dict] = []
            for e in req.entries:
                tree = Tree(model, e.root, e.amount)
                parts: dict = {}
                for node in tree.children(tree.root):
                    value = float(node.impacts[req.method])
                    if value:
                        key = "direct emissions" if node.is_direct else int(node.product)
                        parts[key] = parts.get(key, 0.0) + value
                contents.append(parts)
            label: dict = {}
            for key in {k: None for parts in contents for k in parts}:
                if key == "direct emissions":
                    label[key] = key
                    continue
                m = meta(key)
                name = f"{m['name']} ({m['location']})"
                label[key] = name if name not in label.values() else f"{name} #{key}"
            split = folded([{label[k]: v for k, v in parts.items()} for parts in contents])
        else:
            split = [{name: float(v[0]) for name, v in supply_chain_contribution(
                model, e.root, req.groups, req.rule, e.amount, req.flow_groups, [req.method], req.order or None,
                req.flow_owners).items()}
                     for e in req.entries]
        return {"entries": [{
            "groups": parts,
            "total": sum(parts.values()),
            "totals": _floats(model.score(e.root, e.amount)[req.methods]),
        } for e, parts in zip(req.entries, split)]}

    @app.post("/api/global/contributors")
    def global_contributors(req: ContributorsRequest):
        """Activities making up one group of the whole-supply-chain analysis, largest first."""
        check(activity=[req.root], method=[req.method], rule=[req.rule])
        try:
            members, values = group_contributors(model, req.root, req.groups, req.group, req.rule, req.amount,
                                                 req.flow_groups, [req.method], req.order or None, req.flow_owners)
        except ValueError as e:
            raise HTTPException(400, f"Unknown group {req.group!r}") from e
        impacts = values[:, 0]
        flow_group = req.group in req.flow_groups and req.group not in req.groups
        shown, _, others = _listed(impacts, req.cut)
        return {
            "group": req.group,
            "total": float(impacts.sum()),
            "count": int(len(members)),
            # Under "first match", matched activities bring their supply chain; others only their own emissions
            "cumulative": req.rule == "first_match" and req.group != "other" and not flow_group,
            "flow_group": flow_group,
            "contributors": [{**meta(int(members[i])), "impact": float(impacts[i])} for i in shown],
            "others": others,
        }

    @app.post("/api/flows")
    def flows_contribution(req: FlowsRequest):
        """Contribution of elementary flows to the score: the whole inventory of the
        functional unit, characterised, largest |impact| first."""
        if req.by not in ("substance", "flow"):
            raise HTTPException(400, f"Unknown grouping {req.by!r}")
        if req.keys is not None and (not req.keys or any(k not in ("name", "compartment", "subcompartment") for k in req.keys)):
            raise HTTPException(400, f"Unknown grouping {req.keys!r}")
        check(activity=[req.root], method=[req.method])
        amounts, impacts = flow_contribution(model, req.root, req.method, req.amount)
        table = pd.DataFrame({"name": flows["name"], "compartment": flows["compartment"],
                              "subcompartment": flows["subcompartment"], "place": flows["place"], "unit": flows["unit"],
                              "amount": amounts, "impact": impacts})
        table = table[table["impact"] != 0]
        if req.keys is not None:
            table = table.fillna({"compartment": "", "subcompartment": ""})
            columns = [*req.keys, *(["unit"] if "name" in req.keys else [])]
            items = []
            for key, g in table.groupby(columns, sort=False):
                values = dict(zip(columns, key if isinstance(key, tuple) else (key,)))
                places = ", ".join(str(values[c]) for c in ("compartment", "subcompartment") if c in values and values[c])
                if "name" in values:
                    place = places if places else (f"{g['compartment'].nunique()} compartments" if g["compartment"].nunique() > 1
                                                    else g["place"].iat[0])
                    name = values["name"]
                else:
                    place, name = "", places or "(unspecified)"
                items.append({"name": name, "compartment": place, "unit": values.get("unit", ""),
                              "amount": float(g["amount"].sum()) if "name" in values else 0.0, "impact": float(g["impact"].sum()),
                              "flows": [int(i) for i in g.index],
                              "compartments": list(g["place"])})
        elif req.by == "substance":
            keys = ["name", "unit"]
            items = [{"name": name, "compartment": f"{len(g)} compartments" if len(g) > 1 else g["place"].iat[0],
                      "unit": unit, "amount": float(g["amount"].sum()), "impact": float(g["impact"].sum()),
                      "flows": [int(i) for i in g.index],
                      "compartments": list(g["place"])}
                     for (name, unit), g in ((k, g.reindex(g["impact"].abs().sort_values(ascending=False).index))
                                             for k, g in table.groupby(keys, sort=False))]
        else:
            items = [{"name": r.name, "compartment": r.place,
                      "unit": r.unit, "amount": float(r.amount), "impact": float(r.impact), "flows": [int(i)]}
                     for i, r in zip(table.index, table.itertuples(index=False))]
        shown, _, others = _listed([it["impact"] for it in items], req.cut, impacts.sum())
        return {
            "total": float(impacts.sum()),
            "count": len(items),
            "items": [items[i] for i in shown],
            "others": others,
        }

    def flow_row(f: int) -> dict:
        return {"flow": int(f), "name": flows["name"].iat[f], "compartment": flows["place"].iat[f],
                "unit": flows["unit"].iat[f]}

    def matching(items: list[dict], query: str) -> list[dict]:
        """The ``items`` whose name and compartment have all the words of ``query`` (any case)."""
        words = query.lower().split()
        return [it for it in items if all(w in f"{it['name']} {it['compartment']}".lower() for w in words)] if words else items

    def by_unit(items: list[dict], key, limit: int, total: bool = False) -> list[dict]:
        """``items`` in one list per unit (amounts in different units do not compare), largest |key| first in each,
        those the reference method characterises (likely gaps) before the others; the units with the most flows
        first. ``total``: with the sum of ``key`` over each unit's items."""
        units: dict[str, list[dict]] = {}
        for it in items:
            units.setdefault(it["unit"] or "", []).append(it)
        out = [{"unit": u, "count": len(rows), "gaps": sum(1 for it in rows if it.get("reference_cf")),
                **({"total": float(sum(key(it) for it in rows))} if total else {}),
                "items": sorted(rows, key=lambda it: (not it.get("reference_cf"), -abs(key(it))))[:limit]}
               for u, rows in units.items()]
        return sorted(out, key=lambda g: -g["count"])

    @app.post("/api/flows/uncharacterised")
    def flows_uncharacterised(req: UncharacterisedRequest):
        """The elementary flows of the functional unit's inventory that the method has no factor for: what its score
        leaves out, by unit, largest amount first, with the factor of a ``reference`` method."""
        check(activity=[req.root], method=[req.method, req.reference])
        found = uncharacterised(model, supply(req.root, req.amount), req.method, req.reference)
        items = [{**flow_row(f), "amount": float(found["amounts"][i]), "emitters": int(found["emitters"][i]),
                  "methods": int(found["methods"][i]),
                  **({"reference_cf": float(found["reference_cf"][i])} if req.reference is not None else {})}
                 for i, f in enumerate(found["flows"])]
        shown = matching(items, req.query)
        return {"method": req.method, "reference": req.reference, "query": req.query,
                "inventory": found["inventory"], "characterised": found["characterised"], "count": len(items),
                "gaps": sum(1 for it in items if it.get("reference_cf")), "matched": len(shown),
                "units": by_unit(shown, lambda it: it["amount"], req.limit, total=True)}

    def unlinked_items(table: pd.DataFrame, limit: int) -> list[dict]:
        """The rows of a table of :func:`unlinked_factors`, the largest factor first, at most ``limit``."""
        return [{"name": r.name, "compartment": ", ".join(str(c) for c in r.categories if c), "unit": r.unit,
                 "cf": float(r.cf), "id": int(r.id) if r.id >= 0 else None, "match_kind": r.match_kind,
                 "match": flow_row(int(r.match)) if r.match >= 0 else None}
                for r in table.sort_values("cf", key=lambda c: -c.abs()).head(limit).itertuples(index=False)]

    @app.get("/api/methods/{method}/coverage")
    def coverage(method: int, reference: int | None = None, limit: int = 300, query: str = ""):
        """How the method covers the flows of the whole model: its factors on flows of the model, the flows activities
        exchange that it has no factor for (by unit, those the reference characterises then the most emitted first, at
        most ``limit`` per unit) and its factors on flows outside the model (``limit`` of them, largest first). ``query``:
        only the flows whose name or compartment has all these words."""
        check(method=[method, reference])
        found = method_coverage(model, method, reference)
        missing = found["uncharacterised"]
        items = [{**flow_row(f), "emitters": int(missing["emitters"][i]), "methods": int(missing["methods"][i]),
                  **({"reference_cf": float(missing["reference_cf"][i])} if reference is not None else {})}
                 for i, f in enumerate(missing["flows"])]
        gaps = sum(1 for it in items if it.get("reference_cf"))
        shown = matching(items, query)
        unlinked = None
        if found["unlinked"] is not None:
            table = found["unlinked"]
            words = query.lower().split()
            text = (table["name"].astype(str) + " " + table["categories"].map(lambda c: ", ".join(str(x) for x in c if x))).str.lower()
            shown_unlinked = table[text.map(lambda t: all(w in t for w in words))] if words else table
            unlinked = {
                "count": int(len(table)),
                "same": int((table["match_kind"] == "same").sum()),
                "matched": int(len(shown_unlinked)),
                "items": unlinked_items(shown_unlinked, limit),
            }
        return {"method": method, "reference": reference, "query": query, "linked": found["linked"],
                "characterised_unused": found["characterised_unused"], "inventory": found["inventory"],
                "uncharacterised": {"count": int(len(missing["flows"])), "gaps": gaps, "matched": len(shown),
                                    "units": by_unit(shown, lambda it: it["emitters"], limit)},
                "unlinked": unlinked}

    @app.post("/api/flows/of-process")
    def flows_of_process(req: ProcessFlowsRequest):
        """The elementary flows behind what one process of the supply chain contributes by its own emissions (the
        contribution of a process, reversed: its flows), largest first."""
        check(activity=[req.root, req.activity], method=[req.method])
        level = float(supply(req.root, req.amount)[req.activity])
        amounts = model.B[:, req.activity].toarray().ravel() * level
        impacts = amounts * model.methods[req.method].cfs
        emitted = np.flatnonzero(impacts)
        shown, _, others = _listed(impacts[emitted], req.cut)
        return {
            "total": float(impacts.sum()),
            "count": len(emitted),
            "flows": [{"flow": int(f), "name": flows["name"].iat[f], "compartment": flows["place"].iat[f],
                       "unit": flows["unit"].iat[f], "amount": float(amounts[f]), "impact": float(impacts[f])}
                      for f in emitted[shown]],
            "others": others,
        }

    @app.post("/api/flows/contributors")
    def flows_contributors(req: FlowContributorsRequest):
        """Activities emitting some elementary flows in the supply chain, largest first."""
        check(activity=[req.root], method=[req.method])
        try:
            members, amounts, impacts, main = flow_contributors(model, req.root, req.flows, req.method, req.amount)
        except IndexError as e:
            raise HTTPException(400, f"Unknown flow: {e}") from e
        shown, _, others = _listed(impacts, req.cut)
        return {
            "total": float(impacts.sum()),
            "count": int(len(members)),
            "contributors": [{**meta(int(members[i])), "amount": float(amounts[i]), "impact": float(impacts[i]),
                              "flow": int(main[i])} for i in shown],
            "others": others,
        }

    emitter_rows: list = []  # B by rows, made at the first request (a few hundred ms on ecoinvent)

    @app.post("/api/category")
    def category(req: CategoryRequest):
        """An impact category seen from its elementary flows: the flows of the model it characterises, their factor
        (and that of ``compare``), the activities emitting them and, for ``root``, their contribution, largest first."""
        check(activity=[req.root], method=[req.method, req.compare])
        if not emitter_rows:
            emitter_rows.append(direct_emitters(model))
        rows = emitter_rows[0]
        s = supply(req.root, req.amount) if req.root is not None else None
        both = req.compare is not None
        table = compare_methods(model, req.method, req.compare, s, rows)
        table = table.join(flows[["name", "compartment", "subcompartment", "place", "unit"]].fillna(""))
        sides = ["a", "b"] if both else ["a"]
        totals = {k: float(table[f"impact_{k}"].sum()) for k in sides} if s is not None else {}
        # one row per flow, in the order of compare_methods (thousands for some categories); the pop-up arranges them
        # by substance or compartment itself
        columns = {c: table[c].tolist() for c in table.columns}
        # the location of each flow (regionalised flows), for *Organize by location*: models built before the flows kept
        # theirs have no such column (``locations``: False, the pop-up asks for a rebuild)
        located = "location" in flows
        if located:
            where = flows["location"].reindex(table.index)
            places = [None if not isinstance(v, str) or not v.strip() else v for v in where.tolist()]
        items = []
        for n, f in enumerate(table.index):
            side = {k: {"min": columns[f"cf_{k}"][n], "max": columns[f"cf_{k}"][n], "rel": columns[f"rel_{k}"][n],
                        "impact": columns[f"impact_{k}"][n] if s is not None else None} for k in sides}
            ratio = columns["ratio"][n] if both else None
            items.append({
                "name": columns["name"][n], "compartment": columns["place"][n], "unit": columns["unit"][n],
                "top": columns["compartment"][n], "sub": columns["subcompartment"][n],
                "flows": [int(f)], "compartments": [columns["place"][n]], "emitters": int(columns["emitters"][n]),
                "amount": columns["amount"][n] if s is not None else None,
                "a": side["a"], "b": side.get("b"),
                "ratio": None if ratio is None or ratio != ratio else ratio,  # NaN: not both
                "only": columns["only"][n] if both else "",
                **({"location": places[n]} if located else {}),
            })
        # the factors of each method on flows that are not in the model (None: the model did not record them)
        unlinked = None
        if model.unlinked is not None:
            unlinked = {}
            for key, m in (("a", req.method), ("b", req.compare)):
                if m is None:
                    continue
                found = unlinked_factors(model, m)
                unlinked[key] = {"count": int(len(found)), "same": int((found["match_kind"] == "same").sum()),
                                 "items": unlinked_items(found, req.unlinked_limit)}
        return {
            "method": req.method, "compare": req.compare,
            "flows": int(len(table)),  # flows of the model characterised (by either method)
            "in_inventory": int(np.count_nonzero(table["amount"])) if s is not None else None,
            "totals": totals,
            "locations": located,
            "items": items,
            "unlinked": unlinked,
        }

    @app.post("/api/locate")
    def locate(req: LocateRequest):
        """View changes that show ``product`` in the tree, along its most important supply path."""
        check(activity=[req.product, *req.root], method=[req.method], rule=[req.rule])
        start = req.root[0]
        path = None
        if req.rule == "first_match" and req.groups:
            path = main_path(model, start, req.product, req.method, blocked=matched(model, req.groups))
        path = path or main_path(model, start, req.product, req.method)
        if path is None:
            raise HTTPException(404, f"{describe(req.product)['name']} is not in the supply chain of the process")
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
    open: bool = True
    """False: built only (Rebuild, in the storage dialog)."""
    methods: list[list[str]] | None = None
    """The impact methods of a build (names); None: those of the previous build, else all."""


class AddMethodsRequest(BaseModel):
    key: str
    """A database that is open, built from Brightway."""
    methods: list[list[str]]


class UnloadRequest(BaseModel):
    key: str


class StorageDeleteRequest(BaseModel):
    """``kind``: "model" (made from files), "segment" (with the segments that use it), "results" (the ``G`` of a model
    or a segment), "project" (a project cache), "obsolete", "leftover", "download" (with ``path``), or "obsolete" /
    "leftovers" / "all" without ``path`` (every obsolete cache / left-over / every cache, model and left-over)."""
    kind: Literal["model", "segment", "results", "project", "obsolete", "leftover", "download", "leftovers", "all"]
    path: str | None = None


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


class RenameProjectRequest(BaseModel):
    folder: str
    project: str
    new_name: str


class DeleteDatabaseRequest(BaseModel):
    folder: str
    project: str
    database: str


class RenameDatabaseRequest(DeleteDatabaseRequest):
    new_name: str


class CopyDatabaseRequest(BaseModel):
    folder: str
    project: str
    """The project the copy goes to."""
    database: str
    new_name: str
    from_project: str | None = None
    """The project of the database to copy (default: ``project``)."""


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
    import copy
    import gc
    import json
    import shutil
    import subprocess
    import sys
    import threading
    import time
    import uuid

    from fastapi.responses import RedirectResponse
    from starlette.routing import Mount

    from . import methods_library, project_cache, sources

    data = Path(data or "data").resolve()
    library = methods_library.Library(data / "methods")
    # Files the page uploaded are deleted once imported: those left are of an import that failed or was
    # abandoned (no job runs yet)
    shutil.rmtree(data / "uploads", ignore_errors=True)
    app = FastAPI(title="lca_viewer")
    loaded: dict[str, dict] = {}  # key -> {"key", "title", "path", "activities", "route"}
    jobs: dict[str, dict] = {}
    # The jobs under way, which the server finishes before it stops (see lifetime.Lifetime)
    app.state.running_jobs = lambda: [job["title"] for job in list(jobs.values()) if job["status"] == "running"]
    lock = threading.Lock()
    projects_cache: dict[str, list] = {}  # Brightway data folder -> its projects, as the Projects page last read them
    counts_file = data / "cache" / "node-counts.json"
    try:
        node_counts = json.loads(counts_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        node_counts = {}

    def projects_in(folder: str) -> list[dict]:
        """:func:`sources.brightway_projects`, the nodes counted again only in the projects written
        since the last time (seconds for ecoinvent); the counts are kept in data/cache for the next start."""
        cache = dict(node_counts)
        projects = sources.brightway_projects(folder, cache)
        if any(node_counts.get(k) is not v for k, v in cache.items()):
            node_counts.update(cache)
            try:
                counts_file.parent.mkdir(parents=True, exist_ok=True)
                counts_file.write_text(json.dumps(node_counts), encoding="utf-8")
            except OSError:
                pass  # counted again at the next start
        return projects

    def entries() -> list[dict]:
        """What can be opened: the models of ``models.json`` made from files (those of Brightway databases, from before
        the project cache, are obsolete: listed in Storage only), and the databases built in the project caches."""
        out = [m for m in sources.catalog(data) if not (m.get("source") or {}).get("brightway")]
        for cache in sorted((data / "cache").glob("project-*")):
            info = project_cache.read_project(cache)
            if info:
                folder = str(Path(info["brightway"]).resolve())
                for r in info["segments"]:
                    out += [bw_entry(folder, info["project"], d) for d in r["databases"]]
        return out

    def cache_of(folder: str, project: str) -> Path:
        return sources.project_cache_dir(data, folder, project).resolve()

    def bw_entry(folder: str, project: str, database: str) -> dict:
        """A database of a project cache, as the catalog lists models; ``path``: its name in the cache (not a folder)."""
        cache = cache_of(folder, project)
        return {"path": str(sources.cached_database(cache, project, database)), "title": f"{database} ({project})",
                "default_root": None, "cache": str(cache), "database": database,
                "source": {"brightway": folder, "project": project, "database": database}}

    def is_built(entry: dict) -> bool:
        if "cache" in entry:
            return project_cache.status(entry["cache"], entry["database"])["built"]
        return (Path(entry["path"]) / "A.npz").exists()

    def listing() -> list[dict]:
        """The loaded databases; ``brightway``: the data folder, project and database a model was built from."""
        found = {Path(c["path"]).resolve(): c for c in entries()}
        out = []
        for m in loaded.values():
            entry = found.get(m["path"])
            origin = entry and built_from(entry)
            out.append({"key": m["key"], "title": m["title"], "activities": m["activities"],
                        "brightway": origin and dict(zip(("folder", "project", "database"), origin)),
                        "outdated": origin and outdated(entry, *origin, model=m["model"])})
        return out

    def outdated(entry: dict, folder: str, project: str, database: str, p: dict | None = None,
                 model: LCAModel | None = None) -> str | None:
        """What changed in the project since a Brightway database was built (a rebuild is required), or None.
        ``p``: the project, as :func:`sources.brightway_projects` lists it (read when not given); ``model``: the
        database open, which may have been put together from segments built again since."""
        try:
            p = p or next(p for p in projects_in(folder) if p["name"] == project)
        except (StopIteration, FileNotFoundError, ValueError):
            return None
        state = project_cache.status(entry["cache"], database, p)
        if not state["built"]:
            return "no longer built"
        origin = getattr(model, "project_cache", None)
        if state["outdated"] is None and origin:
            info = project_cache.read_project(entry["cache"])
            now = {r["id"]: [r["version"], r.get("basis")] for r in project_cache.closure(info, database)}
            if now != origin["segments"]:
                return "built again since it was opened"
        return state["outdated"] or library.stale(project_cache.read_project(entry["cache"]))

    def in_use(cache) -> set[str]:
        """The segment folders of a project cache that open databases read."""
        return {f for m in loaded.values() if (getattr(m["model"], "project_cache", None) or {}).get("cache") == str(cache)
                for f in m["model"].project_cache["folders"]}

    def clean(cache) -> None:
        """Delete the folders of segments rebuilt or deleted that no open database reads any more."""
        try:
            project_cache.remove_retired(cache, in_use(cache))
        except OSError:
            pass  # deleted at the next try

    def loaded_key(path) -> str | None:
        path = Path(path).resolve()
        return next((k for k, m in loaded.items() if m["path"] == path), None)

    def mount(path, title: str, default_root: str | None, entry: dict | None = None) -> str:
        """Load, factorise and serve a model (``entry``: a database of a project cache); returns its key."""
        path = Path(path).resolve()
        model = (project_cache.load(entry["cache"], entry["database"]) if entry and "cache" in entry
                 else LCAModel.load(path))
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
            loaded[key] = {"key": key, "title": title, "path": path, "activities": model.size, "route": route, "model": model}
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
            projects = projects_in(folder)
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

    build_locks: dict[str, threading.Lock] = {}

    def build_lock(cache) -> threading.Lock:
        """One build at a time in a project cache: two would write its files at once."""
        with lock:
            return build_locks.setdefault(str(cache), threading.Lock())

    def build(job: dict, folder: str, project: str, database: str, rebuild: bool, reopen: bool = False,
              methods: list[list[str]] | None = None) -> dict:
        """The entry of a Brightway database, built (in a separate process) in its project cache if it is not yet,
        or its results are to be solved again, or again with ``rebuild`` (the page asks for it when the project
        changed since): the segments it needs that are missing or changed are read, the others kept. A database
        that is open is not rebuilt, unless ``reopen``: it keeps being served while the build is made (from other
        folders), then it is closed (the caller opens it again). ``methods``: the impact methods of the project
        cache; None: those it has, else all of the project's."""
        import importlib.util

        folder = str(Path(folder).expanduser().resolve())
        p, _ = find_database(folder, project, database)
        entry = bw_entry(folder, project, database)
        cache = Path(entry["cache"])
        state = project_cache.status(cache, database, p)
        if state["built"] and not rebuild and not state["solve"] and not library.stale(project_cache.read_project(cache)):
            return entry
        if loaded_key(entry["path"]) and not reopen:
            raise ValueError(f"{database} is open: close it first (Close, in the list of loaded databases), then rebuild it")
        if importlib.util.find_spec("bw2data") is None:
            raise ValueError("Brightway is not installed in the Python environment running lca_viewer: "
                             "run pip install -e \".[brightway]\" in it, then restart the app")
        building = (f"Building {database} (the databases it uses once for the project: one or two minutes for "
                    f"ecoinvent)" if not state["built"] else f"Building {database} again")
        job["message"] = building
        if methods is None:
            methods = project_cache.method_names(cache) or None  # the project's methods, as chosen before
        chosen = cache.with_name(cache.name + ".methods.json")
        args = ["build", "--project", project, "--database", database, "--cache", str(cache)]
        if rebuild:
            args.append("--rebuild")
        with build_lock(cache):
            if methods is not None:
                if not methods:
                    raise ValueError("Choose at least one impact method")
                methods = library_file(chosen, methods, folder, project, args)
                if not methods:
                    raise ValueError("None of the chosen impact methods is in the project or the methods library")
                chosen.parent.mkdir(parents=True, exist_ok=True)
                chosen.write_text(json.dumps(methods), encoding="utf-8")
                args += ["--methods-file", str(chosen)]
            try:
                # The steps of project_cache.build, not Brightway's own messages
                cli(job, folder, args, lambda line: line.startswith(("Reading", "Solving")) and
                    f"{building}: {line[0].lower()}{line[1:]}")
            except ValueError as e:
                raise ValueError(f"The build failed: {e}") from None
            finally:
                chosen.unlink(missing_ok=True)
                chosen.with_suffix(".library.json").unlink(missing_ok=True)
        with lock:
            key = loaded_key(entry["path"])
            if key and reopen:  # served until now, from the folders of its former build: opened again by the caller
                closed = loaded.pop(key)
                app.router.routes.remove(closed["route"])
        gc.collect()
        clean(cache)
        return entry

    def library_file(chosen: Path, methods: list, folder: str, project: str, args: list[str]) -> list:
        """The methods of a build, those of the library under their current names (those no longer in it left out);
        the library's factors on the project's flows written next to ``chosen`` for the build process
        (``--library-file`` added to ``args``)."""
        names, found = methods_library.build_entries(library, methods, folder, project)
        if found:
            path = chosen.with_suffix(".library.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(found), encoding="utf-8")
            args += ["--library-file", str(path)]
        return names

    def run(job: dict, req: LoadRequest) -> dict:
        if req.brightway:
            entry = build(job, req.brightway, req.project, req.database, req.rebuild, req.reopen, req.methods)
        else:
            entry = next((m for m in entries() if Path(m["path"]) == Path(req.path).resolve()), None)
            if entry is None:
                raise ValueError(f"{req.path} is not in {data / 'models.json'}")
        if not req.open:
            return {"message": f"{entry['title']} is built"}
        key = loaded_key(entry["path"])
        if key is None:
            if not is_built(entry):
                raise ValueError(f"No model in {entry['path']}: build it again")
            job["message"] = f"Loading {entry['title']} and factorising its matrix"
            key = mount(entry["path"], entry["title"], entry.get("default_root"), entry)
        return {"key": key, "message": f"{entry['title']} is ready"}

    def public(job: dict) -> dict:
        out = {k: v for k, v in job.items() if not k.startswith("_")}
        if job["status"] == "running":  # for how long, so that the page shows it is still under way
            out["seconds"] = round(time.perf_counter() - job["_start"])
        return out

    def start(title: str, target: tuple, work, *args, message: str = "Starting") -> dict:
        """Run ``work(job, *args)`` in the background, one job per ``target`` at a time; what it
        returns is added to the job, which the page follows with ``/api/jobs/<id>``. ``message``:
        what the page shows until the job says more. A job other than a load changes the projects:
        they are listed again afterwards."""
        with lock:
            for job in jobs.values():  # already under way
                if job["status"] == "running" and job["_target"] == target:
                    return public(job)
            job = {"id": uuid.uuid4().hex[:12], "status": "running", "message": message, "log": [],
                   "title": title, "_target": target, "_start": time.perf_counter()}
            jobs[job["id"]] = job

        def go():
            try:
                found = {**(work(job, *args) or {}), "status": "done"}
            except Exception as e:  # reported to the page
                found = {"status": "failed", "error": str(e) or type(e).__name__}
            if target[0] not in ("load", "methods"):  # before the page, seeing the job end, lists the projects again
                projects_cache.clear()
            job["seconds"] = round(time.perf_counter() - job["_start"], 1)
            job.update(found)

        threading.Thread(target=go, daemon=True).start()
        return public(job)

    @app.get("/api/databases")
    def databases():
        return listing()

    @app.get("/api/sources")
    def sources_():
        """What can be loaded (built models, Brightway data folders), and the loads under way."""
        models = []
        for m in entries():
            path = Path(m["path"])
            models.append({"path": str(path), "title": m["title"], "key": loaded_key(path),
                           "built": is_built(m), "source": m.get("source")})
        return {
            "data": str(data),
            "loaded": listing(),
            "models": models,
            "brightway": sources.brightway_dirs(),
            "jobs": [public(j) for j in jobs.values() if j["status"] == "running"],
        }

    @app.get("/api/brightway")
    def brightway(folder: str, refresh: bool = False):
        """Projects and databases of a Brightway data folder, with the model built from each, if any.
        They are read once, then kept until a job changes them; ``refresh``: read them again (changes
        made outside the app, in Activity Browser…), the nodes counted again only where written since."""
        key = str(Path(folder).expanduser().resolve())
        if refresh or key not in projects_cache:
            try:
                projects_cache[key] = projects_in(folder)
            except FileNotFoundError as e:
                if not Path(folder).expanduser().is_dir():
                    raise HTTPException(404, str(e))
                projects_cache[key] = []  # a folder without projects yet: New project creates the first one
        projects = copy.deepcopy(projects_cache[key])
        folder = key
        built = {}
        for m in entries():
            origin = built_from(m)
            if origin and origin[0] == folder and "cache" in m and is_built(m):
                built[origin[1:]] = m
        for p in projects:
            for d in p["databases"]:
                d["locked"] = d["name"] in locked_read(folder, p["name"])
                b = built.get((p["name"], d["name"]))
                # outdated: what changed in the project since the build (None: nothing)
                key = b and loaded_key(b["path"])
                d["model"] = b and {"key": key, "outdated": outdated(b, folder, p["name"], d["name"], p,
                                                                     key and loaded[key]["model"])}
        return {"folder": folder, "projects": projects}

    links_file = data / "cache" / "links.json"
    try:
        links_cache = json.loads(links_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        links_cache = {}

    @app.get("/api/brightway/links")
    def brightway_links(folder: str, project: str):
        """The links between the databases of a project, counted in its exchanges (seconds to tens of seconds the first
        time, then kept until the project is written again): ``{"links": [[database, database it uses, exchanges]]}``."""
        try:
            directory = sources.project_dir(folder_of(folder), project)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(404, str(e))
        key = str(directory / "lci" / "databases.db")
        before = links_cache.get(key)
        found = sources.database_links(directory, links_cache)
        if links_cache.get(key) is not before:  # counted again: kept for the next start
            try:
                links_file.parent.mkdir(parents=True, exist_ok=True)
                links_file.write_text(json.dumps(links_cache), encoding="utf-8")
            except OSError:
                pass
        return {"links": found}

    # ------------------------------------------------ Storage: what the app and Brightway take on disk

    def storage_items() -> dict[str, dict]:
        """What the storage dialog lists, by path: project caches and their segments, models made from files, obsolete
        caches, left-overs in data/cache, downloads. Only these paths can be deleted from it."""
        items: dict[str, dict] = {}
        cache_root = (data / "cache").resolve()
        catalog_paths = set()
        known_projects: dict[str, dict] = {}

        def databases_of(folder: str) -> dict[str, set]:
            if folder not in known_projects:
                try:
                    known_projects[folder] = {p["name"]: {d["name"] for d in p["databases"]} for p in projects_in(folder)}
                except (FileNotFoundError, OSError):
                    known_projects[folder] = {}
            return known_projects[folder]

        for m in sources.catalog(data):
            path = Path(m["path"]).resolve()
            catalog_paths.add(path)
            source = m.get("source") or {}
            if source.get("brightway"):  # one model per database, before the project cache
                items[str(path)] = {"kind": "obsolete", "path": str(path), "title": m["title"], "size": sources.folder_size(path),
                                    "open": loaded_key(path) is not None,
                                    "reason": "a model of its own (before the project cache): the database is built "
                                              "again in its project cache when next opened"}
                continue
            results = sum((path / f).stat().st_size for f in ("G.npy", "G.json") if (path / f).exists())
            items[str(path)] = {"kind": "model", "path": str(path), "title": m["title"], "size": sources.folder_size(path),
                                "results": results, "open": loaded_key(path) is not None,
                                "orphan": None if (path / "A.npz").exists() else "not built"}
        for cache in sorted(cache_root.glob("project-*")) if cache_root.is_dir() else []:
            if not cache.is_dir():
                continue
            info = project_cache.read_project(cache)
            if info is None:
                items[str(cache)] = {"kind": "obsolete", "path": str(cache), "title": cache.name, "size": sources.folder_size(cache),
                                     "reason": "a project cache of another version of LCA Explorer"}
                continue
            folder, project = str(Path(info["brightway"]).resolve()), info["project"]
            names = databases_of(folder).get(project)
            gone = None if names is not None else f"project {project} no longer exists"
            used = in_use(cache)
            segments = project_cache.ordered(info)
            for r in segments:
                path = project_cache.folder(cache, r)
                missing = [d for d in r["databases"] if names is not None and d not in names]
                items[str(path)] = {
                    "kind": "segment", "path": str(path), "title": ", ".join(r["databases"]), "databases": r["databases"],
                    "project": project, "folder": folder, "size": sources.folder_size(path),
                    "results": sum((path / f).stat().st_size for f in ("G.npy", "G.json") if (path / f).exists()),
                    "open": str(path) in used, "users": [", ".join(u["databases"]) for u in project_cache.users(info, [r["id"]])],
                    "orphan": gone or (missing and f"database {missing[0]} no longer in {project}") or None}
            listed = {r["folder"] for r in info["segments"]}
            for child in sorted((cache / "segments").iterdir()) if (cache / "segments").is_dir() else []:
                if child.name not in listed:
                    retired = child.name in info.get("retired", [])
                    items[str(child)] = {"kind": "leftover", "path": str(child), "title": f"{project}: {child.name}",
                                         "size": sources.folder_size(child), "open": str(child) in used,
                                         "bulk": str(child) not in used,
                                         "reason": "replaced by a rebuild" if retired else "build left unfinished"}
            files = sum((cache / f).stat().st_size for f in ("project.json", "flows.pkl", "methods.pkl", "unlinked.pkl")
                        if (cache / f).exists())
            items[str(cache)] = {"kind": "project", "path": str(cache), "title": project, "folder": folder,
                                 "size": sources.folder_size(cache), "files": files, "open": bool(used), "orphan": gone,
                                 "methods": len(info["methods"]), "segments": [str(project_cache.folder(cache, r)) for r in segments]}
        if cache_root.is_dir():  # what the catalog does not list: models of an older layout, builds left half done
            for first in cache_root.iterdir():
                if first.name.startswith("project-"):
                    continue
                for path in ([first] if (first / "A.npz").exists() or first.suffix in (".part", ".old") else
                             list(first.iterdir()) if first.is_dir() else []):
                    if path.is_dir() and path.resolve() not in catalog_paths:
                        items[str(path.resolve())] = {"kind": "leftover", "path": str(path.resolve()), "title": path.name,
                                                      "size": sources.folder_size(path), "bulk": path.suffix in (".part", ".old"),
                                                      "reason": ("build left unfinished" if path.suffix in (".part", ".old")
                                                                 else "not in models.json (a model opened with --model?)")}
                if first.is_dir() and first.name.startswith("bw-") and not any(first.iterdir()):
                    items[str(first.resolve())] = {"kind": "leftover", "path": str(first.resolve()), "title": first.name,
                                                   "size": 0, "bulk": True, "reason": "empty folder"}
        for path, title in [(data / "downloads", "Downloads of LCA Explorer (biosphere, methods, projects)"),
                            (sources.default_brightway_dir().parent / "EcoinventInterface", "ecoinvent releases (ecoinvent_interface)")]:
            if path.is_dir():
                items[str(path.resolve())] = {"kind": "download", "path": str(path.resolve()), "title": title,
                                              "size": sources.folder_size(path)}
        return items

    @app.get("/api/storage")
    def storage(folder: str | None = None):
        """Sizes on disk: the app's items (see ``storage_items``) and the projects of the Brightway data folder."""
        items = list(storage_items().values())
        base = Path(folder_of(folder)) if folder else sources.brightway_dir()
        projects = []
        try:
            for name, twofive, directory in sources._projects(base):
                projects.append({"name": name, "twofive": twofive, "path": str(directory),
                                 "size": sources.folder_size(directory)})
        except (FileNotFoundError, OSError, ValueError):
            pass
        return {"data": str(data), "brightway": str(base), "items": items, "projects": projects,
                "total": sum(i["size"] for i in items if i["kind"] != "segment"), "brightway_total": sources.folder_size(base) if base.is_dir() else 0}

    @app.post("/api/storage/delete")
    def storage_delete(req: StorageDeleteRequest):
        """Delete what the storage dialog lists: a model (``results``: its ``G`` only, solved again when it is next
        opened), a left-over, a download folder. A model that is open is closed first by the page."""
        items = storage_items()
        path = str(Path(req.path).resolve()) if req.path else None
        bulk = {"all": lambda i: i["kind"] in ("model", "project", "obsolete") or i.get("bulk"),
                "leftovers": lambda i: i.get("bulk"), "obsolete": lambda i: i["kind"] == "obsolete"}
        if req.kind in bulk and not path:
            targets = [i for i in items.values() if bulk[req.kind](i)]
        else:
            targets = [items[path]] if path in items else []
            if not targets:
                raise HTTPException(404, "Nothing to delete there")
            kinds = ("model", "segment") if req.kind == "results" else (req.kind,)
            if targets[0]["kind"] not in kinds:
                raise HTTPException(400, f"{targets[0]['title']} is not a {req.kind}")
        if not targets:
            raise HTTPException(404, "Nothing to delete there")
        if any(j["status"] == "running" for j in jobs.values()):
            raise HTTPException(409, "Wait for the job under way to end")
        if req.kind == "segment":  # with the segments that use it
            gone = [targets[0]["title"], *targets[0]["users"]]
            busy = [i["title"] for i in items.values() if i["kind"] == "segment" and i["title"] in gone
                    and i.get("project") == targets[0]["project"] and i["open"]]
        else:
            busy = [i["title"] for i in targets if i.get("open") and req.kind != "results"]
        if busy:
            raise HTTPException(409, f"Close the databases of {', '.join(busy)} first")
        for item in targets:
            target = Path(item["path"])
            if item["kind"] == "segment":
                cache, info = target.parent.parent, project_cache.read_project(target.parent.parent)
                ids = [r["id"] for r in info["segments"] if r["folder"] == target.name]
                if req.kind == "results":
                    project_cache.drop_results(cache, ids)
                else:
                    project_cache.delete_segments(cache, ids)
                    clean(cache)
                continue
            if req.kind == "results":
                for name in ("G.npy", "G.json"):
                    (target / name).unlink(missing_ok=True)
                continue
            shutil.rmtree(target, ignore_errors=True)
            if item["kind"] in ("model", "obsolete"):
                sources.unregister(data, target)
        return storage(None)

    @app.post("/api/load")
    def load(req: LoadRequest):
        """Load a database in the background; follow it with ``/api/jobs/<id>``."""
        if not req.path and not (req.brightway and req.project and req.database):
            raise HTTPException(400, "Give a model path, or a Brightway folder, project and database")
        target = ("load", req.path and str(Path(req.path).resolve()),
                  req.brightway and str(Path(req.brightway).expanduser().resolve()), req.project, req.database)
        return start(req.database or Path(req.path).name, target, run, req)

    @app.get("/api/brightway/methods")
    def brightway_methods(folder: str, project: str, database: str | None = None):
        """The impact methods of a project, for the choice of those to build a database with:
        ``selected``, those of its model when it was built before, ``default``, those offered
        otherwise (the family of the default method: EF v3.1 when the project has it)."""
        folder = str(Path(folder).expanduser().resolve())
        try:
            methods = sources.project_methods(folder, project)
        except ValueError as e:
            raise HTTPException(404, str(e))
        # One list of methods per project: those of its project cache, whichever database is built
        selected = project_cache.method_names(cache_of(folder, project)) or None
        default = []
        if methods:
            family = _method_family(methods[_default_index([m["name"] for m in methods])]["name"])
            default = [m["name"] for m in methods if _method_family(m["name"]) == family]
        # The methods of the app's library, under the names they take in a model, with how many of their factors are
        # on flows of the project (the others are dropped by a build: the flows of another biosphere, e.g. of another
        # ecoinvent release); those chosen before, under their current names
        offered = []
        for m in library.all():
            try:
                cover = library.coverage(m, folder, project)
            except (FileNotFoundError, ValueError):
                cover = {"matched": 0, "total": len(m["factors"])}
            offered.append({"name": methods_library.model_name_of(m), "unit": m["unit"], "library": m["id"],
                            "matched": cover["matched"], "factors": cover["total"],
                            "databases": sorted({f["database"] for f in m["factors"]})})
        if selected and any(methods_library.is_library(n) for n in selected):
            selected = methods_library.build_entries(library, selected, folder, project)[0]
        try:  # the databases of the project's elementary flows, which the library's factors are matched to
            biosphere = sorted({f["database"] for f in methods_library.project_flows(folder, project)})
        except (OSError, ValueError, sqlite3.Error):
            biosphere = []
        return {"methods": methods, "selected": selected, "default": default, "library": offered, "biosphere": biosphere}

    def add_methods_job(job: dict, key: str, names: list[list[str]]) -> dict:
        m = loaded.get(key)
        if m is None:
            raise ValueError(f"{key} is not loaded")
        model = m["model"]
        origin = getattr(model, "project_cache", None)
        entry = origin and next((e for e in entries() if Path(e["path"]).resolve() == m["path"]), None)
        if not entry:
            raise ValueError("This database was not built from a Brightway project: its impact methods cannot be added to")
        folder, project, database = built_from(entry)
        have = {tuple(x.name) for x in model.methods}
        names = [n for n in names if tuple(n) not in have]
        if not names:
            return {"added": 0, "message": "These methods are there already"}
        cache = Path(entry["cache"])
        chosen = cache.with_name(cache.name + ".add-methods.json")
        job["message"] = f"Reading {len(names)} impact methods from {project}"
        args = ["add-methods", "--project", project, "--database", database, "--cache", str(cache)]
        with build_lock(cache):
            names = [n for n in methods_library.build_entries(library, names, folder, project)[0] if tuple(n) not in have]
            if not names:
                return {"added": 0, "message": "These methods are there already, or no longer in the library"}
            # The library methods the project cache has are given again: the build process keeps only those it knows
            library_file(chosen, project_cache.method_names(cache) + names, folder, project, args)
            chosen.write_text(json.dumps(names), encoding="utf-8")
            try:
                # Their factors read, and their columns of G solved for every segment, in the build process
                cli(job, folder, [*args, "--methods-file", str(chosen)],
                    lambda line: line.startswith(("Reading", "Solving")) and line)
            finally:
                chosen.unlink(missing_ok=True)
                chosen.with_suffix(".library.json").unlink(missing_ok=True)
        methods, columns, unlinked = project_cache.method_columns(model, names)
        added = model.add_methods(methods, unlinked=unlinked, columns=columns)
        clean(cache)
        return {"added": added, "message": f"{added} impact {'method' if added == 1 else 'methods'} added"}

    @app.post("/api/methods/add")
    def add_methods(req: AddMethodsRequest):
        """Add impact methods to a database that is open (built from Brightway): their factors are read
        from its project, then only their impacts are computed. Follow it with ``/api/jobs/<id>``."""
        if req.key not in loaded:
            raise HTTPException(404, f"{req.key} is not loaded")
        if not req.methods:
            raise HTTPException(400, "Choose at least one impact method")
        return start("Add impact methods", ("methods", req.key), add_methods_job, req.key, req.methods,
                     message="Reading the impact methods")

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
        origin = getattr(m["model"], "project_cache", None)
        if origin:  # the folders of segments rebuilt since it was opened
            clean(origin["cache"])
        return listing()

    # ------------------------------------------------ Brightway projects: changes, in jobs

    def folder_of(path: str) -> str:
        return str(Path(path.strip().strip("'\"")).expanduser().resolve())

    def project_names(folder: str) -> list[str]:
        try:
            return sources.project_names(folder)
        except FileNotFoundError:
            return []

    def project_in(folder: str, name: str) -> None:
        if name not in project_names(folder):
            raise HTTPException(404, f"No project {name!r} in {folder}")

    def new_project_name(folder: str, name: str, empty: str) -> str:
        """``name`` stripped, for a project to make in ``folder``: not empty (``empty``: the message), not taken."""
        name = name.strip()
        if not name:
            raise HTTPException(400, empty)
        if name in project_names(folder):
            raise HTTPException(400, f"Project {name!r} already exists in {folder}")
        return name

    def ours(line: str):
        """The progress lines of lca_viewer.editing, not Brightway's own messages."""
        return line if line.startswith(("Created project", "Installing", "Deleted", "Restor", "Downloading", "Writing",
                                        "Renam", "Copying", "Reading")) \
            or line.endswith("impact methods installed") else None

    def uploaded(path: Path) -> None:
        """Delete a file the page uploaded (``/api/upload``) once it has been used."""
        if Path(path).resolve().parent == (data / "uploads").resolve():
            Path(path).unlink(missing_ok=True)

    # The methods library (data/methods): /api/library/methods…
    app.include_router(methods_library.router(library, folder_of, uploaded))

    @app.post("/api/brightway/projects")
    def create_project(req: ProjectRequest):
        folder = folder_of(req.folder)
        name = new_project_name(folder, req.name, "Give the project a name")

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
        project_in(folder, req.project)

        def work(job):
            cli(job, folder, ["project", "delete", req.project], ours)
            return {"message": f"Project {req.project} deleted"}

        return start(f"project {req.project}", ("delete", folder, req.project), work)

    @app.post("/api/brightway/projects/rename")
    def rename_project(req: RenameProjectRequest):
        """Rename a project, unless a model built from one of its databases is open."""
        folder = folder_of(req.folder)
        project_in(folder, req.project)
        new_name = new_project_name(folder, req.new_name, "Give the project a new name")
        if any((built_from(m) or ())[:2] == (folder, req.project) and loaded_key(m["path"]) for m in entries()):
            raise HTTPException(400, f"A database of {req.project} is open: close it first")

        def work(job):
            cli(job, folder, ["project", "rename", req.project, "--to", new_name], ours)
            return {"message": f"{req.project} is now {new_name}"}

        return start(f"project {req.project}", ("rename-project", folder, req.project), work)

    @app.post("/api/brightway/projects/copy")
    def copy_project(req: RenameProjectRequest):
        """A copy of a project, with its databases and impact methods, under another name."""
        folder = folder_of(req.folder)
        project_in(folder, req.project)
        new_name = new_project_name(folder, req.new_name, "Give the copy a name")

        def work(job):
            cli(job, folder, ["project", "copy", req.project, "--to", new_name], ours)
            return {"message": f"{new_name} is a copy of {req.project}"}

        return start(f"project {new_name}", ("copy-project", folder, new_name), work)

    @app.post("/api/brightway/databases/delete")
    def delete_database(req: DeleteDatabaseRequest):
        """Delete a database from a project, unless a database linking to it, or its open model, needs it."""
        folder = folder_of(req.folder)
        try:
            p = next((p for p in projects_in(folder) if p["name"] == req.project), None)
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
        if any(built_from(m) == (folder, req.project, req.database) and loaded_key(m["path"]) for m in entries()):
            raise HTTPException(400, f"{req.database} is open: close it first (Close, in the list of loaded databases)")

        def work(job):
            cli(job, folder, ["database", "delete", req.database, "--project", req.project], ours)
            return {"message": f"{req.database} removed from {req.project}"}

        return start(f"{req.database} ({req.project})", ("delete-database", folder, req.project, req.database), work)

    def twofive_project(folder: str, name: str) -> dict:
        """A project of the folder made with Brightway 2.5, or the error to show."""
        try:
            p = next((p for p in projects_in(folder) if p["name"] == name), None)
        except FileNotFoundError:
            p = None
        if p is None:
            raise HTTPException(404, f"No project {name!r} in {folder}")
        if not p["twofive"]:
            raise HTTPException(400, f"Project {name!r} was made with Brightway 2: upgrade it to Brightway 2.5 first")
        return p

    @app.post("/api/brightway/databases/rename")
    def rename_database(req: RenameDatabaseRequest):
        """Rename a database of a project, unless a database links to it, it is locked, or its model is open."""
        folder = folder_of(req.folder)
        new_name = req.new_name.strip()
        p = twofive_project(folder, req.project)
        names = [d["name"] for d in p["databases"]]
        if req.database not in names:
            raise HTTPException(404, f"No database {req.database!r} in project {req.project!r}")
        if not new_name:
            raise HTTPException(400, "Give the database a new name")
        if new_name in names:
            raise HTTPException(400, f"Project {req.project!r} already has a database {new_name!r}")
        users = [d["name"] for d in p["databases"] if req.database in d["depends"]]
        if users:
            raise HTTPException(400, f"{req.database} is used by {', '.join(users)}: it cannot be renamed while "
                                     f"{'it links' if len(users) == 1 else 'they link'} to it")
        if req.database in locked_read(folder, req.project):
            raise HTTPException(400, f"{req.database} is locked: unlock it first")
        if any(built_from(m) == (folder, req.project, req.database) and loaded_key(m["path"]) for m in entries()):
            raise HTTPException(400, f"{req.database} is open: close it first")

        def work(job):
            cli(job, folder, ["database", "rename", req.database, "--project", req.project, "--to", new_name], ours)
            return {"message": f"{req.database} is now {new_name}"}

        return start(f"{req.database} ({req.project})", ("rename-database", folder, req.project, req.database), work)

    @app.post("/api/brightway/databases/copy")
    def copy_database(req: CopyDatabaseRequest):
        """A copy of a database, of the same project or of another one, as a new database of ``project``."""
        folder = folder_of(req.folder)
        new_name = req.new_name.strip()
        target = twofive_project(folder, req.project)
        source = twofive_project(folder, req.from_project or req.project)
        if req.database not in [d["name"] for d in source["databases"]]:
            raise HTTPException(404, f"No database {req.database!r} in project {source['name']!r}")
        if not new_name:
            raise HTTPException(400, "Give the copy a name")
        if new_name in [d["name"] for d in target["databases"]]:
            raise HTTPException(400, f"Project {req.project!r} already has a database {new_name!r}")

        def work(job):
            cli(job, folder, ["database", "copy", req.database, "--project", req.project, "--to", new_name,
                              "--from-project", source["name"]], ours)
            return {"message": f"{new_name} is in {req.project}"}

        return start(f"{new_name} ({req.project})", ("copy-database", folder, req.project, new_name), work)

    @app.post("/api/brightway/defaults/install")
    def install_defaults_(req: DefaultsRequest):
        """Add the biosphere and impact methods of an ecoinvent version to a project (``lca-viewer defaults``)."""
        folder = folder_of(req.folder)
        if not re.fullmatch(r"bundled|\d+(\.\d+)+", req.version):
            raise HTTPException(400, f"Unknown default biosphere {req.version!r}")
        project_in(folder, req.project)

        def work(job):
            cli(job, folder, ["defaults", req.version, "--project", req.project, "--data", str(data)], ours)
            return {"message": f"Biosphere and impact methods of ecoinvent {req.version.replace('bundled', '3.9')} "
                               f"are in {req.project}"}

        return start(f"biosphere ({req.project})", ("defaults", folder, req.project, req.version), work,
                     message="Installing the biosphere and impact methods of Brightway (a few minutes)" if req.version == "bundled"
                     else f"Adding the biosphere and impact methods of ecoinvent {req.version} (a download of about 20 MB "
                          f"the first time, then several minutes)")

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
        project_in(folder, req.project)

        def work(job):
            cli(job, folder, ["add-remote", req.remote, "--project", req.project, "--data", str(data)], ours)
            return {"message": f"{req.remote} is in {req.project}"}

        return start(f"{req.remote} ({req.project})", ("add-remote", folder, req.project, req.remote), work,
                     message=f"Adding {req.remote} from files.brightway.dev (a download the first time, then a few minutes)")

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

        return start(f"project {name or req.remote or Path(req.file).name}", ("restore", folder, name, req.file, req.remote), work,
                     message=f"Downloading {req.remote} from files.brightway.dev" if req.remote else "Restoring the backup")

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
        project_in(folder, req.project)
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

        return start(f"{database} ({req.project})", ("import", folder, req.project, database), work,
                     message=f"Importing {database} into {req.project}")

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
        project_in(folder, project)
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

        return start(f"ecoinvent {req.version} {req.system_model}", ("ecoinvent", folder, project), work,
                     message=f"Downloading ecoinvent {req.version} {req.system_model} (about 1 GB, once), then importing it "
                             f"(10 to 20 minutes in all)")

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
                                     f"{'it' if len(hit) == 1 else 'them'} in Projects to change what it contains")

    def touched_by(change: dict) -> set[str]:
        return {d for d in (change.get("database"), (change.get("spec") or {}).get("database")) if d}

    @app.post("/api/brightway/lock")
    def lock_database(req: LockRequest):
        """Lock or unlock a database of a project: what it contains cannot be edited, deleted or added to."""
        import json

        folder = folder_of(req.folder)
        project_in(folder, req.project)
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
            p = next(p for p in projects_in(folder) if p["name"] == project)
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
        project_in(folder, req.project)
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

    @app.post("/api/brightway/dataset")
    def write_dataset(req: DatasetRequest):
        """Write a new dataset into a project, or write one again in place when ``spec`` has its
        ``code`` (``lca-viewer dataset``)."""
        import json
        import tempfile

        folder = folder_of(req.folder)
        project_in(folder, req.project)
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
