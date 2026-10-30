# LCA Explorer user guide

What the [README](../README.md) leaves out: how to use the explorer, load and manage databases,
create datasets, add Agribalyse, run it on a shared server, set it up by hand and run the tests.

- [Using the explorer](#using-the-explorer)
- [Loading a database, e.g. from your Brightway projects](#loading-a-database-eg-from-your-brightway-projects),
  [managing projects](#managing-brightway-projects-from-the-app) (BAFU, imports) and
  [creating a dataset](#creating-a-dataset)
- [Adding Agribalyse](#adding-agribalyse-or-another-simapro-csv-database)
- [Shared server (SSH)](#using-the-explorer-on-the-shared-server-ssh)
- [Manual setup, step by step](#manual-setup-step-by-step)
- [Development](#development)

## Using the explorer

- **Tabs**: one process per tab (**+** for a new one). Drag a tab
  onto the edge of a pane to compare side by side or one above the other,
  or use the split buttons at the right of the tab bar; the side panel
  (charts, grouping) follows the pane clicked last and is hidden and shown with
  the side-panel icon (the last button of its tab bar).
- **Search** a process at the top of the page (what you type fills a first filter, *activity name contains*;
  add filters for the location, product or classification under the box; the **(i)** of a
  filter lists its values and what codes stand for); ↑/↓ and Enter to choose.
- Pick the **impact method** in the *Methods* panel of the left panel (the round mark is the
  active method, the ticks the ones shown together) and the group rules in its *Groups* tab (a rule
  set shared by all tabs). The **amount** of each process is in its *Processes* tab. The
  **cut-off** and the other options of the view (List / Tree, names, colours…) are in the pane that
  the sliders icon of the status bar (or **v**) opens. **Ctrl/⌘ + ←** and **→** hide or show the left and the
  right panel.
- **List / Tree** switch the left panel between a table and a node-and-edge
  view. Expand with ▸ or ±.
- **Group / Merge**: Ctrl/⌘-click sibling rows, then *Group* (bundle them
  under one row) or *Merge* (one process with their inventories added,
  common inputs summed); or use the *Group rules* tab (quick groups, group by
  location / activity type / ISIC, or your own filter).
- **Right-click** a process for its description, metadata and main emissions,
  and *Used by*: which activities use its product, and how much each takes
  in this supply chain.
- **×** on a row (hover) moves it to *others*, out of the tree and the chart;
  click an *others* row to see what it contains and bring processes back
  (also the way to show processes hidden by the cut-off). Changing the
  cut-off clears these choices; **Reset view** clears them, removes every
  group and merge (and *Group by product*) and collapses the tree. Both can be
  undone from the notice that appears.
- **Group by product** and **Full / Short / Custom names** (bar under the
  toolbar) apply to the tree and the chart.
- **Save ▾**: download the view as a file; the arrow menu loads a saved view
  back (or copies it as JSON).
- The **process contribution** chart shows exactly the rows visible in the tree:
  expand, collapse or group to change it. Export as PNG or CSV.
- *Flow contribution* splits the score by elementary flow (substance, or
  substance and compartment) over the whole supply chain; click one to list
  the processes emitting it.
- *Group contributions* answers "how much electricity / transport / … anywhere
  upstream?" using the named groups, and "how much N2O outside them?" with
  flow groups (*Add as a group* on a substance in *Flow contribution*). Click a group (e.g. *other*) to list
  the processes it is made of; right-click one and *Go to in tree* to open
  the tree where it sits (along its largest supply path). *Export CSV* saves
  the table of groups, and the list of a group's processes (all of them, not
  only those shown).

## Loading a database, e.g. from your Brightway projects

The app starts with no database loaded. **Load database** (top bar) lists:

- **Built models**, ready in a few seconds: every database loaded from Brightway before.
- **Brightway projects**: the projects of Brightway's data folder, the one
  Brightway 2.5 and Activity Browser use: `BRIGHTWAY2_DIR` if it is set,
  otherwise Brightway's default folder. The first time the app starts, it
  creates this folder with an empty `default` project if Brightway has never
  run on this computer, as Brightway itself would. *Other folder…* takes any
  other Brightway data folder (the one with a `projects.db` file). Pick a database and click **Build and open**: the app
  builds the matrices it reads from the database and every database it uses
  (one or two minutes for ecoinvent), keeps them in `data/cache/` and adds
  them to *Built models*. A database changed in Brightway since then (or a
  database it links to, or the impact methods) is offered as *Rebuild and
  open*; an open database is closed first. The app does not watch the
  projects: an open database stays as it was built until it is rebuilt.

Several databases can be open at once: switch with the picker in the top bar
(each keeps its own tabs), and **Close** the ones you no longer need to free
their memory. Things to know:

- Loading a database only reads its project. Projects made with Brightway 2
  (bw2data 3) are listed but can't be opened: upgrade them in Activity
  Browser 3 or Brightway 2.5 first.
- The impact methods are those of the project. Foreground databases work as
  they are: their model includes the background databases they use.

### Managing Brightway projects from the app

The Brightway projects section also changes projects. These are the same
projects Brightway and Activity Browser use, so changes show there too:

- **+ New project** in the folder: an empty project (its *Add database* then
  opens), or one from a Brightway backup: a ready project from
  [files.brightway.dev](https://files.brightway.dev) (**BAFU 2025**, the
  Swiss LCI database of BAFU/UVEK, 60 MB, with the Ecological Scarcity 2021
  methods; USEEIO, forwast, the ecoinvent biospheres), or a backup file
  (`.tar.gz`) you downloaded or made with `bw2io.backup_project_directory`.
- **+ Add database** on a project, from one of these sources:
  - **ecoSpold2**: an ecoSpold2 release (the ecoinvent `.7z` archive or the
    folder it was unpacked to; about 10 minutes for ecoinvent).
  - **SimaPro CSV**: a SimaPro CSV export (e.g. Agribalyse), linked to the
    background databases you tick (e.g. ecoinvent) through the *adapters* of
    Départ de Sentier's randonneur_data: PRé's SimaPro ↔ ecoinvent name
    correspondences and flow mappings. Those for the ticked databases are
    chosen for you; *Your own adapter* adds a correspondence file of your
    own (a JSON file in randonneur's format). If some inputs or flows can't
    be linked, nothing is written and they are listed, unless you chose
    *Import without them*.
  - **BAFU**: the Swiss LCI database of BAFU/UVEK, downloaded from
    [files.brightway.dev](https://files.brightway.dev) (60 MB) into the project,
    with its 21 Ecological Scarcity 2021 methods, named `BAFU-2025-v2, …`. It
    has its own elementary flows, so the project's other methods score nothing
    on it, and its methods score nothing on the other databases.
  - **ecoinvent**: a release downloaded with your ecoinvent account (log in
    once; kept in the system's credential store if you tick *Remember*).

  The form opens under the project's row. As in Activity Browser, tick
  **Add the default biosphere and impact methods** for a project without
  them, and pick their ecoinvent version: for ecoSpold2 it is read from the
  file name (`ecoinvent 3.10.1_cutoff…` → 3.10), for SimaPro CSV from the
  ecoinvent database ticked as background; otherwise choose it. Versions 3.8
  to 3.12 come from [files.brightway.dev](https://files.brightway.dev)
  (ecoinvent's elementary flows and impact methods of that version, about
  20 MB and 3 minutes, once per project; the methods are named
  `ecoinvent-<version>, …`); *ecoinvent 3.9, bundled with Brightway* is
  `biosphere3` and the 3.9 methods that come with bw2io, without download.
  For ecoinvent, they are the release's own biosphere and methods.
  Unticked, the elementary flows link to a biosphere database the project
  already has, and no methods are added.
- **Delete** a project and its folder (type its name to confirm). Models
  already built from it stay in the app.

Files can be given as a path on the computer running the app, or with
*Choose file…*, which copies the file into `data/uploads` for the import (handy
on the shared server). Each of these runs in the background like a build,
with its progress in the dialog.

## Creating a dataset

**New process** (*Edit* mode, in the top bar) enters a new process as in SimaPro, and writes
it into a Brightway project:

- **Save into**: a database of the project being edited, or a new database.
- **Process**: name, location, comment, and the documentation fields
  (technology, time period, geography, data sources, collection method, data
  treatment, allocation rules, verification, data entry by).
- **Products**: the reference product, or several **co-products** with their
  allocation percentages (adding up to 100). The process is then split into
  one allocated process per product with Brightway's `multifunctional`
  library, as for an imported SimaPro CSV: each product takes its percentage
  of every input and emission.
- **Inputs from technosphere**, **inputs from nature**, **emissions**, **waste
  to treatment** and **avoided products**: search the processes or flows of
  the project (all its databases, e.g. ecoinvent and its biosphere), then give
  each one its amount (for the product amounts above), an optional lognormal
  uncertainty (SD², as in SimaPro) and a comment.

*Stage the new process*, then *Apply and rebuild* to explore it. The form is kept
in the browser until you *Clear* it, so a dataset can be entered in several
sittings. A dataset with co-products goes into a database made by this panel
(Brightway's `multifunctional` backend); a single-output one can go into any
database of the project.

**View and Edit modes.** The switch in the top bar sets what the right panel is.
*View* is the explorer on the model built from the database: fast, precalculated.
*Edit* replaces the right panel with the edit panel for the project the open database
was built from. Nothing there writes to Brightway at once: each change is *staged*,
and *Apply* makes them all in one transaction. *Apply and rebuild* then builds the open model
again if the changes affect it (it keeps being served until the new one is ready) and goes back
to View. The number of staged changes shows on the Edit button; they are kept by the server
(`data/pending`), so they survive a reload.

- **Impacts that may be wrong are greyed out**: in Edit mode once staged changes affect the open
  database (theirs, and the databases using them), and in either mode when the project changed
  since the model was built. The top bar then shows **Rebuild required**, and the same goes for
  the other models listed in *Load database* (*outdated*).
- **View-only features are left out in Edit mode**: merging, custom names and replacing a
  supplier by an alternative only change how the view looks, not the data, so they are hidden
  and not applied (a grouping rule's *merge* shows as a plain group); they come back in View.
  Pins, cut-off and groups stay; they act on the impacts of the last build.
- **Double-click a value to change it** (the process's name or location, its reference product,
  its unit, an input's amount, the description…), in the tree or in a pop-up. It is staged
  like any other change. In the tree, the name is the process's own name (the label shown may
  be its product), and the amount is the input in the dataset of the process above, not the
  amount of this supply chain. The pop-up has an **Edit** button for the whole form.
- **Lock a database** (*Load database*, next to each database of a project): nothing in it can be
  edited, deleted or added to, whichever database is open when one of its processes is reached.
  The lock is kept by the server (`data/locks.json`) and enforced when changes are staged and
  again when they are applied. Copying from a locked database is fine.

- **New process** opens the form, empty, or filled from a copy of any process of
  the project (ecoinvent included): its products, exchanges, lognormal uncertainties and
  documentation. Negative technosphere inputs are read as waste to treatment when what
  supplies them treats waste (negative production, as in ecoinvent), else as avoided products.
- **Edit**, **Copy as new** and **Delete** act on the focused tab's process. Any process can
  be edited, ecoinvent's included (lock the databases you want to keep as they are). What the
  form cannot show (pedigree, formulas, uncertainties other than lognormal) is left as it is:
  exchanges are changed one by one.
- Edited processes keep their products' identity, so the datasets using them stay linked
  and their results follow. Co-products can be added to a process with one product, and
  removed (down to one) when no other dataset of the project uses them; a used product
  cannot be removed, nor a dataset deleted while another one uses a product of it (unless
  that one is staged for deletion, earlier in the list). The error names the datasets.
- A failed apply makes nothing: the error says which change, and they stay staged.
  A staged process cannot yet be used as an input by another staged one.

## Adding Agribalyse (or another SimaPro CSV database)

Agribalyse is imported into the same Brightway project as ecoinvent (it
uses ecoinvent processes), then loaded in the app next to ecoinvent. This needs ecoinvent 3.9.1
imported first, in a project of your Brightway folder (**+ Add database** in **Load database**). The commands
below use the project `lca_viewer_ei391` and the database `ecoinvent-3.9.1-cutoff`: replace them with yours.
Details and caveats:
[docs/data-import.md](data-import.md).

**1. Get the Agribalyse 3.2 SimaPro CSV.** There is no ready-made Brightway
download. Fill in the form on the
[SimaPro Agribalyse page](https://simapro.com/products/agribalyse-agricultural-database/)
(an ecoinvent licence is required). Unzip the export into `data/agribalyse/`,
e.g. `data/agribalyse/AGB32_final.CSV`.

**2. Import it**, linked to ecoinvent. Run this in the `lca_viewer`
environment (manual setup, step 2, and step 4 if you set `BRIGHTWAY2_DIR`). It
takes a few minutes and needs internet access (unit definitions are fetched
online).

```bash
lca-viewer import simapro-csv data/agribalyse/AGB32_final.CSV --project lca_viewer_ei391 --database agribalyse-3.2 --link-to ecoinvent-3.9.1-cutoff
```

When some inputs or emissions can't be linked, nothing is written and they
are listed in `data/import-agribalyse-3.2-unlinked.csv`. Check the list, then
re-run with `--unlinked drop` to import without them. In the app, choose this
before importing (*Import without them, and list them*), so that the export
is read only once.

The SimaPro export of Agribalyse carries the ecoinvent processes it uses as
aggregated copies (`…| Cut-off, S - Copied from Ecoinvent`, about 1,800 of
them, with 96 % of the file's elementary flows). With ecoinvent ticked as a
background database, they are replaced by links to the real ecoinvent
processes of the project, so the tree shows ecoinvent's supply chains.
Elementary flows are linked with Flowmapper's and ecoinvent's
correspondences; with Agribalyse 3.1 and ecoinvent 3.8, about 1,500 flows
that ecoinvent 3.8 lacks remain (pesticides in forest soil, "Biological pest
controls", "Water, green water", 9 % of Agribalyse's own emission exchanges):
the project's impact methods could not characterise them, as they only cover
flows of its biosphere. Details:
[docs/data-import.md](data-import.md).

**3. Open it** in the app: **Load database**, folder *lca_viewer*, project
`lca_viewer_ei391`, then **Build and open** next to `agribalyse-3.2`. The
model includes the ecoinvent activities that Agribalyse uses. Without the
app, `lca-viewer build --project lca_viewer_ei391 --database agribalyse-3.2
--output data/cache/agb32` builds the same model, and `lca-viewer serve
--model data/cache/agb32` serves it.

Each open database takes its own memory (the Agribalyse model also holds
ecoinvent). If memory is short, close ecoinvent: the Agribalyse model covers
both databases.

## Using the explorer on the shared server (SSH)

For people with access to the team server, where the app already runs with
ecoinvent 3.9.1 cut-off. It is only reachable from the server itself
(`127.0.0.1:8765`), so you view it in your own browser through an SSH tunnel.
Nothing needs to be installed on your computer besides an SSH client
(built into macOS, Linux and Windows 10+ PowerShell).

Replace `<user>`, `<host>` and `<ssh-port>` with the values you normally use
to log in to the server.

**1. Open a tunnel** from your computer and keep this terminal open:

```bash
ssh -L 8765:127.0.0.1:8765 -p <ssh-port> <user>@<host>
```

**2. In that SSH session, check whether the explorer is already running**:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/
```

`200` means it is running: go to step 4. Anything else: start it (step 3).

**3. Start it** (takes ~5 s; it keeps running after you log out):

```bash
nohup /home/user1/fileshare/adrienburg/lca_viewer/scripts/serve.sh > /tmp/lca_viewer.log 2>&1 &
```

**4. Open http://localhost:8765** in your browser on your computer.

Several people can use the same running server at the same time: each
browser keeps its own tree, groups and charts.

- **Port 8765 already used on your computer?** Pick another local port, e.g.
  `ssh -L 8800:127.0.0.1:8765 …` and open http://localhost:8800.
- **Stop the server** (from an SSH session):
  `pkill -f "lca-viewer serve"` — this stops it for everyone.
- **Logs**: `/tmp/lca_viewer.log`.
- **Account**: log in as `user1` (the only account on this server). The
  script uses files under `/home/user1` (code, the `lca_viewer` conda env,
  the cached ecoinvent model), which other accounts cannot read.

## Manual setup, step by step

What `lca_explorer.cmd` does, one command at a time. Run every command from the repository folder.

### 1. Install conda

Install Miniforge from [conda-forge.org/download](https://conda-forge.org/download/)
(Anaconda or Miniconda work too), then open a terminal where conda works: the *Miniforge Prompt* on Windows,
a new terminal on macOS and Linux.

### 2. Create the Python environment

```bash
conda create -n lca_viewer -c conda-forge python=3.12 pip
conda activate lca_viewer
pip install -e ".[brightway,fast]" fastapi uvicorn py7zr
```

On a Mac with an Apple chip, use `".[brightway]"` instead of `".[brightway,fast]"` (the fast solver needs an
Intel processor).

### 3. Choose where Brightway keeps its data (optional)

The app uses Brightway's data folder, the one Brightway and Activity Browser use: `BRIGHTWAY2_DIR` if you set
it, otherwise Brightway's default folder. To keep these projects apart, e.g. in `data/brightway`, set
`BRIGHTWAY2_DIR` in **every new terminal** before `lca-viewer serve`.

```powershell
New-Item -ItemType Directory -Force data\brightway; $env:BRIGHTWAY2_DIR = "$PWD\data\brightway"
```

```bash
mkdir -p data/brightway && export BRIGHTWAY2_DIR=$PWD/data/brightway
```

### 4. Start the app

```bash
lca-viewer serve --open
```

The app opens in your browser at **http://localhost:8765**; stop it with Ctrl+C. It starts with no database:
load one from **Load database**, or import one with **+ Add database** (see above). Next time, only this step is
needed, in a terminal where the `lca_viewer` environment is active.

### If something goes wrong

- **`lca-viewer` is not recognised**: the environment isn't active. Run `conda activate lca_viewer` (step 2).

## Development

Install the test tools too (`pip install pytest httpx`), then:

```bash
python -m pytest tests -q
```

Add `--reload` to `lca-viewer serve` to restart it when Python code changes;
front-end files (`src/lca_viewer/static/`) only need a browser reload
(Ctrl+Shift+R). More in [environment.md](environment.md) and
[README.md](README.md).
