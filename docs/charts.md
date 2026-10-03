# Contribution charts

## Decision: the chart is the visible tree

A contribution chart is built from the tree's **frontier**: every visible row
that is not expanded (processes, groups, direct emissions, "below cut-off"
rows). Because children + direct emissions always equal their parent
([calculation-engine.md](calculation-engine.md)), any frontier is a complete,
non-overlapping decomposition of the total — the bars always sum to 100 %.

So the user shapes the chart with the tree: expand a node to split its bar,
collapse to merge, group siblings to bundle them. No separate chart
configuration to keep in sync.

## Modes

- **Current method**: one horizontal bar per frontier row, sorted by
  absolute impact; groups in a distinct colour, negative contributions in
  orange.
- **Several methods (100 %)**: one 100 % stacked bar per selected method
  (default: the current method's family, e.g. the 25 EF 3.1 categories). The
  frontier is the same for all methods; segments are the 11 largest rows for
  the current method plus "n others", to keep the legend readable.

Labels: processes as `name (location)`; direct emissions and below-cut-off
rows name their parent ("direct: wheat production (RoW)",
"other inputs of …"), since several of them can be on the frontier.

## Export

- PNG from the chart toolbox (2× resolution).
- CSV of the frontier with impacts for all methods requested.

## Next

- Compare several functional units (one bar each, same frontier structure
  by grouping rules rather than node ids).
- Regional chart / map: supply × direct impacts by location (phase 5).
