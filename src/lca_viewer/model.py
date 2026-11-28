# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Matrix model and per-unit cumulative impacts.

Conventions (same as Brightway):

- ``A`` is the technosphere matrix, products x activities. The production
  exchange is on the diagonal (positive for normal products, negative for
  waste treatment); inputs are negative.
- ``B`` is the biosphere matrix, elementary flows x activities.
- ``U`` holds direct impacts per unit of activity operation, one row per
  impact method: ``U[k, j] = sum_f CF[k, f] * B[f, j]``. It is as large as ``G``,
  so it is not kept: ``direct(j)`` gives a column, ``direct_impacts(methods)`` the rows
  an analysis uses.

The key quantity is ``G = (A^T)^-1 U^T`` (products x methods): ``G[p, k]`` is
the cumulative impact of supplying one unit of product ``p``, supply chain
included. It is computed with a single factorisation of ``A`` for all methods
and lets the tree be expanded lazily, node by node, at no extra cost.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp


from .solver import SOLVER_LOCK as _SOLVER_LOCK, Solver


def _factorized_solver(matrix: sp.csc_matrix):
    """Return ``solve(rhs)`` for ``matrix @ x = rhs``, with ``rhs`` 1D or 2D (safe to call from threads)."""
    with _SOLVER_LOCK:
        return Solver(matrix).solve


# Elements of G solved at once (100 MB): G is solved by chunks of methods, so the peak is G and one chunk
G_CHUNK = 12_500_000

# The files of the model that ``G`` is made from: if one changes (the model was built again), the cached ``G`` is not used
G_SOURCES = ("A.npz", "B.npz", "methods.pkl")

# Exchange cache (SQLite): see exchange_store
from .exchange_store import EXCHANGE_COLUMNS, PEDIGREE  # noqa: E402,F401  (named here by the server and tests)
from .exchange_store import read as _read_exchanges  # noqa: E402
from .exchange_store import write as _write_exchanges  # noqa: E402


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
    details: list[dict] | None = None
    """Optional descriptive metadata per activity (description, dates, …); loaded lazily from a cache."""
    exchanges: list[tuple[int, str, int, dict]] | None = None
    """Optional metadata of each exchange (comment, uncertainty, pedigree, …) as ``(activity, type,
    row, data)``: ``row`` is a product (row of ``A``), or a flow (row of ``B``) for "biosphere"
    exchanges. Cached in an SQLite file, read one activity at a time."""
    unlinked: pd.DataFrame | None = None
    """Optional characterisation factors of the methods on flows that are not rows of ``B`` (dropped by the LCA): one
    row per factor with ``method`` (position in ``methods``), ``id``, ``name``, ``categories``, ``unit`` and ``cf``.
    None when not recorded (models built before it, or by importers that do not read the methods' own factors)."""

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
        self._g_lock = threading.Lock()
        self._directory = None  # where the model was loaded from: G is kept there too
        self._solver = None  # one factorisation for A and A^T, made on first use
        self._solver_lock = threading.Lock()
        self._CF = None
        self._details_paths = []  # details.pkl files, one per segment of a project cache, read on first use
        self._exchanges_path = None
        # A model put together from a project cache: (first activity, end, SQLite file) of each run of activities whose
        # exchanges are read (one or more per segment), whose rows are node ids, and the node ids of the model's
        # products and flows (sorted) with their rows
        self._exchange_parts = None
        self._node_rows = None
        # A model opened while its build still reads the exchanges: they come later (``project_cache.attach_exchanges``)
        self.exchanges_pending = False
        self.exchanges_error = None

    @property
    def size(self) -> int:
        return self.A.shape[0]

    @property
    def CF(self) -> sp.csr_matrix:
        """Characterisation factors (methods x elementary flows), sparse: a method characterises a few flows."""
        if self._CF is None:
            self._CF = sp.csr_matrix(np.vstack([m.cfs for m in self.methods]) if self.methods
                                     else np.zeros((0, self.B.shape[0])))
        return self._CF

    def direct(self, j: int) -> np.ndarray:
        """Direct impacts of one unit of operation of activity ``j``, one per method (a column of ``U``)."""
        return np.asarray(self.CF @ self.B[:, [j]].toarray()).ravel()

    def direct_impacts(self, methods=slice(None)) -> np.ndarray:
        """Direct impacts per unit of activity operation (``methods`` x activities): the rows of ``U`` an analysis uses."""
        return np.asarray((self.CF[methods] @ self.B).todense())

    @property
    def U(self) -> np.ndarray:
        """Direct impacts per unit of activity operation (methods x activities), computed at each call, not kept: as
        large as ``G``. The app uses :meth:`direct` and :meth:`direct_impacts`."""
        return self.direct_impacts()

    def _solve_G(self, CF: sp.csr_matrix) -> np.ndarray:
        """``(A^T)^-1 (CF B)^T``: the columns of ``G`` of the methods ``CF``, solved by chunks of methods."""
        out = np.empty((self.size, CF.shape[0]))
        step = max(1, G_CHUNK // max(self.size, 1))
        for a in range(0, CF.shape[0], step):
            U = np.asarray((CF[a:a + step] @ self.B).todense())
            out[:, a:a + step] = self.solver.solve_transposed(U.T).reshape(self.size, -1)
        return out

    @property
    def G(self) -> np.ndarray:
        """Cumulative impacts per unit of product (products x methods). Read from the cache next to the model
        files when it was made from them, else computed (about 10 s for ecoinvent with 762 methods: the solves,
        not the reading, are what takes time) and cached."""
        if self._G is None:
            with self._g_lock:
                if self._G is None:
                    G = self._read_cached_G()
                    if G is None:
                        G = self._solve_G(self.CF)
                        self._write_cached_G(G)
                    self._G = G
        return self._G

    @property
    def solver(self) -> Solver:
        """Factorisation of ``A``, for ``A`` and ``A^T`` (seconds for ecoinvent), made on first use."""
        if self._solver is None:
            with self._solver_lock:
                if self._solver is None:
                    self._solver = Solver(self.A)
        return self._solver

    def _g_meta(self) -> dict | None:
        """What the cached ``G`` is made from: the files of the model (size and time) and its shape."""
        if self._directory is None:
            return None
        try:
            sources = {name: [(self._directory / name).stat().st_size, (self._directory / name).stat().st_mtime_ns]
                       for name in G_SOURCES}
        except OSError:
            return None
        return {"sources": sources, "shape": [self.size, len(self.methods)]}

    def _read_cached_G(self) -> np.ndarray | None:
        meta = self._g_meta()
        if meta is None:
            return None
        try:
            if json.loads((self._directory / "G.json").read_text()) != meta:
                return None
            G = np.load(self._directory / "G.npy")
        except (OSError, ValueError):
            return None
        return G if G.shape == tuple(meta["shape"]) else None

    def _write_cached_G(self, G: np.ndarray) -> None:
        meta = self._g_meta()
        if meta is None:
            return
        try:
            tmp = self._directory / "G.tmp.npy"
            np.save(tmp, G)
            os.replace(tmp, self._directory / "G.npy")
            (self._directory / "G.json").write_text(json.dumps(meta))
        except OSError:
            pass  # a folder that cannot be written: it is computed again next time

    def score(self, product: int, amount: float = 1.0) -> np.ndarray:
        """LCIA scores (one per method) for ``amount`` of ``product``."""
        return amount * self.G[product]

    def supply(self, product: int, amount: float = 1.0) -> np.ndarray:
        """Activity operation levels needed for ``amount`` of ``product``."""
        demand = np.zeros(self.size)
        demand[product] = amount
        # Factorised once: each functional unit is then one solve, not a new factorisation
        return self.solver.solve(demand)

    def cumulative(self, direct: np.ndarray) -> np.ndarray:
        """Cumulative impacts per unit of product (products x columns) of ``direct`` impacts per unit of
        activity operation (columns x activities), as ``G`` is of ``U``: ``(A^T)^-1 direct^T``. Used for
        part of an inventory (a group of flows)."""
        out = self.solver.solve_transposed(np.asarray(direct, dtype=float).T)
        return out.reshape(self.size, -1)

    def add_methods(self, methods: list[Method], unlinked: pd.DataFrame | None = None,
                    columns: np.ndarray | None = None) -> int:
        """Add impact methods to the model (those it has already are passed over), after the others, so the
        positions of these do not move. Only their columns of ``G`` are computed (one solve), and the model's
        files are written again with them when it was loaded from a folder. ``unlinked``: their factors on flows
        outside the model, as :attr:`unlinked` (``method``: position in ``methods``), added to the model's when it
        has them recorded (a model built without them keeps none: a part would read as the whole). ``columns``:
        their columns of ``G`` when solved already (a project cache solves them in the build process), one per
        method of ``methods``. Returns how many were added."""
        import pickle

        have = {tuple(m.name) for m in self.methods}
        new = [m for m in methods if tuple(m.name) not in have and not have.add(tuple(m.name))]
        if not new:
            return 0
        G = self.G  # read or computed first: its columns for the methods already there
        with self._g_lock:
            CF_new = sp.csr_matrix(np.vstack([m.cfs for m in new]))
            if columns is None:
                G_new = self._solve_G(CF_new)
            else:
                taken = {id(m) for m in new}
                G_new = columns[:, [k for k, m in enumerate(methods) if id(m) in taken]]
            # The arrays first, then the list of methods: a request in another thread sees no method without them
            self._CF, self._G = sp.vstack([self.CF, CF_new]).tocsr(), np.hstack([G, G_new])
            self.methods = [*self.methods, *new]
            if self._directory is not None:
                tmp = self._directory / "methods.tmp.pkl"
                with open(tmp, "wb") as f:
                    pickle.dump(self.methods, f)
                os.replace(tmp, self._directory / "methods.pkl")
                self._write_cached_G(self._G)
        if unlinked is not None and self.unlinked is not None:
            at = {id(m): len(self.methods) - len(new) + j for j, m in enumerate(new)}  # their positions in the model
            position = {k: at[id(m)] for k, m in enumerate(methods) if id(m) in at}
            rows = unlinked[unlinked["method"].isin(list(position))].copy()
            rows["method"] = rows["method"].map(position).astype("int32")
            self.unlinked = pd.concat([self.unlinked, rows], ignore_index=True)
            if self._directory is not None:
                tmp = self._directory / "unlinked.tmp.pkl"
                self.unlinked.to_pickle(tmp)
                os.replace(tmp, self._directory / "unlinked.pkl")
        return len(new)

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
        if self.details is not None:
            with open(directory / "details.pkl", "wb") as f:
                pickle.dump(self.details, f)
        if self.exchanges is not None:
            _write_exchanges(directory / "exchanges.sqlite", self.exchanges)
        if self.unlinked is not None:
            self.unlinked.to_pickle(directory / "unlinked.pkl")
        else:
            (directory / "unlinked.pkl").unlink(missing_ok=True)

    @classmethod
    def load(cls, directory) -> "LCAModel":
        from pathlib import Path
        import pickle

        directory = Path(directory)
        with open(directory / "methods.pkl", "rb") as f:
            methods = pickle.load(f)
        model = cls(
            A=sp.load_npz(directory / "A.npz"),
            B=sp.load_npz(directory / "B.npz"),
            activities=pd.read_pickle(directory / "activities.pkl"),
            flows=pd.read_pickle(directory / "flows.pkl"),
            methods=methods,
            unlinked=pd.read_pickle(directory / "unlinked.pkl") if (directory / "unlinked.pkl").exists() else None,
        )
        if (directory / "details.pkl").exists():
            model._details_paths = [directory / "details.pkl"]
        if (directory / "exchanges.sqlite").exists():
            model._exchanges_path = directory / "exchanges.sqlite"
        model._directory = directory
        return model

    @property
    def has_exchanges(self) -> bool:
        return self.exchanges is not None or self._exchanges_path is not None or bool(self._exchange_parts)

    def exchange_details(self, index: int) -> list[dict]:
        """Exchanges of activity ``index`` in dataset order, as ``{"type", "row", **data}``
        ([] when the model has no exchange metadata)."""
        if self.exchanges is not None:
            return [{"type": t, "row": r, **d} for a, t, r, d in self.exchanges if a == index]
        if self._exchange_parts:
            import bisect

            k = bisect.bisect_right([start for start, _, _ in self._exchange_parts], index) - 1
            if k < 0 or index >= self._exchange_parts[k][1]:
                return []  # in a segment whose exchanges are not read (yet)
            start, _, path = self._exchange_parts[k]
            products, product_rows, flows, flow_rows = self._node_rows
            out = []
            for e in _read_exchanges(path, index - start):  # rows are node ids: those of the model's rows
                ids, rows = (flows, flow_rows) if e["type"] == "biosphere" else (products, product_rows)
                at = np.searchsorted(ids, e["row"])
                if at < len(ids) and ids[at] == e["row"]:
                    out.append({**e, "row": int(rows[at])})
            return out
        return [] if self._exchanges_path is None else _read_exchanges(self._exchanges_path, index)

    def activity_details(self, index: int) -> dict:
        """Descriptive metadata of one activity ({} when the model has none)."""
        if self.details is None and self._details_paths:
            import pickle

            details = []
            for path in self._details_paths:
                with open(path, "rb") as f:
                    details += pickle.load(f)
            self.details = details
        return self.details[index] if self.details is not None else {}
