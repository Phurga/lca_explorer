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

:func:`group_contributors` lists the activities making up one group, and
:func:`main_path` finds the supply path along which an activity weighs most,
to show it in the tree.
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import dijkstra

from .model import LCAModel, _factorized_solver
from .query import derived_activities, evaluate

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


def _activity_values(model: LCAModel, product: int, assignment: np.ndarray, n_groups: int, rule: str, amount: float):
    """Impacts attributed to each activity (activities x methods) under ``rule``."""
    if rule == "direct":
        s = model.supply(product, amount)
        return s[:, None] * model.U.T
    is_matched = assignment < n_groups
    demand = np.zeros(model.size)
    demand[product] = amount
    s = _factorized_solver(_cut_inputs(model, is_matched))(demand)
    return np.where(
        is_matched[:, None],
        (s * model.diagonal)[:, None] * model.G,  # amount of product reaching it × cumulative
        s[:, None] * model.U.T,  # direct emissions of unmatched activities
    )


def direct_contribution(model: LCAModel, product: int, groups: dict[str, dict], amount: float = 1.0):
    """Direct emissions of all activities in the supply chain, summed by group."""
    assignment = _assign(model, groups)
    values = _activity_values(model, product, assignment, len(groups), "direct", amount)
    return _sum_by_group(values, assignment, groups)


def first_match_contribution(model: LCAModel, product: int, groups: dict[str, dict], amount: float = 1.0):
    """Cumulative impacts of the first matching activities on each supply path, by group."""
    assignment = _assign(model, groups)
    values = _activity_values(model, product, assignment, len(groups), "first_match", amount)
    return _sum_by_group(values, assignment, groups)


def group_contributors(model: LCAModel, product: int, groups: dict[str, dict], group: str,
                       rule: str = "first_match", amount: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Activities making up ``group`` (or ``OTHER``), in index order, and their impacts
    (activities x methods); the impacts sum to the group's total."""
    names = list(groups) + [OTHER]
    assignment = _assign(model, groups)
    values = _activity_values(model, product, assignment, len(groups), rule, amount)
    members = np.flatnonzero(assignment == names.index(group))
    members = members[np.any(values[members] != 0, axis=1)]
    return members, values[members]


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
