# Calculation engine

Code: `src/lca_viewer/model.py`, `src/lca_viewer/tree.py`.

## Decision: precompute cumulative impacts per unit, expand the tree lazily

SimaPro-style trees cannot be built eagerly on ecoinvent: the supply chain is
full of loops (electricity → coal mining → electricity…), so the tree is
infinite. Classic graph traversal (bw2calc's former `GraphTraversal`,
`bw_graph_tools`) does a cut-off-driven search over supply amounts, which ties
what the user sees to a calculation parameter.

We instead compute, once per (database, set of methods):

```
U = CF · B                 (methods × activities)   direct impacts per unit of operation
G = (Aᵀ)⁻¹ Uᵀ              (products × methods)     cumulative impacts per unit of product
```

`G[p, k]` is "impact k of 1 unit of product p, whole supply chain included".
All methods are solved with a **single factorisation** of `A` (multiple
right-hand sides).

A tree node is *(path, amount of product p)*. Expanding it reads column `p`
of `A`:

```
operation x  = amount / A[p, p]            (handles production ≠ 1 and waste, A[p,p] < 0)
child i      = amount_i = −A[i, p] · x     impact = amount_i · G[i]
direct       = x · U[:, p]
```

Because `Aᵀ G = Uᵀ`, children + direct = parent **exactly**, at every node.
Consequences:

- Any frontier of the tree (any combination of expanded / collapsed nodes) is a
  complete, non-overlapping decomposition of the root score. This is what makes
  "the chart is the visible tree" possible (see [charts.md](charts.md)).
- Loops cost nothing: they are followed only as far as the user expands.
- Cut-off is a display parameter (hide nodes < x %), not a calculation one.
- Expansion is O(number of inputs) — microseconds. No server round trip
  needed for performance reasons.

### Waste treatment sign convention

Checked on ecoinvent 3.9.1: 2,884 waste treatment products have a negative
diagonal (−1 kg of "waste" produced per treatment), and all 39,448
off-diagonal entries on those rows are **positive**. Expanding a waste
generator therefore yields a child with a **negative amount** of the waste
product (e.g. −0.2 kg), while the treatment's operation level
(`amount / A[p,p]`) and its burdens are positive. This is the ecoinvent /
Brightway reading; the UI should display it as "0.2 kg treated"
(SimaPro style). Covered by `tests/test_engine.py::TestWasteTreatment`.

Node identity is the path of product indices from the root, so the same
product reached through two routes gives two distinct nodes, as in SimaPro.
Paths use matrix indices, which are only stable for a given cached model:
re-importing or adding a foreground database can reorder them. Saved views
must therefore translate paths to activity `(database, code)` keys when
persisted (TODO, phase 4).

## Model independence

`LCAModel` only holds `A`, `B`, characterisation vectors and metadata
dataframes. It does not depend on Brightway: importers (Brightway now,
direct ecoSpold2 / openLCA later) produce an `LCAModel`. The model can be
cached to disk (`save` / `load`, npz + pickle; `G`, whose 762 solves take about 10 s on ecoinvent, is kept as
`G.npy` with a `G.json` of the files it was made from, and computed again when they change) — loading ecoinvent from the
Brightway project takes ~4 s.

Restrictions (for now):
- square, single-output technosphere (true for ecoinvent allocated system
  models and for Brightway databases built on them);
- static calculation (no Monte Carlo yet; to be done later by re-solving `G`
  on sampled matrices).

## Solver

`pypardiso` when available (MKL, multithreaded), else `scipy.sparse.linalg.splu`.

## Validation (ecoinvent 3.9.1 cut-off, 21,238 activities, 26 methods)

Script: `scripts/validate_wheat.py` — see [reference-example.md](reference-example.md).

| check | result |
|---|---|
| root scores vs `bw2calc.LCA`, 26 methods | max rel. diff 2e-15 |
| `G` vs `bw2calc.LCA` for 20 random activities | max rel. diff 5e-16 |
| children + direct = parent, 275 expanded nodes | max rel. diff 1.4e-15 |
| build model from Brightway project | 3.6 s |
| factorise + solve `G` for 26 methods | 0.6 s |
| expand 275 nodes | 0.02 s |
