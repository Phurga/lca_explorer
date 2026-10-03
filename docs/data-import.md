# Data import

## Sources

| source | status |
|---|---|
| Brightway 2.5 project (bw2data 4) | done — `lca_viewer.brightway.from_brightway` |
| ecoinvent ecoSpold2 release (`.7z`) | done via Brightway import (`scripts/import_ecoinvent.py`) |
| direct ecoSpold2 → `LCAModel` (no Brightway) | later, only if a non-Brightway deployment needs it |
| openLCA JSON-LD, SimaPro CSV | later |

## Decision: go through Brightway for ecoSpold2

Target users are LCA practitioners; Brightway / Activity Browser users already
have ecoinvent imported in a project. Using bw2io for ecoSpold2 means one
importer to maintain, and the same linking/strategies everyone else uses.
The engine itself does not depend on Brightway (see
[calculation-engine.md](calculation-engine.md)), so a direct importer can be
added later.

## ecoinvent 3.9.1 cut-off import notes

- Archive: `ecoinvent 3.9.1_cutoff_ecoSpold02.7z` (21,239 datasets + MasterData),
  extracted with `py7zr` to `data/ei391_cutoff/` (2.6 GB). No 7z binary needed.
- Brightway data directory: `data/brightway/` (set `BRIGHTWAY2_DIR`), project
  `lca_viewer_ei391`, database `ecoinvent-3.9.1-cutoff`.
- `bw2io.bw2setup()` fails with bw2data 4.7 + bw2io 0.9.17: the bundled LCIA
  data uses lists as flow keys and `Method.write` now requires tuples. The
  import script converts them and calls `create_default_biosphere3`,
  method writing and `create_core_migrations` itself.
- bw2io bundles the **ecoinvent 3.9 biosphere and LCIA methods** (762
  methods), which match 3.9.1. Import: 0 unlinked exchanges.
- For other ecoinvent versions, the matching LCIA implementation file has to
  be provided (bw2io's `EcoinventLCIAImporter` or ecoinvent_interface).

## Licensing

ecoinvent data must never be committed (`.gitignore` excludes `data/`,
`*.7z`, `*.spold`). Hosting ecoinvent-derived matrices for other users in a
SaaS would need an agreement with ecoinvent; the local-first design avoids
this (see [architecture.md](architecture.md)).
