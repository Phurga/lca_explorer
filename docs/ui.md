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
  node above a threshold (hotspot drill-down). A "direct emissions" row
  expands into its elementary flows (substance, compartment, amount
  emitted), groupable by substance or compartment — see
  [grouping.md](grouping.md). Ctrl/⌘-click selects sibling
  rows; a selection bar then offers **Group** (bundle them under one row,
  "ungroup" chip to undo), **Merge** (combine their inventories into one
  process, "merged ×" chip to undo — see [grouping.md](grouping.md)) and
  **To others**.
  Every row has a **×** (on hover, next to ▸ in the list, at the box corner
  in the tree view) that moves it to its parent's **others** row; "To others"
  does the same for a selection. Hovering an "others" row previews its
  panel (closed when the mouse leaves the row and the panel); clicking keeps
  it open, clicking again closes it. The panel follows the view's naming
  choice, and right-clicking one of its processes opens the info pop-up. It shows
  listing what it contains and why (below cut-off / moved); ticking a process
  shows it again — also how to pin exceptions to the cut-off ("pinned ×" chip
  to undo). See [grouping.md](grouping.md).
  **Reset view** collapses the tree and clears pins and moved rows (groups
  kept); changing the cut-off also clears pins and moved rows. Both show a
  notice with **Undo**.
- **Left — tree graph** ("Tree" toggle next to "List"): the same visible
  tree drawn as nodes and edges, sharing all state with the list (expansion,
  selection, groups — the chart follows either). Root on the left (→) or on
  top (↓, SimaPro-like). Each box shows name, location, amount and a share
  bar; edge thickness ∝ share of the total, so the main supply paths stand
  out. ± handle or double-click to expand, Ctrl/⌘-click to select, drag to
  pan, wheel to zoom, "Fit" for the whole tree. See below for the layout
  choice.
- **View bar** (under the toolbar): *Group by product* and *Full / Short /
  Custom* names, applied
  to the list, the tree view, the others panel and the chart; see
  [charts.md](charts.md).

  ![View settings](img/view-settings.png)
- **Right-click a process** (list or tree view): pop-up with its place in
  this tree (amount, impact, path from the root), per-unit cumulative and
  direct impacts, the main direct emissions for the current method,
  **Used by** (the activities consuming its product — or, for a waste
  treatment, generating the waste — with the amount per unit of their
  product and, largest first, the amount each takes in this functional
  unit's supply chain and its share; click one for its own pop-up, *go to*
  to open the tree at it), the ecoinvent description, included activities, time period, classifications,
  authors and file; actions "Use as functional unit" and "Move to others".
  Descriptions are stored in the cached model (`details.pkl`, 77 MB for
  ecoinvent, loaded on first use).
  **Exchanges** as recorded in the dataset: *Inputs* and *Elementary flows*
  with their amount, impact per unit of product and share of the cumulative
  impact, largest first; click a row (▸) for its comment, uncertainty
  (distribution, σ with and without pedigree), pedigree scores, formula and
  variable name. For a tree row, *In this tree* also shows the parent's
  input of this product with its comment and uncertainty. *Reference product
  exchange* gives the production volume and product properties (price,
  carbon and water content, dry mass, …). Exchanges are stored in the cached
  model (`exchanges.sqlite`, 105 MB for ecoinvent 3.9.1, read one activity
  at a time); a model built before this has none — rebuild it to see them.
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
    or direct attribution (see [grouping.md](grouping.md)). Clicking a group
    lists the processes it is made of; right-click one for *Go to in tree*
    (opens and highlights it along its largest supply path) or *Details*.

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
`expanded`, `rules`, pins, moved rows, merges) and posts it to `/api/rows` on
every change (~70 ms on ecoinvent). It is kept in `localStorage` per browser
(convenience only).

**Save ▾** downloads the view as JSON; the arrow menu offers *Load view…*
and *Copy view as JSON*. Saved files record the model (title, size), the
functional unit's name and location, method names and the naming settings,
besides positions. Node ids are positions in the model, so on load, if the
functional unit is not at the same position (view saved from another model
or database version), it is found by name and location and only the
position-free parts are applied (functional unit, amount, method, cut-off,
rules scoped everywhere, names); expansions, pins, moved rows and merges are
dropped, and the notice says so. Loading is undoable.

Expansion semantics: a group that hides an expanded member opens by itself,
so adding a group-by rule does not hide the branch being explored; collapsing
it on purpose is remembered as `!<group id>` in `expanded`.

## Verification

Screenshots are taken headless with Playwright (`chromium-headless-shell`).
The container lacks `libnss3`/`libnspr4`; they are installed from
conda-forge in `~/.cache/lca_viewer_browser_libs` and passed with
`LD_LIBRARY_PATH` (no root needed).
