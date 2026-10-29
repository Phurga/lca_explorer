# Data import

## Sources

| source | status |
|---|---|
| Brightway 2.5 project (bw2data 4) | done: `lca_viewer.brightway.from_brightway`; from the app with *Load database* (`lca_viewer.sources`) |
| ecoinvent ecoSpold2 release (`.7z`) | done via Brightway import (`lca-viewer import ecospold2`, or `scripts/import_ecoinvent.py`) |
| ecoinvent release downloaded with the user's account | done: into a project (`lca-viewer ecoinvent import`, *Add database* → *ecoinvent*) |
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

The Brightway projects section of *Load database* and the *edit panel*
panel change projects. Each change runs as `lca-viewer project …`,
`lca-viewer import …` or `lca-viewer dataset …` in a separate process with
`BRIGHTWAY2_DIR` set to the chosen folder (`server.create_library.cli`), as
builds do: the server itself never imports bw2data.

- **Projects** (`lca_viewer.editing`): `create_project` (the page makes them
  empty; `--setup` adds `importing.setup_project`: biosphere3 and the
  ecoinvent 3.9 methods bundled with bw2io), `delete_project` (with its folder; bw2data refuses to delete
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
  (default `biosphere3`, installed with the methods if missing, unless
  `--no-setup`; with another biosphere database, nothing is installed), and
  `--defaults VERSION`: the page's *Add the default biosphere and impact
  methods* (unticked: the chosen biosphere database and `--no-setup`).
  `importing.install_defaults` installs, once per project, files.brightway.dev's
  project `ecoinvent-<version>-biosphere` (3.8 to 3.12, about 20 MB, kept in
  `data/downloads`): ecoinvent's elementary flows and LCIA implementation of
  that version (3.10: 4,362 flows, 668 methods). It is read from the archive
  without restoring the project (`read_biosphere_archive`: `databases.json`,
  the flows from `lci/databases.db`, the methods from `methods.json` and
  `intermediate/<abbreviation>.pickle`, with factors keyed by node id mapped
  back to (database, code); pickles read with an unpickler that builds no
  class): restoring took 86 s, then bw2data reprocessed every method again.
  Writing the 668 methods takes about 3 minutes. The methods are named
  `("ecoinvent-<version>", …)` as bw2io's `import_ecoinvent_release` names
  them, so they never replace another version's; a project that has the
  database and methods of that version keeps them. `bundled` is
  `setup_project` (biosphere3 and bw2io's 3.9 methods, offline). The page
  picks the version from the ecoSpold2 file name or the SimaPro background
  database (same version, else same major.minor; 3.9 with biosphere3 already
  in the project: `bundled`), else asks. The SimaPro CSV import is
  unchanged; the page lists randonneur_data's transformations (`/api/adapters`,
  with those `default_migrations` picks for the ticked background databases)
  and, when exchanges stay unlinked, the rows of the report CSV, then offers
  `--unlinked drop`.
- **ecoinvent with the user's account** (*Add database* → *ecoinvent*, or
  `lca-viewer ecoinvent login` then `lca-viewer ecoinvent import --project P
  --version 3.10.1 --system-model cutoff`): `importing.import_ecoinvent_release`
  downloads the release with `ecoinvent_interface` (kept in its own cache,
  `%LOCALAPPDATA%\pylca\EcoinventInterface\cache` on Windows, so a second
  project of the same release downloads nothing) and runs bw2io's
  `import_ecoinvent_release` into the project. With the default biosphere and
  methods: the release's own biosphere (`ecoinvent-<version>-biosphere`,
  patched with the flows it lacks if already there) and LCIA methods
  (`("ecoinvent-<version>", …)`, skipped when the project has them; the
  namespace keeps them apart from `bw2setup`'s). Without (`--biosphere B`):
  linked to a biosphere database of the project (bw2io adds the release's
  flows it lacks), no methods. A project created by the CLI for it is deleted
  if the import fails.
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
- **One Brightway data folder**: lca_viewer works in the folder bw2data
  picks, `BRIGHTWAY2_DIR`, else Brightway's default folder
  (`sources.brightway_dir`), the one Activity Browser and the user's own
  scripts use. It no longer keeps projects of its own in `data/brightway`:
  that made a second copy of ecoinvent next to the user's. `lca-viewer
  serve` creates the folder the first time, as Brightway would
  (`lca-viewer project init`: `projects.db` and an empty `default`
  project), in a separate process; *Other folder…* still opens any other.

## Datasets created in the app

`editing.write_dataset` writes a dataset entered in the edit panel:

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
  comment, into `comment`, which the process pop-up shows (the comment as
  typed is kept in `lca_viewer["comment"]`, with the creation date: that mark
  is also what tells the app a dataset is its own, so only those can be edited
  or deleted).
- **Copy** (`sources.read_dataset`): the form's content of any process, read
  from the project's SQLite file. A process allocated from a multi-output one
  stands for its parent; so does a product for the dataset producing it.
- **Edit** (`write_dataset` with `code`): one SQLite transaction
  (`sqlite3_lci_db.atomic()`) that replaces the exchanges and the node's
  fields and processes the database again before it ends. What the datasets
  using it link to is never deleted or recreated, so they stay linked, and
  their own processed matrices stay valid (they hold node ids, which do not
  change):
  - a process that is its own product keeps its node;
  - a multi-output process keeps the nodes of its products (renamed in place;
    their allocated processes keep their codes through `mf_allocated_process_code`);
  - a process with one product can get co-products: its node cannot become
    the multi-output process (users link to it), so it becomes the *first
    product* node and a new multi-output node takes its place, the allocated
    process of that product producing it. The dataset's code changes; the old
    code still finds it (a product leads to the dataset producing it);
  - co-products are removed down to one (then the process is a plain process
    with one product node: `multifunctional` retypes it on save). A product
    that another dataset of the project uses (any database: `_used_by`)
    cannot be removed, nor the dataset deleted: deleting a node deletes the
    exchanges that point to it, which would silently take an input out of those
    datasets and change their results.
  - Checked in `tests/test_editing.py` on models built the way the app builds
    them, for the edited database and for one that uses it.
- **Staged changes** (`editing.apply_changes`): the page stages creates, edits and
  deletes (`data/pending/<project>.json`, one file per Brightway folder and project);
  applying them runs all in order inside one `atomic()`, processes each touched database
  once at the end, and on any failure rolls back and removes the databases made for
  the batch. Later changes see earlier ones (delete the user, then the used dataset).
  Limit: a staged new process is not in the project yet, so another staged process
  cannot take it as an input.
- **Rebuilding** an open database: the model is built beside the old one,
  which keeps being served until it is replaced (`reopen`).
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
- Brightway data directory: `BRIGHTWAY2_DIR` (the folder must exist), else
  Brightway's default folder; project `lca_viewer_ei391`, database
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
2. **Aggregated ecoinvent copies** (when linking to ecoinvent): exports such
   as Agribalyse carry the ecoinvent processes they use as *system*
   (aggregated) copies, products named `…| Cut-off, S - Copied from Ecoinvent`,
   without technosphere inputs and with thousands of elementary flows each.
   A copy is removed (with its product and allocated parts) when the
   ecoinvent process it copies is in a `--link-to` database (found through
   Pré's correspondence below); the inputs that used it get their SimaPro
   ecoinvent name back (`…| Cut-off, U`) and link to that process
   (`importing.replace_aggregated_copies`). A process with inputs is never
   removed (it may be an ecoinvent dataset the authors changed), nor a copy
   with no match. randonneur_data's `agribalyse-3.1.1-delete-aggregated-ecoinvent`
   does the same by *process identifiers*, which change with every SimaPro
   export (a re-exported Agribalyse 3.1 matched none), and by name it would
   remove 7 Agribalyse processes named like ecoinvent ones.
3. **Internal links** by product name and unit, on SimaPro's own names,
   before any renaming: they are unique, while bw2io's
   `split_simapro_name_geo` later turns `Yeast, at plant/FR U` and `Yeast, at
   plant/RER U` into the same name (location FR and RER). Linking after it
   made processes use themselves (zero on the matrix diagonal). bw2io's own
   internal matching also compares the location, which products take from
   their process's `Geography` while inputs have none, so it misses them.
4. **Links to ecoinvent** (`--link-to ecoinvent-3.9.1-cutoff`). Unlinked
   inputs named the SimaPro way (`Diesel {Europe without Switzerland}| market
   for diesel | Cut-off, U`) are renamed to ecoinvent's (name, reference
   product, location) with Pré's correspondence file
   `simapro-ecoinvent-3.9.1-cutoff` from `randonneur_data` (picked from the
   ecoinvent version in the database name; override with `--migration`), then
   linked on those three fields. Waste to treatment (written by
   bw_simapro_csv as non-functional production exchanges) is linked the same
   way.
5. **Emissions and resources**, in this order, each for the flows still
   unlinked: SimaPro's final waste flows, non material emissions, social and
   economic issues are left out (not elementary flows in ecoinvent);
   randonneur_data's SimaPro → ecoinvent correspondences (`SimaPro-9-ecoinvent-3.9-biosphere`
   and `-3.8-`, made with Flowmapper by its authors and shipped as data:
   Flowmapper itself is not installed nor run; and the manual
   `simapro-2024-biosphere-ecoinvent-3.10-biosphere`) link a
   flow directly when the target's ecoinvent UUID is in the project's
   biosphere (most UUIDs are the same across versions, so the 3.9 list
   covers a 3.8 biosphere); manual renames (`agribalyse-3.1.1-ecoinvent-3.10-biosphere-manual-matches`,
   `simapro-2024-biosphere-ecoinvent-3.10-biosphere`, e.g. Agribalyse's EF
   names `COD (Chemical Oxygen Demand)`); bw2io's classic SimaPro → ecoinvent
   normalisation (compartments, names, units, regionalised water); ecoinvent's
   EF 3.1 correspondences read backwards for EF names (`Phosphorus, total` →
   `Phosphorus` in the same compartment, when a single ecoinvent flow maps
   there); then a link on name, compartment and unit, whatever the case.
6. **Unlinked exchanges** are listed in `data/import-<database>-unlinked.csv`
   (type, name, compartment, unit, count, an example process). By default
   nothing is written: a matrix with dangling inputs would silently miss part
   of the supply chain. `--unlinked drop` writes the database without them
   (and lists them); the app asks which one before importing, so that a large
   export is not read twice.
7. **Names**: the `{FR} U` suffix of Agribalyse names moves into `location`
   (`Soft wheat grain, at farm gate {FR} U` → name without suffix, location
   `FR`). The original name is kept as `simapro name`.

`lca-viewer build` then loads the technosphere from one node of the new
database. Brightway adds every database it depends on (ecoinvent,
biosphere), so the model is square and complete. Processes whose product is
a separate node are mapped to their product's row
(`brightway._product_rows`): the input of their *functional* exchange (a
production one, or a technosphere one for a waste treatment), not any
production exchange. SimaPro's waste to treatment are production exchanges
too, so taking the first one made processes "produce" the waste they send
("Several activities produce the same product"). The build only loads the
matrices, without solving.

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
