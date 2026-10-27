"""Whole-supply-chain contribution analyses, grouped by queries.

Tree groups only bundle siblings. Questions like "how much does electricity
contribute, anywhere upstream?" need a global attribution rule, because
groups nest (electricity contains transport, which contains electricity).
Two exact rules are provided; both sum to the total score.

- :func:`direct_contribution`: each activity's own emissions go to the first
  group that matches it (SimaPro's "process contribution").
- :func:`first_match_contribution`: walking down the supply chain from the
  functional unit, the first activity matching a group takes its whole
  cumulative impact (its supply chain included); unmatched activities keep
  their direct emissions. Computed exactly by cutting the inputs of matched
  activities and solving once, so loops are handled without traversal.

**Flow groups** (``flow_groups``: predicates over elementary flows, e.g. all
"Dinitrogen monoxide") split what the activity groups leave: the direct
emissions of activities outside any group (under first match: outside the
supply chains of matched activities too). An activity group keeps every flow
it reaches first, the N2O of a fertiliser's supply chain included; the parts
still add up exactly to the total.

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
from .query import derived_activities, derived_flows, evaluate

OTHER = "other"


def _assign(model: LCAModel, groups: dict[str, dict]) -> np.ndarray:
    """Group index per activity (first match wins), ``len(groups)`` for none."""
    activities = derived_activities(model)
    assignment = np.full(model.size, len(groups))
    for g, predicate in reversed(list(enumerate(groups.values()))):
        assignment[evaluate(predicate, activities)] = g
    return assignment


def matched(model: LCAModel, groups: dict[str, dict]) -> np.ndarray:
    """Activities matched by at least one group."""
    return _assign(model, groups) < len(groups)


def _sum_by_group(values: np.ndarray, assignment: np.ndarray, groups: dict) -> dict[str, np.ndarray]:
    names = list(groups) + [OTHER]
    totals = np.zeros((len(names), values.shape[1]))
    np.add.at(totals, assignment, values)
    return dict(zip(names, totals))


def _cut_inputs(model: LCAModel, is_matched: np.ndarray) -> sp.csc_matrix:
    """``A`` where matched activities keep their production but lose their inputs."""
    A = model.A.tocoo()
    keep = (A.row == A.col) | ~is_matched[A.col]
    return sp.csc_matrix((A.data[keep], (A.row[keep], A.col[keep])), shape=A.shape)


def _flow_assign(model: LCAModel, flow_groups: dict[str, dict]) -> np.ndarray:
    """Flow group index per elementary flow (first match wins), ``len(flow_groups)`` for none."""
    flows = derived_flows(model)
    assignment = np.full(len(flows), len(flow_groups))
    for g, predicate in reversed(list(enumerate(flow_groups.values()))):
        assignment[evaluate(predicate, flows)] = g
    return assignment


def _direct_of(model: LCAModel, flows: np.ndarray, methods) -> np.ndarray:
    """Direct impacts of ``flows`` only, per unit of activity operation (methods x activities)."""
    cf = sp.csr_matrix(model.CF[methods][:, flows])
    return np.asarray((cf @ model.B[flows]).todense())


def _supply(model: LCAModel, product: int, assignment: np.ndarray, n_groups: int, rule: str, amount: float):
    """Operation levels the impacts are attributed from: the supply chain, or under first
    match the supply chain cut below matched activities."""
    if rule == "direct":
        return model.supply(product, amount)
    demand = np.zeros(model.size)
    demand[product] = amount
    return _factorized_solver(_cut_inputs(model, assignment < n_groups))(demand)


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


def _attribute(model: LCAModel, product: int, groups: dict[str, dict], rule: str, amount: float,
               flow_groups: dict[str, dict], methods):
    """Per activity (activities x methods): the impacts of each activity and flow group and of
    *other*. Flow groups take their flows from the direct emissions of unmatched activities."""
    assignment = _assign(model, groups)
    s = _supply(model, product, assignment, len(groups), rule, amount)
    values = _activity_values(model, s, assignment, len(groups), rule, methods)
    unmatched = (assignment == len(groups))[:, None]
    flow_assignment = _flow_assign(model, flow_groups)
    flow_values = {}
    for g, name in enumerate(flow_groups):
        flow_values[name] = np.where(unmatched, s[:, None] * _direct_of(model, flow_assignment == g, methods).T, 0.0)
    other = np.where(unmatched, values, 0.0) - sum(flow_values.values(), 0.0)
    return assignment, values, flow_values, other


def supply_chain_contribution(model: LCAModel, product: int, groups: dict[str, dict], rule: str = "first_match",
                              amount: float = 1.0, flow_groups: dict[str, dict] | None = None, methods=slice(None)):
    """Activity groups (``rule``), flow groups and *other*: name -> impacts (``methods``)."""
    assignment, values, flow_values, other = _attribute(model, product, groups, rule, amount, flow_groups or {}, methods)
    result = _sum_by_group(values, assignment, groups)
    del result[OTHER]
    return {**result, **{name: v.sum(axis=0) for name, v in flow_values.items()}, OTHER: other.sum(axis=0)}


def direct_contribution(model: LCAModel, product: int, groups: dict[str, dict], amount: float = 1.0):
    """Direct emissions of all activities in the supply chain, summed by group."""
    return supply_chain_contribution(model, product, groups, "direct", amount)


def first_match_contribution(model: LCAModel, product: int, groups: dict[str, dict], amount: float = 1.0):
    """Cumulative impacts of the first matching activities on each supply path, by group."""
    return supply_chain_contribution(model, product, groups, "first_match", amount)


def group_contributors(model: LCAModel, product: int, groups: dict[str, dict], group: str,
                       rule: str = "first_match", amount: float = 1.0, flow_groups: dict[str, dict] | None = None,
                       methods=slice(None)) -> tuple[np.ndarray, np.ndarray]:
    """Activities making up ``group`` (an activity or flow group, or ``OTHER``), in index
    order, and their impacts (activities x methods); the impacts sum to the group's total."""
    flow_groups = flow_groups or {}
    assignment, values, flow_values, other = _attribute(model, product, groups, rule, amount, flow_groups, methods)
    if group == OTHER:
        values = other
    elif group in groups:
        values = np.where((assignment == list(groups).index(group))[:, None], values, 0.0)
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
