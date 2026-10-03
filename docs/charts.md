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
"others under …"), since several of them can be on the frontier.

## Quick views

- **Group products**: chart rows of the same reference product (different
  regions, markets or production routes) are merged into one bar; direct
  emissions are merged per emitting product, others rows into one. The tree
  is unchanged.
- **Row names**: *Full* (`activity name (location)`), *Short* (product name,
  with the qualifiers needed to tell rows apart in grey: location, then
  activity name, then the parent, then a number), or *Custom*: a recipe such
  as `{product} ({location})` with tokens `{product} {name} {location}
  {unit} {type} {isic} {cpc} {database}`; merged rows list their values
  ("FR, DE, US +2"). Choices are per-browser preferences.

Axis label widths are measured with a canvas (ECharts mis-measures two-tone
rich-text labels with `containLabel`).

## Export

- PNG from the chart toolbox (2× resolution).
- CSV of the frontier with impacts for all methods requested.

## Next

- Compare several functional units (one bar each, same frontier structure
  by grouping rules rather than node ids).
- Regional chart / map: supply × direct impacts by location (phase 5).
