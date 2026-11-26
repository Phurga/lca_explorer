# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sparse solver for ``A x = b`` and ``A^T y = c`` that follows the supply chains of ``A``.

Processes are ordered suppliers first. Outside the loops of the technosphere (processes that supply
each other, directly or not: its strongly connected components) the matrix is then triangular, and
each process is solved from its suppliers (``A^T``) or its users (``A``) by substitution, with no
fill-in. Only the large loops (ecoinvent has one of ~15 000 processes) need a fill-reducing LU.

The ordered matrix is cut into segments: each large loop is one, and each run of processes between
them another (block triangular with small loops on its diagonal: an LU in that order fills nothing
outside those loops). One factorisation per segment serves both systems, ``A^T`` as it is and
``A`` as its transpose, so the model is factorised once.

SuperLU orders the columns of ``A^T`` far better than those of ``A`` (ecoinvent: 4 s vs 2 min), so
it is ``A^T`` that is factorised. Pardiso, when installed (x86-64), factorises the large loops.
"""

from __future__ import annotations

import threading

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

# The server answers requests in several threads, and a Pardiso solver (also a second one, for
# another matrix) is not safe to use from two at once: overlapping solves return wrong numbers.
SOLVER_LOCK = threading.RLock()

LARGE_LOOP = 100  # loops of more processes are factorised on their own, with a fill-reducing ordering
PARDISO_LOOP = 2000  # and with Pardiso above this size: its fixed cost outweighs it on smaller ones


def supply_order(A: sp.spmatrix) -> tuple[np.ndarray, np.ndarray]:
    """``(order, loop)``: the processes suppliers first (a process after every process it uses,
    except within a loop, whose processes are consecutive), and the loop (component) of each process."""
    A = sp.csr_matrix(A)
    off = A.copy()
    off.setdiag(0)
    off.eliminate_zeros()
    k, loop = connected_components(off, directed=True, connection="strong")
    rows, cols = off.nonzero()  # process ``cols`` uses the product of process ``rows``
    cross = loop[rows] != loop[cols]
    users = sp.csr_matrix((np.ones(cross.sum()), (loop[rows[cross]], loop[cols[cross]])), shape=(k, k))
    users.sum_duplicates()  # row: a loop, columns: the loops that use it
    waiting = np.diff(sp.csc_matrix(users).indptr)  # suppliers not placed yet, per loop
    level = np.full(k, -1)
    ready, depth = np.flatnonzero(waiting == 0), 0
    while ready.size:
        level[ready] = depth
        hits = users[ready].indices
        np.subtract.at(waiting, hits, 1)
        ready = np.unique(hits[waiting[hits] == 0])
        depth += 1
    if (level < 0).any():  # cannot happen: the loops of a graph form a DAG
        raise RuntimeError("Supply order: loops left unordered")
    return np.lexsort((loop, level[loop])), loop


def _lu(matrix: sp.spmatrix, fill_reducing: bool):
    """``solve(b, transposed)`` for ``matrix`` (given as ``A^T``, see the module docstring)."""
    from scipy.sparse.linalg import splu

    if fill_reducing and matrix.shape[0] > PARDISO_LOOP:
        try:
            from pypardiso import PyPardisoSolver
        except ImportError:
            pass
        else:
            solver, csr = PyPardisoSolver(), sp.csr_matrix(matrix)
            csr.sort_indices()
            solver.factorize(csr)
            # The same arrays read as CSC are the transpose: pypardiso then solves with the transpose of
            # the factorisation it has (iparm(12) = 1), not a new one, as their hash is the same
            transpose = sp.csc_matrix((csr.data, csr.indices, csr.indptr), shape=csr.shape)
            return lambda b, transposed: solver.solve(transpose if transposed else csr, b)
    if fill_reducing:
        lu = splu(sp.csc_matrix(matrix))
    else:  # block triangular: kept in its order, pivots on the diagonal
        lu = splu(sp.csc_matrix(matrix), permc_spec="NATURAL", diag_pivot_thresh=0.0,
                  options={"SymmetricMode": True})
    return lambda b, transposed: lu.solve(b, trans="T" if transposed else "N")


class Solver:
    """``solve(b)`` for ``A x = b`` and ``solve_transposed(c)`` for ``A^T y = c``, with ``b``, ``c`` 1D or
    2D (one right-hand side per column). Safe to call from several threads."""

    def __init__(self, A: sp.spmatrix):
        A = sp.csr_matrix(A, dtype=float)
        n = A.shape[0]
        order, loop = supply_order(A)
        self.order = order
        P = A[order][:, order].tocsr()  # suppliers first: upper block triangular
        PT = P.T.tocsr()  # lower block triangular
        sizes = np.bincount(loop)[loop[order]]
        large = sizes > LARGE_LOOP
        # Segment bounds: where a large loop starts or ends
        ordered = loop[order]
        change = (large[1:] != large[:-1]) | (large[1:] & (ordered[1:] != ordered[:-1]))
        bounds = [0, *(np.flatnonzero(change) + 1).tolist(), n]
        self.segments = []
        for a, b in zip(bounds[:-1], bounds[1:]):
            factor = _lu(PT[a:b, a:b], fill_reducing=bool(large[a]))
            users = P[a:b, b:] if b < n else None  # x[a:b] waits for the processes using them, after b
            suppliers = PT[a:b, :a] if a > 0 else None  # y[a:b] waits for their suppliers, before a
            self.segments.append((a, b, factor, users, suppliers))
        self.n = n
        self.large_loops = sorted((b - a for a, b, *_ in self.segments if large[a]), reverse=True)

    def _run(self, rhs, transposed: bool) -> np.ndarray:
        rhs = np.asarray(rhs, dtype=float)
        rp = rhs[self.order]
        out = np.zeros_like(rp)
        segments = self.segments if transposed else reversed(self.segments)
        with SOLVER_LOCK:
            for a, b, factor, users, suppliers in segments:
                part = suppliers if transposed else users
                local = rp[a:b] if part is None else rp[a:b] - (part @ (out[:a] if transposed else out[b:]))
                # The factor is of P^T's block: solving with P's block (for A) is its transposed solve
                out[a:b] = factor(np.ascontiguousarray(local), not transposed)
        result = np.empty_like(out)
        result[self.order] = out
        return result

    def solve(self, b) -> np.ndarray:
        return self._run(b, transposed=False)

    def solve_transposed(self, c) -> np.ndarray:
        return self._run(c, transposed=True)
