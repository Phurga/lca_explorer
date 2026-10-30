"""The server answers requests in threads: solves from several at once must give the serial results."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import scipy.sparse as sp

from lca_viewer.model import LCAModel, Method, _factorized_solver


def _chain_model(n=400, seed=0):
    """A random acyclic supply system: activity j takes inputs from activities after it."""
    rng = np.random.default_rng(seed)
    rows, cols, data = list(range(n)), list(range(n)), [1.0] * n
    for j in range(n - 1):
        for i in rng.choice(np.arange(j + 1, n), size=min(3, n - j - 1), replace=False):
            rows.append(int(i)), cols.append(j), data.append(-float(rng.uniform(0.01, 0.3)))
    A = sp.csc_matrix((data, (rows, cols)), shape=(n, n))
    B = sp.csc_matrix(rng.uniform(0, 1, size=(3, n)))
    activities = pd.DataFrame({"name": [f"a{i}" for i in range(n)], "reference_product": [f"p{i}" for i in range(n)],
                               "location": ["GLO"] * n, "unit": ["kg"] * n})
    flows = pd.DataFrame({"name": ["x", "y", "z"], "unit": ["kg"] * 3})
    methods = [Method(("m",), "u", np.array([1.0, 2.0, 3.0]))]
    return LCAModel(A, B, activities, flows, methods)


def test_concurrent_supply_matches_serial():
    model = _chain_model()
    products = list(range(0, 400, 7)) * 4
    expected = {p: model.supply(p) for p in set(products)}
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda p: (p, model.supply(p)), products))
    assert all(np.allclose(s, expected[p]) for p, s in results)


def test_concurrent_solvers_of_different_matrices():
    model = _chain_model()
    other = _chain_model(seed=1)
    rhs = np.ones(400)
    expected = [_factorized_solver(m.A)(rhs) for m in (model, other)]
    solvers = [_factorized_solver(m.A) for m in (model, other)]
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda k: (k % 2, solvers[k % 2](rhs)), range(64)))
    assert all(np.allclose(x, expected[k]) for k, x in results)
