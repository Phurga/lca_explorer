# Project cache, storage menu and database graph

Status (2026-11): phases 1 (database graph) and 2 (storage menu, on today's caches) **done**; phases 3 and 4
**design, not implemented**. Replaces the cache of one model per database described in
[data-import.md](data-import.md#loading-from-the-app) and the calculation of `G` at first open described in
[calculation-engine.md](calculation-engine.md#model-independence).

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
  project.json      format version, Brightway origin, methods, segments in build order, the graph
  flows.pkl         elementary flows of the segments built so far (appended to)
  methods.pkl       factors of the methods on those flows (extended when flows are appended)
  unlinked.pkl      factors of the methods on flows outside them
  segments/<slug>-<hash>/
    segment.json    its databases, what it uses, its state (sources.database_state) and its version
    A.npz B.npz     its columns: rows of its own products by position, other segments' products by node id;
                    flows by node id
    G.npy G.json    its rows of G (its products × the project's methods), and what they were solved from
    activities.pkl details.pkl exchanges.sqlite
```

`project.json`'s `format` is a number, increased when the files change meaning. A cache of another format
is listed as *obsolete* in the storage menu, never opened. Models made from files (not from a Brightway project)
keep today's one-folder model.

## Building

`lca-viewer build --project P --database X` (separate process, as today):

1. Read the graph of the databases X uses. Group the loops.
2. For each segment X needs, in order (used first), check its state:
   - **Missing, or its databases changed:** build it (steps 3 to 5).
   - **A segment it uses was rebuilt:** solve its `G` rows again (step 5 only). If a node id it links to no
     longer exists, its database changed too, so build it in full.
   - **Otherwise:** keep it.
3. Read its matrices: as today (`bc.LCA` on one of its nodes, `load_lci_data`), keeping its own columns.
   - This still reads the matrices of the databases it uses (6 s for ecoinvent). It is kept for now because the
     sign conventions and multifunctional allocation then stay Brightway's own.
   - Reading the segment's processed datapackage alone would be faster, but would redo what bw2calc does.
     Measure on a 300k-process project before deciding.
4. Read its activities, details and exchanges, **streamed** to `exchanges.sqlite` in batches instead of held as a
   list. The build's memory then stays flat. Its new elementary flows are appended to `flows.pkl`, and the
   methods' factors on them to `methods.pkl`.
5. Solve its `G` rows: factorise `A_ss`, then `A_ssᵀ G_s = U_sᵀ − A_usᵀ G_u`, **in chunks of methods** written
   into a memory-mapped `G.npy`. The peak is then the size of `G_s` plus one chunk, not ~6.7 × `G` as today.

Products stored apart from their process (SimaPro imports) belong to the segment of the process that produces
them, matched as `brightway._product_rows` does today.

## Opening

1. The closed set of X: its segment and those it uses, from `project.json`.
2. Preallocate `A`, `B`, `G` and the tables, and read each segment's files into place. Concatenating copies (a
   `vstack` of `G`) would double the memory for a moment: 1.9 GB at 300k processes × 800 methods.
3. Map the node ids of links between segments to positions.
4. The factorisation that `supply()` and flow-group analyses need is made on first use, one per segment.

Later step: two databases of one project open at once share their common segments in memory.

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

A *Storage* entry in the top bar opens a dialog: what the app and Brightway take on disk, and what can be removed.
Sizes are measured when the dialog opens: walking the folders is quick (27 GB of Brightway projects: 0.35 s), so
there is no job and nothing kept.

Done in phase 2, on today's one-model caches (`/api/storage`, `/api/storage/delete`, `StorageDialog`):
- built models with their results (`G`), *Drop results* and *Delete* (closed first, removed from `models.json`), and
  *Delete all built models…*;
- left-overs in `data/cache`: `.part` / `.old` folders and empty folders (deleted in bulk), and model folders that
  `models.json` does not list (deleted one by one only: a model served with `--model` is one);
- downloads (`data/downloads`, ecoinvent_interface's releases: 12 GB on the development machine);
- Brightway projects, measured only.

The `format` number comes with phase 3, when the cache files change.

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
