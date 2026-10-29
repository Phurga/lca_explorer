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
        self._solve_A = None
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
        # Factorised once: each functional unit is then one solve, not a new factorisation
        if self._solve_A is None:
            self._solve_A = _factorized_solver(self.A)
        return self._solve_A(demand)

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
        if self.details is not None:
            with open(directory / "details.pkl", "wb") as f:
                pickle.dump(self.details, f)
        if self.exchanges is not None:
            _write_exchanges(directory / "exchanges.sqlite", self.exchanges)

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
        )
        if (directory / "details.pkl").exists():
            model._details_path = directory / "details.pkl"
        if (directory / "exchanges.sqlite").exists():
            model._exchanges_path = directory / "exchanges.sqlite"
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
