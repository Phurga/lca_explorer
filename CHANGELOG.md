# Changelog

All notable changes to LCA Explorer. Versions follow [semantic versioning](https://semver.org/):
a patch (1.0.x) fixes bugs, a minor (1.x.0) adds features without breaking your projects, a major (x.0.0)
may need you to do something when you update. Each version is a git tag; the latest release is the stable
version, `main` can be ahead of it with work in progress.

## Unreleased

Changes on `dev`, not released yet.

- **View options on each process**: the sliders icon of a process in the left panel's *Processes* panel opens the
  view options of that tab in a pop-up (*Display*, *Cut-off*, *Names*, *Organise by*, *Group by*, *Color by*, *Rows*,
  with *Reset* and *Save ▾*); the tab is shown in its window first. Key **v** in View mode opens those of the tab in
  focus. Flow tabs have none.
- **Side panel tabs renamed**: *Custom* (the chart of the tree's rows; it was *Explore view*, formerly *Custom view*,
  and keeps only that chart), *Process*, *Flow*, *Uncharacterised* and *Rules* (they were *Process contribution*,
  *Flow contribution* and *Rules contributions*). When the panel is too narrow for the names, the tab bar shows an icon
  per panel instead, its name in the tooltip.
- **Side panel and Rules with a flow or model tab in focus**: they showed nothing; they now show those of the process
  tab last in focus, with a muted line *Showing ‹process›, the last process in focus*, or, with no process tab open, a
  note that they follow a process tab.
- **Fixed**: dragging the side panel wider (above all with the left panel open) made the page wider than the window
  and pushed the panel past its right edge. The two panels are now fitted to the window, leaving the workspace
  360 px, when dragged, when the other panel opens and when the window is resized.
- **Process cards: Explore and Exchanges.** The first panel of a process tab (the tree) and of a process pop-up (its
  explorer) is now called *Explore*. A new *Exchanges* panel, second, lists the exchanges as the dataset records them,
  in the sections of the process form (products, inputs from technosphere, inputs from nature, emissions, waste to
  treatment, avoided products) with their location or compartment, amount, unit and a comment mark (the comment in
  its tooltip). A row's name or right-click opens its process or flow pop-up, its (i) the exchange pop-up.
- **Edit mode starts from a process card**: the *Edit* button of the *Exchanges* panel (a Brightway-built database)
  enters Edit mode; there it reads *Done editing*, with *Edit process…* (the process form), and amounts change in
  place (double-click, staged). The *Edit* button of the top bar is gone: View leaves Edit mode, and the `e` key and
  the staged-changes warning still enter it.
- **Information panel**: a *Unit* row with what it measures ("kg · mass", "m2·a · land occupation") and the location
  with its name ("FR · France", "RER · Europe"). API: `/api/activity/{index}` gives `location_name`, and each input
  of `/api/activity/{index}/exchanges` whether it is `waste`.
- **Status bar**: it names the **focused card**, the object of the tab in focus (*Process · name (location)*, *Flow ·
  name (compartment)*, *Model · method*), and a click opens its pop-up. The **Source** link of the top bar is now an
  icon (`</>`) at its right. Its left-panel icon is gone: the method and the rule set still open the panel, and so
  do the edge of the page and Ctrl/⌘ + ←.
- **Storage** moved from the top bar into the Projects dialog, as an icon next to *+ New project*.
- **No Shut down button**: closing the app's last tab stops it, as before (the browser asks first). The
  `/api/shutdown` route is gone.
- **Model tabs**: a model card (the model pop-up: a model's elementary flows, their factors and emitters) opens as a
  tab with *Open in new tab* in its ⋯ menu, Ctrl/⌘+Enter, or by dragging it by its header to a tab list or the edge
  of a pane, from a process tab, a flow tab or the *Methods* panel. The tab keeps its *Organize by* and *Compare with*
  across reloads, has pop-ups of its own (a flow of its rows, then the flow's processes…), and is listed in the
  *Processes* panel with the model icon; it is not compared. The UI calls these cards *model cards*.
- **Methods library** (in *Projects*): impact methods of your own, kept by the app (`data/methods`) rather than by a
  Brightway project. Copy a method of a project to edit it, make an empty one, or import a CSV file of factors (flow
  name, compartment, subcompartment, unit, factor) matched to the elementary flows of a project, with the rows of no
  flow listed. The method editor changes, removes and adds factors (searching the project's flows), renames,
  duplicates, deletes and exports a method as CSV. Building a database (and *Add impact methods*) offers the
  library's methods next to the project's, under the source *library*; a database built with a method edited since
  is shown as to rebuild. API under `/api/library/methods…`; see `docs/methods-library.md`.
- The flow and process search of the dataset form (`NodePicker`) searches the words typed again, not only the
  filters.

## 1.6.0 - 2026-11-27

Large databases: one built with hundreds of thousands of processes opens, and every database is solved faster, with
one cache per project. Impact methods get their coverage and a view of their own, elementary flows and models their
pop-ups and tabs, and the first start is made simple.

- **One cache per Brightway project**: each database is built once, into a *segment* of its project's cache
  (databases that use each other are built together), and opening a database puts its segment and those it uses
  together. A foreground database on ecoinvent no longer stores and solves ecoinvent again: the AESA project takes
  1.3 GB instead of 3 GB. A database changed is built again alone, and the results of the databases that use
  it solved again (no new reading from Brightway). The results (`G`) are solved in the build, not at the first open:
  opening ecoinvent 3.12 takes 2 s and 0.4 GB instead of 6 s and 1.3 GB, and the build's peak memory is halved (a
  sixth on the AESA project). A foreground database rebuilt on a built background takes seconds. The caches of the former layout are listed as *obsolete* in *Storage*: delete them
  there, their databases are built again when next opened.
- **Impact methods: one list per project**, for all its databases: methods chosen at a build, or added to an open
  database, are computed for each of them.
- **Storage** lists each project's cache with its segments: *Rebuild*, *Drop results*, *Delete* (with the databases
  that use it).
- **Less memory when open**: the direct impacts per process (`U`, as large as the results) are no longer kept, but
  computed for the process shown or the methods an analysis uses.

- **Impact methods chosen before a build**: *Build and open…* first lists the impact methods of the project, by
  family, those of the previous build (or the family of the default method) ticked. Only their impacts are
  computed and kept: the fewer, the faster and the lighter. A rebuild keeps them.
- **Add impact methods** to an open database, in its *Methods* panel: their factors are read from the project and
  only their impacts computed (seconds), without building the database again.
- **New solver**: the processes are ordered suppliers first, so that only the loops of the technosphere (the
  processes that supply each other) need a full factorisation, and the rest is solved step by step. One
  factorisation serves the tree and the contributions alike. On a database of 300 000 processes on top of
  ecoinvent it takes 2 s, where the previous solver ran out of time and memory; without Pardiso (macOS on
  Apple silicon) ecoinvent is factorised in 4 s instead of 2 minutes.
- **Fixed**: building a database of more than about 30 000 processes failed with "too many SQL variables".
- **Fixed**: when the browser closed the app's last tab without saying so (it quit, crashed, or closed the tab without
  unloading it), the app kept running for about 2½ minutes. A tab on screen now counts as closed after 30 s of
  silence (a hidden one, which browsers slow down, still after 2½ minutes), so the app stops within about 40 s; its
  pings come from a worker, so a dialog or a long calculation on the page no longer holds them up, and one ping that
  fails (on waking up) no longer silences the tab for good.
- Impact methods are read from the project directly when a database is built: the same factors as before, faster.
- **Process information in one place**: the *Description* and *Metadata* tabs of a process (pop-up or tab) are one
  *Information* tab, which also shows what the process is: its product and unit, activity, location, activity type
  (market, production…), ISIC and CPC classes and code, then its description.
- **Characterisation panel** in the process pop-up (and tab), after *Exchanges*: the process's total (with its supply
  chain) and direct impact per unit of its product in the main method and in every method shown together, with the
  direct share; a method opens its model pop-up. The method names there and in a flow's *Characterisation* follow
  the *Name display* of the *Methods* panel, unit included. API: `GET /api/activity/{index}/characterisation`.
- **Fixed**: the impact methods that come with an ecoinvent release (3.10 and later: "ecoinvent-3.10.1 | EF v3.1 |
  climate change | …") were named and grouped by the release instead of their family, and a database built with them
  showed and offered all of the release's methods at first instead of the family of the default one. The release is
  now a *Source* part of *Name display* (off by default) and the token `{source}`.
- **Fixed**: at a cut-off of 0 the processes without impact for the method were all shown, with no *others* row;
  they now go to *others* ("no impact"), as flows did. The cut-off can no longer be set under 0, and a saved view with one
  that is not a number opens at 0.
- **First start made simple**: the tree has the room, with its *Amount*, *Impact* and *Share* columns (*Comment* and the
  others in the columns menu) and the direct emissions organised by compartment; the left panel (processes, methods,
  rules) is closed and the side panel opens on the chart, its *Options* folded. The panels' widths follow the window
  (the side panel leaves the tree at least 720 px) until you drag them. Only what you change is remembered, so these
  defaults reach you where you never chose otherwise; *Default settings* (save menu) brings them all back, panels included.
- **Organise by**: headings in the list, without grouping anything: the processes by *Type* (what *Split by type* did),
  *Location* or *Activity type*, and the direct emissions by *Compartment* (the default), *Subcompartment* or *Type*.
  *Hide others* and *Collapse singletons* are under *Rows*.
- **Custom view** in one order: *Display*, *Cut-off*, *Names*, *Organise by*, *Group by*, *Color by*, then *Rows*.
- **Color by** replaces *Colours*: a switch like *Group by* (*Rules*, *Type*, and *By…* location, activity type or
  compartment), with the colours in use under it, each a swatch to click for another colour. The *Settings* pop-up
  (*Colours…*) has one palette for every mode and lists the rules, the exchange types and the values given a colour.
- **Fixed**: *Group by* let several groupings be chosen, but only the first applied (it leaves nothing for the next). It
  now takes one for the processes (*Product*, or a field of *By…*) and one for the flows (*Substance*, *Compartment*,
  *Subcompartment*), choosing one replaces the other, and *By…* names what it applies.
- **Coverage of an impact method**: does a method characterise your model well, and which flows does it miss?
  - A new **Uncharacterised** tab in the side panel: the flows of a process's inventory that the active method
    has no factor for, in a table like the tree's, one foldable row per unit named by what it measures
    (*Mass (kg)*, *Radioactivity (kBq)*…), largest amount first, with a search by name and compartment.
    **Compare with** one of the methods ticked in the *Methods* panel (e.g. the one a new method replaces) adds
    its factors, and puts the flows it characterises, likely gaps, first.
  - Models record, when they are built, the factors of each method on flows that are not in the model (which
    the calculation drops), for a method's coverage over the whole model (API only for now): **rebuild** a
    model to record them (*Projects*, *Options ▾* → *Rebuild and open*, now offered for a model that is up to
    date too).

- **Method selection**: one box per method instead of a tick and a round mark. Click it in turn: ✓ shown
  together, ★ the main method (the one before stays ✓), then unticked.
- **Impact category view** (elementary flows, phase 4 of the roadmap): the list icon of a method in the *Methods*
  panel opens a pop-up of the flows of the model that it characterises, in the whole database: factor, factor relative
  to the largest of the same unit and the processes emitting each flow directly. Organize by substance, compartment or
  subcompartment (foldable sections), filter, export as CSV. **Compare with** another method (the same category of other families first) lines up the
  factors of both, their ratio and the flows only one of them characterises. Right-click a flow for its pop-up.
  Under the table, the factors of the methods on flows that are not in the model (recorded at build), with what
  the model has under that name.
  API: `POST /api/category`.
- **Model pop-up**: the impact category pop-up is now called the pop-up of a *model* (an impact category of a method,
  e.g. the climate change of EF v3.1). Open a model from the *Characterisation* panel of a flow pop-up, and from it
  a flow, then the processes emitting it: process → flow → model → flow → process in one pop-up, ← going back.
- **Organize by location** in the model pop-up: the flows by their location (regionalised flows), those without one
  under *no location*. A model built before the flows kept their location says *rebuild to see locations*.
- **One list for every pop-up**: *Used by*, *Alternatives*, *Characterisation*, *Emitted by*, *Same substance* and the
  model's flows look and work alike: filter box above, a header that sorts (right-click: filter by column), rows of one
  height whose name opens their pop-up, numbers with their unit in a column of its own, shares as bars.
- **Right-click opens the pop-up of the row's process or flow**, everywhere (the tree's flow rows offered a menu, and
  the emitters of a flow opened their exchange): the exchange (comment, uncertainty, pedigree) is the row's (i).
- **Flow tabs** have pop-ups of their own: a process, an exchange or a model opened from a flow tab opens its pop-up
  there (a process clicked in a flow tab used to open a new tab).
- **Fixed**: the model pop-up did not open from the *Methods* panel while a flow tab had the focus.
- **Fixed**: the *Comment* column (and the other exchange columns) stayed empty on the flows of the direct emissions:
  a flow row now shows the comment of its exchange in the dataset of the process emitting it.

## 1.5.1 - 2026-11-24

LCA Explorer becomes free and open-source software, under the GNU AGPL 3.0 or later.

- **New licence: GNU AGPL 3.0 or later** instead of the PolyForm Strict License. LCA Explorer is now free and
  open-source software: anyone may use, change and share it, for any purpose, as long as what they share (or run
  for others over a network) stays under the AGPL with its source. Each source file carries its licence
  (`SPDX-License-Identifier: AGPL-3.0-or-later`), and a **Source** link in the top bar leads to the code.

## 1.5.0 - 2026-11-06

Avoided products set apart, rules on the exchanges themselves (their type and amount), and projects that open at once.

- **Avoided products** in *Split by flow type*: a negative input of a product (not a waste), as the substitutions of
  consequential databases, has a section of its own instead of being among the inputs from technosphere, and a
  colour of its own in the *Flow type* colours.
- **Rules on exchanges**: a rule can test the exchange that brings a row in, its *exchange type* (technosphere,
  avoided, waste; resource or emission for a flow) and its *exchange amount* (as recorded in the dataset of the
  process above, with <, ≤, >, ≥), alone or with conditions on the process; *Group by* offers the exchange type.
  These rules group and colour the tree, and in *Rules contributions* (and Compare by groups) they take what goes
  through the exchanges they match: under first match the whole supply chain of what they bring in (an avoided
  product with everything it avoids), under own emissions the emissions of that share.
- **Projects open at once**: the projects and databases are read once and kept (the node counts of each project
  too, in `data/cache`, counted again only when the project is written); *↻ Refresh* reads them again, for
  changes made outside the app. A job that changes a project lists them again.
- **Feedback on every job**: a button starting a job shows it under way at once, under the button, with what it is
  doing (MB downloaded, impact methods installed) and for how long; a greyed-out button says why.
- **Lock icon** on each database of the Projects page: shown when the database is locked (a click unlocks it), on
  the row's hover otherwise (a click locks it); a greyed-out database says why it cannot be opened.
- Fixed: *Add database → ecoinvent* stayed greyed out in a project without a biosphere.
- Fixed: *Add database → Brightway repository* seemed to do nothing: the server read every project before starting
  the job (up to 30 s), and the download said nothing until done.
- Files uploaded for an import that failed or was abandoned are deleted when the app starts.

## 1.4.0 - 2026-11-03

Rules on the emissions of some processes, colours that follow the rules everywhere, filters and exports on every list, and a shorter tree with *Collapse singletons*.

- **Collapse singletons** (Custom view, *Organize*): a process made of only one process (a market of one supplier, a
  chain of them) shows as the last process of the chain, "via" those above it, with the same impacts.
- **Rules on emissions of some processes**: a rule on elementary flows can have an *Owning process* query (e.g. the
  N2O only of processes of CPC section 1); it applies in the tree, *Rules contributions* and Compare.
- Fixed: *Colours: Rules* only coloured the rows inside a group, so nothing was coloured with *Group by rules* off (the
  default), and a process a rule matches but that has no sibling to group with stayed grey. A row a rule matches now takes
  its colour, grouped or not, and what is below it inherits it.
- Fixed: the groups of *Group by* product, substance, compartment and subcompartment were coloured with colours that no rule
  has (and, with a palette, with the colour of another rule). Only the groups a rule makes, and rows a rule matches,
  have a rule's colour.
- **Group by**: *Rules*, *Product*, *Substance* and a **By…** menu (location, activity type, ISIC division, CPC section,
  compartment, subcompartment…) replace the *Compartment* and *Subcompartment* buttons and the quick groups *By location*,
  *By activity type*, *By ISIC division*.
- **First match**: with this attribution rule the rules stop grouping below the first row a rule takes (the supply
  chain below it is the rule's); with *Direct* they group all the way down, as before.
- **Custom view**: the single indicator chart has a legend of its colours (the rules, or the flow types), in the PNG too;
  a flow in it no longer says "direct: <process>" unless the rows are split by type; *Reset* is an icon next to *Save*;
  *Split by type* has no heading for the others rows.
- **Process tab**: ↑ on the top row of the tree goes to the panel selection (Process, Used by…), ↓ comes back.
- **Methods**: the legend of the selection is the (i) of the *Method selection* header; *Name in results* is *Name display*, without
  the symbols option (removed).
- **Rules**: the palette (*Colours*) is under the attribution rule, above *Active rules*; *Fertilisers* also takes the packaging for fertilisers.
- **Projects**: rename and copy a project (its menu is now an icon of three dots); the tooltips are above the window.
- **Pages wait with a moving mark**; an (i) tells on hover, a (?) acts when clicked.
- **Filters of the lists** (process and flow contribution, Compare): the *Top* field names its type: click it to choose
  *Top*, *Cut-off* (rows under a share of the total go to others) or *Cumulative* (the largest rows until a share).
- **Process contribution**: a click on a bar or a row chooses it in both (the others fade, the row is shown).
- **Flow contribution**: its own *Substance / Compartment / Subcompartment* buttons; rows no longer coloured by rules.
- The chart legend no longer thins the bars; the Methods list fills the pane; the arrows of the folds are bigger and as
  strong as their header; a tooltip starts under the start of its header.
- **Projects**: the (i) of a form (add a database, rename, copy…) is next to its title, not floating under the fields.
- **Compare**: the *Activity* breakdown is *Each content*: the own emissions of each product and each of its inputs
  with their supply chain, the first tier only (it used to list every process of the supply chain by its own emissions).
- **Export**: one icon in each panel: the chart as a PNG (legend included) and the table as a CSV file; the chart's
  own toolbox is gone. The several-indicator chart has the legend of the single one, every key shown.
- **Process contribution**: a click on a row opens the elementary flows its contribution is made of.
- Rules tooltip: "Group processes by your own criteria, beyond how individual flow or process contribute."
- **Settings** (a palette icon in the status bar): the colours of the rules, a palette and a colour picked per rule, kept
  with the rule sets of the workspace, the same in every tab, chart and comparison (they were a setting of each tab's
  view, and Compare did not follow a change). The palette left the Rules panel.
- The filters of the lists (*Top*, *Cut-off*, *Cumulative*) are applied by the server as the cut-off of the tree: the
  shares are of all the rows, and a single row under the cut-off is shown as itself. The API's list endpoints take a
  `cut` (`{mode, value}`) instead of `limit`.
- Groups and bars take the colour of their rule, not of a rule found in their name (a rule named "Transport: road" was
  coloured as "Transport").
- The comparison of the tabs' trees groups by product as each tab does (it took the preference of the last tab).
- The analyses check what they are asked for: a negative process index (it was an activity from the end) or an
  unknown attribution rule (it was taken as first match) is refused.
- Removed: rules that merge their groups (no control made them any more), `/api/lookup`, `/api/direct` and
  `/api/brightway/dataset/delete` (datasets are deleted through the staged changes), and code nothing used.
- Chart axes drop labels that would overlap.
- README: use the latest release for a stable version; screenshots and wording follow the current interface.

## 1.3.0 - 2026-11-02

A process and its pop-up are one thing, the options sit in the side panel, and the rules and methods can be tuned.

- **Compare**: the number of bars (*Top*) is a field you type in, as in View mode.
- **View options**: headers *Group by* (rules, product, substance, compartment) and *Organize* (split by type,
  hide others), with one-word buttons.
- **Projects**: the last one opened first, its databases folded (a click shows them).
- **Processes panel**: drag the processes between windows, to a new window, or before another; close a window from
  its header. Drag a process from any row (explorer, panels, pop-ups) onto a window, a tab list or the panel to
  open it in a new tab. Closing a tab or a window can be undone (a notice, and *Reopen what I closed*).
- **Edit mode**: the pop-up no longer renames a process; the explanations moved into tooltips (*Stage*, *Products*,
  *Edit mode*) and the *used by* text is gone.
- **Attribution** (first match by default) is one choice for *Rules contributions* and Compare.
- **Compare**: the breakdown is *Rules*, *Flows* or *Processes* (the rows of each tab's *Current view*, or each
  process's *Activity content*), and the scale has its own header.
- **Rules panel**: its sections fold (rule set, quick groups, new rule), and a rule has an icon to edit it.
- **Rules panel**: *Active rules* first, then the attribution (a picture of *Direct* and *First match*
  side by side opens from its (i)), the rule set, quick groups and a new rule; a new rule is a named group
  from a query, applied everywhere.
- **Compare**: no method and rules header; the difference panel has no zoom option (the axis zooms by itself).
- **Charts**: the axes use the numbers' rule of the tree (scientific from 10 000 and below 0.001).
- **Side panel**: a first panel, *Custom view*, holds the view options (they left the pane above the status
  bar) and the chart of the tree's rows; *Process contribution* charts what its table lists; the process and flow
  panels have a **Top** field.
- **Method names**: *Name in results* has a *category* part and a *Custom* format (`{family} {category} {indicator}
  {unit}`, as the custom process names); the name applies to the titles, files, and the several-methods chart and matrix.
- **Defaults**: a first start shows a list with no cut-off, nothing grouped, full names, no colours and the comments;
  climate change is the main method, its family shown together, with five default rules (*Default settings* in the
  save menu brings them back). Loading a view file applies its settings, rules and methods.
- **Custom view**: one line per setting, folded when the chart needs the room, a save icon; no *Collapse* button.
- **Rules panel**: the *Attribution rule* at the top; *Active rules* folds; Esc leaves a change; right-click a rule to
  change it; a rule that exists comes as a copy (*transport 2*); the palette is *Custom* once a colour is picked, and
  *Add palette…* makes your own.
- **Flow contribution** follows *Group by* (*Substance*, *Compartment*, new *Subcompartment*).
- **Compare**: one breakdown switch (*None*, *Rules*, *Flows*, *Custom view*, *Activity*); the answers of the
  calculations are kept, so a panel opened again, or the Custom view breakdown of what a tab shows, does not calculate again.
- Shorter hover texts on the settings; the tree's header is *Supply chain* and its status line gives the unit only;
  the Projects options are an icon.
- **Rule colours**: a palette to choose (*Colours* in the Rules panel); quick groups in use are filled with their colour.
- **Processes panel**: *Open history* at the top of the list replaces *Reopen what I closed*: pick any closed tab or window.
- **Tabs and pop-ups are one**: the first line of a tab shows the panels of the pop-up (Process, Used by, Alternatives,
  Description, Metadata), the tree being the first.
- **Method names**: *Name in results* (Methods panel) sets how a method appears: family, indicator, unit, and the unit
  with symbols (CO₂ eq, m³).
- **Compare**: the panels are *Compare contributions*, *Cascade* and *Multi indicator matrix*; the breakdown of
  *Compare contributions* can be *None* (one bar of the total for each tab).
- Explanations are tooltips, not text on the page; one caret for every list or section that folds;
  the actions of a row never cover its name, and a label never runs into its buttons.
- Shorter descriptions in the app's own words.

- **Projects** (it was *Load database*): one list of projects and their databases. A database has **Open** and an
  **Options** menu: close, lock, rename, add a database, remove; a project has *Add database* and *Delete project*.
- New: **rename a database**, and **add a database from another project** (or a copy of one of the same project).

- **Pop-up**: its explorer is the plain tree, with no options; the actions (use in this tab, open in new tab) and
  *Pin* are in a **⋯** menu at the top right; *Used by* and *Alternatives* lose their headers and texts, *Used by*
  keeps one column, and their rows open with Space or a right-click.
- **Processes panel**: no amounts and no text (tooltips instead); a right-click opens the pop-up of a process.
- **Process contribution**: *Single indicator* and *Multi indicator*, and under the chart the whole list of direct
  contributions of the processes. The Methods panel legend says what the round and the square mark are, and
  the methods show their category and, in grey, their indicator.
- **View options**: the buttons are in groups (*Groups*, *Names*, *Colours*), *Group colours* is *Rules colours*,
  and *Group by substance* and *Group by compartment* replace the quick groups of the Rules panel. The colour
  bar of a row names its flow type on hover.
- A side pane that opens takes the focus; ← and → switch its panel. Buttons with a shortcut show it in their
  tooltip, and View, Compare and Edit underline its letter on hover.

- Fixed: *Hide others* left the rows of an expanded others row on screen (list, tree and Sankey).
- Fixed: a selection kept rows that were no longer shown (hidden, collapsed, grouped away), so the selection bar and
  the totals counted them.
- Fixed: a rule added, or rows grouped, while *Group by rules* was off showed nothing; it is switched on.

## 1.2.0 - 2026-11-01

- The *Groups* panel is now *Rules*, and *Group contributions* is *Rules contributions*.
- A *Group by rules* switch in the view options turns the grouping of the tree by the rules on or off, without
  touching the rules.
- Fixed: in 1.1.0 the Exchanges panel of a process pop-up stayed empty and the pop-up could not be closed
  (the explorer inside it had no impact method). 1.1.0 should be skipped.

## 1.1.0 - 2026-10-30

A new layout, and a much faster start.

- **Left panel**: *Processes* (the processes open, grouped by window, with their amount, a close button, and
  what to compare), *Methods* and *Groups*. The impact methods and the group rules are chosen once and shared by
  all tabs, instead of per tab and per panel; the group rules are built there, in named rule sets.
- **Status bar**: the view options (a pane of the tab in focus, also with the key `v`) and the shortcuts moved
  from the top bar; Ctrl/⌘ + ← and → hide or show the left and the right panel.
- **Search** is a filter: what is typed fills a first *activity name contains* filter, to which others are added.
- **Compare mode** takes its processes, baseline, methods and rules from the left panel.
- The tool speaks of **processes**, no longer of functional units.
- **Faster start**: the cumulative impacts of every process (what took about 10 s at each start on ecoinvent) are
  kept next to the model files and read back in a fraction of a second; they are computed in the background the
  first time. The first start after updating still takes as long as before.

## 1.0.0 - 2026-10-30

First stable release.

- Explore process trees as a list, a tree or a Sankey diagram, with cut-off, reverse "Used by" tree and
  alternative suppliers.
- Contribution analysis by process, by flow and by group, with grouping rules (reorder by drag and drop,
  colours, flow groups) and merging of processes into abstract processes.
- Compare several processes: contribution by group, difference waterfall, impact categories heat map.
- Edit mode: copy, edit and delete processes, replace sources, adapter files.
- Manage Brightway 2.5 projects and databases: ecoSpold2, SimaPro CSV, ecoinvent (with your account),
  Agribalyse, the Brightway repository (BAFU).
- One launcher, `lca_explorer.cmd`, that sets up conda and the environment on the first run.
