"""A tree view: expansion state + grouping rules, rendered as rows.

The view is the user's working state and is JSON-serialisable. The rows it
renders are what a tree widget displays; its *frontier* (visible rows that are
not expanded) is a complete decomposition of the root score and is what
contribution charts are built from.

Grouping rules act on the children of a node, so groups are purely a view
concern: they never change numbers, only how children are bundled. Member
nodes keep their real paths, so expansion state survives regrouping.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .query import derived_activities, evaluate
from .tree import DIRECT, Node, Tree

ROOT_SCOPE = "*"


@dataclass
class GroupRule:
    """Bundle children of nodes in ``scope`` (a node id, or ``"*"`` for all nodes).

    Either a named group (``name`` + ``predicate``: matching children go into
    one group) or a group-by (``by``: children are partitioned by the value of
    that field). Named groups are applied first, then group-bys, each in list
    order; a child goes to the first group that claims it. Groups of a single
    child are not created.
    """

    name: str | None = None
    predicate: dict | None = None
    by: str | None = None
    scope: str = ROOT_SCOPE

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Row:
    id: str
    parent: str | None
    depth: int
    kind: str  # "process", "direct", "group", "rest" (others: below cut-off or moved there)
    label: str
    amount: float
    unit: str
    impacts: np.ndarray
    expandable: bool
    expanded: bool
    node: Node | None = None
    members: list["Row"] | None = None
    """For "rest" rows: the rows they contain."""
    pinned: bool = False
    """Shown although below the cut-off, because the user pinned it."""
    moved: bool = False
    """Put in "others" by the user."""


@dataclass
class View:
    tree: Tree
    expanded: set[str] = field(default_factory=set)
    rules: list[GroupRule] = field(default_factory=list)
    cutoff: float = 0.0
    """Hide children whose |impact| < cutoff × |root impact| (for ``method``); they are summed in a "rest" row."""
    method: int = 0
    pinned: set[str] = field(default_factory=set)
    """Row ids shown even when below the cut-off (exceptions chosen by the user)."""
    others: set[str] = field(default_factory=set)
    """Row ids moved to their parent's "others" row by the user, whatever their impact."""

    def __post_init__(self):
        self._activities = derived_activities(self.tree.model)
        self._masks: dict[int, np.ndarray] = {}

    # --- state -----------------------------------------------------------
    def expand(self, row_id: str) -> None:
        self.expanded.discard(f"!{row_id}")
        self.expanded.add(row_id)

    def collapse(self, row_id: str) -> None:
        self.expanded.discard(row_id)
        if "/g:" in row_id:
            self.expanded.add(f"!{row_id}")

    def to_dict(self) -> dict:
        return {
            "root": list(self.tree.root.path),
            "amount": self.tree.root.amount,
            "expanded": sorted(self.expanded),
            "rules": [r.to_dict() for r in self.rules],
            "cutoff": self.cutoff,
            "method": self.method,
            "pinned": sorted(self.pinned),
            "others": sorted(self.others),
        }

    @classmethod
    def from_dict(cls, model, data: dict) -> "View":
        tree = Tree(model, data["root"][0], data.get("amount", 1.0))
        return cls(
            tree,
            expanded=set(data.get("expanded", [])),
            rules=[GroupRule(**r) for r in data.get("rules", [])],
            cutoff=data.get("cutoff", 0.0),
            method=data.get("method", 0),
            pinned=set(data.get("pinned", [])),
            others=set(data.get("others", [])),
        )

    # --- rendering -------------------------------------------------------
    def rows(self) -> list[Row]:
        """All visible rows, depth first, children sorted by impact."""
        out: list[Row] = []
        self._render(self._process_row(self.tree.root, None), out)
        return out

    def frontier(self) -> list[Row]:
        """Visible rows that are not expanded: they sum to the root."""
        return [r for r in self.rows() if not (r.expandable and r.expanded)]

    def _render(self, row: Row, out: list[Row]) -> None:
        out.append(row)
        if not (row.expandable and row.expanded):
            return
        for child in self._child_rows(row):
            self._render(child, out)

    def _child_rows(self, row: Row) -> list[Row]:
        if row.kind == "group":
            members = row.node_members  # type: ignore[attr-defined]
            rows = [self._process_row(n, row.id, row.depth + 1) for n in members]
            return self._apply_cutoff(rows, row)
        children = self.tree.children(row.node)
        rows = self._group(children, row)
        return self._apply_cutoff(rows, row)

    def _group(self, children: list[Node], parent: Row) -> list[Row]:
        depth = parent.depth + 1
        free = [c for c in children if not c.is_direct]
        rows: list[Row] = []
        ordered = [r for r in self.rules if r.by is None] + [r for r in self.rules if r.by is not None]
        for rule in ordered:
            if rule.scope not in (ROOT_SCOPE, parent.id) or not free:
                continue
            if rule.predicate is not None:
                mask = self._mask(rule)
                members = [c for c in free if mask[c.product]]
                if len(members) > 1:
                    rows.append(self._group_row(rule.name or "group", members, parent, depth))
                    free = [c for c in free if not mask[c.product]]
            elif rule.by is not None:
                column = self._activities[rule.by]
                buckets: dict[str, list[Node]] = {}
                for c in free:
                    buckets.setdefault(str(column.iat[c.product]), []).append(c)
                free = []
                for value, members in buckets.items():
                    if len(members) > 1:
                        rows.append(self._group_row(f"{rule.by}: {value}", members, parent, depth))
                    else:
                        free.extend(members)
        rows.extend(self._process_row(c, parent.id, depth) for c in free)
        rows.extend(
            self._process_row(c, parent.id, depth)
            for c in children
            if c.is_direct and np.any(c.impacts != 0)
        )
        return rows

    def _apply_cutoff(self, rows: list[Row], parent: Row) -> list[Row]:
        """Sort siblings and bundle into "others" those below the cut-off or moved there."""
        rows.sort(key=lambda r: -abs(r.impacts[self.method]))
        threshold = self.cutoff * abs(self.tree.root.impacts[self.method]) if self.cutoff > 0 else None
        kept, hidden = [], []
        for r in rows:
            r.moved = r.id in self.others
            below = threshold is not None and bool(abs(r.impacts[self.method]) < threshold)
            r.pinned = below and not r.moved and r.id in self.pinned
            (hidden if r.moved or (below and not r.pinned) else kept).append(r)
        # A single row below the cut-off is shown as itself; moved rows always go to "others"
        if len(hidden) > 1 or any(r.moved for r in hidden):
            rest = Row(
                id=f"{parent.id}/rest",
                parent=parent.id,
                depth=parent.depth + 1,
                kind="rest",
                label=f"others ({len(hidden)} {'process' if len(hidden) == 1 else 'processes'})",
                amount=0.0,
                unit="",
                impacts=sum(r.impacts for r in hidden),
                expandable=False,
                expanded=False,
                members=hidden,
            )
            return kept + [rest]
        return rows

    def _mask(self, rule: GroupRule) -> np.ndarray:
        key = id(rule)
        if key not in self._masks:
            self._masks[key] = evaluate(rule.predicate, self._activities)
        return self._masks[key]

    def _process_row(self, node: Node, parent: str | None, depth: int = 0) -> Row:
        if node.is_direct:
            return Row(node.id, parent, depth, "direct", "direct emissions", 0.0, "",
                       node.impacts, False, False, node)
        a = self._activities.iloc[node.product]
        label = f"{a['reference_product']} | {a['name']} | {a['location']}"
        return Row(node.id, parent, depth, "process", label, node.amount, a["unit"],
                   node.impacts, True, node.id in self.expanded, node)

    def _group_row(self, name: str, members: list[Node], parent: Row, depth: int) -> Row:
        row_id = f"{parent.id}/g:{name}"
        # A group opens by itself when it hides an expanded member, unless the
        # user explicitly collapsed it ("!" + id in ``expanded``)
        expanded = row_id in self.expanded or (
            f"!{row_id}" not in self.expanded and any(m.id in self.expanded for m in members)
        )
        row = Row(row_id, parent.id, depth, "group", name, 0.0, "",
                  sum(m.impacts for m in members), True, expanded)
        row.node_members = members  # type: ignore[attr-defined]
        return row
