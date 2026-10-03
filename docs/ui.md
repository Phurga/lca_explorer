# User interface

Code: `src/lca_viewer/static/` (`index.html`, `app.js`, `style.css`),
API in `src/lca_viewer/server.py`.

![Tree and contribution chart](img/tree-and-chart.png)

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

- **Header**: functional unit search (name, product or location words),
  amount, impact method (all 762 ecoinvent methods, grouped by family),
  display cut-off.
- **Left — tree**: expand/collapse (▸/▾), amount (waste shown as
  "x kg treated"), impact, share bar. "Expand above x %" auto-expands every
  node above a threshold (hotspot drill-down). Ctrl/⌘-click selects sibling
  rows → "Group selected" creates a manual group; groups have an "ungroup" chip.
- **Right — tabs**:
  - *Contribution chart*: current method bars, or several methods as 100 %
    stacked bars; CSV export. See [charts.md](charts.md).
  - *Grouping*: active rules (reorder / remove), quick groups (transport,
    electricity, heat & fuels, waste treatment, by location / activity type /
    ISIC), query builder (conditions on any metadata field, value
    suggestions, scope everywhere or under the selected row).
  - *Whole supply chain*: global grouping with the named groups, first-match
    or direct attribution (see [grouping.md](grouping.md)).

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
