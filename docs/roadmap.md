# Roadmap

| phase | content | status |
|---|---|---|
| 0. Spike | import ecoinvent 3.9.1, `G` matrix, lazy tree, validate vs bw2calc | **done** — exact to 1e-15 |
| 1. Core model | tree view state, grouping rules, queries, global grouping | **done (Python)** |
| 2. Local app | FastAPI API + browser UI: tree, expand/collapse, grouping UI, method picker | **done (first version)** |
| 3. Charts | contribution bars from the tree frontier, multi-method, export | **done (first version)**; FU comparison to do |
| 4. Practitioner features | saved views, search, "skip markets", Excel export, biosphere flow view per node | flows of direct emissions in the tree: **done** |
| 5. Extensions | regional maps, Sankey, scenario comparison, Monte Carlo, AB integration, hosted mode | |

## Next steps

1. Feedback from use on real projects (foreground databases on top of
   ecoinvent): label/amount display, grouping presets that practitioners want.
2. Saved views keyed on activity codes instead of matrix indices.
3. ~~Compare several processes in one chart~~: first version done (*Compare*
   mode: contribution by group, difference, impact categories). Next: aligned
   tree diff, flow-level diff, what-ifs (substitutions) in a comparison.
4. "Transparent markets" option.
5. Elementary flows across the whole supply chain ("all N2O, wherever
   emitted"); flow query builder in the grouping panel.
6. ~~Load a model directly from a Brightway project in the UI~~: done
   (*Load database*, see [data-import.md](data-import.md#loading-from-the-app)).

## Ideas parked

- "Transparent markets": option to replace market nodes by their suppliers
  in the tree (practitioners rarely care about the market node itself).
- Text query syntax compiled to the JSON predicates.
- Direct ecoSpold2 → `LCAModel` importer, without Brightway.
