# Changelog

All notable changes to LCA Explorer. Versions follow [semantic versioning](https://semver.org/):
a patch (1.0.x) fixes bugs, a minor (1.x.0) adds features without breaking your projects, a major (x.0.0)
may need you to do something when you update. Each version is a git tag; the `main` branch is always the
latest release, new work happens on `dev`.

## Unreleased

Changes on `dev`, not released yet.

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
