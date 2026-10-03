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
Rules apply in order, a child goes to the first group claiming it, and groups
of one child are not created. Group rows can be expanded to show members;
members keep their real node ids, so expansion state survives regrouping.

The view also applies a display **cut-off**: children below x % of the root
are summed into one "n processes below cut-off" row, so the frontier stays
complete.

### Cut-off exceptions (pins)

The rest row keeps its `members` (the hidden rows), so the UI can show what
it contains: selecting it opens a panel listing them with their impacts.
Ticking one **pins** it: its id goes into `View.pinned`, and pinned rows are
kept whatever the cut-off (flagged `pinned` while below it). They then
appear in the tree and, being on the frontier, in the contribution chart;
the rest row shrinks accordingly, so totals stay exact. Pins are part of the
saved view and are cleared when the functional unit changes.

![Cut-off exceptions](img/cutoff-exceptions.png)

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
