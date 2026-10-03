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
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp

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


def _sum_by_group(values: np.ndarray, assignment: np.ndarray, groups: dict) -> dict[str, np.ndarray]:
    names = list(groups) + [OTHER]
    totals = np.zeros((len(names), values.shape[1]))
    np.add.at(totals, assignment, values)
    return dict(zip(names, totals))


def direct_contribution(model: LCAModel, product: int, groups: dict[str, dict], amount: float = 1.0):
    """Direct emissions of all activities in the supply chain, summed by group."""
    s = model.supply(product, amount)
    values = s[:, None] * model.U.T
    return _sum_by_group(values, _assign(model, groups), groups)


def first_match_contribution(model: LCAModel, product: int, groups: dict[str, dict], amount: float = 1.0):
    """Cumulative impacts of the first matching activities on each supply path, by group."""
    assignment = _assign(model, groups)
    matched = assignment < len(groups)

    # Matched activities keep their production but lose their inputs
    A = model.A.tocoo()
    keep = (A.row == A.col) | ~matched[A.col]
    A_cut = sp.csc_matrix((A.data[keep], (A.row[keep], A.col[keep])), shape=A.shape)
    demand = np.zeros(model.size)
    demand[product] = amount
    s = _factorized_solver(A_cut)(demand)

    values = np.where(
        matched[:, None],
        (s * model.diagonal)[:, None] * model.G,  # amount of product reaching it × cumulative
        s[:, None] * model.U.T,  # direct emissions of unmatched activities
    )
    return _sum_by_group(values, assignment, groups)
