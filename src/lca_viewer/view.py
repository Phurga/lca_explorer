# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
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

from .query import EXCHANGE_FIELDS, derived_activities, derived_flows, evaluate, exchange_frame, uses_exchange
from .tree import DIRECT, Node, Tree

ROOT_SCOPE = "*"


CUTOFF_BASES = ("top", "total", "parent", "cumulative")


def below_cutoff(magnitudes: list[float], basis: str, cutoff: float, total: float) -> list[bool]:
    """Which rows (``magnitudes``: their |impact|, largest first) go to "others", for every list of the app.

    ``top``: all but the ``cutoff`` largest (none when it is under 1); ``total``: the smallest rows that together weigh at
    most ``cutoff`` (a fraction) of ``total``; ``parent``: the same, of all the rows together; ``cumulative``: the rows
    after the largest ones that together reach ``cutoff`` of all the rows."""
    below = [False] * len(magnitudes)
    if basis == "top":
        keep = round(cutoff)
        return [i >= keep for i in range(len(magnitudes))] if keep >= 1 else below
    if basis not in CUTOFF_BASES:
        raise ValueError(f"Unknown cut-off basis {basis!r}")
    budget = cutoff * (total if basis == "total" else sum(magnitudes))
    if budget <= 0:
        return below
    cumulated = 0.0
    if basis == "cumulative":
        for i, magnitude in enumerate(magnitudes):
            below[i] = cumulated >= budget
            cumulated += magnitude
        return below
    for i in reversed(range(len(magnitudes))):
        cumulated += magnitudes[i]
        if cumulated > budget:
            break
        below[i] = True
    return below


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
    flow metadata (``name``, ``compartment``, ``subcompartment``, ``unit``). A flow rule can have an
    ``owner``: a predicate over activities, and it then bundles only the flows of the processes that match.

    A ``disabled`` rule is kept (in the list, in saved views) but not applied.
    """

    name: str | None = None
    predicate: dict | None = None
    by: str | None = None
    scope: str = ROOT_SCOPE
    target: str | None = None
    disabled: bool | None = None
    owner: dict | None = None
    plain: bool | None = None
    """Not a rule of the Rules panel but a grouping of the view options (by substance, by compartment): its groups are not
    coloured as a rule's."""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "GroupRule":
        """A rule as the page or a view file has it; what a rule no longer has (``merge``, from older files) is left out."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


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
    by_product: bool = False
    """For "group" rows: made by ``View.group_products`` (not by a rule)."""
    rule_group: str | None = None
    """For a group made by a rule of the Rules panel: the rule (its name, or the field of a group-by rule)."""
    claimed: bool = False
    """A rule took this row or one above it (under first match, what is below it is the rule's: no further grouping)."""
    regroup: str | None = None
    """For process rows: the "group by product" row the user ungrouped them from (its id),
    so that they can be put back in it."""
    sources: list[tuple[int, float]] | None = None
    """For direct rows and every row below them (flows, groups of flows, others):
    the (activity, operation level) pairs whose own emissions they are part of."""
    replaced: str | None = None
    """Process row whose input the user replaced by another supplier: the row id the input
    has without the substitution (``View.substitutions``)."""
    chain: list[Node] | None = None
    """For process rows, with ``View.collapse_singletons``: the processes above this one that are made of it alone (no
    other input, no emissions of their own), first first. The row stands for all of them: its node is the last."""


@dataclass
class View:
    tree: Tree
    expanded: set[str] = field(default_factory=set)
    rules: list[GroupRule] = field(default_factory=list)
    attribution: str = "direct"
    """``first_match``: the rules stop grouping below the first row a rule takes, as the contributions attribute the
    whole supply chain of that row to the rule; ``direct``: they group all the way down."""
    colour_rules: list[GroupRule] = field(default_factory=list)
    """Rules that only colour (see :meth:`matching_rule`), not group."""
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
    collapse_singletons: bool = False
    """A process made of only one process (a single input, no emissions of its own, e.g. a market of one supplier) is
    shown as that process, and so on down the chain (``Row.chain``): the impacts are the same, the tree is shorter."""

    def __post_init__(self):
        self._activities = derived_activities(self.tree.model)
        self._flows = derived_flows(self.tree.model)
        self._masks: dict[int, np.ndarray] = {}
        self._replaced: dict[str, str] = {}  # row id of a substituted input -> its original id
        # node id -> the type and amount of the exchange that brings it in (see ``query.EXCHANGE_FIELDS``)
        self._exchanges: dict[str, tuple[str, float]] = {}

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
            "collapse_singletons": self.collapse_singletons,
        }

    @classmethod
    def from_dict(cls, model, data: dict) -> "View":
        tree = Tree(model, data["root"][0], data.get("amount", 1.0))
        return cls(
            tree,
            expanded=set(data.get("expanded", [])),
            rules=[GroupRule.from_dict(r) for r in data.get("rules", [])],
            colour_rules=[GroupRule.from_dict(r) for r in data.get("colour_rules", [])],
            attribution=data.get("attribution", "direct"),
            cutoff=data.get("cutoff", 0.0),
            cutoff_basis=data.get("cutoff_basis", "parent"),
            method=data.get("method", 0),
            pinned=set(data.get("pinned", [])),
            others=set(data.get("others", [])),
            merges=list(data.get("merges", [])),
            ungrouped=set(data.get("ungrouped", [])),
            substitutions={k: int(v) for k, v in data.get("substitutions", {}).items()},
            group_products=bool(data.get("group_products", False)),
            collapse_singletons=bool(data.get("collapse_singletons", False)),
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
            shown_as = self._shown_as(rows)
            visible = {r.id for r in rows} | set(shown_as)
            if row_id in visible:
                return shown_as.get(row_id, row_id)
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
        rows = self.rows()
        shown_as = self._shown_as(rows)
        visible = {r.id for r in rows} | set(shown_as)
        shown = [i for i in range(1, len(parts) + 1) if "/".join(parts[:i]) in visible]
        return shown_as.get("/".join(parts[: shown[-1]]), "/".join(parts[: shown[-1]])) if shown else None

    @staticmethod
    def _shown_as(rows: list[Row]) -> dict[str, str]:
        """The ids of the processes collapsed into a row (``Row.chain``) -> that row's id."""
        return {n.id: r.id for r in rows for n in r.chain or []}

    # --- rendering -------------------------------------------------------
    def rows(self) -> list[Row]:
        """All visible rows, depth first, children sorted by impact."""
        out: list[Row] = []
        self._replaced = {}  # filled as the children of the expanded rows are built
        self._render(self._process_row(self.tree.root, None), out)
        return out

    def _sources(self, parent: Row) -> list[tuple[int, float]]:
        nodes = parent.merged if parent.kind == "merged" else [parent.node]
        diagonal = self.tree.model.diagonal
        return [(int(n.product), float(n.amount / diagonal[n.product])) for n in nodes]

    def frontier(self) -> list[Row]:
        """Visible rows that are not expanded: they sum to the root."""
        return [r for r in self.rows() if not (r.expandable and r.expanded)]

    def _claims(self, row: Row) -> bool:
        """Whether a rule takes ``row`` under first match: it is a group a rule made, or a process a rule matches."""
        if self.attribution != "first_match":
            return False
        return row.rule_group is not None or (row.kind == "process" and self.matching_rule(row, self.rules) is not None)

    def _render(self, row: Row, out: list[Row], inherited: bool = False) -> None:
        row.claimed = inherited or self._claims(row)
        out.append(row)
        if not (row.expandable and row.expanded):
            return
        for child in self._child_rows(row):
            self._render(child, out, row.claimed)

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
        self._note_exchanges(children, parent)
        ordered = [r for r in self.rules if r.by is None] + [r for r in self.rules if r.by is not None]
        for rule in ordered:
            if rule.disabled or (rule.target == "flows") != flows or rule.scope not in (ROOT_SCOPE, parent.id) or not free:
                continue
            if parent.claimed and rule.scope == ROOT_SCOPE and not rule.plain:
                continue  # first match: below the row a rule took, the rules group no more
            if flows and rule.owner is not None and not self._owned(rule, parent.sources):
                continue
            if rule.predicate is not None:
                hits = self._hits(rule, free, index)
                members = [c for c, hit in zip(free, hits) if hit]
                if len(members) > 1:
                    made = self._group_row(rule.name or "group", members, parent, depth)
                    made.rule_group = None if rule.plain else rule.name or "group"
                    rows.append(made)
                    free = [c for c, hit in zip(free, hits) if not hit]
            elif rule.by is not None:
                if rule.by in EXCHANGE_FIELDS:
                    at = EXCHANGE_FIELDS.index(rule.by)
                    value_of = lambda c: str(self._exchanges.get(c.id, ("", ""))[at])  # noqa: E731
                else:
                    column = table[rule.by]
                    value_of = lambda c: str(column.iat[index(c)])  # noqa: E731
                buckets: dict[str, list[Node]] = {}
                for c in free:
                    buckets.setdefault(value_of(c), []).append(c)
                free = []
                for value, members in buckets.items():
                    if len(members) > 1:
                        # flows read better by value alone: "air", "Carbon dioxide, fossil"
                        name = (value or f"no {rule.by}") if flows else f"{rule.by}: {value}"
                        made = self._group_row(name, members, parent, depth)
                        made.rule_group = None if rule.plain else rule.by
                        rows.append(made)
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
        """Flags the smallest rows (``rows`` sorted by decreasing |impact|) under the cut-off (see :func:`below_cutoff`):
        a share of the root's score, or of the siblings together."""
        return below_cutoff([float(abs(r.impacts[self.method])) for r in rows], self.cutoff_basis, self.cutoff,
                            float(abs(self.tree.root.impacts[self.method])))

    def matching_rule(self, row: Row, rules: list[GroupRule] | None = None) -> str | None:
        """The name of the first rule, in list order, whose query matches the process (or elementary flow) of ``row``.

        A process (or a flow) that a rule matches takes its colour even when it is alone and no group was made; a flow rule with an owner also needs the processes emitting
        the flow (``row.sources``) to match."""
        if row.node is None or row.kind not in ("process", "flow"):
            return None
        flows = row.kind == "flow"
        for rule in self.colour_rules if rules is None else rules:
            if rule.disabled or rule.predicate is None or (rule.target == "flows") != flows:
                continue
            if rule.scope != ROOT_SCOPE and not row.id.startswith(rule.scope + "/"):
                continue
            if flows and rule.owner is not None and not self._owned(rule, row.sources):
                continue
            # the node the exchange brings in: the first of a chain of singletons
            node = row.chain[0] if row.chain else row.node
            if uses_exchange(rule.predicate):
                if node.id in self._exchanges and self._hits(rule, [node], lambda c: c.flow if flows else c.product)[0]:
                    return rule.name or "group"
                continue
            index = row.node.flow if flows else row.node.product
            if index is not None and bool(self._mask(rule)[index]):
                return rule.name or "group"
        return None

    def _note_exchanges(self, children: list[Node], parent: Row) -> None:
        """Keep the type and amount of the exchange bringing in each of ``children`` (``query.EXCHANGE_FIELDS``). The
        amount is as recorded in the dataset of the process above (summed over the members of a merged process), per unit
        of its reference product: a negative input of a product is an avoided product."""
        m = self.tree.model
        sources = parent.sources if parent.kind == "direct" else (
            self._sources(parent) if parent.kind in ("process", "merged") else None)
        if not sources:
            return
        output = sum(abs(op * m.diagonal[j]) for j, op in sources)
        sign = -1.0 if sources[0][1] < 0 else 1.0  # under an avoided product, the amounts below are negative
        for c in children:
            if c.is_direct:
                continue
            amount = float(sign * c.amount / output) if output else 0.0
            if c.flow is not None:
                kind = "resource" if "resource" in str(self._flows["compartment"].iat[c.flow]).lower() else "emission"
            elif m.diagonal[c.product] < 0:
                kind = "waste"
            else:
                kind = "avoided" if amount < 0 else "technosphere"
            self._exchanges[c.id] = (kind, amount)

    def _hits(self, rule: GroupRule, nodes: list[Node], index) -> list[bool]:
        """Whether the query of ``rule`` matches each of ``nodes``: their activity (flow) and, when the query tests it,
        the exchange that brings them in."""
        if not uses_exchange(rule.predicate):
            mask = self._mask(rule)
            return [bool(mask[index(c)]) for c in nodes]
        if not nodes:
            return []
        table = self._flows if rule.target == "flows" else self._activities
        frame = exchange_frame(table, [index(c) for c in nodes],
                               [self._exchanges.get(c.id, ("", float("nan"))) for c in nodes])
        return [bool(x) for x in evaluate(rule.predicate, frame)]

    def _owned(self, rule: GroupRule, sources: list[tuple[int, float]] | None) -> bool:
        """Whether the processes emitting some flows (``sources``: the (activity, operation level) pairs of a direct
        row and of every row below it) match the owner predicate of ``rule``; all of them, for a merged process."""
        if not sources:
            return False
        key = ("owner", id(rule))
        if key not in self._masks:
            self._masks[key] = evaluate(rule.owner, self._activities)
        return all(bool(self._masks[key][j]) for j, _ in sources)

    def _mask(self, rule: GroupRule) -> np.ndarray:
        key = id(rule)
        if key not in self._masks:
            self._masks[key] = evaluate(rule.predicate, self._flows if rule.target == "flows" else self._activities)
        return self._masks[key]

    def _process_row(self, node: Node, parent: str | None, depth: int = 0) -> Row:
        if node.flow is not None:
            f = self._flows.iloc[node.flow]
            return Row(node.id, parent, depth, "flow", f"{f['name']} | {f['place']}", node.amount, f["unit"],
                       node.impacts, False, False, node)
        if node.is_direct:
            return Row(node.id, parent, depth, "direct", "direct emissions", 0.0, "",
                       node.impacts, True, node.id in self.expanded, node)
        chain = []
        if self.collapse_singletons and parent is not None:
            node, chain = self._singleton_end(node)
        a = self._activities.iloc[node.product]
        label = f"{a['reference_product']} | {a['name']} | {a['location']}"
        return Row(node.id, parent, depth, "process", label, node.amount, a["unit"],
                   node.impacts, True, node.id in self.expanded, node, replaced=self._replaced.get(node.id),
                   chain=chain or None)

    def _singleton_end(self, node: Node) -> tuple[Node, list[Node]]:
        """Down from ``node``, while a process is made of only one other process (its single input, no emissions of its
        own in any method): the last process, and those above it."""
        chain: list[Node] = []
        seen = {node.product}
        while True:
            children = self._substituted(self.tree.children(node))
            inputs = [c for c in children if not c.is_direct]
            own = any(c.is_direct and np.any(c.impacts != 0) for c in children)
            if own or len(inputs) != 1 or inputs[0].product in seen:
                return node, chain
            chain.append(node)
            node = inputs[0]
            seen.add(node.product)

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
