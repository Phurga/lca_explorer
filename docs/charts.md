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
Elementary flows name the substance, then the emitter and compartment in
grey ("Dinitrogen monoxide · direct: wheat production (RoW) · air, …"):
the emitter comes first so that it survives when a long label is shortened.

## View settings: product grouping and row names

Decision: names were first a chart-only "quick view"; they are now a **view
setting** (bar under the left toolbar) applied to the list, the tree view
and the chart alike, so what the user reads in the tree and in the chart
match.

- **Group by product**: in the tree, sibling inputs delivering the same
  reference product (other regions, markets or routes) are bundled into one
  group row named after the product, with the summed amount when the units
  match (`View.group_products`, applied after the user's rules; flows under
  direct emissions are not affected). The chart follows the tree: a
  collapsed product group is one bar. A first version also merged
  same-product bars *across* the tree in the chart; it was removed, and
  brought back (2026-10) for the tree only.

- **Row names** (tree and chart): *Full* (`activity name (location)`), *Short* (product name,
  with the qualifiers needed to tell rows apart in grey: location, then
  activity name, then the parent, then a number), or *Custom*: two recipes,
  a **main** part (normal text, default `{product}`) and a **secondary**
  part (grey, default `{location}`), with tokens `{product} {name}
  {location} {unit} {type} {isic} {cpc} {database}`; combined rows list
  their values comma-separated ("FR, DE, US +2"; up to 6 in the secondary
  part). Choices are per-browser preferences.

Axis label widths are measured with a canvas (ECharts mis-measures two-tone
rich-text labels with `containLabel`).

## Export

- PNG from the chart toolbox (2× resolution).
- CSV of the frontier with impacts for all methods requested.

## Next

- Compare several functional units (one bar each, same frontier structure
  by grouping rules rather than node ids).
- Regional chart / map: supply × direct impacts by location (phase 5).
