"""The solver that follows the supply chains (lca_viewer.solver) gives the results of a plain sparse solve."""

import sys

import numpy as np
import pytest
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve

from lca_viewer import solver as solver_module
from lca_viewer.solver import Solver, supply_order


def technosphere(n=600, loops=((50, 180), (300, 305), (400, 402)), seed=0):
    """A random technosphere: each activity uses 0-3 products of activities before it (no loop), plus
    loops (all processes of a range supply the next one, the last the first), waste treatments
    (negative production) and inputs between loops."""
    rng = np.random.default_rng(seed)
    diag = np.where(rng.random(n) < 0.05, -1.0, 1.0)
    rows, cols, vals = list(range(n)), list(range(n)), list(diag * rng.uniform(0.5, 2, n))
    for j in range(1, n):
        for i in rng.choice(j, size=min(j, rng.integers(0, 4)), replace=False):
            rows.append(int(i)); cols.append(j); vals.append(-rng.uniform(0.01, 0.3))
    for a, b in loops:
        for i in range(a, b):
            rows.append(i); cols.append(i + 1 if i + 1 < b else a); vals.append(-rng.uniform(0.01, 0.2))
    A = sp.coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsr()
    perm = rng.permutation(n)  # not in supply order: the solver finds it
    return A[perm][:, perm].tocsc()


def check(A):
    rng = np.random.default_rng(1)
    s = Solver(A)
    b, C = rng.random(A.shape[0]), rng.random((A.shape[0], 4))
    np.testing.assert_allclose(s.solve(b), spsolve(A.tocsc(), b), rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(s.solve_transposed(C), spsolve(A.T.tocsc(), C), rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(s.solve(C[:, :2]), spsolve(A.tocsc(), C[:, :2]), rtol=1e-9, atol=1e-12)
    return s


def test_supply_order_puts_suppliers_first():
    A = technosphere()
    order, loop = supply_order(A)
    position = np.empty_like(order)
    position[order] = np.arange(len(order))
    rows, cols = sp.coo_matrix(A).nonzero()
    cross = loop[rows] != loop[cols]  # row: the product of a supplier of column's process
    assert (position[rows[cross]] < position[cols[cross]]).all()
    for k in np.unique(loop):  # the processes of a loop are consecutive
        p = np.sort(position[loop == k])
        assert p[-1] - p[0] == len(p) - 1


@pytest.mark.parametrize("large_loop", [100, 3])  # the 130-process loop alone, or the small ones too
def test_solves_match_a_plain_solve(monkeypatch, large_loop):
    monkeypatch.setattr(solver_module, "LARGE_LOOP", large_loop)
    s = check(technosphere())
    assert s.large_loops[0] == 130


def test_without_loops_and_one_big_loop():
    assert check(technosphere(loops=())).large_loops == []
    assert check(technosphere(n=300, loops=((0, 300),))).large_loops == [300]


def test_pardiso_for_large_loops_and_superlu_without_it(monkeypatch):
    monkeypatch.setattr(solver_module, "PARDISO_LOOP", 50)
    check(technosphere())  # with Pardiso when installed: the transposed solve reuses its factorisation
    monkeypatch.setitem(sys.modules, "pypardiso", None)  # as on macOS arm64
    check(technosphere())
