# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whole-supply-chain contribution analyses, grouped by queries.

Tree groups only bundle siblings. Questions like "how much does electricity
contribute, anywhere upstream?" need a global attribution rule, because
groups nest (electricity contains transport, which contains electricity).
:func:`supply_chain_contribution` has two exact rules; both sum to the total score.

- ``rule="direct"``: each activity's own emissions go to the first
  group that matches it (SimaPro's "process contribution").
- ``rule="first_match"``: walking down the supply chain from the
  functional unit, the first activity matching a group takes its whole
  cumulative impact (its supply chain included); unmatched activities keep
  their direct emissions. Computed exactly by cutting the inputs of matched
  activities and solving once, so loops are handled without traversal.

**Flow groups** (``flow_groups``: predicates over elementary flows, e.g. all
"Dinitrogen monoxide"; ``flow_owners``: optionally, a predicate over the activities
that emit them, e.g. only those of CPC section 1) go with the activity groups by
the order of the rules
(``order``): an activity group listed before a flow group keeps every flow it
reaches, the N2O of a fertiliser's supply chain included; a flow group listed
before it takes its flows from the activity group (under first match, from its
whole supply chain). What no activity group claims is split by the flow groups,
else *other*. Without an order, activity groups come first. The parts still add
up exactly to the total.

**Exchange groups**: a group whose predicate tests the exchange (``query.EXCHANGE_FIELDS``: its type, avoided
product or waste, its amount) matches edges of the supply chain, not activities: the input ``i`` of activity ``j``
(``A[i, j]``), with the fields of ``i``. Under first match a matched edge is cut like the inputs of a matched
activity, and the group takes the whole cumulative impact of what goes through it; under ``direct`` it takes the own
emissions of the share of ``i`` that the edge brings in. An edge goes to the first group in the order of the rules: an
activity group of ``i`` listed before the exchange group keeps it. Flow groups take from exchange groups as from
activity groups. A flow group can test the exchange too: the entries of ``B`` (``exchange_type`` resource or emission,
``exchange_amount`` per unit of the emitting activity's product).

:func:`group_contributors` lists the activities making up one group, and
:func:`main_path` finds the supply path along which an activity weighs most,
to show it in the tree.

:func:`flow_contribution` splits the score by elementary flow instead (the
whole inventory of the functional unit, characterised), and
:func:`flow_contributors` lists the activities emitting some flows.
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import dijkstra

from .model import LCAModel, _factorized_solver
import pandas as pd

from .query import EXCHANGE_FIELDS, derived_activities, derived_flows, evaluate, predicate_fields, uses_exchange

OTHER = "other"


def _matching(predicate: dict, frame) -> np.ndarray:
    """``evaluate``, except that a query testing the exchange (``query.EXCHANGE_FIELDS``) matches no activity: it
    matches edges (see :func:`_edge_mask`)."""
    if uses_exchange(predicate):
        return np.zeros(len(frame), dtype=bool)
    return evaluate(predicate, frame)


def _edges(model: LCAModel):
    """The entries of ``A`` (coordinates, values) with, off the diagonal, the type and amount of each exchange: the
    input ``row`` of activity ``col``, as recorded per unit of its reference product (avoided products and wastes
    negative), computed once per model."""
    if getattr(model, "_edge_cache", None) is None:
        A = model.A.tocoo()
        rows, cols, data = A.row.astype(np.int64), A.col.astype(np.int64), A.data
        off = rows != cols
        amount = np.where(off, -data / np.abs(model.diagonal[cols]), 0.0)
        kind = np.where(model.diagonal[rows] < 0, "waste", np.where(amount < 0, "avoided", "technosphere"))
        model._edge_cache = (rows, cols, data, off, amount, kind)
    return model._edge_cache


def _entry_mask(predicate: dict, table: pd.DataFrame, rows: np.ndarray, kind: np.ndarray, amount: np.ndarray) -> np.ndarray:
    """``predicate`` over some entries of a matrix: the fields of ``table`` at ``rows``, with the exchange of each."""
    fields = predicate_fields(predicate) - set(EXCHANGE_FIELDS)
    frame = pd.DataFrame({f: table[f].to_numpy()[rows] for f in fields})
    frame["_row"], frame["exchange_type"], frame["exchange_amount"] = rows, kind, amount
    return evaluate(predicate, frame)


def _edge_mask(model: LCAModel, predicate: dict) -> np.ndarray:
    """The entries of ``A`` (as :func:`_edges` lists them) that ``predicate`` matches: off the diagonal only."""
    rows, _, _, off, amount, kind = _edges(model)
    mask = np.zeros(len(rows), dtype=bool)
    mask[off] = _entry_mask(predicate, derived_activities(model), rows[off], kind[off], amount[off])
    return mask


def _flow_mask(model: LCAModel, predicate: dict, B: sp.csr_matrix) -> sp.csr_matrix:
    """0/1 on the entries of ``B`` that ``predicate`` matches (by flow, or by exchange when it tests it)."""
    flows = derived_flows(model)
    if not uses_exchange(predicate):
        return sp.diags(evaluate(predicate, flows).astype(float)) @ (B != 0).astype(float)
    E = B.tocoo()
    rows, cols = E.row.astype(np.int64), E.col.astype(np.int64)
    resource = flows["compartment"].astype(str).str.contains("resource", case=False).to_numpy()
    kind = np.where(resource[rows], "resource", "emission")
    amount = E.data / np.abs(model.diagonal[cols])
    hit = _entry_mask(predicate, flows, rows, kind, amount).astype(float)
    return sp.csr_matrix((hit, (rows, cols)), shape=B.shape)


def _assign(model: LCAModel, groups: dict[str, dict]) -> np.ndarray:
    """Group index per activity (first match wins), ``len(groups)`` for none."""
    activities = derived_activities(model)
    assignment = np.full(model.size, len(groups))
    for g, predicate in reversed(list(enumerate(groups.values()))):
        assignment[_matching(predicate, activities)] = g
    return assignment


def matched(model: LCAModel, groups: dict[str, dict]) -> np.ndarray:
    """Activities matched by at least one group."""
    return _assign(model, groups) < len(groups)


def _sum_by_group(values: np.ndarray, assignment: np.ndarray, groups: dict) -> dict[str, np.ndarray]:
    names = list(groups) + [OTHER]
    totals = np.zeros((len(names), values.shape[1]))
    np.add.at(totals, assignment, values)
    return dict(zip(names, totals))


def _cut_inputs(model: LCAModel, is_matched: np.ndarray, cut: np.ndarray | None = None) -> sp.csc_matrix:
    """``A`` where matched activities keep their production but lose their inputs, without the edges ``cut`` (a mask
    over the entries as :func:`_edges` lists them)."""
    rows, cols, data, off, _, _ = _edges(model)
    keep = ~off | ~is_matched[cols]
    if cut is not None:
        keep &= ~cut
    return sp.csc_matrix((data[keep], (rows[keep], cols[keep])), shape=model.A.shape)


def _flow_directs(model: LCAModel, flow_groups: dict[str, dict], flow_owners: dict[str, dict], methods) -> list[np.ndarray]:
    """Direct impacts of each flow group, per unit of activity operation (methods x activities).

    An emission (a flow of an activity) goes to the first flow group, in list order, whose predicate matches the flow
    and, when the group has an owner predicate, the activity that emits it."""
    if not flow_groups:
        return []
    flows = derived_flows(model)
    activities = derived_activities(model) if flow_owners else None
    remaining = sp.csr_matrix(model.B)
    CF = sp.csr_matrix(model.CF[methods])
    out = []
    for name, predicate in flow_groups.items():
        taken = sp.csr_matrix(remaining.multiply(_flow_mask(model, predicate, remaining)))
        if flow_owners.get(name):
            taken = taken @ sp.diags(evaluate(flow_owners[name], activities).astype(float))
        remaining = remaining - taken
        out.append(np.asarray((CF @ taken).todense()))
    return out


def _supply(model: LCAModel, product: int, assignment: np.ndarray, n_groups: int, rule: str, amount: float,
            cut: np.ndarray | None = None):
    """Operation levels the impacts are attributed from: the supply chain, or under first
    match the supply chain cut below matched activities and at the matched edges (``cut``)."""
    if rule == "direct":
        return model.supply(product, amount)
    demand = np.zeros(model.size)
    demand[product] = amount
    return _factorized_solver(_cut_inputs(model, assignment < n_groups, cut))(demand)


def _activity_values(model: LCAModel, s: np.ndarray, assignment: np.ndarray, n_groups: int, rule: str,
                     methods=slice(None)):
    """Impacts attributed to each activity (activities x methods) under ``rule``."""
    U = model.U[methods]
    if rule == "direct":
        return s[:, None] * U.T
    return np.where(
        (assignment < n_groups)[:, None],
        (s * model.diagonal)[:, None] * model.G[:, methods],  # amount of product reaching it × cumulative
        s[:, None] * U.T,  # direct emissions of unmatched activities
    )


NEVER = np.iinfo(np.int64).max  # the position of what no activity group claims


def _positions(groups: dict[str, dict], flow_groups: dict[str, dict], order: list[str] | None) -> tuple[list[int], list[int]]:
    """Where each activity group and each flow group stands in the order of the rules: a group listed in ``order``
    by its place there, the others after those, activity groups first (the order before there was one)."""
    rank = {name: i for i, name in enumerate(order or [])}
    base = len(rank)
    names = [*groups, *flow_groups]
    place = [rank.get(name, base + k) for k, name in enumerate(names)]
    return place[: len(groups)], place[len(groups):]


def _edge_groups(model: LCAModel, groups: dict[str, dict], places: list[int], activity_place: np.ndarray,
                 inputs_cut: np.ndarray | None) -> np.ndarray:
    """Per entry of ``A`` (:func:`_edges`): the exchange group taking it (an index in ``groups``), else -1. An edge goes
    to the first group in the order of the rules (``places``) that matches it, unless the activity group of its input
    comes before (``activity_place``: per activity); none out of an activity whose inputs are cut (``inputs_cut``)."""
    rows, cols, *_ = _edges(model)
    taken = np.full(len(rows), -1)
    best = activity_place[rows].copy()
    for k, predicate in enumerate(groups.values()):
        hit = _edge_mask(model, predicate) & (places[k] < best)
        taken[hit], best[hit] = k, places[k]
    if inputs_cut is not None:
        taken[inputs_cut[cols]] = -1
    return taken


def _attribute(model: LCAModel, product: int, groups: dict[str, dict], rule: str, amount: float,
               flow_groups: dict[str, dict], methods, order: list[str] | None = None,
               flow_owners: dict[str, dict] | None = None):
    """Per activity (activities x methods): the impacts of each activity group, of each flow group, of *other* and of
    each exchange group (on the activities its edges bring in).

    The groups go by the order of the rules (``order``: their names, first rule first): what an activity emits goes to
    its activity group or to a flow group, whichever is first. A flow group before the group of an activity takes its
    flows from it (under first match, from its whole supply chain: the cumulative impact of the flows of the group),
    a flow group after it takes nothing from it; the flows of an activity no group claims go to the first flow group
    that claims them, else to *other*. Without ``order``, activity groups come first.
    """
    edge_groups = {name: p for name, p in groups.items() if uses_exchange(p)}
    groups = {name: p for name, p in groups.items() if name not in edge_groups}
    n = len(groups)
    assignment = _assign(model, groups)
    places, flow_place = _positions({**groups, **edge_groups}, flow_groups, order)
    group_place, edge_place = places[:n], places[n:]
    place = np.array([*group_place, NEVER])[assignment]  # of the group each activity belongs to
    taken = _edge_groups(model, edge_groups, edge_place, place, assignment < n if rule == "first_match" else None)
    s = _supply(model, product, assignment, n, rule, amount, taken >= 0)
    # Per exchange group, the operation level of each activity that its edges bring in
    rows, cols, data, *_ = _edges(model)
    portions = []
    for k in range(len(edge_groups)):
        on = taken == k
        portions.append(np.bincount(rows[on], weights=-data[on] * s[cols[on]], minlength=model.size) / model.diagonal)
    own = s - sum(portions, np.zeros(model.size)) if rule == "direct" else s  # under first match the cut edges are not in ``s``
    matched = (assignment < n)[:, None]
    total = _activity_values(model, own, assignment, n, rule, methods)
    U, G = model.U[methods], model.G[:, methods]
    edge_values = {name: (p[:, None] * U.T if rule == "direct" else (p * model.diagonal)[:, None] * G)
                   for name, p in zip(edge_groups, portions)}
    directs = _flow_directs(model, flow_groups, flow_owners or {}, methods)
    last = max(group_place, default=-1)
    reaching = (own * model.diagonal)[:, None]  # amount of product reaching each activity
    flow_values, flow_own = {}, []
    for h, name in enumerate(flow_groups):
        direct = directs[h]  # methods x activities
        part = own[:, None] * direct.T
        before = [k for k in range(len(edge_groups)) if flow_place[h] < edge_place[k]]  # exchange groups it takes from
        cumulative = model.cumulative(direct) if rule == "first_match" and (flow_place[h] < last or before) else None
        if rule == "first_match" and flow_place[h] < last:  # before some activity group: its flows in their supply chains
            part = np.where(matched, reaching * cumulative, part)
        flow_own.append(np.where((place > flow_place[h])[:, None], part, 0.0))
        flow_values[name] = flow_own[-1]
        for k in before:
            name_k = list(edge_groups)[k]
            take = (portions[k][:, None] * direct.T if rule == "direct"
                    else (portions[k] * model.diagonal)[:, None] * cumulative)
            flow_values[name] = flow_values[name] + take
            edge_values[name_k] = edge_values[name_k] - take
    rest = total - sum(flow_own, 0.0)
    return assignment, np.where(matched, rest, 0.0), flow_values, np.where(matched, 0.0, rest), edge_values


def supply_chain_contribution(model: LCAModel, product: int, groups: dict[str, dict], rule: str = "first_match",
                              amount: float = 1.0, flow_groups: dict[str, dict] | None = None, methods=slice(None),
                              order: list[str] | None = None, flow_owners: dict[str, dict] | None = None):
    """Activity groups (``rule``), flow groups and *other*: name -> impacts (``methods``)."""
    assignment, values, flow_values, other, edge_values = _attribute(model, product, groups, rule, amount, flow_groups or {},
                                                                     methods, order, flow_owners)
    activity_groups = {name: p for name, p in groups.items() if name not in edge_values}
    result = _sum_by_group(values, assignment, activity_groups)
    del result[OTHER]
    result.update({name: v.sum(axis=0) for name, v in edge_values.items()})
    result = {name: result[name] for name in groups}  # in the order of the groups asked
    return {**result, **{name: v.sum(axis=0) for name, v in flow_values.items()}, OTHER: other.sum(axis=0)}


def group_contributors(model: LCAModel, product: int, groups: dict[str, dict], group: str,
                       rule: str = "first_match", amount: float = 1.0, flow_groups: dict[str, dict] | None = None,
                       methods=slice(None), order: list[str] | None = None,
                       flow_owners: dict[str, dict] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Activities making up ``group`` (an activity or flow group, or ``OTHER``), in index
    order, and their impacts (activities x methods); the impacts sum to the group's total."""
    flow_groups = flow_groups or {}
    assignment, values, flow_values, other, edge_values = _attribute(model, product, groups, rule, amount, flow_groups,
                                                                     methods, order, flow_owners)
    if group == OTHER:
        values = other
    elif group in edge_values:  # the activities its edges bring in
        values = edge_values[group]
    elif group in groups:
        activity_groups = [name for name in groups if name not in edge_values]
        values = np.where((assignment == activity_groups.index(group))[:, None], values, 0.0)
    elif group in flow_values:
        values = flow_values[group]
    else:
        raise ValueError(f"Unknown group {group!r}")
    members = np.flatnonzero(np.any(values != 0, axis=1))
    return members, values[members]


def flow_contribution(model: LCAModel, product: int, method: int, amount: float = 1.0):
    """Inventory of the whole supply chain (amount per elementary flow) and its
    characterised impacts for ``method``; the impacts sum to the total score."""
    inventory = model.B @ model.supply(product, amount)
    return inventory, inventory * model.methods[method].cfs


def flow_contributors(model: LCAModel, product: int, flows, method: int, amount: float = 1.0):
    """Activities emitting ``flows`` in the supply chain (index order), with the amount
    they emit (summed over ``flows``), its impact for ``method`` and, for each, the flow
    weighing most (e.g. the compartment a substance mostly goes to)."""
    flows = np.asarray(flows, dtype=int)
    s = model.supply(product, amount)
    B = model.B[flows]
    amounts = np.asarray(B.sum(axis=0)).ravel() * s
    per_flow = np.abs(B.toarray() * model.methods[method].cfs[flows][:, None])
    impacts = (model.methods[method].cfs[flows] @ B) * s
    members = np.flatnonzero(impacts != 0)
    return members, amounts[members], impacts[members], flows[per_flow[:, members].argmax(axis=0)]


def uncharacterised(model: LCAModel, s: np.ndarray, method: int, reference: int | None = None) -> dict:
    """The elementary flows of the inventory ``B·s`` (``s``: operation levels of a supply chain) that ``method`` has no
    factor for, whatever their amount: what the score leaves out. For each flow (index order): its ``amount``, the
    number of activities of the supply chain emitting it (``emitters``), the number of methods of the model that do
    characterise it (``methods``) and, with ``reference``, the factor of that method (``reference_cf``). Also the
    number of flows of the inventory (``inventory``) and of those characterised (``characterised``)."""
    inventory = model.B @ s
    cfs = model.methods[method].cfs
    in_inventory = inventory != 0
    flows = np.flatnonzero(in_inventory & (cfs == 0))
    operating = np.flatnonzero(s)
    B = model.B[:, operating].tocsr()[flows]
    out = {
        "flows": flows,
        "amounts": inventory[flows],
        "emitters": np.asarray((B != 0).sum(axis=1)).ravel(),
        "methods": np.count_nonzero(model.CF[:, flows], axis=0) if model.methods else np.zeros(len(flows), dtype=int),
        "inventory": int(np.count_nonzero(in_inventory)),
        "characterised": int(np.count_nonzero(in_inventory & (cfs != 0))),
    }
    if reference is not None:
        out["reference_cf"] = model.methods[reference].cfs[flows]
    return out


def _flow_keys(names, categories) -> list[tuple[str, tuple]]:
    return [(str(n).strip().lower(), tuple(c) if isinstance(c, (tuple, list)) else ()) for n, c in zip(names, categories)]


def method_coverage(model: LCAModel, method: int, reference: int | None = None) -> dict:
    """How well ``method`` covers the elementary flows of the whole model (every activity, not one supply chain).

    - ``linked``: its factors on flows of the model; ``characterised_unused``: of those, on flows no activity
      exchanges (a row of ``B`` with no amount).
    - ``uncharacterised``: the flows that at least one activity exchanges and that it has no factor for, as arrays
      ``flows``, ``emitters`` (number of activities exchanging it), ``methods`` (number of methods of the model that
      characterise it) and, with ``reference``, ``reference_cf``; most emitters first. ``inventory``: how many flows
      activities exchange.
    - ``unlinked``: its factors on flows that are not in the model (a table, see ``LCAModel.unlinked``; None when the
      model did not record them), each with ``match``: a flow of the model with the same name and compartments
      (``match_kind`` "same"), else with the same name only ("name"), else -1 (""): a flow of another biosphere
      version, or one the inventory never uses.
    """
    cfs = model.methods[method].cfs
    emitters = np.asarray((model.B != 0).sum(axis=1)).ravel()
    used = emitters > 0
    missing = np.flatnonzero(used & (cfs == 0))
    order = np.lexsort((missing, -emitters[missing]))
    missing = missing[order]
    out = {
        "linked": int(np.count_nonzero(cfs)),
        "characterised_unused": int(np.count_nonzero((cfs != 0) & ~used)),
        "inventory": int(np.count_nonzero(used)),
        "uncharacterised": {
            "flows": missing,
            "emitters": emitters[missing],
            "methods": np.count_nonzero(model.CF[:, missing], axis=0),
        },
        "unlinked": None,
    }
    if reference is not None:
        out["uncharacterised"]["reference_cf"] = model.methods[reference].cfs[missing]
    out["unlinked"] = unlinked_factors(model, method)
    return out


def unlinked_factors(model: LCAModel, method: int) -> pd.DataFrame | None:
    """The factors of ``method`` on flows that are not in the model (see ``LCAModel.unlinked``; None when the model did
    not record them), each with ``match``: a flow of the model with the same name and compartments (``match_kind``
    "same"), else with the same name only ("name"), else -1 (""): a flow of another biosphere version, or one the
    inventory never uses."""
    if model.unlinked is None:
        return None
    table = model.unlinked[model.unlinked["method"] == method].reset_index(drop=True).copy()
    flows = model.flows
    categories = flows["categories"] if "categories" in flows else pd.Series([()] * len(flows))
    exact: dict[tuple, int] = {}
    by_name: dict[str, int] = {}
    for i, key in enumerate(_flow_keys(flows["name"], categories)):
        exact.setdefault(key, i)
        by_name.setdefault(key[0], i)
    match, kind = [], []
    for key in _flow_keys(table["name"], table["categories"]):
        if key in exact:
            match.append(exact[key]); kind.append("same")
        elif key[0] in by_name:
            match.append(by_name[key[0]]); kind.append("name")
        else:
            match.append(-1); kind.append("")
    table["match"] = np.asarray(match, dtype=int)
    table["match_kind"] = kind
    return table


def direct_emitters(model: LCAModel) -> sp.csr_matrix:
    """``B`` by rows (flows), explicit zeros dropped: row ``f`` lists the activities emitting (or taking) flow ``f``
    directly. Worth keeping: ``np.diff(indptr)`` counts them."""
    B = model.B.tocsr(copy=True)
    B.eliminate_zeros()
    return B


def _relative(cfs: np.ndarray, units: np.ndarray) -> np.ndarray:
    """|CF| over the largest |CF| of the flows of the same unit (factors in kg and in kBq do not compare)."""
    out = np.zeros(len(cfs))
    for u in pd.unique(units):
        same = units == u
        top = np.abs(cfs[same]).max(initial=0.0)
        if top:
            out[same] = np.abs(cfs[same]) / top
    return out


def compare_methods(model: LCAModel, a: int, b: int | None = None, s: np.ndarray | None = None,
                    emitters: sp.csr_matrix | None = None) -> pd.DataFrame:
    """The elementary flows of the model that impact category ``a`` (and ``b``, when given) characterises, one row per
    flow (index: the row of ``B``), largest first.

    Columns: ``cf_a``, ``rel_a`` (|CF| relative to the largest of the same unit in the method), ``emitters`` (activities
    emitting it directly); with ``b``: ``cf_b``, ``rel_b``, ``ratio`` (``cf_b / cf_a``, NaN unless both have one) and
    ``only`` ("a", "b" or ""); with ``s`` (operation levels of a functional unit): ``amount`` (its inventory, ``B·s``)
    and ``impact_a`` (``impact_b``), the contribution of the flow to the score. Sorted by the largest share of a score
    when ``s`` is given, else by the largest relative factor.

    Only flows of ``B`` are listed: the factors of a method on flows that are not in the model are in
    :func:`unlinked_factors` (when the model recorded them).
    """
    cf_a = model.methods[a].cfs
    cf_b = model.methods[b].cfs if b is not None else np.zeros_like(cf_a)
    flows = np.flatnonzero((cf_a != 0) | (cf_b != 0))
    units = model.flows["unit"].astype(str).to_numpy() if "unit" in model.flows else np.full(len(cf_a), "")
    emitters = direct_emitters(model) if emitters is None else emitters
    table = pd.DataFrame({
        "cf_a": cf_a[flows],
        "rel_a": _relative(cf_a, units)[flows],
        "emitters": np.diff(emitters.indptr)[flows],
    }, index=pd.Index(flows, name="flow"))
    weight = table["rel_a"].to_numpy().copy()
    if b is not None:
        table["cf_b"] = cf_b[flows]
        table["rel_b"] = _relative(cf_b, units)[flows]
        both = (table["cf_a"] != 0) & (table["cf_b"] != 0)
        table["ratio"] = np.where(both, table["cf_b"] / table["cf_a"].where(both, 1.0), np.nan)
        table["only"] = np.where(both, "", np.where(table["cf_a"] != 0, "a", "b"))
        weight = np.maximum(weight, table["rel_b"].to_numpy())
    if s is not None:
        amount = (model.B @ s)[flows]
        table["amount"] = amount
        shares = []
        for key, cfs in (("a", cf_a), ("b", cf_b if b is not None else None)):
            if cfs is None:
                continue
            impact = amount * cfs[flows]
            table[f"impact_{key}"] = impact
            scale = np.abs(impact).sum()
            shares.append(np.abs(impact) / scale if scale else np.zeros(len(flows)))
        weight = np.maximum.reduce(shares)
        # flows of the inventory first, then by factor: what this process emits leads, what it does not is still listed
        order = np.lexsort((-table[["rel_a", "rel_b"] if b is not None else ["rel_a"]].max(axis=1).to_numpy(), -weight))
    else:
        order = np.argsort(-weight, kind="stable")
    return table.iloc[order]


def category_flows(model: LCAModel, method: int, s: np.ndarray | None = None,
                   emitters: sp.csr_matrix | None = None) -> pd.DataFrame:
    """The elementary flows that impact category ``method`` characterises: :func:`compare_methods` with one method."""
    return compare_methods(model, method, None, s, emitters)


def main_path(model: LCAModel, product: int, target: int, method: int,
              blocked: np.ndarray | None = None) -> list[int] | None:
    """Products from ``product`` to ``target`` along the path that carries the largest
    share of the root's impact (``method``) to ``target``, or None if unreachable.

    Each edge weighs the share of the parent's |impact| that flows through the child;
    the best path maximises the product of shares (shortest path on -log share).
    Inputs of ``blocked`` activities are not followed (e.g. activities matched by a
    group under the first-match rule, whose supply chain belongs to that group).
    """
    if product == target:
        return [product]
    g = np.abs(model.G[:, method])
    A = model.A.tocoo()
    edge = (A.row != A.col) & (A.data != 0)
    if blocked is not None:
        edge &= ~blocked[A.col]
    parent, child = A.col[edge], A.row[edge]
    flow = np.abs(A.data[edge] / model.diagonal[parent]) * g[child]
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(g[parent] > 0, flow / g[parent], 0.0)
    useful = share > 0
    # Shares above 1 (negative impacts elsewhere) count as 1; tiny weights keep edges explicit
    weight = np.maximum(-np.log(np.minimum(share[useful], 1.0)), 1e-12)
    graph = sp.csr_matrix((weight, (parent[useful], child[useful])), shape=A.shape)
    _, predecessors = dijkstra(graph, indices=product, return_predecessors=True)
    if predecessors[target] < 0:
        return None
    path = [target]
    while path[-1] != product:
        path.append(int(predecessors[path[-1]]))
    return path[::-1]
