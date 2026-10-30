# Grouping

Code: `src/lca_viewer/query.py`, `src/lca_viewer/view.py`, `src/lca_viewer/analysis.py`.
Demo: `scripts/demo_grouping.py` (reference example).

## Decision: two kinds of grouping, kept separate

"Group processes" covers two different questions, and mixing them leads to
double counting:

1. **Tree grouping** — bundle *siblings* under a virtual node
   ("show the European suppliers of this market as one line").
   Purely a display concern: numbers never change.
2. **Global grouping** — "how much does electricity contribute, anywhere in
   the supply chain?". Groups nest (electricity → transport → electricity),
   so an explicit attribution rule is needed.

## Tree grouping (`View` + `GroupRule`)

A rule bundles the children of nodes in its `scope` (one node id, or `*` for
the whole tree). Two forms:

- **named group**: `name` + `predicate` — matching children go into one group;
- **group by**: `by` = a metadata field — children partitioned by value
  (thematic grouping: location, ISIC division, CPC section, activity type…).

Manual grouping = named group with `{"products": [...]}` scoped to one node.
Named groups are applied first, then group-by rules, each in list order (so
a broad "group by location" never swallows a group the user named); a child
goes to the first group claiming it, and groups of one child are not created.
A group that hides an expanded member opens by itself. Group rows can be expanded to show members;
members keep their real node ids, so expansion state survives regrouping.

The view also applies a display **cut-off** (`View.cutoff`, `cutoff_basis`):
under each parent, the smallest children are bundled into one **others** row
as long as their cumulative |impact| stays within x % of the reference:

- `cutoff_basis="parent"`: the parent (sum of the siblings' |impact|);
- `cutoff_basis="total"`: the root score.

Decision: cumulative "bottom x %" rather than "each child below x %", so an
others row never weighs more than x % of its reference — with an individual
threshold, hundreds of small children could add up to a large, opaque
others. The basis is a toggle next to the cut-off ("% of total | parent").

### "Others": cut-off, pins and moved rows

Each parent can have one **others** row (kind `rest`) bundling the siblings
that are not shown individually. A sibling goes there when

- it is below the display cut-off (and not pinned), or
- the user **moved** it there (`View.others`), whatever its impact — to keep
  irrelevant sub-processes out of the tree and the contribution chart.

Conversely, the user can **pin** a row (`View.pinned`) to show it although
it is below the cut-off. Moving wins over pinning. The others row keeps its
`members`, so the UI can show what it contains and why (`moved` / cut-off);
ticking a member shows it again (un-move + pin). Because others is just one
more row on the frontier, totals stay exact whatever is moved or pinned.
A single row below the cut-off is shown as itself, but a single moved row
still gets an others row. An others row expands like a group: its members
are rendered under it (parent = the others row) and replace it on the
frontier, so the chart breaks it down too; merges and group rules on them
are still scoped to the real parent. Pins and moved rows are part of the saved view and
are cleared when the functional unit changes.

Decision: **changing the cut-off clears pins and moved rows** (with an Undo
notice that also restores the previous cut-off). Pins are exceptions to one
particular cut-off; kept across changes they made a raised cut-off look
ignored ("Show all" pins every member), and moved rows mixed confusingly
with a different set of cut-off rows. **Reset view** does the same plus
collapsing the tree, keeping grouping rules, functional unit, method and
cut-off (also undoable).

![Cut-off exceptions](img/cutoff-exceptions.png)

### Merge

Merging (`View.merges`: `{"scope": parent id, "products": [...]}`) combines
sibling processes into **one process whose inventory is the union of
theirs**: expanding it shows each input once, with the amounts and impacts of
inputs common to several members summed, and the members' direct emissions
summed. This differs from a group, which only bundles rows (expanding a
group shows the original processes). A merged process's inputs are ordinary
nodes (same product, summed amount), so they expand normally and totals stay
exact by linearity. Its amount is shown only when all members deliver the
same product in the same unit (e.g. wheat grain from FR + DE). Members
sharing a name are listed once: "steel (RER + RoW)", "market for urea
(RoW + CN)"; different names stay separate ("steel (RER) + iron (DE)"). Merges are
kept by Reset view and cut-off changes, like groups, and cleared with the
functional unit.

A group of processes (named, group-by or by product) has a **merge** chip
that turns it into a merged process with the same members under the same
parent. Merges are applied before grouping rules, so the rule no longer
claims those members; a manual group of that parent is removed, while a rule
applying elsewhere ("group by location" everywhere) keeps grouping the other
parents. Groups of elementary flows have no merge chip: their amounts are
already summed.

A whole rule can be merged too (`GroupRule.merge`, the **merge** button in
the Group rules tab): every group it makes, at any depth and in rows expanded
later, is shown as a merged process instead. Those rows are marked
"merged" without the × (unmerging one would change the rule everywhere; the
button in the Group rules tab does it). A rule can also be **disabled**
(`GroupRule.disabled`): kept in the list and the saved view but not applied,
in the tree or in the *Group contributions* analysis, to compare with and
without it.

![Merge](img/merge.png)

### Quick group "infrastructure"

ecoinvent 3 has no infrastructure flag (ecoinvent 2 had one), so the quick
group is a query, checked on ecoinvent 3.9.1 (1,570 activities, no services,
energy carriers or transport services among them):

- CPC section 5, *constructions*: buildings, factories, plants, roads,
  railway tracks, electricity networks, mines (900);
- CPC section 4 counted per `unit`: vehicles, machines, heat pumps, CHP
  units… (635);
- machinery and vehicles inventoried in kg or per unit, by product name:
  tractor, harvester, agricultural machinery, lorry, bus, locomotive,
  aircraft, ship… ("machine operation" excluded: it is a service in hours).

On 1 kg of GLO wheat (feed), infrastructure is 10.0 % of EF 3.1 climate
change (first-match rule, *Group contributions* tab). In the tree, like any
named group, it only bundles two or more infrastructure siblings.

### Direct emissions: elementary flows

A "direct emissions" row expands into its **elementary flows** (kind
`flow`, id `<direct id>/f<flow index>`): amount = operation level × `B`
entry, impact = amount × characterisation factor, for every method, so the
flows sum exactly to the direct row. Under a merged process the members'
flows are summed. Flows without a factor in any method are left out (they
weigh nothing anywhere); flows without impact for the *current* method go
to "others" whatever the cut-off ("no impact" in its panel), since they are
kept only for the other methods of the multi-method chart. The cut-off,
pins, "To others" and the chart work on flows as on processes.

Flows are grouped by rules with `target: "flows"`, which apply only under
direct rows (and process rules only to processes). Their predicates and
group-by fields refer to flow metadata (`query.derived_flows`: `name`,
`compartment`, `subcompartment` split from the Brightway `categories`,
`unit`): quick groups **by substance** (CO2 to urban, rural and
unspecified air as one row, amounts summed) and **by compartment**, and
Ctrl/⌘-click → Group for a manual `{"flows": [...]}` group. Flow groups are
labelled by the value alone ("air", "Carbon dioxide, fossil"). Named flow
rules (a predicate over flows, e.g. *Add as a group* on a substance in the
Flow contribution tab: `{"field": "name", "op": "eq", "value": "Dinitrogen
monoxide"}`, all compartments) are also **flow groups** of the *Whole supply
chain* analysis (below); `by` flow rules are not.

## Queries (`query.py`)

JSON predicates, evaluated vectorised over activity metadata
(21k activities: milliseconds):

```json
{"all": [
  {"field": "reference_product", "op": "startswith", "value": "electricity"},
  {"not": {"field": "location", "op": "in", "value": ["CH", "FR"]}}
]}
```

Operators `eq ne in contains startswith regex`, combinators `all any not`,
explicit `products`. Chosen over a text DSL for the first version because a UI
filter builder maps 1:1 onto it and it is trivially serialisable in saved
views; a text syntax can be parsed into the same structure later.

Derived fields for thematic grouping: `activity_type` (market / market group /
treatment / production, from the ecoinvent naming convention),
`isic_division`, `cpc_section`.

## Global grouping (`analysis.py`)

Both rules are exact and sum to 100 % of the score:

| rule | meaning | computation |
|---|---|---|
| `direct_contribution` | each activity's own emissions go to its group | supply vector × direct impacts |
| `first_match_contribution` | walking down from the functional unit, the first matching activity takes its whole cumulative impact | cut the inputs of matched activities, one extra solve |

The first-match rule is computed with linear algebra rather than traversal, so
it is exact even through loops: with `A_cut` = `A` without the inputs of
matched columns, `s = A_cut⁻¹ f`; a matched product contributes
`s_j · A_jj · G_j`, an unmatched activity its direct emissions `s_j · U_j`.

Reference example (EF 3.1 climate change), same four groups:

| group | direct | first match |
|---|---|---|
| electricity | 12.1 % | 6.6 % |
| fertilisers | 6.6 % | 26.8 % |
| transport | 9.2 % | 10.3 % |
| machinery & diesel | 10.1 % | 17.6 % |
| other (incl. field emissions) | 62.0 % | 38.7 % |

The two readings differ a lot, so charts must always state which rule is used.

### Flow groups

`supply_chain_contribution(..., flow_groups=...)` also takes groups of
**elementary flows** ("how much N2O, outside the process groups?").
Decision: **the order of the rules decides** (the API takes `order`, the names
of the groups in the order of the Group rules tab; without it, process groups
come first, as before). What an activity emits goes to its activity group or to
a flow group, whichever is listed first:

- a process group listed before a flow group keeps every flow it reaches —
  under first match, a fertiliser's supply chain with its N2O; under direct, a
  fertiliser process's own N2O;
- a flow group listed before the process group takes its flows from it: under
  direct, `s_j · (CF_g B_g)_j` of the activities concerned; under first match,
  the flows of the group in the whole supply chain of a matched activity,
  `x_j · G_g[j]` with `x_j` the amount of product reaching it and `G_g` the
  cumulative impact of the flows of the group alone, `(Aᵀ)⁻¹ (CF_g B_g)ᵀ`;
- what no process group claims is split by the flow groups, else *other*
  (under first match, `s` is the cut supply, so emissions inside matched
  supply chains are not counted again).

Each flow then goes to one group only (a flow goes to the first flow group
that claims it), so the parts add up to the score exactly, whatever the order.
Cost: under direct, a few vector operations per flow group. Under first match,
a flow group listed before some process group needs `G_g`: one solve per such
flow group (one right-hand side per method asked for) with `Aᵀ`, factorised on
the first such request and kept (about 0.7 s once on ecoinvent 3.9.1, then
nothing noticeable); flow groups listed after all process groups need none.
A first version took the flows from the whole supply chain before the process
groups always; that moved N2O reached through fertilisers out of the fertiliser
group, which reads wrong when the fertiliser rule is listed first, and is now
what listing the flow rule first asks for. The API computes the current method
only (`method`).

Reference example (EF 3.1 climate change, wheat grain feed), with
pesticides / fertilisers / agricultural machinery and a *Dinitrogen
monoxide* flow group: see the Group contributions tab; the flow group is the
field N2O of the crops (the Flow contribution tab gives all N2O, 34.1 %).

### What a group is made of, and finding it in the tree

Clicking a group (bar or table row) lists its activities, largest first, from
the same per-activity values the totals are summed from
(`group_contributors`), so the list adds up to the group exactly. Under first
match, a matched activity counts with its supply chain and an unmatched one
("other") with its own emissions; under direct, every activity with its own
emissions.

*Go to in tree* (right-click, or double-click) opens the tree at one of these
activities. An activity usually appears at many places in the tree, so the
decision is to open it along the **path that carries the most of its
impact**: each edge parent → child weighs the share of the parent's |impact|
flowing through the child, and the best path maximises the product of shares
(Dijkstra on −log share, `main_path`). The product of shares telescopes to
`path amount × G_target / G_root`, so this is the path delivering the most of
the activity's product. Under first match the path does not go through
matched activities (their supply chain belongs to their group), unless no
other path exists. The server then expands the path, opens groups and pins
rows hidden in "others" (`View.reveal`); the change can be undone from the
notice.
