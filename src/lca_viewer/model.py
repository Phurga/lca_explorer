# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
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


# The files of the model that ``G`` is made from: if one changes (the model was built again), the cached ``G`` is not used
G_SOURCES = ("A.npz", "B.npz", "methods.pkl")

# Exchange cache (SQLite): one column per common field, named as in Brightway, other fields in
# ``extra`` (JSON). Comments repeat a lot (ecoinvent 3.9: 100 MB, 8 MB distinct), so are stored once;
# pedigree scores in the usual order are stored as "2,3,5,5,1".
EXCHANGE_COLUMNS = {
    "amount": "REAL", "unit": "TEXT", "uncertainty type": "INTEGER", "loc": "REAL", "scale": "REAL",
    "scale without pedigree": "REAL", "shape": "REAL", "minimum": "REAL", "maximum": "REAL",
    "formula": "TEXT", "variable name": "TEXT",
}
PEDIGREE = ("reliability", "completeness", "temporal correlation", "geographical correlation",
            "further technological correlation")


def _write_exchanges(path, exchanges) -> None:
    import json
    import sqlite3
    from contextlib import closing

    comments: dict[str, int] = {}

    def record(activity, kind, row, data):
        data = dict(data)
        comment = data.pop("comment", None)
        pedigree = data.get("pedigree")
        if isinstance(pedigree, dict) and set(pedigree) == set(PEDIGREE) and all(type(v) is int for v in pedigree.values()):
            pedigree = ",".join(str(pedigree[k]) for k in PEDIGREE)
            del data["pedigree"]
        else:
            pedigree = None
        values = [data.pop(k, None) for k in EXCHANGE_COLUMNS]
        comment_id = None if comment is None else comments.setdefault(comment, len(comments))
        extra = json.dumps(data, default=str) if data else None
        return (int(activity), kind, int(row), comment_id, pedigree, *values, extra)

    columns = ", ".join(f'"{k}" {t}' for k, t in EXCHANGE_COLUMNS.items())
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"CREATE TABLE exchanges (activity INTEGER, type TEXT, row INTEGER, comment INTEGER, "
                   f"pedigree TEXT, {columns}, extra TEXT)")
        db.executemany(f"INSERT INTO exchanges VALUES ({', '.join('?' * (len(EXCHANGE_COLUMNS) + 6))})",
                       (record(*e) for e in exchanges))
        db.execute("CREATE TABLE comments (id INTEGER PRIMARY KEY, text TEXT)")
        db.executemany("INSERT INTO comments VALUES (?, ?)", ((i, c) for c, i in comments.items()))
        db.execute("CREATE INDEX by_activity ON exchanges (activity)")
        db.commit()
        db.execute("VACUUM")


def _read_exchanges(path, activity: int) -> list[dict]:
    import json
    import sqlite3
    from contextlib import closing

    columns = ", ".join(f'e."{k}"' for k in EXCHANGE_COLUMNS)
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        rows = db.execute(
            f"SELECT e.type, e.row, c.text, e.pedigree, {columns}, e.extra FROM exchanges e "
            f"LEFT JOIN comments c ON c.id = e.comment WHERE e.activity = ? ORDER BY e.rowid", (activity,))
        out = []
        for kind, row, comment, pedigree, *values, extra in rows:
            d = {"type": kind, "row": row}
            d.update((k, v) for k, v in zip(EXCHANGE_COLUMNS, values) if v is not None)
            if comment is not None:
                d["comment"] = comment
            if pedigree is not None:
                d["pedigree"] = dict(zip(PEDIGREE, map(int, pedigree.split(","))))
            if extra:
                d.update(json.loads(extra))
            out.append(d)
        return out


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
        self._U = None
        self._CF = None
        self._details_path = None
        self._exchanges_path = None

    @property
    def size(self) -> int:
        return self.A.shape[0]

    @property
    def CF(self) -> np.ndarray:
        """Characterisation factors (methods x elementary flows)."""
        if self._CF is None:
            self._CF = np.vstack([m.cfs for m in self.methods])
        return self._CF

    @property
    def U(self) -> np.ndarray:
        """Direct impacts per unit of activity operation (methods x activities)."""
        if self._U is None:
            self._U = np.asarray((sp.csr_matrix(self.CF) @ self.B).todense())
        return self._U

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
                        G = self.solver.solve_transposed(self.U.T).reshape(self.size, -1)
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

    def add_methods(self, methods: list[Method], unlinked: pd.DataFrame | None = None) -> int:
        """Add impact methods to the model (those it has already are passed over), after the others, so the
        positions of these do not move. Only their columns of ``G`` are computed (one solve), and the model's
        files are written again with them when it was loaded from a folder. ``unlinked``: their factors on flows
        outside the model, as :attr:`unlinked` (``method``: position in ``methods``), added to the model's when it
        has them recorded (a model built without them keeps none: a part would read as the whole). Returns how
        many were added."""
        import pickle

        have = {tuple(m.name) for m in self.methods}
        new = [m for m in methods if tuple(m.name) not in have and not have.add(tuple(m.name))]
        if not new:
            return 0
        G = self.G  # read or computed first: its columns for the methods already there
        with self._g_lock:
            CF = np.vstack([self.CF, *[m.cfs for m in new]])
            U_new = np.asarray((sp.csr_matrix(np.vstack([m.cfs for m in new])) @ self.B).todense())
            G_new = self.solver.solve_transposed(U_new.T).reshape(self.size, -1)
            # The arrays first, then the list of methods: a request in another thread sees no method without them
            self._CF, self._U, self._G = CF, np.vstack([self.U, U_new]), np.hstack([G, G_new])
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

    @staticmethod
    def method_names(directory) -> list[list[str]]:
        """The names of the impact methods of the model saved in ``directory`` ([] when there is none)."""
        import pickle

        try:
            with open(Path(directory) / "methods.pkl", "rb") as f:
                return [list(m.name) for m in pickle.load(f)]
        except (OSError, pickle.UnpicklingError, EOFError):
            return []

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
            model._details_path = directory / "details.pkl"
        if (directory / "exchanges.sqlite").exists():
            model._exchanges_path = directory / "exchanges.sqlite"
        model._directory = directory
        return model

    @property
    def has_exchanges(self) -> bool:
        return self.exchanges is not None or self._exchanges_path is not None

    def exchange_details(self, index: int) -> list[dict]:
        """Exchanges of activity ``index`` in dataset order, as ``{"type", "row", **data}``
        ([] when the model has no exchange metadata)."""
        if self.exchanges is not None:
            return [{"type": t, "row": r, **d} for a, t, r, d in self.exchanges if a == index]
        return [] if self._exchanges_path is None else _read_exchanges(self._exchanges_path, index)

    def activity_details(self, index: int) -> dict:
        """Descriptive metadata of one activity ({} when the model has none)."""
        if self.details is None and self._details_path is not None:
            import pickle

            with open(self._details_path, "rb") as f:
                self.details = pickle.load(f)
        return self.details[index] if self.details is not None else {}
