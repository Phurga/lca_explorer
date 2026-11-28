# Project cache, storage menu and database graph

Status (2026-11): phases 1 (database graph), 2 (storage menu), 3 (project cache, `project_cache.py`) and 4 (`U` not
kept, `CF` sparse, `G` solved by chunks) **done**; a database opens before its exchanges are read, which processes
of their own read (see [Opening before the exchanges](#decision-opening-before-the-exchanges)). Not done: segments shared in memory between two open databases,
the method list in the project's ⚙ menu (it is chosen at a build), a smaller default method list. Replaces the cache
of one model per database and the calculation of `G` at first open (see [data-import.md](data-import.md#loading-from-the-app)
and [calculation-engine.md](calculation-engine.md#model-independence)). Measured: see [Measured](#measured).

## Problem

Each Brightway database is built into a model of its own, which holds the database **and every database it uses**.
A project with several small foreground databases on ecoinvent stores and computes ecoinvent once per foreground
database.

Measured on the `AESA_CC_sg_reunion` project (ecoinvent 3.10.1 cut-off, Agribalyse 3.1, two small foreground
databases):

| model | uses | cache |
|---|---|---|
| ecoinvent-3.10.1-cutoff | biosphere | 399 MB |
| canne_sucre_reunion (9 processes) | ecoinvent | 399 MB |
| agribalyse3.1 | its own biosphere (not ecoinvent) | 911 MB |
| aval_sucre_reunion (72 processes) | agribalyse, canne_sucre, ecoinvent | 1.3 GB |
| **total** | | **3.0 GB**, of which ~1.7 GB are copies |

Time goes the same way. Building ecoinvent 3.12 (26.5k processes, 633 methods) took 60 s:
- 34 s reading the 893k exchanges, which also used +1.35 GB of RAM;
- 14 s writing `exchanges.sqlite`;
- 6 s reading the matrices;
- the rest reading activities and methods.

Then the first open factorises `A` (1.8 s) and solves `G` (3.6 s). A 9-process database built on ecoinvent pays
the full cost again.

The app also does not show how much disk its caches and the Brightway projects take, and cannot clear them.

## Decision: one cache per Brightway project, made of segments

The cache is kept per Brightway project. It is made of **segments**: one per database, or one per group of
databases that use each other (see [Loops between databases](#loops-between-databases)). Each segment is built
once. Building a new database adds its segment and reuses those it depends on. Opening a database puts its
segment and the segments it uses, directly or not, together into one `LCAModel`. The tree, views, analyses and
API do not change.

### Why it is exact

Order the products with those of the databases a segment uses first (*u*), then its own (*s*). No database in
*u* uses a product of *s*, so `A` is block triangular:

```
A = | A_uu  A_us |      G_u : the rows of G already computed for the segments used (unchanged)
    |  0    A_ss |      A_ssᵀ G_s = U_sᵀ − A_usᵀ G_u      (one factorisation of A_ss, small)
```

- The rows of `G` of a segment do not depend on the segments built after it.
- A database and everything it uses form a closed set: its model is an exact slice of the project's.
- This is the same structure `solver.Solver` already uses inside one model. It is applied here between databases.

### What it changes in practice

| | today | with the project cache |
|---|---|---|
| a foreground database on ecoinvent | ecoinvent read, stored and solved again (~1 min, ~400 MB) | its own exchanges and a small solve (seconds, its own size) |
| AESA project on disk | 3.0 GB | ~1.3 GB (ecoinvent and Agribalyse once), estimate |
| a foreground database changed | its model rebuilt, ecoinvent included | its segment rebuilt, then the `G` rows of the segments that use it |
| ecoinvent changed | each model rebuilt in full | ecoinvent rebuilt once, then the `G` rows of every segment above it (no new reading from Brightway for them) |
| `G` | computed at first open, in the server | computed in the build process, whose memory is freed when it ends |

## Segments

### Which segments

The database graph comes from the exchanges (`input_database` → `output_database` in `lci/databases.db`), not
from Brightway's `depends` metadata, which a script can leave stale. The build runs this check in its own
process, restricted to the databases it builds. The whole-project query takes ~20 s for 5.8M exchanges, too
slow for the page (see [Database graph](#database-graph)).

Biosphere databases are not segments. Their flows are rows of `B`, and they hold no processes.

### Loops between databases

Databases that use each other, directly or not, form one segment and are always built together. These are the
strongly connected components of the database graph.

The usual case is a user editing a background database:
- **Editing ecoinvent's own data** (amounts, exchanges, links inside ecoinvent) keeps the structure. The ecoinvent
  segment is rebuilt, then the `G` rows of every segment above it are solved again.
- **Linking ecoinvent to a foreground product** (e.g. replacing the electricity of an ecoinvent market by the
  user's own PV process in `my_db`) makes ecoinvent use `my_db`. The two then form one segment. Every database
  built on ecoinvent then includes `my_db`, which is correct since ecoinvent really uses it.

There is no assumption to impose on users and no lock is required. The build says it when it happens:
*"ecoinvent-3.10.1-cutoff now uses products of my_db: they are built together."* Locks (*Lock* in *Projects*)
remain the way to protect a background database from edits made in the app.

### Methods

There is one list of impact methods per project cache. A segment's `G` needs the `G` of the segments it uses for
the same methods.
- **Adding methods:** solve their columns for every segment, in order. Each segment's factorisation is reused.
- **Removing methods:** drop their columns and their entries in `methods.pkl`, with no solve. Saved tabs refer to
  methods by position. The layout hash (`/api/info`'s `layout`) already detects the change, and the methods are
  found again by name.
- The first build of a project asks for the methods, with a smaller default than all of them (see
  [Memory](#memory)).

### Positions and links between segments

Each activity has a **position**: its column in `A`, its row in `G`. Positions in a model are the segments'
positions one after the other, in the order of the closed set (segments used first). Two rules follow:

- **A segment stores its links to other segments as Brightway node ids, not positions.** The ids are mapped to
  positions when the model is put together. This is a lookup over the segment's inputs from other segments,
  typically thousands to tens of thousands of entries: milliseconds.
- **Positions move when a segment used before is rebuilt with another size.** Saved tabs keep the behaviour they
  have today after a rebuild: the layout hash differs, the process and methods are found again by name, and
  expanded rows are dropped. Keying saved views on activity codes ([roadmap.md](roadmap.md), next step 2) removes
  the issue. Adding a database elsewhere in the project moves nothing: a model only holds its closed set.

### Files

```
data/cache/project-<hash>/              hash of (Brightway folder, project)
  project.json      format, Brightway origin, methods (name, unit, token), segments, retired folders
  flows.pkl         elementary flows of the segments built so far (appended to)
  methods.pkl       factors of the methods on those flows (extended when flows are appended)
  unlinked.pkl      factors of the methods on flows outside them
  segments/<slug>-<version>/
    segment.json    its databases, what it uses, the state of its databases (their `modified`)
    A.npz           its own block (by position), the node id of its products, its inputs of other segments'
                    products (node id, column, amount)
    B.npz           its flows (node id, column, amount)
    G.npy G.json    its rows of G (its products × the project's methods), and what they were solved from
    activities.pkl details.pkl
    exchanges-<k>.sqlite   the exchanges of a run of its activities (rows: node ids), one file per process that read them
    exchanges.json  the runs and their files, written once all are read (none: the exchanges are not read yet)
```

Segments built before the runs have one `exchanges.sqlite` (no `exchanges.json`): it is read as one run.

`project.json`'s `format` is a number, increased when the files change meaning. A cache of another format
is listed as *obsolete* in the storage menu, never opened, and replaced by the next build. Models made from files
(not from a Brightway project) keep the one-folder model.

Decided while building it:
- **A segment rebuilt goes to a new folder** (its `version`). A database open keeps reading the old one (its details
  and exchanges are read on demand), which the server deletes once no open database reads it (`retired`). Windows
  cannot delete a folder with open files, and a database open during a build keeps working.
- **What `G` was solved from.** A segment's `basis` changes when its version, or the basis of a segment it uses,
  changes: then all its rows are solved again. Its columns are named by method tokens: a method added is solved
  alone, one removed is dropped, one whose factors changed in the project gets a new token. *Drop results* keeps the
  basis: the next build solves the same numbers, and the segments above it are left as they are.
- **Methods: one list per project.** Choosing other methods at a build changes it for every database of the project
  (columns dropped without a solve, new ones solved for every segment).
- **Methods of the app's library** (`docs/methods-library.md`) are in that list too, named `["library", …]`: the
  server gives their factors on the project's node ids to the build process (`--library-file`), and `project.json`
  records each one's digest (`library`), so that an edit of the library marks the databases built with it as to
  rebuild. They are read again at every build; a method whose factors are the same keeps its token and its columns.

## Building

`lca-viewer build --project P --database X` (separate process, as today):

1. Read the graph of the databases X uses. Group the loops. A database whose segment is unchanged since its build
   gives the links its exchanges had then; the others start from Brightway's `depends`, which is checked against
   their exchanges as they are read (step 4). When they differ, the segments are grouped again from the exchanges
   and built again. A query of the exchanges up front took 23 s on Agribalyse (5M exchanges).
2. For each segment X needs, in order (used first), check its state:
   - **Missing, or its databases changed:** build it (steps 3 to 5).
   - **A segment it uses was rebuilt:** solve its `G` rows again (step 5 only). If a node id it links to no
     longer exists, its database changed too, so build it in full.
   - **Otherwise:** keep it.
3. Read its matrices: as today (`bc.LCA` on one of its nodes, `load_lci_data`), keeping its own columns, then its
   activities and details.
   - This still reads the matrices of the databases it uses (6 s for ecoinvent). It is kept for now because the
     sign conventions and multifunctional allocation then stay Brightway's own.
   - Reading the segment's processed datapackage alone would be faster, but would redo what bw2calc does.
     Measure on a 300k-process project before deciding.
   - The links between databases are checked here, from the matrix: the databases of the products in its columns.
     When they differ from what step 1 assumed (`depends` out of date), the segments are grouped again and built
     again, before any result is solved. They were checked from the exchanges, which are now read later (step 4).
4. Start reading its exchanges (comments, uncertainty, pedigree), by **processes of their own**
   (`_ExchangeReader`, `exchange_store.read_part`), while the build goes on: see
   [Opening before the exchanges](#decision-opening-before-the-exchanges). Its new elementary flows are appended to
   `flows.pkl`, and the methods' factors on them to `methods.pkl`.
5. Solve its `G` rows: factorise `A_ss`, then `A_ssᵀ G_s = U_sᵀ − A_usᵀ G_u`, **in chunks of methods** written
   into a memory-mapped `G.npy`. The peak is then the size of `G_s` plus one chunk, not ~6.7 × `G` as today.
6. Once the rows of `G` of X and the segments it uses are solved, log *Results ready* (`project_cache.READY`): X can
   be opened. Then wait for the exchanges, and write each segment's `exchanges.json`.

Products stored apart from their process (SimaPro imports) belong to the segment of the process that produces
them, matched as `brightway._product_rows` does today.

## Opening

1. The closed set of X: its segment and those it uses, from `project.json`.
2. Preallocate `A`, `B`, `G` and the tables, and read each segment's files into place. Concatenating copies (a
   `vstack` of `G`) would double the memory for a moment: 1.9 GB at 300k processes × 800 methods.
3. Map the node ids of links between segments to positions.
4. The factorisation that `supply()` and flow-group analyses need is made on first use, one per segment.

Later step: two databases of one project open at once share their common segments in memory.

## Decision: opening before the exchanges

Asked: show the process tree as early as possible, e.g. *exchanges and tree*, then *metadata*, then *impacts*.
Measured (2026-11-28, see [Measured](#measured)), that order does not fit this engine. "Exchanges" are two different
things here:

| step of a first build | ecoinvent 3.12 | Agribalyse 3.1 | needed for |
|---|---|---|---|
| imports, database graph | 3.4 s | 8.8 s | |
| matrices (`A`, `B`: the exchanges' amounts) | 2.6 s | 4.1 s | the tree |
| activities (names, locations) and details | 2.1 s | 0.5 s | the tree, the pop-ups |
| flows and methods | 0.3 s | 0.2 s | the impacts |
| `G` (factorise, solve) | 4.8 s | 0.5 s | the impacts, the tree's order and sizes |
| **the exchanges' metadata** (comments, uncertainty, pedigree) | **65.6 s** | **177.3 s** | the Exchanges panel, the comments of tree rows |
| total | 79 s | 191 s | |

The tree cannot be shown without its impacts: its rows are ordered and sized by them, and every view, chart and
panel reads `G`. The impacts take 0.5 to 5 s; the slow part is the metadata of the exchanges (Agribalyse: 5.07M
exchanges, 1 GB of pickled data in Brightway's SQLite file). So the order is:

1. **Tree and impacts**: matrices, activities, methods, `G` (~14 s for either database). The build logs *Results
   ready*; the server opens the database at that line (`create_library.run`, `on_ready`): the job gets its `key`
   and `ready: true`, and the page opens it.
2. **The exchanges' metadata**, read meanwhile by processes of their own. Until they are read, `/api/info` lists
   `pending: ["exchanges"]`, the exchanges and flow endpoints answer `pending` (not "missing"), and the top bar shows
   *Reading exchanges…*; then the model gets them (`project_cache.attach_exchanges`), and the page asks its tabs'
   rows and the Exchanges panel again.

How they are read: raw SQLite (`exchange_store.read_part`, 3 to 4× faster than Brightway's ORM: no row objects, no
second join for the output, `data` unpickled as bw2data's `PickleField` does), by runs of consecutive activities
with about as many exchanges each (counted from the index of the outputs, 0.2 to 1 s), one process per run, each
writing its own SQLite file: no merge. Processes: as many as the machine has cores but two, at most 16
(`exchange_workers`, `LCA_VIEWER_WORKERS` overrides it, 0: none), started with the `spawn` method and run below the
normal priority, so that the solve and the app come first. Agribalyse's exchanges: 30 s with 4 processes, 23 s with
8, 17 s with 16; SQLite reading does not scale much further. A segment of fewer than 50,000 exchanges is read in the
build process, after its results (starting a process takes about a second). The numbers do not depend on it: a
test builds the same project with and without processes and finds `A`, `B`, `G`, the activities and the exchanges
equal.

Not taken:
- **Impacts last.** They are a few seconds of a first build and the tree needs them: a tree without impacts
  would be a list of names in no order, and every endpoint would need a state without `G`.
- **Exchanges read on demand from Brightway's file** (no copy in the cache, nothing to wait for). An open database
  would then show the project's exchanges as they are now, not as they were when it was built: after an edit,
  amounts in the Exchanges panel would disagree with the tree until the rebuild. The cache keeps what the model was
  built from.
- **One SQLite file merged from the runs.** Copying 5M rows into one file costs 10 to 15 s after the parallel part;
  a model reads one run's file per activity anyway (`LCAModel._exchange_parts`: `(start, end, file)`). The comments,
  stored once per file, repeat across files: ecoinvent's cache grew from 336 to 344 MB.

A build stopped after *Results ready* (the app closed) leaves a database that opens without its exchanges
(`status()["exchanges"]` false). The next opening builds it again for them alone (no matrix read, nothing solved),
the database opening at once, if the project has not changed since: else it opens without them (they would be those
of the changed project), and its rebuild reads them.

Other costs of a build, cut on the way:
- `A.npz` and `B.npz` at deflate level 1 (`_save_npz`): Agribalyse's `B.npz` in 0.7 s and 24 MB, against 2 s and
  16 MB; read faster too.
- The databases with processes, found with the index on `(database, code)` for the databases of the graph only,
  not a scan of the whole `activitydataset` table (4.8 s on the 4.7 GB AESA project).

And of opening a built database: `sources._methods_stamp` listed each method's file with a `stat` (5,054 for the
projects here, 0.6 s), paid twice per opening; one `os.scandir` of the folder gives the same stamp in 0.05 s.

## Memory

Kept from the memory review, and applied within segments:
- **`G` solved in chunks into a memory-mapped file** (build, step 5).
- **Exchanges streamed** (build, step 4).
- **`U` not kept dense.** Compute `CF @ B[:, j]` for a node, and `CF[methods] @ B` for the methods an analysis
  uses. This saves the size of `G` in RAM.
- **A smaller default method list** at the first build of a project. `G` and `U` grow with processes × methods:
  1.9 GB each at 300k processes × 800 methods.

Not taken: memory-mapping `G` when opening (Windows cannot replace a mapped file, and reading a column of `G`
reads the whole file), and `G` in float32 (children + direct would add up to their parent within 1e-7, not 1e-15).

## Storage menu

A *Storage* icon in the Projects dialog (it was an entry of the top bar) opens a dialog: what the app and Brightway take on disk, and what can be removed.
Sizes are measured when the dialog opens: walking the folders is quick (27 GB of Brightway projects: 0.35 s), so
there is no job and nothing kept.

Done in phase 2 on the one-model caches, then in phase 3 on the project caches (`/api/storage`, `/api/storage/delete`,
`StorageDialog`): projects with their segments (*Rebuild*, *Drop results*, *Delete* with the segments that use it),
models made from files, obsolete caches (*Delete all*), left-overs (unfinished builds, folders of rebuilt segments
that no open database reads), downloads, Brightway projects (measured only), *Clear all app caches…*. Orphans
(a project or database that no longer exists) are marked on their row rather than listed apart.

| row | size | actions |
|---|---|---|
| **Project** (one per Brightway project) | Brightway folder (`lci/`, `processed/`, `search/`, `backups/`…) + app cache | *Show graph* (see below) |
| ├ segment (database, or databases built together) | its files, with `G` apart | *Rebuild*, *Drop results* (`G` only: solved again at next open), *Delete* |
| ├ project files | `flows.pkl`, `methods.pkl`, `unlinked.pkl` | |
| **Obsolete caches** | caches of another format, today's one-model folders | *Delete all* |
| **Orphans** | caches whose project or database no longer exists, `.part` folders of failed builds | *Delete all* |
| **Downloads** | `data/downloads`, ecoinvent_interface's cache (`%LOCALAPPDATA%\pylca\EcoinventInterface\cache`) | *Delete* |
| **Total** | | *Clear all app caches* |

Rules:
- **Delete a segment:** the dialog lists the segments that use it, which are deleted too, and asks to confirm. An
  open database must be closed first, as for a rebuild.
- **Brightway projects are only measured here.** They are deleted with *Delete project…* in *Projects*, which
  asks for the name.
- **Change the methods** (the project's ⚙ menu, *Impact methods…*): removing methods frees their columns of
  `G` without a solve; adding methods solves only their columns.

API: `GET /api/storage` (sizes, with a job when they must be counted again), `POST /api/storage/delete`
(`{"kind": "segment" | "results" | "obsolete" | "orphans" | "downloads" | "all", …}`).

## Database graph

**Entry point:** in *Projects*, the project's ⚙ menu (*Add database…*, *Rename…*, *Copy…*, *Delete project…*)
gets **Databases graph…**. It opens under the project's row, like the other project forms, and is also reached
from the storage menu.

**What it shows:**
- One node per database, with:
  - its number of processes, or of flows for a biosphere database (drawn smaller and greyed);
  - its state: built, outdated (rebuild required), open, locked (lock icon);
  - its cache size.
- One arrow from each database to each database it uses, with its number of exchanges when known.
- Databases built together (a loop) drawn inside one box, labelled *built together*.
- Layout in layers: the databases used by others at the bottom (biosphere, then ecoinvent), foreground databases
  above. This is the order segments are built in.
- Clicking a node selects that database in the list (open, build). Hovering a node highlights what it uses and
  what uses it.

**Data:**
- The graph comes from each project's `databases.json` (`depends`, and `number` for the size), which the server
  already reads. This is instant.
- The exchange counts and the check that `depends` is right come from a `GROUP BY output_database,
  input_database` on `lci/databases.db`. This runs in a job (~20 s for 5.8M exchanges) and is kept with the
  file's time.
- Arrows found in the exchanges but missing from `depends` are drawn dashed, with *"links found that the project
  metadata does not list"*.

Drawn with ECharts' graph series (already vendored). Done in phase 1 (`DatabaseGraph`, `/api/brightway/links`,
`sources.database_links`; the counts are kept in `data/cache/links.json`). Not done yet: cache sizes on the nodes,
clicking a node to select its database, a box around databases built together (a red ring marks them).

**The (i)** next to the title:

> An arrow goes from a database to one it uses. Changing a database rebuilds it and every database that uses it,
> directly or not: editing ecoinvent rebuilds all the databases built on it. Databases that use each other are
> built together. Lock the background databases you do not mean to change.

## Phases

Each phase is usable on its own.

1. **Database graph** from `depends`, with counts and the exchange check in a job. Independent of the cache;
   shows users how their projects are built.
2. **Storage menu** on today's caches: sizes, *Drop results*, *Delete*, obsolete and orphans, downloads, clear
   all. The `format` number goes in from here.
3. **Project cache**: segments, building with streamed exchanges and chunked `G`, opening the closed set. Today's
   caches become *obsolete* in the storage menu.
4. **Memory:** `U` not dense, segments shared between open databases, the method list per project in the ⚙ menu.

## Open questions

- Reading only a segment's own matrices (build step 3): measure the `bc.LCA` approach on the 300k-process
  project first.
- Products in a database of their own, and the multifunctional backend: check that a product always falls in the
  segment of the process that makes it.
- Datasets made in the app (Edit mode) change one foreground database. Its segment alone is rebuilt, so check
  how long *Apply* then takes on a large foreground database.

## Measured

2026-11-27, all the project's methods, peak working set of the process (psutil `peak_wset`). *Before*: the model of
one database and all it uses (HEAD of phase 2); *after*: the project cache. Times vary by ±20 % between runs on
this machine (a cold disk added 30 s once).

| | before | after |
|---|---|---|
| ecoinvent 3.12 (26.5k processes, 633 methods): build | 66 s, 2.0 GB | 88 s, 1.1 GB |
| ecoinvent 3.12: open (load, factorise, `G`) | 5.8 s, 1.26 GB (`G` solved: 3.5 s) | 2.3 s, 0.43 GB (`G` read) |
| ecoinvent 3.12 on disk | 477 MB | 483 MB |
| AESA, canne_sucre_reunion (9 processes on ecoinvent 3.10.1): build | 54 s, 1.7 GB | 101 s, 1.1 GB (ecoinvent included, once) |
| AESA, aval_sucre_reunion (uses the three others): build | 216 s, 5.7 GB | 178 s, 0.9 GB (Agribalyse and aval only) |
| AESA, canne_sucre_reunion rebuilt on its built background | 54 s, 1.7 GB | 6.5 s, 0.26 GB (aval above it solved again) |
| AESA, aval_sucre_reunion: open | 8.8 s, 1.56 GB | 3.3 s, 0.63 GB |
| AESA project on disk (4 databases) | 3.0 GB | 1.33 GB |

- `G` opened from segments equals that of a model built on its own: relative difference at most 2.2e-12 (aval,
  42,210 × 687) and 4.1e-13 (ecoinvent 3.12), on entries above 1e-9 of their column's largest.
- Agribalyse has 5.07M exchanges: reading them takes 160 s of the aval build (36 s to iterate them alone). The
  exchanges of ecoinvent 3.12 took 65 s streamed to SQLite, against 34 + 14 s read into a list then written: the
  join that maps the inputs to node ids costs some of it. Not looked into further.
- The build's peak no longer grows with the exchanges (streamed) nor with `G` (solved by chunks into a
  memory-mapped file): it is that of `bc.LCA` reading the matrices.

2026-11-28, results first and exchanges read by processes of their own (22 cores, 16 processes for the
exchanges; `scripts/time_loading.py`). The methods of the user's caches: 30 for ecoinvent 3.12, 25 for Agribalyse
3.1 (project `AESA_CC_sg_reunion`), each built alone into a scratch cache. *Open*: from `/api/load` to the first
tree of a built database, through the app's API.

| | before | after |
|---|---|---|
| ecoinvent 3.12: first build | 79 s (exchanges 65.6 s) | **10 to 14 s to open**, 11 to 15 s in all (16 processes; 13.7 and 17.9 s with 8) |
| Agribalyse 3.1: first build | 191 s (exchanges 177.3 s) | **11.5 s to open**, 28 s in all (16 processes; 36 s with 8) |
| ecoinvent 3.12: open a built database | 1.72 s | 0.41 s |
| Agribalyse 3.1: open a built database | 1.85 s | 0.66 s |
| build's peak memory (build process) | 0.57 / 0.80 GB | 0.57 / 0.79 GB |
| ecoinvent 3.12 cache on disk | 336 MB | 344 MB |

- Of what is left before the database opens: 3.4 s of imports (Brightway), 2.5 to 6 s of `bc.LCA` reading the
  matrices, 1.4 s of activities (ecoinvent), 0.5 to 4 s of `G`.
- With the processes reading the exchanges at the normal priority, ecoinvent's solve took 8.2 s instead of 3.9 s:
  they run below it.
- In the app (the page's *Build and open*), ecoinvent 3.12 showed its tree about 18 s after the click, Agribalyse
  about 15 s, with *Reading exchanges…* in the top bar until the exchanges came.
