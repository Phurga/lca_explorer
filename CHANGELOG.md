# Changelog

All notable changes to LCA Explorer. Versions follow [semantic versioning](https://semver.org/):
a patch (1.0.x) fixes bugs, a minor (1.x.0) adds features without breaking your projects, a major (x.0.0)
may need you to do something when you update. Each version is a git tag; the `main` branch is always the
latest release, new work happens on `dev`.

## Unreleased

Changes on `dev`, not released yet.

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
