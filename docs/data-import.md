# Data import

## Sources

| source | status |
|---|---|
| Brightway 2.5 project (bw2data 4) | done: `lca_viewer.brightway.from_brightway`; from the app with *Load database* (`lca_viewer.sources`) |
| ecoinvent ecoSpold2 release (`.7z`) | done via Brightway import (`lca-viewer import ecospold2`, or `scripts/import_ecoinvent.py`) |
| ecoinvent release downloaded with the user's account | done: a new project per release (`lca-viewer ecoinvent import`, *New project from ecoinvent*) |
| SimaPro CSV process export (Agribalyse 3.2) | done via Brightway import (`lca-viewer import simapro-csv`), tested on toy data only |
| direct ecoSpold2 → `LCAModel` (no Brightway) | later, only if a non-Brightway deployment needs it |
| openLCA JSON-LD | later |

Every database goes through the same steps: into a Brightway project
(`lca-viewer import`, or the user's own projects), then built into a model in
`data/cache/<name>`, then loaded by the server. The README has the commands
("Adding Agribalyse").

## Loading from the app

`lca-viewer serve` starts with no database (unless given `--model`); *Load
database* lists what can be loaded (`/api/sources`, `/api/brightway`) and
loads it in the background (`/api/load`, followed with `/api/jobs/<id>`).
Each loaded model is mounted at `/db/<key>/` with the API of `create_app`;
`/api/unload` removes it.

- **Built models**: the catalog `data/models.json` (`sources.catalog` /
  `sources.register`). Entries built from Brightway record their `source`
  (folder, project, database) and what the build depends on
  (`sources.database_state`): the `modified` time of the database and of the
  databases it links to, directly or not, and a stamp of the impact methods
  (`methods.json` has no dates: the stamp hashes it with the time and size of
  each method's `processed/<abbreviation>.zip`). The dialog says what changed
  since (`sources.changes`) and offers a rebuild; *Open old build* opens the
  build as it is. Entries registered before dependencies and methods were
  recorded are compared on their database alone. A database that is open is
  not rebuilt (its matrices in memory would not match the new metadata
  files): close it first. Saved tabs record the layout of their model (a
  hash of the activities, flows and methods in order, `/api/info`'s
  `layout`): restored on a rebuilt model whose positions moved, the
  functional unit and methods are found again by name, and the expanded
  rows, pins, merges and names are dropped.
- **Brightway projects** are listed by reading the files directly:
  `projects.db` (project names; the pickled metadata, read with an unpickler
  that refuses any class, says whether the project is Brightway 2.5), each
  project's `databases.json` and `methods.json`, and `lci/databases.db`
  (nodes per database and type, to count processes and tell biosphere
  databases apart). No bw2data import, so listing is instant and read-only.
  Project folders are named as bw2data names them (`safe_filename`, short or
  full md5 hash).
- **Building** runs `lca-viewer build` in a separate process with
  `BRIGHTWAY2_DIR` set to the chosen folder: bw2data's data folder is fixed
  when it is imported, and the build's memory is freed when it ends. Its steps
  are shown in the dialog. Output goes to
  `data/cache/bw-<hash>/<project>-<database>` (the hash of the Brightway
  folder, project and database: the same names in two folders are two
  models) through a `.part` folder, so an interrupted build leaves nothing
  half written; the previous build is moved aside before the new one takes
  its place, and kept if it cannot be (a file in it in use). A model built
  before keeps its folder: the catalog is looked up by `source`, not path. Checked on ecoinvent 3.11 consequential (633 methods, 22,241
  activities, 1.5 min): same score as bw2calc.
- Brightway 2 projects (no `25` flag) are refused with a message: opening
  them with bw2data 4 needs `projects.migrate_project_25()`, which changes
  them, and that is the user's call.
- Default method of a model: EF v3.1 climate change (GWP100), also when the
  method names start with the ecoinvent release (as `ecoinvent_interface`
  writes them), else the first climate change GWP100 method, else the first
  "global warming" one (Ecological Scarcity 2021 in BAFU).

## Changing projects from the app

The Brightway projects section of *Load database* and the *Create dataset*
panel change projects. Each change runs as `lca-viewer project …`,
`lca-viewer import …` or `lca-viewer dataset …` in a separate process with
`BRIGHTWAY2_DIR` set to the chosen folder (`server.create_library.cli`), as
builds do: the server itself never imports bw2data.

- **Projects** (`lca_viewer.editing`): `create_project` (optionally with
  `importing.setup_project`: biosphere3 and the ecoinvent 3.9 methods bundled
  with bw2io), `delete_project` (with its folder; bw2data refuses to delete
  the last project of a folder), `restore_project` (`bw2io.restore_project_directory`).
  bw2data adds a `default` project to a folder that has none when it is
  imported, so a new folder gets one.
- **Backups from files.brightway.dev**: the list is its
  `projects-config.json` (with a built-in copy when offline). `BAFU-2025-v2`
  is the Swiss LCI database (BAFU/UVEK 2025) as a Brightway 2.5 project: its
  own biosphere (`BAFU-2025-biosphere`, every flow typed `emission`, resources
  told apart by their SimaPro compartment `resources`), 11,747 processes and
  the 21 Ecological Scarcity 2021 methods. Downloaded to `data/downloads`
  (60 MB), restored, then deleted. Checked: restore, build (20 s) and a
  dataset written into it.
- **Imports**: `lca-viewer import ecospold2` now takes the `.7z` archive too
  (unpacked into `data/unpacked`, deleted after the import) and `--biosphere`
  (default `biosphere3`, installed with the methods if missing; with another
  biosphere database, nothing is installed). The SimaPro CSV import is
  unchanged; the page lists randonneur_data's transformations (`/api/adapters`,
  with those `default_migrations` picks for the ticked background databases)
  and, when exchanges stay unlinked, the rows of the report CSV, then offers
  `--unlinked drop`.
- **ecoinvent with the user's account** (*New project from ecoinvent*, or
  `lca-viewer ecoinvent login` then `lca-viewer ecoinvent import --project P
  --version 3.10.1 --system-model cutoff`): `importing.import_ecoinvent_release`
  downloads the release with `ecoinvent_interface` (kept in its own cache,
  `%LOCALAPPDATA%\pylca\EcoinventInterface\cache` on Windows, so a second
  project of the same release downloads nothing) and runs bw2io's
  `import_ecoinvent_release` into a new project: its own biosphere
  (`ecoinvent-<version>-biosphere`) and LCIA methods (`("ecoinvent-<version>",
  …)`), no `bw2setup`. A project created for it is deleted if the import fails.
- **Credentials** (`lca_viewer.credentials`): kept with `keyring` in the
  system's credential store (Windows Credential Manager, macOS Keychain,
  Secret Service), never in a file of the repository or of `data/`;
  plain-file backends (`keyrings.alt`) are refused. Without a secure store, or
  without *Remember*, they stay in the server's memory until it stops. The
  password is sent once, to `/api/ecoinvent/login` (checked with ecoinvent
  first, accepted from this computer only); the API returns the user name,
  never the password. The import process gets them in its environment
  (`EI_USERNAME`, `EI_PASSWORD`, which `ecoinvent_interface` reads), not on
  its command line nor in the job log. `ecoinvent_interface`'s own
  `permanent_setting` (plain files) is not used.
- **Uploads**: a file chosen in the page is streamed to `data/uploads`
  (`/api/upload`, raw body, no multipart dependency) and deleted once an
  import or restore used it.
- `/api/brightway` lists a folder without `projects.db` as empty (New project
  creates it), and 404 only for a folder that does not exist.

## Datasets created in the app

`editing.write_dataset` writes a dataset entered in *Create dataset*:

- **Single output**: a process with a production exchange to itself (as
  ecoinvent), in any database of the project.
- **Co-products**: a `multifunctional` node with one product node per
  co-product, each functional production exchange carrying
  `properties["manual_allocation"]` = its percentage, then
  `node.allocate("manual_allocation")`: one read-only allocated process per
  product, scaling every non-functional exchange by its share (the structure
  of a SimaPro CSV import, which `brightway._product_rows` already handles).
  Needs a database with the `multifunctional` backend: new databases made by
  the panel have it (`default_allocation="manual_allocation"`).
- **Exchanges**: technosphere inputs link to a process's product node when it
  has a separate one (`editing._supplier`); waste to treatment is a negative
  technosphere amount (as in ecoinvent, where treatment produces -1); avoided
  products are `substitution` exchanges; resources and emissions `biosphere`.
  Each carries its comment, and SimaPro's lognormal SD² becomes
  `uncertainty type` 2 with `scale = ln(SD²)/2`.
- **Documentation**: the fields go to `documentation` (a dict) and, after the
  comment, into `comment`, which the process pop-up shows.
- **Search** for the exchanges reads the project's SQLite file directly
  (`sources.search_nodes`): processes with SQL `LIKE` on name, product and
  location (a word equal to a location ranks that location first), flows from
  a per-project cache matched on name and compartment. About 0.1 s on
  ecoinvent, without bw2data.
- Checked: co-product scores equal the hand calculation, on toy data
  (`tests/test_editing.py`) and on BAFU (Swiss electricity, methane, river
  water; Ecological Scarcity 2021).

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
- Brightway data directory: `data/brightway/` (set `BRIGHTWAY2_DIR`; the
  folder must exist), project `lca_viewer_ei391`, database
  `ecoinvent-3.9.1-cutoff`.
- `bw2io.bw2setup()` fails with bw2data 4.7 + bw2io 0.9.17: the bundled LCIA
  data uses lists as flow keys and `Method.write` now requires tuples.
  `lca_viewer.importing.setup_project` converts them and calls
  `create_default_biosphere3`, method writing and `create_core_migrations`
  itself (first import into a project only, ~3 min).
- bw2io bundles the **ecoinvent 3.9 biosphere and LCIA methods** (762
  methods), which match 3.9.1. Import: 0 unlinked exchanges.
- For other ecoinvent versions, the matching LCIA implementation file has to
  be provided (bw2io's `EcoinventLCIAImporter` or ecoinvent_interface).
- Checked on Windows with bw2data 4.8 / bw2calc 3.1 (env `lca_viewer_bw`):
  the project created on the Linux server opens as is, and a rebuild gives the
  same model as the cache (same activities, `A`, `B` and factors).

## Agribalyse 3.2 (SimaPro CSV)

### Getting it

- AGRIBALYSE 3.2 (ADEME) is built in SimaPro on **ecoinvent 3.9.1** (and
  WFLDB). The SimaPro version is the reference.
- Full LCI: SimaPro CSV export, downloaded after a form on the
  [SimaPro Agribalyse page](https://simapro.com/products/agribalyse-agricultural-database/).
  ADEME requires an ecoinvent licence (academic, commercial or enterprise) to
  use it in LCA software.
- There is **no ready-made Brightway download**. files.brightway.dev only has
  biosphere databases, USEEIO, forwast and BAFU, because of ecoinvent
  licensing. ADEME's own Brightway route,
  [sentier-agribalyse](https://github.com/sentier-dev/sentier-agribalyse),
  is a pipeline that reads the same CSV (`AGB32_final.CSV`) and needs a local
  ecoinvent 3.9.1.
- sentier-agribalyse also lists `source/harmonised-flows-simple.json.gz`
  among its inputs. It is a list of EF 3.1 elementary flows with their
  synonyms (`identifier`, `prefLabel`, `altLabel`, `context_iri`), used only
  as one fallback tier when matching Agribalyse emissions to EF flows; its
  reader returns nothing when the file is missing. `lca-viewer import
  simapro-csv` does not use it (flows go through bw2io's SimaPro → ecoinvent
  normalisation and randonneur_data).
- An **Agribalyse 3.1** SimaPro export (`AGB3.1.CSV`, 17,550 processes)
  contains the ecoinvent processes it uses as aggregated system processes
  (1,792 "Cut-off, S - Copied from Ecoinvent", 143 "Adapted from Ecoinvent"),
  so its technosphere links inside the file; only its emissions need linking
  to a biosphere.
- openLCA (Nexus) has a full version and a "core" version without ecoinvent;
  ADEME advises against the core version (about 200 ecoinvent datasets are
  adjusted in the official one).

### What `lca-viewer import simapro-csv` does

`lca_viewer.importing.import_simapro_csv`, all with bw2io 0.9.17 and its data:

1. **Read** with bw2io's `SimaProBlockCSVImporter` (parser:
   `bw_simapro_csv`). Each product becomes a product node linked to its
   process. Multi-output processes are allocated with SimaPro's allocation
   factors into read-only processes (library `multifunctional`, database
   backend `multifunctional`).
2. **Internal links** by product name and unit (SimaPro product names are
   unique). bw2io's own internal matching also compares the location, which
   products take from their process's `Geography` while inputs have none, so
   it misses them.
3. **Links to ecoinvent** (`--link-to ecoinvent-3.9.1-cutoff`). Unlinked
   inputs named the SimaPro way (`Diesel {Europe without Switzerland}| market
   for diesel | Cut-off, U`) are renamed to ecoinvent's (name, reference
   product, location) with Pré's correspondence file
   `simapro-ecoinvent-3.9.1-cutoff` from `randonneur_data` (picked from the
   ecoinvent version in the database name; override with `--migration`), then
   linked on those three fields. Agribalyse processes that copy and modify an
   ecoinvent dataset are linked internally first, so the modified version is
   used, as in SimaPro.
4. **Emissions and resources**: the SimaPro compartment becomes
   `categories`. Then bw2io's classic SimaPro → ecoinvent normalisation runs
   (compartments, flow names such as `Methane, biogenic` → `Methane,
   non-fossil`, units, regionalised water), and flows are linked to
   `biosphere3` on name, categories and unit.
5. **Unlinked exchanges** are listed in `data/import-<database>-unlinked.csv`
   (type, name, compartment, unit, count, an example process). By default
   nothing is written: a matrix with dangling inputs would silently miss part
   of the supply chain. `--unlinked drop` writes the database without them.
6. **Names**: the `{FR} U` suffix of Agribalyse names moves into `location`
   (`Soft wheat grain, at farm gate {FR} U` → name without suffix, location
   `FR`). The original name is kept as `simapro name`.

`lca-viewer build` then loads the technosphere from one node of the new
database. Brightway adds every database it depends on (ecoinvent,
biosphere), so the model is square and complete. Processes whose product is
a separate node are mapped to their product's row
(`brightway._product_rows`). The build only loads the matrices, without
solving.

### Not checked yet (no Agribalyse file here)

Tested end to end on `tests/fixtures/simapro_toy.csv`: a synthetic crop, a
multi-output dairy farm, a waste treatment, and real ecoinvent SimaPro names.
The milk score matches Brightway and a hand calculation. The real 3.2 export
may still need:

- extra biosphere mappings. Agribalyse uses some substances that Brightway's
  biosphere doesn't have; ADEME warns about small differences, mainly for
  water. `randonneur_data` has `agribalyse-3.1.1-biosphere-ecoinvent-3.8-biosphere`
  (manual matches for 3.1.1), usable with `--migration`.
- handling of ecoinvent *system* (aggregated) processes, if the export
  contains any. For 3.1.1 these were removed with
  `agribalyse-3.1.1-delete-aggregated-ecoinvent`.
- a check of results against ADEME's published scores (sentier-agribalyse
  backtests 2,451 products this way).

`import_simapro_csv` uses bw2io's internal unit matching, which fetches
unit definitions from vocab.sentier.dev, so the import needs internet access.

## Licensing

ecoinvent data must never be committed (`.gitignore` excludes `data/`,
`*.7z`, `*.spold`). The same goes for Agribalyse exports and the models built
from them (they contain ecoinvent): keep them in `data/`. Hosting
ecoinvent-derived matrices for other users in a SaaS would need an agreement
with ecoinvent; the local-first design avoids this (see
[architecture.md](architecture.md)).
