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

- **Load database** (top bar): the app starts with no database and shows
  this dialog as its start page. Three lists: databases loaded (*Switch to*,
  *Close* to free memory), built models (`data/models.json`, *Open*), and
  the projects of the Brightway data folder (`BRIGHTWAY2_DIR`, else
  Brightway's default folder; *Other folder…* types another), each database with its process count and *Build and open* / *Open*
  / *Rebuild and open*. A load runs on the server: the dialog shows its steps
  (*Details* for the build's output) and opens the database when it is
  ready; it can be closed meanwhile and finds the load again when reopened.
  Decision: one database per page (`/db/<key>/`), switched with the picker
  next to the title, rather than databases mixed in one workspace: tabs,
  saved views and node ids are per model. An address of a database that is
  not loaded (after a restart) shows the start page with a note.
  The Brightway section also changes projects: *+ New project* (empty, or
  from a backup: files.brightway.dev, e.g. BAFU, or a `.tar.gz`), and on
  each project *+ Add database* and *Delete* (typing the name to confirm), and
  on each of its databases *Remove* (after a confirmation; refused while another
  database of the project links to it or its model is open; the model already
  built stays in the app). *Add database* asks for the source (ecoSpold2,
  SimaPro CSV, ecoinvent with the user's account, or *Brightway repository*:
  only the biosphere and impact methods of an ecoinvent version, from
  files.brightway.dev, without a database), with a box *Add the default biosphere and impact
  methods* and their ecoinvent version (detected from the file name or the
  SimaPro background, else chosen), ticked when the project has no biosphere
  database; it opens by itself on a project just created empty. Forms on a
  project, and the jobs started from it, open under its row, where it was
  clicked. Each opens a small form above the
  list (`ProjectForm`); file fields take a path or *Choose file…*
  (uploaded). A SimaPro import that leaves exchanges unlinked shows them,
  with *Import without them…*. Decision: as in Activity Browser, projects
  are created first and databases added to them, so there is one way in for
  each source (ecoinvent no longer makes its own project). These forms sit
  in the Load database dialog, next to the projects they change, rather than
  in a separate manager.
- **Process form** (opened from the edit panel): a wide dialog to enter
  a process as in SimaPro: target (folder, project, database or new one),
  process and documentation, products (co-products with allocation %, their
  sum shown), then one section per exchange kind (technosphere, nature,
  emissions, waste to treatment, avoided products), each with a search box
  over the project (↑↓ Enter to add) and rows with amount, unit, SD² and
  comment. The footer says what is missing before *Stage*. A new process
  is a draft kept in `localStorage` (per browser) until *Clear*. Decision: a dialog over the
  explorer rather than a page of its own, so that one can look up processes
  in the open database while entering a dataset.
  *Start from* (same form) copies any process of the project into it
  (`GET /api/brightway/dataset`, read from the SQLite file).
- **Mode switch** (top bar, at the right: **View / Compare / Edit**; keys v, c, e).
- **Compare mode** (the middle button): the workspace and the side
  panel give way to a comparison of the functional units of the open tabs (the
  tabs stay mounted, so nothing is lost when going back). Left: the tabs to
  compare (tick them; the radio marks the **baseline**, the others are read against
  it) and the impact method. Tabs are named by what tells them apart (the place when
  they share the process). Three panels, each with CSV export. The first two split the
  bars the same way, chosen with the switch **By groups / By tree / By flows** (shared
  by both):
  - *By groups*: the named rules of one tab's *Group rules* (process rules as groups,
    flow rules as flow groups, as in *Group contributions*), and the attribution (*own
    emissions of each group*, or *whole supply chain below the first match*, see
    [grouping.md](grouping.md)). The rules are edited in the fourth panel, *Group rules*
    (link under the groups on the left): it is the *Group rules* tab of the tab chosen
    in *Rules of*, the same rules, so a change there is seen in its tab and the other way
    round, without leaving Compare mode. A tab counts by
    its functional unit and amount; its cut-off, expansion, merges and substitutions do
    not enter (`POST /api/compare` works on the matrices, each entry split exactly into
    the groups and *other*; the page warns when a tab has substitutions, or when the
    units differ).
  - *By tree*: the tree each tab displays in View mode (`/api/rows` from the tab's
    own view: expansion, groups, merges, restored rows, substitutions, cut-off, at the
    comparison's method): its expanded rows are split, the others are bars. Bars are
    lined up by process (wherever it is in the tree; its direct emissions, flows and
    others row go with it); the functional unit of each tab is the same bar whatever
    process it is. A process expanded in one tab and collapsed in another shows as its
    inputs on one side and as one bar on the other, so shape the trees alike.
  - *By flows*: the elementary flows of the whole inventory (`basis: "flows"`), one per
    substance, the largest *n* over all tabs (5 to 40) and *other* for the rest.
  A row a tab does not have shows as – in the table.
  - *Compare contribution*: one stacked bar per tab, in the unit, as % of the
    baseline's total, or each as its own 100 %; the table under it has a value and a
    share column per tab (numbers right-aligned, the totals in the same columns) and
    the change against the baseline.
  - *Difference*: a waterfall from the baseline's total to another tab's, one step per
    group, row or flow (largest first; orange adds, green takes away; the steps add up to the
    difference exactly). *Zoom on the steps* (on by default when the difference is
    under 25 % of the totals) starts the axis near the steps; the totals are then cut.
  - *Impact categories*: a heat map of tabs × the methods ticked in the **method list on
    the left**, the one place where methods are chosen: nested by family (fold, tick a
    whole family or single methods; a filter box searches them). A tick means "in this
    heat map" (the family of the first method to begin with); the round orange mark is
    the one method of *Compare contribution* and *Difference*. Each cell is the change against the baseline
    (the colour stops at ±100 %; values in the tooltip), optionally with the largest
    difference first.
  - *Group rules*: the rules of the tab chosen in *Rules of*, to edit (see *By groups*).
  Decision: a mode of its own, not a panel of the side panel: the side panel shows
  one tab, and a comparison needs several. Tabs, not a new kind of object, so that
  a comparison is made of what is already open. Groups instead of tree rows so that
  tabs with different supply chains line up (`charts.md` has the same reasoning).
  The choices are kept while the page is open, not saved.
- **View / Edit switch** (top bar). View is the explorer. Edit swaps the right
  panel for the *edit panel* (the side panel's tabs stay mounted, hidden): the
  focused tab's process with *Edit*, *Copy as new* and *Delete*, *+ New process*,
  the list of staged changes (*Open*, *Remove*), and *Apply and rebuild*. Decision:
  the results shown never change under the user's hands; edits are collected,
  then made in one transaction and the model rebuilt once, instead of a rebuild per
  save. The form (a dialog: its tables need the width) stages instead of writing:
  the change goes to `POST /api/brightway/pending` with the form's state, which is
  how a staged change is opened again. The process of the focused tab is found from the
  activity's database and code. Switching to Edit needs a Brightway-built database.
  *Apply* runs `lca-viewer apply`; with *and rebuild* and when the open database is
  affected, `/api/load` with `reopen`: the old model is served until the new one is ready, then
  the page reloads in View mode.
- **Edit mode and the view.** `EditorContext` carries what the pop-ups and the tree need
  (mode, project, staged changes, locked databases, whether the impacts are stale). In Edit mode
  `FUView` sends `merges: []`, `substitutions: {}`, rules with `merge` off, and applies no custom
  names (they stay in the tab, for View). Conflicts found: a grouping rule's *merge* acts on the
  tree, not the data, so it is off too; pins, cut-off and groups stay but act on impacts that may
  be stale (greyed: `.app.stale .impact`). Edit mode is plum (`.app.editing` redefines the accent,
  bar and background variables, an EDIT MODE badge and an underlined top bar), View mode green. In
  View mode with changes staged, the Edit button reads *Edit (n staged)* and a warning under the top
  bar offers to review them (`.staged-warning`).
  `Editable` is a value that turns into an input on double-click (Enter or blur stages, Escape
  cancels); its `start` prop (a new object each time) opens it without a double-click; its `format` prop
  (`fmtAmount` for amounts) is how the value reads when not edited, the input has it in full;
  `quickEdit` patches the form of the dataset (its staged form if there is one) and stages
  it again; exchanges are found by what they link to (`input`, recorded by the build, so models
  built before need one rebuild for amounts to be editable), and an allocated process's share of
  an exchange is scaled back to the dataset's. The list has no direct editing: **e** on the selected
  process opens its form (`DatasetForm`), where everything is changed; the pop-ups' values are still
  edited by double-click. An exchange's source is replaced in the process form
  (*replace* on its row: the exchange keeps its id, amount, comment and uncertainty; `editing._update_exchange`
  links it to the new process or flow) and, for a product, with *use instead* in the *Alternatives*
  panel of its pop-up, which stages that replacement in the dataset(s) of the process(es) above it.
- **Rebuild required**: `/api/databases` gives each Brightway-built model's `outdated` (what
  changed in the project since: `sources.changes`); the top bar shows the button when it is set.
- **Locks**: *Lock* in *Load database* (`POST /api/brightway/lock`, kept in `data/locks.json`);
  `/api/brightway` lists `locked` per database and `/api/brightway/dataset` says whether a process is
  locked, which disables its fields, Edit and Delete.
- **Tabs and panes** (like a notes app such as Obsidian): each tab is one
  functional unit with its own tree, method, cut-off, rules and merges. Tabs
  sit on top of their pane: **+** opens an empty tab (the search box takes
  the focus; method, cut-off and tree-wide rules come from the tab it was
  opened from), × or middle-click closes, drag to reorder. Drag a tab onto
  another pane to move it there, or onto a pane's left, right, top or bottom
  quarter to split it (side by side or one above the other); the dividers
  between panes can be dragged. The two buttons at the right of a tab bar
  split right / down with a *copy* of the active tab, e.g. to compare one
  functional unit under two methods. *Open in new tab* in the right-click
  pop-up opens a process as the functional unit of a new tab.
  Decision: one shared **side panel** on the right (process and flow
  contribution, group rules, group contributions), showing the active tab of the pane clicked
  last, as in Obsidian's sidebars, rather than one per pane: panes stay wide
  enough for the tree. It is hidden with the side-panel icon at the end of its tab bar
  (a window with a filled panel on the right, as in VS Code and Obsidian; the edit panel
  has it in its title), shown again with the same icon, empty, on the right edge of the
  page, and resized by dragging its left edge. Each tab
  renders its panels into its own slot of the side panel (a small portal), so
  a tab keeps its panel state (current tab, chart choices) when hidden.
- **Top bar**: in the middle, the **search** of a process, like a command palette
  (Ctrl/⌘ + K focuses it; Enter makes the result the functional unit of the tab in
  focus, Ctrl/⌘ + Enter opens it in a new tab), and the **View options** switch.
  View options shows or folds away, in all tabs and pop-ups at once, the options of
  the views: the header (amount, impact method (all 762 ecoinvent methods, grouped by
  family), display cut-off), the toolbar (List / Tree / Sankey, Collapse, Reset view) and
  the view bar. The switch is common (and remembered); the options are those of each tab
  or pop-up, which have their own.
- **Header** (per tab, with View options): amount, impact method, display cut-off.
- **Search**: free words (each must appear in the name or product, or equal
  the location: `electricity low voltage fr`) and/or **filters** built with
  the same query builder as grouping rules (location, activity type, ISIC,
  CPC, product, …, with value suggestions). Filters alone browse the
  database (e.g. all `RER` market groups). The **(i)** after the value of a
  condition (search filters and group rules alike, for processes and flows) opens the
  values of its field with how many activities have each (`GET /api/options/{field}`):
  for a *location*, the country or region of each code (`codes.py`: ISO 3166 countries,
  ecoinvent regions such as `RER`, states and provinces of the US, Canada, Brazil and
  China, searched by code or name: type "brazil" to find `BR`); for *ISIC* and *CPC*
  classes the names that the data writes after the code, for an *ISIC division* and a
  *CPC section* their standard names; for the others the values (product, activity
  name: with a box to search them). A short list (unit, activity type, product /
  waste, database, compartment) also shows when the pointer rests on the (i). A click
  on a row fills the value in (added to the list of *is one of*; for ISIC and CPC
  the code with *starts with*, since the data reads `0111:Growing of cereals …`).
  The menu is drawn on the page itself, outside the filter panel, which stays open.
  The filters are always shown under the search box while it is open (there is no
  *Filters* button). The search of processes and flows in the **process form** of Edit mode
  (exchanges, *Start from*) has the same filters, on the fields it can read without opening
  the datasets: location, reference product, name and database for processes;
  compartment, subcompartment, substance, unit and database for flows
  (`/api/brightway/search?filters=` takes a predicate as in [grouping.md](grouping.md),
  `/api/brightway/options` the values for the (i), from the project rather than from the open model).
  Results show the match count,
  highlighted words, location chips; ↑/↓/Enter to choose (Ctrl/⌘+Enter or
  Ctrl/⌘-click: in a new tab), Esc to close.
  Ranking (`query.search`, punctuation ignored): exact name, exact product,
  markets first, name/product starting with the text, text contained as a
  phrase, shorter names.
- **Left — tree**: expand/collapse (▸/▾), amount (waste shown as
  "x kg treated"), impact, share bar — the share **of the total** or **of
  the parent row**, following the cut-off basis switch (total | parent; the
  tree view's boxes too, while edge widths stay shares of the total). A "direct emissions" row
  expands into its elementary flows (substance, compartment, amount
  emitted), groupable by substance or compartment — see
  [grouping.md](grouping.md). Ctrl/⌘-click selects sibling
  rows; a selection bar at the bottom of the tree (so the rows
  do not move), shown when two or more rows are selected, offers **Group** (bundle them under one row,
  "ungroup" chip to undo; a group of processes, from any rule or "Group by
  product" (whose "ungroup" chip splits just that group back into its
  members, each of which then has a "regroup" chip that bundles them again;
  turning "Group by product" on again regroups all of them; a group made by a "group by"
  rule, which only its rule can undo, shows the "ungroup" chip greyed and unusable, its
  tooltip saying that the rule keeps the group), has a "merge" chip — or select it and click Merge — to turn it into a
  merged process), **Merge** (combine their inventories into one
  process, "merged ×" chip to undo — see [grouping.md](grouping.md)). The status bar under the tree sums the selected processes,
  merged processes and groups of processes (a group or merged row counts its
  member processes): amounts per unit (units are not added together) and
  impact; a row inside another selected row is not counted again, its impact
  is in that one's.
  Every row has a **×** (on hover: at the right end of the name in the list,
  after the other row actions; at the box corner in the tree view) that moves
  it to its parent's **others** row; with several rows selected, the × of one of
  them (like **o**) moves all of them. Rows shown inside an expanded others row have **restore** instead
  (↑ in the tree view, **o** on the keyboard; with several selected, all of them):
  it moves them back from others to the main section, shown despite the cut-off ("restored ×" to
  undo), like ticking them in the others pop-up. Row actions ("(i)", "✎", "merge", "merged ×", "ungroup",
  "restored ×", "×") show only on the hovered or selected row.
  **Rename** (✎, or **r** on the selected row): the name is edited in place
  (Enter or leaving the field saves, Esc cancels, **↺** or an empty name
  restores the original; the tree view asks in a dialog). It is a *display* name only,
  part of the tab's view like groups and merges: the database is not changed.
  A process or a flow is renamed wherever it appears (by activity / flow), a
  merged or group row where it is. The custom name replaces the main part of
  the label in the tree, the pop-ups' titles and the chart (direct emissions
  and their flows follow their process's name); the grey qualifier and the
  tooltip (which gives the original name) are kept. Names are saved with the
  view and cleared by Reset view.
  Groups are purple, merged processes blue, direct emissions and others
  grey italic.
  **Every kind of row is explored the same way**: ▸ expands it in place
  (a process into its inputs, direct emissions into flows, a group into its
  members, a merged process into the union of its inputs, others into the
  rows it bundles — which then also replace it in the chart); click selects;
  right-click or Space opens its **pop-up** — the **(i)** shown on the hovered
  row, or **i**, opens the pop-up of its *exchange* (comment and uncertainty)
  — whatever the kind: the activity pop-up below for a process or direct
  emissions, the flow pop-up for an elementary flow, and a **members pop-up**
  for a bundle (others, group, merged; nothing opens on hover or on a plain
  click, which only selects). Members pop-ups follow the view's naming choice;
  clicking or right-clicking a member opens its own pop-up (← comes back).
  - *Others*: what the row contains and why (below cut-off / moved);
    ticking a member **restores** it: shown again in the tree and the chart
    ("restored ×" chip to undo; *Show all* / *Hide restored*) — also how to show
    exceptions to the cut-off. See [grouping.md](grouping.md).
  - *Group* and *merged*: each member's amount and impact (current method),
    absolute and as a share of the row (amount shares only when every member
    delivers the same product), with Merge / Ungroup / Unmerge.
  Decision: bundles first had a panel docked under the tree (opened on
  hover, then on click); it moved into the pop-up so that every row shows its
  details the same way, with the same back navigation.
  **Reset view** goes back to the plain tree: collapsed, no pins or moved
  rows, no groups (every grouping rule, quick groups included, and *Group by
  product*), no merges and no custom names. Changing the cut-off clears pins and moved rows.
  Both show a notice saying what they removed; Ctrl/⌘+Z brings it back.
- **Keyboard** (the tree clicked last; not while typing in a field). The
  **Shortcuts** button of the top bar lists these keys, those of the pop-ups
  and those of the search fields (`SHORTCUTS` in `app.js`); the panels
  themselves carry no key hints (what a tab of the side panel shows is in a popup on its
  name). Four keys work anywhere (not while typing, in a
  pop-up or in a dialog): **v** goes to View mode, and in View mode
  shows or hides the view options; **e** goes to Edit mode (a Brightway-built database;
  once in Edit mode, **e** is the tree's, below); **c** goes to Compare mode; **s** goes to
  the search of the top bar (as Ctrl/⌘ + K). The key of the
  exchange pop-up is **i** (info), which was **c** before.

  | Key | Action |
  |---|---|
  | ↓ / ↑ | select the row below / above (from the last position if nothing is selected) |
  | Shift + ↓ / ↑ | extend the selection over siblings (what Group and Merge act on) |
  | → | expand; if already expanded, go to the first child |
  | ← | collapse; if already collapsed, go to the parent |
  | ← → ↓ ↑ in the tree view with the root on top | the arrows are turned with the layout: ← → go across the processes (siblings), ↓ expands (then goes to the first child), ↑ collapses (then goes to the parent) |
  | Space | open (or close) the row's pop-up (members pop-up for a bundle) |
  | i | open (or close) the pop-up of the row's exchange (info): comment and uncertainty |
  | o | move the selection to others (inside an expanded others row: restore it) |
  | m | merge the selection, or unmerge a selected merged row |
  | g | one group: ungroup it (a "group by product" becomes an exception, put back by its regroup chip or by g on one of its processes); one process: put back its ungrouped product group, else group its same-named siblings under the product name, else nothing; several rows: group them (named after the product when they all deliver one, else "group N") |
  | r | rename the selected row (display name only) |
  | Enter | make the selected process the functional unit of the tab |
  | e | in Edit mode: open the form of the selected process (outside Edit mode: switch to it, see below) |
  | t / w | open a new tab like this one / close this tab |
  | Ctrl/⌘ + Enter | open the selected process as the functional unit of a new tab |
  | Ctrl/⌘ + Z | undo the last change to the tree |
  | Esc | clear the selection |
  | Backspace | in a pop-up: back to the previous one |

  After g / m the new group or merged row is selected (the same key undoes
  it); after o, unmerge or ungroup, the row now at the same position is, so
  the keys can be chained. The selected row is kept in view.
- **Left — tree graph** ("Tree" toggle next to "List"): the same visible
  tree drawn as nodes and edges, sharing all state with the list (expansion,
  selection, groups — the chart follows either). Root on the left (→) or on
  top (↓, SimaPro-like). Each box shows name, location, amount and a share
  bar; edge thickness ∝ share of the total, so the main supply paths stand
  out. ± handle or double-click to expand, Ctrl/⌘-click to select, drag to
  pan, wheel to zoom, "Fit" for the whole tree. See below for the layout
  choice.
  **Cycles** (toggle in the graph controls) draws the supply chain as a
  graph rather than a tree: a process used in several places is one node
  with an edge from each place (edges between the same two nodes are
  combined), and a loop — a process reached again inside its own supply
  chain, such as an electricity market through the ENTSO-E market group — is a
  dashed edge back to the node already shown. Direct emissions, flows,
  groups and others hang under their process's node. A node shown in
  several places reads "×N": its amount is the sum of its places, its
  impact counts a place inside another one's supply chain only once (the
  outer place's cumulative impact already includes it), and its share is of
  the total unless it has a single parent. ± expands it at its first place
  and collapses it everywhere; click, right-click and the row actions act on
  its first place.
- **Left — Sankey** ("Sankey" next to "List" and "Tree"): the same visible
  tree as a flowchart. Each row is a bar as tall as its |impact| (the root
  480 px at 100 %), joined to its parent by a band of the same thickness, so
  the bands leaving a process add up to its bar and the main supply paths
  are the widest. Bars and bands take the colour of the list's colour mode (see the view bar), else
  groups are purple, merged processes blue, direct emissions and others grey; the label beside
  each bar gives the name, qualifier, amount and share (of the total or of
  the parent, as in the other views). ▸ or double-click expands, click and
  Ctrl/⌘-click select, right-click opens the pop-up, drag pans, wheel zooms;
  row actions are in the selection bar and the pop-ups. Always a tree drawn
  left to right: the Cycles and orientation controls are hidden here.
- **View bar** (under the toolbar): *Group by product*, *Split by flow type* (below each
  row, the rows in the sections of the form of a new process, as in the flow type colours, with a
  heading each: inputs from technosphere, inputs from nature, emissions, direct emissions, waste
  to treatment, others; the order inside a section is kept), *Hide others* (the others
  rows, and their boxes in the tree view and the Sankey, are left out; the chart keeps them), *Full / Short /
  Custom* names, applied
  to the list, the tree view, the others panel and the chart; see
  [charts.md](charts.md); and the colours: *No colours*, *Group colours* or *Flow type colours*.
  **Columns of the list**: right-click the column headers to choose them (the process is
  always there; remembered, saved with the view). *Amount*, *Impact* and *Share* are on by
  default; *Exchange data* columns, off by default, show what the exchange supplying
  each process records, in the dataset of the process above it (several datasets for a
  merged one; the whole text in the tooltip or with **i**): *Comment*, *Uncertainty*
  (distribution and parameters), *Pedigree* (the five scores), *Formula* and *Variable
  name*. `/api/rows` sends the comment as the row's `comment` and the rest as its `exchanges`.

  **Amounts** never show more than three digits, in the list, tree view, pop-ups and chart
  tables: from 0.01 to 1000 as `0.0101`, `1.00`, `123`; below and above in scientific notation
  (`5.29e-3`, `1.23e+3`), `fmtAmount` in `app.js`. Impacts keep `fmt`. The input of an edited
  amount always has the full value.

  ![View settings](img/view-settings.png)
- **Right-click a process** (list or tree view) or **Space**: the activity
  pop-up, with panels *Exchanges*, *Used by*, *Alternatives*, *Description*
  and *Metadata*. **i**, or the **(i)** of a row, opens instead the **exchange
  pop-up**: only the amount and impact in this tree, the path from the root,
  and the comment, uncertainty and pedigree of the parent's input of this
  product. Once open, a pop-up has the keyboard: ←/→ switch panel, ↓ enters
  the panel. *Exchanges* is the main view's left panel itself (see below); the
  panels of processes *Used by* and *Alternatives* are lists like the main one
  (`ProcessRow`): ↑/↓ move between the rows (↑ on the first goes back to the panels),
  → expands a row (or goes to its first child), ← collapses it (or goes to the row it
  hangs from; where a row does not use ←/→ they switch panel), Space opens the
  process's pop-up, **i** the pop-up of the exchange the row stands for, and **Enter**
  makes it the functional unit of the tab (the pop-up closes), **Ctrl/⌘+Enter** the one of a new tab. Expanding an
  exchange or an alternative lists its own inputs; expanding a user, the activities
  using it. Esc or Space with no row selected closes the pop-up; Backspace goes back.
  **Used by** (the activities consuming its product — or, for a waste
  treatment, generating the waste — with the amount per unit of their
  product and, largest first, the amount each takes in this functional
  unit's supply chain and its share; *go to*
  to open the tree at it), the ecoinvent description, included activities, time period, classifications,
  authors and file; actions "Use as functional unit" and "Open in new tab".
  **Alternatives** lists the other activities producing the same product
  with their cumulative impact per unit, lowest first (rows like the others;
  below an expanded one, its inputs with their impact and share of it).
  Descriptions are stored in the cached model (`details.pkl`, 77 MB for
  ecoinvent, loaded on first use).
  **Exchanges** is an instance of the main view's left panel (header, list, bars
  and the keys of the list, ↑ on its top row unselects it so that ←/→ switch panel again), rooted at the pop-up's process (amount 1, its
  inputs, direct emissions and waste below it, as in the tree) and with a state of
  its own: expanding, selection, cut-off (starting at the 500 largest rows, so that
  every input is listed), method, groups, merges, pins and names are not shared with
  the tab, nor with another pop-up, and are kept while the pop-up stays on the
  process. Its defaults differ from the tab's: full names, *Split by flow type* on, every input
  listed (cut-off); its options (with *View options*) are the header, Collapse, Reset view, *Group
  by product* and *Split by flow type* only. Space, **i** and the **(i)** of a
  process or flow row replace the pop-up with that process's, that exchange's or
  that flow's pop-up (←/Backspace comes back, the explorer then starts afresh);
  **Enter** makes the process the functional unit of the tab, **Ctrl/⌘+Enter** of a new tab. *Reference product exchange* (production
  volume, price, carbon and water content, dry mass, …) is in *Description*; comments,
  uncertainty (distribution, σ with and without pedigree), pedigree scores, formula
  and variable name of an exchange are in its exchange pop-up. Exchanges are stored in the cached
  model (`exchanges.sqlite`, 105 MB for ecoinvent 3.9.1, read one activity
  at a time); a model built before this has none — rebuild it to see them.
  The activity pop-up opens on *Exchanges*; *Description* (with the reference product exchange) and
  *Metadata* follow; empty panels are not shown. The exchange pop-up has no
  panels or actions. Rows followed inside a pop-up (a supplier, a user, an
  exchange, a flow's emitter) replace it and are kept in a
  history: **←** next to × (or **Backspace**) goes back; opening a pop-up
  from outside, or closing it, starts a new history.
- **Pop-up windows** (all kinds). Drag the header to move a pop-up and any
  edge or corner to resize it. The **pin** button keeps it open, so that several
  can be side by side: a pinned pop-up ignores clicks outside and opening
  another pop-up, and Esc / Backspace act on it only while the pointer is on
  it; the one that is not pinned is replaced by the next you open. A pop-up
  clicked comes to the front. Dragging an activity's pop-up onto a pane's
  tab list opens that activity as a new tab there; dragging it to within
  ~56 px of a pane's edge opens it in a new pane on that side (a hint shows
  where it would go before you drop). Pop-ups belong to their tab and are
  not kept in saved views.
- **Right-click an elementary flow** (a flow row in the tree, in an others or
  group panel, or a substance / flow of the *Flow contribution* tab): pop-up
  with tabs *Exchange* (for each process emitting it there — the emitting
  process, or every member of a merged one — the exchange as recorded in its
  dataset: amount, comment, uncertainty, pedigree, formula), *Characterisation*
  (its factor in every method of the model that has one, the current method
  highlighted, filterable) and *Metadata* (name, compartment, unit, database
  id). For a substance spread over several compartments, chips switch between
  them. API: `/api/flow/{index}?method=&activities=`.
- **Side panel — tabs** (what each shows is explained in a popup on its name, not in the panel):
  - *Process contribution* (the former contribution chart): current method
    bars, or several methods as 100 % stacked bars; CSV export. See
    [charts.md](charts.md).
  - *Flow contribution*: the score split by elementary flow: the whole
    inventory of the functional unit (B·s) times the current method's
    characterisation factors, so it sums to the total. *By substance*
    sums the compartments of a substance (one row per name and unit), *By
    flow* keeps them apart. Bar chart of the 25 largest |impacts| (the rest
    as *others*) and a table with amount, impact and share; click a
    substance or flow (bar or row) to list the processes emitting it in the
    supply chain, with their own amount and impact. As in *Whole supply
    chain*, right-click (or double-click) one to go to it in the tree or
    open its details, or *Exchange…* for its exchange of that flow.
    Right-click a substance or flow for its pop-up (below). API:
    `/api/flows`, `/api/flows/contributors` (`analysis.flow_contribution`,
    `flow_contributors`).
  - *Group rules*: active rules (reorder, **edit** — the form below is filled in with
    the rule, with *Save rule* and *Cancel*; a group made from selected rows or a nested
    query can only be renamed —, remove; the eye switches a rule off and on without losing
    it, crossed out while it is off; what rules do is in the **(i)** next to *Active rules*;
    the *merge* button of a rule is gone, a group is merged from its row), quick groups (transport,
    electricity, heat & fuels, infrastructure, waste treatment, pesticides
    (CPC 3466 and "pesticide, unspecified"), fertilisers (CPC 3461–3465:
    mineral, organic and organo-mineral fertilisers, manure), agricultural
    machinery (tractors, harvesters, agricultural machinery and trailers, and
    the field operations using them: tillage, sowing, harvesting, spraying,
    fertilising, mowing, … — by product name, as ecoinvent names them), by
    location / activity type / ISIC), and the shared query builder — the same component as the search
    filters, so a filter reads and behaves the same in both places — with a
    live "matches N activities, e.g. …" preview; scope everywhere or under
    the selected row. *Group elementary flows* makes the same kinds of rules
    on flows (substance, compartment, subcompartment, unit), which group the
    flows under direct emissions and split what the process groups leave in
    *Group contributions*; "product / waste" (`flow_type`) is a field of
    processes, not of flows. Each rule has a colour swatch: click it to pick
    another colour (right-click resets it). The groups of a rule carry it in
    the tree, graph and Sankey, and the bars of the *Process contribution*,
    *Flow contribution* (a substance in a named flow group) and *Group
    contributions* charts; *No colours* in the view bar switches all colours off. *Flow type
    colours* colour the rows (list, tree view, Sankey and charts) by the kind of exchange instead:
    product (the root), technosphere input, waste (output to treatment),
    resource (input from nature), emission (output to nature) and direct
    emissions rows; *Group colours* goes back to the colours of groups.
  - *Group contributions*: global grouping with the named groups, direct
    (the default) or first-match attribution (see [grouping.md](grouping.md)),
    groups sorted by decreasing impact with *other* last.
    Named rules on elementary flows are **flow groups** ("flows" chip): process
    groups come first and keep every flow they reach first; flow groups split
    the rest (emissions of processes outside the groups);
    add one with *Add as a group* on a substance (or a flow) in the *Flow
    contribution* tab. Clicking a group
    lists the processes it is made of; click one for its pop-up, right-click
    it (or double-click) for *Go to in tree* (opens and highlights it along its
    largest supply path) or *Details*. Each group has the colour of its rule
    in *Group rules* (a swatch next to the rule), and its rows carry the same
    colour in the tree.

## Tree graph layout

Decision: own tidy-tree layout and SVG rendering instead of a graph library
(ECharts `tree` series, d3-hierarchy, …). The layout is ~40 lines (a parent
is centred on its children, depth sets the other axis), and owning the SVG gives full control over boxes with impact
bars, per-edge widths, selection and grouping interactions — which the
ECharts tree series does not offer. Opening a functional unit zooms to a
readable 60 % centred on the root rather than fitting a large tree into an
unreadable thumbnail; "Fit" shows everything. The view does not re-fit on
each expansion, so the user keeps their place.

The layout is compact: a subtree is pushed along the slot axis only as far as
needed not to meet a node of the subtrees before it at the same depth (a
contour per depth, as in Reingold–Tilford). So several processes can share a
row in the horizontal display (a column in the vertical one) when they are at
different depths, while a depth never holds two nodes in one slot. Decision:
compactness over a strict one-node-per-row reading; edges stay readable
because, at each depth, the order of the nodes along the slot axis is the
order of their subtrees, so edges between two adjacent depths never cross.

In cycles mode the same tidy layout is applied to a breadth-first spanning
tree of the graph (each node at its shortest distance from the root, under
the first node reaching it); the other edges are drawn on top, those going
to a node at the same depth or nearer the root (loops) curving round from
the far side of the source box to the near side of the target. Decision:
no force-directed or layered (Sugiyama) layout, so that switching the mode
keeps the familiar left-to-right reading of the tree.

The Sankey has its own layout in the same SVG shell (pan, zoom, Fit), not
the ECharts sankey series: ECharts lays out nodes itself, which would move
every bar on each expansion, and has no expand, selection or pop-up
interactions. A column per depth; a subtree takes the height of its bar, of
its children stacked, or of two label lines, whichever is largest, with the
bar centred on it; bands leave the parent stacked in row order. A row too
small to see keeps a 1.5 px bar.

Display choice (list / tree / Sankey), orientation and cycles mode are per-browser
preferences (`localStorage`).

## State

The client owns the view state (`root`, `amount`, `method`, `cutoff`,
`expanded`, `rules`, pins, moved rows, merges) and posts it to `/api/rows` on
every change (~70 ms on ecoinvent). Each tab's state, the pane layout and
the focused pane are kept in `localStorage` per browser (convenience only),
so tabs and splits come back on reload; a view saved before tabs existed
opens as the first tab. A tab moved to another pane is rebuilt from its
saved state (selection and undo are not kept).

**Save ▾** (in the toolbar, hidden with the view options) downloads the view as JSON; the arrow menu offers *Load view…*
and *Copy view as JSON*. Files are `version` 2: `cutoff` is a fraction, as in
`/api/rows` (0.05 for the 5 % shown in the page); version 1 files stored the
percentage and still load. The `labels` of a file (`p:<activity>`, `f:<flow>`,
`r:<row id>`) can be sent to `/api/rows` too: the rows they name come back with
the name as `custom`. `GET /api/lookup?name=&location=` says whether an
activity exists, and lists the same name in other places or the closest names
(`matches`, `same_name`, `similar`). Saved files record the model (title, size), the
functional unit's name and location, method names and the naming settings,
besides positions. Node ids are positions in the model, so on load, if the
functional unit is not at the same position (view saved from another model
or database version), it is found by name and location and only the
position-free parts are applied (functional unit, amount, method, cut-off,
rules scoped everywhere, names); expansions, pins, moved rows and merges are
dropped, and the notice says so. Loading can be undone with Ctrl/⌘+Z.

**Undo** (Ctrl/⌘+Z): each change of the tree — groups,
ungroups, merges, pins, moved rows, names, cut-off, amount, method, display
settings and a functional unit change — is a step, and undo goes back one step
at a time. Expanding and collapsing are not steps of their own: they are kept
with the step they fall in. A tab's first functional unit has nothing to go
back to. Steps are kept per tab and not saved.

Expansion semantics: a group that hides an expanded member opens by itself,
so adding a group-by rule does not hide the branch being explored; collapsing
it on purpose is remembered as `!<group id>` in `expanded`.

## Verification

Screenshots are taken headless with Playwright (`chromium-headless-shell`).
The container lacks `libnss3`/`libnspr4`; they are installed from
conda-forge in `~/.cache/lca_viewer_browser_libs` and passed with
`LD_LIBRARY_PATH` (no root needed).
