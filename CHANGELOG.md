# Changelog

All notable changes to LCA Explorer. Versions follow [semantic versioning](https://semver.org/):
a patch (1.0.x) fixes bugs, a minor (1.x.0) adds features without breaking your projects, a major (x.0.0)
may need you to do something when you update. Each version is a git tag; the `main` branch is always the
latest release, new work happens on `dev`.

## Unreleased

Changes on `dev`, not released yet.

- Updating with `git pull` reinstalls the packages on the next launch when `pyproject.toml` changed.

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
