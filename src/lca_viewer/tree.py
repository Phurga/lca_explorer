"""Lazy supply chain tree.

A node is "``amount`` of product ``p``, reached through ``path``". Expanding it
reads one column of ``A``: each input becomes a child whose cumulative impact
is ``amount_child * G[child]``, and the activity's own emissions become a
"direct" pseudo-child. Children plus direct always sum to the parent, so any
frontier of the tree (any set of expanded nodes) is a complete, non-overlapping
decomposition of the root score. Loops are not a problem: they are only
followed as far as the user expands them.

A direct node can itself be split into its elementary flows (one row of ``B``
each, impact ``amount * CF``); they sum to the direct node for every method.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import LCAModel

DIRECT = -1


@dataclass(frozen=True)
class Node:
    path: tuple[int | str, ...]
    """Product indices from the root; ``DIRECT`` as last element for direct emissions,
    then ``"f<flow index>"`` for one elementary flow of them.
    May start with a view row id (a string) for inputs of merged processes."""
    amount: float
    """Amount of product supplied, in the product's unit (0 for direct nodes; for flow
    nodes, the amount of the elementary flow, in the flow's unit)."""
    impacts: np.ndarray
    """Cumulative impacts, one per method."""

    @property
    def product(self) -> int:
        return self.path[-1]

    @property
    def is_direct(self) -> bool:
        return self.product == DIRECT

    @property
    def flow(self) -> int | None:
        """Elementary flow index (row of ``B``) of a flow node, else None."""
        last = self.path[-1]
        return int(last[1:]) if len(self.path) > 1 and isinstance(last, str) and last.startswith("f") else None

    @property
    def id(self) -> str:
        return "/".join(map(str, self.path))

    @property
    def depth(self) -> int:
        return len(self.path) - 1


class Tree:
    def __init__(self, model: LCAModel, product: int, amount: float = 1.0):
        self.model = model
        self.root = Node((product,), amount, model.score(product, amount))

    def children(self, node: Node, include_direct: bool = True) -> list[Node]:
        """Inputs of the node's activity, plus its direct emissions."""
        if node.is_direct:
            return []
        m = self.model
        j = node.product
        operation = node.amount / m.diagonal[j]
        start, end = m.A.indptr[j], m.A.indptr[j + 1]
        rows = m.A.indices[start:end]
        values = m.A.data[start:end]
        keep = rows != j
        rows, values = rows[keep], values[keep]
        amounts = -values * operation
        impacts = amounts[:, None] * m.G[rows]
        nodes = [
            Node(node.path + (int(i),), float(a), imp)
            for i, a, imp in zip(rows, amounts, impacts)
        ]
        if include_direct:
            nodes.append(Node(node.path + (DIRECT,), 0.0, operation * m.U[:, j]))
        return nodes

    def flows(self, node: Node, sources: list[tuple[int, float]]) -> list[Node]:
        """Elementary flows of a direct node emitted by ``sources``, (activity, operation
        level) pairs (several for a merged process). Flows without impact in any method
        are left out, so the flows still sum to the direct node."""
        m = self.model
        flows, amounts = [], []
        for j, operation in sources:
            start, end = m.B.indptr[j], m.B.indptr[j + 1]
            flows.append(m.B.indices[start:end])
            amounts.append(m.B.data[start:end] * operation)
        if not flows:
            return []
        flows, inverse = np.unique(np.concatenate(flows), return_inverse=True)
        amounts = np.bincount(inverse, weights=np.concatenate(amounts), minlength=len(flows))
        impacts = amounts[:, None] * m.CF[:, flows].T
        keep = np.any(impacts != 0, axis=1)
        return [
            Node(node.path + (f"f{f}",), float(a), imp)
            for f, a, imp in zip(flows[keep], amounts[keep], impacts[keep])
        ]

    def node(self, path: tuple[int, ...]) -> Node:
        """Rebuild a node from its path (e.g. from a saved view)."""
        if path[0] != self.root.product:
            raise KeyError(path)
        current = self.root
        for step in path[1:]:
            match = [c for c in self.children(current) if c.product == step]
            if not match:
                raise KeyError(path)
            current = match[0]
        return current
