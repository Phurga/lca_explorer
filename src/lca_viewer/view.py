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

from .query import derived_activities, derived_flows, evaluate
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

    With ``target="flows"`` the rule bundles the elementary flows under direct
    emissions rows instead of processes; its predicate and field then refer to
    flow metadata (``name``, ``compartment``, ``subcompartment``, ``unit``).

    A ``disabled`` rule is kept (in the list, in saved views) but not applied. With
    ``merge``, each group the rule makes becomes a merged process instead (process
    rules only): its members' inventories combined, common inputs summed.
    """

    name: str | None = None
    predicate: dict | None = None
    by: str | None = None
    scope: str = ROOT_SCOPE
    target: str | None = None
    disabled: bool | None = None
    merge: bool | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Row:
    id: str
    parent: str | None
    depth: int
    kind: str  # "process", "direct", "flow", "group", "merged", "rest" (others: below cut-off or moved there)
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
    merged: list[Node] | None = None
    """For "merged" rows: the sibling processes whose inventories are combined."""
    by_rule: bool = False
    """For "merged" rows: made by a grouping rule with ``merge`` (not by ``View.merges``)."""
    by_product: bool = False
    """For "group" rows: made by ``View.group_products`` (not by a rule)."""
    regroup: str | None = None
    """For process rows: the "group by product" row the user ungrouped them from (its id),
    so that they can be put back in it."""
    sources: list[tuple[int, float]] | None = None
    """For direct rows and every row below them (flows, groups of flows, others):
    the (activity, operation level) pairs whose own emissions they are part of."""
    replaced: str | None = None
    """Process row whose input the user replaced by another supplier: the row id the input
    has without the substitution (``View.substitutions``)."""


@dataclass
class View:
    tree: Tree
    expanded: set[str] = field(default_factory=set)
    rules: list[GroupRule] = field(default_factory=list)
    cutoff: float = 0.0
    """Under each parent, the smallest children that together weigh at most ``cutoff`` of
    the reference (see ``cutoff_basis``), for ``method``, are bundled in "others"."""
    cutoff_basis: str = "parent"
    """How ``cutoff`` is read: "parent" (a share of the siblings' |impact| sum), "total" (a
    share of the root score), "cumulative" (the largest siblings are kept until they reach
    that share of the sum: the rest is below) or "top" (``cutoff`` is a count: the largest
    siblings are kept, the rest is below; below 1, no limit)."""
    method: int = 0
    pinned: set[str] = field(default_factory=set)
    """Row ids shown even when below the cut-off (exceptions chosen by the user)."""
    others: set[str] = field(default_factory=set)
    """Row ids moved to their parent's "others" row by the user, whatever their impact."""
    group_products: bool = False
    """Bundle sibling processes delivering the same reference product (e.g. one product
    from several regions or routes) into one group row, after the user's rules."""
    merges: list[dict] = field(default_factory=list)
    """``{"scope": parent row id, "products": [...]}``: sibling processes merged into one
    process whose inventory is the union of theirs (common inputs summed)."""
    ungrouped: set[str] = field(default_factory=set)
    """Ids of "group by product" rows the user ungrouped: their members stay separate rows."""
    substitutions: dict[str, int] = field(default_factory=dict)
    """``{row id of an input: activity index}``: the input is supplied by that activity
    instead (same unit, same amount), with its own supply chain. The key is the input's
    row id as it is without the substitution."""

    def __post_init__(self):
        self._activities = derived_activities(self.tree.model)
        self._flows = derived_flows(self.tree.model)
        self._masks: dict[int, np.ndarray] = {}
        self._replaced: dict[str, str] = {}  # row id of a substituted input -> its original id

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
            "cutoff_basis": self.cutoff_basis,
            "method": self.method,
            "pinned": sorted(self.pinned),
            "others": sorted(self.others),
            "merges": self.merges,
            "ungrouped": sorted(self.ungrouped),
            "substitutions": self.substitutions,
            "group_products": self.group_products,
        }

    @classmethod
    def from_dict(cls, model, data: dict) -> "View":
        tree = Tree(model, data["root"][0], data.get("amount", 1.0))
        return cls(
            tree,
            expanded=set(data.get("expanded", [])),
            rules=[GroupRule(**r) for r in data.get("rules", [])],
            cutoff=data.get("cutoff", 0.0),
            cutoff_basis=data.get("cutoff_basis", "parent"),
            method=data.get("method", 0),
            pinned=set(data.get("pinned", [])),
            others=set(data.get("others", [])),
            merges=list(data.get("merges", [])),
            ungrouped=set(data.get("ungrouped", [])),
            substitutions={k: int(v) for k, v in data.get("substitutions", {}).items()},
            group_products=bool(data.get("group_products", False)),
        )

    def reveal(self, row_id: str) -> str | None:
        """Expand, open groups and pin rows so that ``row_id`` is visible; returns the
        deepest row of its path that could be shown (a merge can absorb the rest)."""
        parts = row_id.split("/")
        for i in range(1, len(parts)):
            self.expand("/".join(parts[:i]))
        contains = lambda r, i: r.id == i or any(m.id == i for m in getattr(r, "node_members", []))
        for _ in range(4 * len(parts)):  # each pass opens at least one group or others row
            rows = self.rows()
            visible = {r.id for r in rows}
            if row_id in visible:
                return row_id
            target = next("/".join(parts[:i]) for i in range(1, len(parts) + 1) if "/".join(parts[:i]) not in visible)
            changed = False
            for r in rows:
                if r.kind == "group" and not r.expanded and contains(r, target):
                    self.expand(r.id)
                    changed = True
                for m in r.members or []:  # others rows
                    if contains(m, target):
                        self.pinned.add(m.id)
                        self.others.discard(m.id)
                        changed = True
            if not changed:
                break
        shown = [i for i in range(1, len(parts) + 1) if "/".join(parts[:i]) in {r.id for r in self.rows()}]
        return "/".join(parts[: shown[-1]]) if shown else None

    # --- rendering -------------------------------------------------------
    def rows(self) -> list[Row]:
        """All visible rows, depth first, children sorted by impact."""
        out: list[Row] = []
        self._replaced = {}  # filled as the children of the expanded rows are built
        self._render(self._process_row(self.tree.root, None), out)
        return out

    def find(self, row_id: str) -> Row:
        """A visible row, or a row bundled in an others row."""
        for r in self.rows():
            if r.id == row_id:
                return r
            for m in r.members or []:
                if m.id == row_id:
                    return m
        raise KeyError(row_id)

    def direct_sources(self, row: Row) -> list[tuple[int, float]]:
        """(activity, operation level) pairs whose own emissions make up a direct row."""
        return row.sources

    def _sources(self, parent: Row) -> list[tuple[int, float]]:
        nodes = parent.merged if parent.kind == "merged" else [parent.node]
        diagonal = self.tree.model.diagonal
        return [(int(n.product), float(n.amount / diagonal[n.product])) for n in nodes]

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
        if row.kind == "rest":
            # An expanded others row shows its members in place, like a group (the
            # chart then breaks it down too, as for any expanded row)
            for m in row.members:
                m.parent, m.depth = row.id, row.depth + 1
            return row.members
        if row.kind == "group":
            members = row.node_members  # type: ignore[attr-defined]
            rows = [self._process_row(n, row.id, row.depth + 1) for n in members]
            for r in rows:
                r.sources = row.sources
            return self._apply_cutoff(rows, row)
        if row.kind == "direct":
            children = self.tree.flows(row.node, row.sources)
        elif row.kind == "merged":
            children = self._substituted(self._merged_children(row))
        else:
            children = self._substituted(self.tree.children(row.node))
        rows = self._group(children, row)
        return self._apply_cutoff(rows, row)

    def _merged_children(self, row: Row) -> list[Node]:
        """Union of the members' inputs (and direct emissions); a product used by
        several members becomes one node with the summed amount and impacts."""
        combined: dict[int, list] = {}
        for member in row.merged:
            for child in self.tree.children(member):
                if child.product in combined:
                    combined[child.product][0] += child.amount
                    combined[child.product][1] = combined[child.product][1] + child.impacts
                else:
                    combined[child.product] = [child.amount, child.impacts]
        return [Node((row.id, product), amount, impacts) for product, (amount, impacts) in combined.items()]

    def _group(self, children: list[Node], parent: Row) -> list[Row]:
        depth = parent.depth + 1
        free = [c for c in children if not c.is_direct]
        rows: list[Row] = []
        for merge in self.merges:
            if merge["scope"] != parent.id:
                continue
            products = set(merge["products"])
            members = [c for c in free if c.product in products]
            if len(members) > 1:
                rows.append(self._merged_row(members, parent, depth))
                free = [c for c in free if c.product not in products]
        # Under a direct row the children are elementary flows, grouped by flow rules only
        flows = parent.kind == "direct"
        table = self._flows if flows else self._activities
        index = (lambda c: c.flow) if flows else (lambda c: c.product)
        ordered = [r for r in self.rules if r.by is None] + [r for r in self.rules if r.by is not None]
        for rule in ordered:
            if rule.disabled or (rule.target == "flows") != flows or rule.scope not in (ROOT_SCOPE, parent.id) or not free:
                continue
            bundle = self._rule_merged_row if rule.merge and not flows else self._group_row
            if rule.predicate is not None:
                mask = self._mask(rule)
                members = [c for c in free if mask[index(c)]]
                if len(members) > 1:
                    rows.append(bundle(rule.name or "group", members, parent, depth))
                    free = [c for c in free if not mask[index(c)]]
            elif rule.by is not None:
                column = table[rule.by]
                buckets: dict[str, list[Node]] = {}
                for c in free:
                    buckets.setdefault(str(column.iat[index(c)]), []).append(c)
                free = []
                for value, members in buckets.items():
                    if len(members) > 1:
                        # flows read better by value alone: "air", "Carbon dioxide, fossil"
                        name = (value or f"no {rule.by}") if flows else f"{rule.by}: {value}"
                        rows.append(bundle(name, members, parent, depth))
                    else:
                        free.extend(members)
        regroup: dict = {}  # member node id -> the product group it was ungrouped from
        if self.group_products and not flows and len(free) > 1:
            products = self._activities["reference_product"]
            buckets = {}
            for c in free:
                buckets.setdefault(str(products.iat[c.product]), []).append(c)
            free = []
            for product, members in buckets.items():
                group_id = f"{parent.id}/g:product:{product}"
                if len(members) > 1 and group_id not in self.ungrouped:
                    group = self._group_row(f"product:{product}", members, parent, depth, label=product)
                    group.by_product = True
                    rows.append(group)
                else:
                    free.extend(members)
                    if len(members) > 1:
                        regroup.update((m.id, group_id) for m in members)
        for c in free:
            row = self._process_row(c, parent.id, depth)
            row.regroup = regroup.get(c.id)
            rows.append(row)
        if flows:
            for r in rows:
                r.sources = parent.sources
        for c in children:
            if c.is_direct and np.any(c.impacts != 0):
                direct = self._process_row(c, parent.id, depth)
                direct.sources = self._sources(parent)
                rows.append(direct)
        return rows

    def _substituted(self, children: list[Node]) -> list[Node]:
        """Inputs the user replaced (``substitutions``) take the supply chain of their new
        activity, with the same amount. A replacement in another unit, or by a product the
        parent already has as an input (a row id would then be taken twice), is ignored."""
        if not self.substitutions:
            return children
        units = self._activities["unit"]
        present = {c.product for c in children if not c.is_direct}
        out = []
        for c in children:
            new = None if c.is_direct else self.substitutions.get(c.id)
            if new is None or new in present or units.iat[new] != units.iat[c.product]:
                out.append(c)
                continue
            node = Node(c.path[:-1] + (new,), c.amount, c.amount * self.tree.model.G[new])
            self._replaced[node.id] = c.id
            out.append(node)
        return out

    def _apply_cutoff(self, rows: list[Row], parent: Row) -> list[Row]:
        """Sort siblings and bundle into "others" those below the cut-off or moved there."""
        rows.sort(key=lambda r: -abs(r.impacts[self.method]))
        # Flows without impact for this method (they are kept for the other methods) are
        # noise here: always in others, like moved rows
        zero = [r.kind == "flow" and bool(r.impacts[self.method] == 0) for r in rows]
        kept, hidden, forced = [], [], False
        for r, b, z in zip(rows, self._bottom_contributors(rows), zero):
            r.moved = r.id in self.others
            r.pinned = (b or z) and not r.moved and r.id in self.pinned
            if r.moved or ((b or z) and not r.pinned):
                hidden.append(r)
                forced = forced or r.moved or z
            else:
                kept.append(r)
        # A single row below the cut-off is shown as itself; moved rows always go to "others"
        if len(hidden) > 1 or forced:
            flows = parent.sources is not None  # under a direct row, or a group of its flows
            noun = ("flow" if flows else "process") if len(hidden) == 1 else ("flows" if flows else "processes")
            rest = Row(
                id=f"{parent.id}/rest",
                parent=parent.id,
                depth=parent.depth + 1,
                kind="rest",
                label=f"others ({len(hidden)} {noun})",
                amount=0.0,
                unit="",
                impacts=sum(r.impacts for r in hidden),
                expandable=True,
                expanded=f"{parent.id}/rest" in self.expanded,
                members=hidden,
                sources=parent.sources,
            )
            return kept + [rest]
        return rows

    def _bottom_contributors(self, rows: list[Row]) -> list[bool]:
        """Flags the smallest rows (``rows`` sorted by decreasing |impact|) whose
        cumulative |impact| stays within ``cutoff`` of the siblings' total or of the root."""
        below = [False] * len(rows)
        magnitudes = [float(abs(r.impacts[self.method])) for r in rows]
        if self.cutoff_basis == "top":
            keep = round(self.cutoff)
            return [i >= keep for i in range(len(rows))] if keep >= 1 else below
        if self.cutoff_basis == "total":
            reference = float(abs(self.tree.root.impacts[self.method]))
        elif self.cutoff_basis in ("parent", "cumulative"):
            reference = sum(magnitudes)
        else:
            raise ValueError(f"Unknown cut-off basis {self.cutoff_basis!r}")
        budget = self.cutoff * reference
        if budget <= 0:
            return below
        if self.cutoff_basis == "cumulative":
            # The largest rows are kept until together they reach the budget
            cumulated = 0.0
            for i, magnitude in enumerate(magnitudes):
                below[i] = cumulated >= budget
                cumulated += magnitude
            return below
        cumulated = 0.0
        for i in reversed(range(len(rows))):
            cumulated += magnitudes[i]
            if cumulated > budget:
                break
            below[i] = True
        return below

    def _mask(self, rule: GroupRule) -> np.ndarray:
        key = id(rule)
        if key not in self._masks:
            self._masks[key] = evaluate(rule.predicate, self._flows if rule.target == "flows" else self._activities)
        return self._masks[key]

    def _process_row(self, node: Node, parent: str | None, depth: int = 0) -> Row:
        if node.flow is not None:
            f = self._flows.iloc[node.flow]
            compartment = ", ".join(c for c in (f["compartment"], f["subcompartment"]) if c)
            return Row(node.id, parent, depth, "flow", f"{f['name']} | {compartment}", node.amount, f["unit"],
                       node.impacts, False, False, node)
        if node.is_direct:
            return Row(node.id, parent, depth, "direct", "direct emissions", 0.0, "",
                       node.impacts, True, node.id in self.expanded, node)
        a = self._activities.iloc[node.product]
        label = f"{a['reference_product']} | {a['name']} | {a['location']}"
        return Row(node.id, parent, depth, "process", label, node.amount, a["unit"],
                   node.impacts, True, node.id in self.expanded, node, replaced=self._replaced.get(node.id))

    def _merged_row(self, members: list[Node], parent: Row, depth: int) -> Row:
        products = sorted(m.product for m in members)
        row_id = f"{parent.id}/m:{'+'.join(map(str, products))}"
        acts = self._activities.iloc[[m.product for m in members]]
        # Members with the same name are listed once: "steel (RER + RoW) + iron (DE)"
        locations: dict[str, list[str]] = {}
        for n, l in zip(acts["name"], acts["location"]):
            locations.setdefault(n, []).append(str(l))
        label = " + ".join(f"{n} ({' + '.join(ls)})" for n, ls in locations.items())
        # An amount only makes sense when all members deliver the same product
        same = acts["reference_product"].nunique() == 1 and acts["unit"].nunique() == 1
        return Row(row_id, parent.id, depth, "merged", label,
                   sum(m.amount for m in members) if same else 0.0,
                   acts["unit"].iat[0] if same else "",
                   sum(m.impacts for m in members), True, row_id in self.expanded, merged=members)

    def _rule_merged_row(self, name: str, members: list[Node], parent: Row, depth: int) -> Row:
        row = self._merged_row(members, parent, depth)
        row.by_rule = True
        return row

    def _group_row(self, name: str, members: list[Node], parent: Row, depth: int, label: str | None = None) -> Row:
        row_id = f"{parent.id}/g:{name}"
        # A group opens by itself when it hides an expanded member, unless the
        # user explicitly collapsed it ("!" + id in ``expanded``)
        expanded = row_id in self.expanded or (
            f"!{row_id}" not in self.expanded and any(m.id in self.expanded for m in members)
        )
        amount, unit = 0.0, ""
        if members[0].flow is not None:
            # Flows of one substance (several compartments) add up to an amount
            flows = self._flows.iloc[[m.flow for m in members]]
            if flows["name"].nunique() == 1 and flows["unit"].nunique() == 1:
                amount, unit = sum(m.amount for m in members), flows["unit"].iat[0]
        else:
            # Processes of one product (several regions or routes) add up too
            acts = self._activities.iloc[[m.product for m in members]]
            if acts["reference_product"].nunique() == 1 and acts["unit"].nunique() == 1:
                amount, unit = sum(m.amount for m in members), acts["unit"].iat[0]
        row = Row(row_id, parent.id, depth, "group", label or name, amount, unit,
                  sum(m.impacts for m in members), True, expanded)
        row.node_members = members  # type: ignore[attr-defined]
        return row
