# Roadmap

| phase | content | status |
|---|---|---|
| 0. Spike | import ecoinvent 3.9.1, `G` matrix, lazy tree, validate vs bw2calc | **done** — exact to 1e-15 |
| 1. Core model | tree view state, grouping rules, queries, global grouping | **done (Python)** |
| 2. Local app | FastAPI API + browser UI: tree, expand/collapse, grouping UI, method picker | next |
| 3. Charts | contribution bars from the tree frontier, multi-method, comparison of FUs, export | |
| 4. Practitioner features | saved views, search, "skip markets", Excel export, biosphere flow view per node | |
| 5. Extensions | regional maps, Sankey, scenario comparison, Monte Carlo, AB integration, hosted mode | |

## Ideas parked

- "Transparent markets": option to replace market nodes by their suppliers
  in the tree (practitioners rarely care about the market node itself).
- Text query syntax compiled to the JSON predicates.
- Direct ecoSpold2 → `LCAModel` importer, without Brightway.
