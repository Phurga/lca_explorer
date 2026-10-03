# User interface

Code: `src/lca_viewer/static/` (`index.html`, `app.js`, `style.css`),
API in `src/lca_viewer/server.py`.

![Tree and contribution chart](img/tree-and-chart.png)

![Tree graph](img/tree-graph.png)

![Search with filters](img/search.png)

## Decision: no-build Preact + htm + ECharts, vendored

- **No build step** (no Node toolchain on the machine, and none needed for a
  local tool): Preact + htm give components with ES modules only.
- **Vendored** in `static/vendor/` (Preact 10.24, htm 3.1, ECharts 5.5): the
  app works offline, which matters for a local-first tool handling licensed
  data.
- **ECharts**: horizontal bars, 100 % stacked bars, PNG export built in, and
  later maps / Sankey for phases 5.
- If the UI grows (virtualised tree for thousands of rows, more panels),
  migrate to Vite + TypeScript; the API does not change.

## Layout

- **Header**: functional unit search, amount, impact method (all 762
  ecoinvent methods, grouped by family), display cut-off.
- **Search**: free words (each must appear in the name or product, or equal
  the location: `electricity low voltage fr`) and/or **filters** built with
  the same query builder as grouping rules (location, activity type, ISIC,
  CPC, product, …, with value suggestions). Filters alone browse the
  database (e.g. all `RER` market groups). Results show the match count,
  highlighted words, location chips; ↑/↓/Enter to choose, Esc to close.
  Ranking (`query.search`, punctuation ignored): exact name, exact product,
  markets first, name/product starting with the text, text contained as a
  phrase, shorter names.
- **Left — tree**: expand/collapse (▸/▾), amount (waste shown as
  "x kg treated"), impact, share bar. "Expand above x %" auto-expands every
  node above a threshold (hotspot drill-down). Ctrl/⌘-click selects sibling
  rows → "Group selected" creates a manual group; groups have an "ungroup" chip.
- **Left — tree graph** ("Tree" toggle next to "List"): the same visible
  tree drawn as nodes and edges, sharing all state with the list (expansion,
  selection, groups — the chart follows either). Root on the left (→) or on
  top (↓, SimaPro-like). Each box shows name, location, amount and a share
  bar; edge thickness ∝ share of the total, so the main supply paths stand
  out. ± handle or double-click to expand, Ctrl/⌘-click to select, drag to
  pan, wheel to zoom, "Fit" for the whole tree. See below for the layout
  choice.
- **Right — tabs**:
  - *Contribution chart*: current method bars, or several methods as 100 %
    stacked bars; CSV export. See [charts.md](charts.md).
  - *Grouping*: active rules (reorder / remove), quick groups (transport,
    electricity, heat & fuels, waste treatment, by location / activity type /
    ISIC), and the shared query builder — the same component as the search
    filters, so a filter reads and behaves the same in both places — with a
    live "matches N activities, e.g. …" preview; scope everywhere or under
    the selected row.
  - *Whole supply chain*: global grouping with the named groups, first-match
    or direct attribution (see [grouping.md](grouping.md)).

## Tree graph layout

Decision: own tidy-tree layout and SVG rendering instead of a graph library
(ECharts `tree` series, d3-hierarchy, …). The layout is ~20 lines (leaves
take consecutive slots, a parent is centred on its children, depth sets the
other axis), and owning the SVG gives full control over boxes with impact
bars, per-edge widths, selection and grouping interactions — which the
ECharts tree series does not offer. Opening a functional unit zooms to a
readable 60 % centred on the root rather than fitting a large tree into an
unreadable thumbnail; "Fit" shows everything. The view does not re-fit on
each expansion, so the user keeps their place.

Display choice (list / tree) and orientation are per-browser preferences
(`localStorage`).

## State

The client owns the view state (`root`, `amount`, `method`, `cutoff`,
`expanded`, `rules`) and posts it to `/api/rows` on every change (~70 ms on
ecoinvent). It is kept in `localStorage` per browser (convenience only) and
can be downloaded as JSON ("Save view").

Expansion semantics: a group that hides an expanded member opens by itself,
so adding a group-by rule does not hide the branch being explored; collapsing
it on purpose is remembered as `!<group id>` in `expanded`.

## Verification

Screenshots are taken headless with Playwright (`chromium-headless-shell`).
The container lacks `libnss3`/`libnspr4`; they are installed from
conda-forge in `~/.cache/lca_viewer_browser_libs` and passed with
`LD_LIBRARY_PATH` (no root needed).
