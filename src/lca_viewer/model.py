"""Matrix model and per-unit cumulative impacts.

Conventions (same as Brightway):

- ``A`` is the technosphere matrix, products x activities. The production
  exchange is on the diagonal (positive for normal products, negative for
  waste treatment); inputs are negative.
- ``B`` is the biosphere matrix, elementary flows x activities.
- ``U`` holds direct impacts per unit of activity operation, one row per
  impact method: ``U[k, j] = sum_f CF[k, f] * B[f, j]``.

The key quantity is ``G = (A^T)^-1 U^T`` (products x methods): ``G[p, k]`` is
the cumulative impact of supplying one unit of product ``p``, supply chain
included. It is computed with a single factorisation of ``A`` for all methods
and lets the tree be expanded lazily, node by node, at no extra cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import scipy.sparse as sp


def _factorized_solver(matrix: sp.csc_matrix):
    """Return ``solve(rhs)`` for ``matrix @ x = rhs``, with ``rhs`` 1D or 2D."""
    try:
        from pypardiso import PyPardisoSolver

        solver = PyPardisoSolver()
        csr = sp.csr_matrix(matrix)
        solver.factorize(csr)

        def solve(rhs):
            return solver.solve(csr, np.asarray(rhs, dtype=float))

        return solve
    except ImportError:
        from scipy.sparse.linalg import splu

        lu = splu(sp.csc_matrix(matrix))
        return lambda rhs: lu.solve(np.asarray(rhs, dtype=float))


@dataclass
class Method:
    """An impact method, as a vector of characterisation factors over ``B`` rows."""

    name: tuple[str, ...]
    unit: str
    cfs: np.ndarray


@dataclass
class LCAModel:
    """A square technosphere with metadata, independent of any LCA software.

    ``activities`` is a dataframe aligned with the columns of ``A`` and
    ``flows`` with the rows of ``B``. ``activities`` must have at least ``name``,
    ``reference_product``, ``location`` and ``unit`` columns. Column ``j``
    produces product ``j`` (square, single-output system).
    """

    A: sp.csc_matrix
    B: sp.csc_matrix
    activities: pd.DataFrame
    flows: pd.DataFrame
    methods: list[Method] = field(default_factory=list)

    def __post_init__(self):
        self.A = sp.csc_matrix(self.A, dtype=float)
        self.A.sum_duplicates()
        self.B = sp.csc_matrix(self.B, dtype=float)
        n = self.A.shape[0]
        if self.A.shape != (n, n):
            raise ValueError(f"A must be square, got {self.A.shape}")
        if self.B.shape[1] != n:
            raise ValueError("B must have one column per activity")
        self.diagonal = self.A.diagonal()
        if np.any(self.diagonal == 0):
            raise ValueError("A has zero entries on its diagonal")
        self._G = None
        self._U = None

    @property
    def size(self) -> int:
        return self.A.shape[0]

    @property
    def U(self) -> np.ndarray:
        """Direct impacts per unit of activity operation (methods x activities)."""
        if self._U is None:
            cfs = np.vstack([m.cfs for m in self.methods])
            self._U = np.asarray((sp.csr_matrix(cfs) @ self.B).todense())
        return self._U

    @property
    def G(self) -> np.ndarray:
        """Cumulative impacts per unit of product (products x methods)."""
        if self._G is None:
            solve = _factorized_solver(self.A.T.tocsc())
            G = solve(self.U.T)
            self._G = G.reshape(self.size, -1)
        return self._G

    def score(self, product: int, amount: float = 1.0) -> np.ndarray:
        """LCIA scores (one per method) for ``amount`` of ``product``."""
        return amount * self.G[product]

    def supply(self, product: int, amount: float = 1.0) -> np.ndarray:
        """Activity operation levels needed for ``amount`` of ``product``."""
        demand = np.zeros(self.size)
        demand[product] = amount
        return _factorized_solver(self.A)(demand)

    def method_index(self, name) -> int:
        name = tuple(name)
        for i, m in enumerate(self.methods):
            if m.name == name:
                return i
        raise KeyError(name)

    def save(self, directory) -> None:
        from pathlib import Path
        import pickle

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        sp.save_npz(directory / "A.npz", self.A)
        sp.save_npz(directory / "B.npz", self.B)
        self.activities.to_pickle(directory / "activities.pkl")
        self.flows.to_pickle(directory / "flows.pkl")
        with open(directory / "methods.pkl", "wb") as f:
            pickle.dump(self.methods, f)

    @classmethod
    def load(cls, directory) -> "LCAModel":
        from pathlib import Path
        import pickle

        directory = Path(directory)
        with open(directory / "methods.pkl", "rb") as f:
            methods = pickle.load(f)
        return cls(
            A=sp.load_npz(directory / "A.npz"),
            B=sp.load_npz(directory / "B.npz"),
            activities=pd.read_pickle(directory / "activities.pkl"),
            flows=pd.read_pickle(directory / "flows.pkl"),
            methods=methods,
        )
