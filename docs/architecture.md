# Architecture

## Decision: local-first web app, not an Activity Browser plugin (for now)

Research: [research/activity-browser-and-existing-tools.md](research/activity-browser-and-existing-tools.md).

- Activity Browser 3 is still a beta (verified on PyPI: latest
  `3.0.0b202608291724`); its plugin system from AB 2 was removed and is being
  redesigned — there is no stable API to build on.
- AB 3 beta already ships a lazy **contribution tree** tab (verified:
  `activity_browser/app/pages/lca_results/contribution_tree_tab.py`). Our
  difference is therefore **grouping (manual / thematic / query), the
  tree-driven charts, multi-method views and saved views** — not the tree
  itself.
- A web UI can later be embedded in AB (AB already uses QtWebEngine for its
  Sankey and graph views), so the local web app does not close that door.

## Shape

```
Brightway project / ecoSpold2
        │  importers (brightway.py, …)
        ▼
LCAModel (A, B, CFs, metadata)  ── cached on disk (npz)
        │  one factorisation → G (cumulative per unit, all methods)
        ▼
Tree (lazy) → View (expanded set, group rules, cut-off) → rows / frontier
        │                                        │
        ▼                                        ▼
analysis.py (global grouping)            charts (frontier → bars)
        │
FastAPI (local, `lca-viewer serve`) ── JSON ──► browser UI
```

- **Local-first**: the server runs on the practitioner's machine and reads
  their Brightway projects. No ecoinvent data leaves the machine, which avoids
  the licensing issue of a hosted SaaS (see [data-import.md](data-import.md)).
  A hosted/SaaS mode can reuse the same API later.
- **Python core** (`lca_viewer`): no web dependency, usable from notebooks.
- **API**: thin FastAPI layer over `View` — the view state is held
  client-side and sent with each request (stateless server, views are plain
  JSON, easy to save/share).
- **UI**: see [ui.md](ui.md).
