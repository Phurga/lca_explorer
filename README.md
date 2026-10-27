# lca_viewer

Process explorer for LCA results: a SimaPro-like supply chain tree you can
expand, collapse and group (manually, by theme or by query), and contribution
charts built from what the tree shows. It runs on your own computer and opens
in your browser. It reads ecoinvent ecoSpold2 releases and SimaPro CSV
exports (e.g. Agribalyse), imported with Brightway 2.5, and the databases of
your own Brightway 2.5 projects (Activity Browser 3 included), chosen with
**Load database** in the app.

- [Quick start](#quick-start-install-and-run-the-app): install the app, with
  ecoinvent if you have the archive, and open it in your browser.
- [Manual setup](#manual-setup-step-by-step): the same, one command at a time.
- [Using the explorer](#using-the-explorer)
- [Loading a database, e.g. from your Brightway projects](#loading-a-database-eg-from-your-brightway-projects)
- [Adding Agribalyse](#adding-agribalyse-or-another-simapro-csv-database)
- [Shared server (SSH)](#using-the-explorer-on-the-shared-server-ssh)
- Design decisions and status: [docs/](docs/README.md)

## Quick start: install and run the app

You don't need Python or any programming tools installed: the setup installs
everything the app needs. You need:

- **Databases to explore**, either or both of:
  - **your Brightway 2.5 projects** (made with Brightway or Activity Browser
    3): nothing to prepare, the app lists them under **Load database**;
  - **the ecoinvent ecoSpold2 archive**, downloaded from
    [ecoinvent.org](https://ecoinvent.org) with your licence, e.g.
    `ecoinvent 3.9.1_cutoff_ecoSpold02.7z`, which the setup imports for you.
    The impact methods it installs are those of ecoinvent **3.9**, so 3.9.1
    is the tested version (cut-off or another system model). With another
    ecoinvent version, the inventory imports but the impact factors may not
    match it.

  The archive is optional: skip it if your Brightway projects already have
  the databases you need. You can add it later by running the setup again.
- **Access to the code.** The GitHub repository is private: ask its owner to
  add your GitHub account, or to send you the code as a ZIP file.
- **About 10 GB of free disk space** with ecoinvent (the unpacked archive
  takes 2.6 GB and is deleted after the import; 2 GB without it), an internet
  connection, and about 30 minutes with ecoinvent (10 without), mostly
  waiting.

### 1. Get the code

On the [GitHub page](https://github.com/Phurga/lca_viewer), signed in,
click the green **Code** button, then **Download ZIP**. Unzip it somewhere
easy to find, e.g. your Documents folder. On Windows: right-click the ZIP,
*Extract All…*, then open the extracted folder; the setup doesn't work from
inside the ZIP. You now have a folder named `lca_viewer` (or
`lca_viewer-main`) with `setup.cmd`, `setup.sh` and `README.md` in it.

If you use git, `git clone https://github.com/Phurga/lca_viewer.git` does the
same.

### 2. Run the setup

**Windows**: in that folder, double-click **`setup.cmd`**. A black window
opens and asks the questions below. You can also drag the ecoinvent `.7z`
file onto `setup.cmd`, to skip one of them. If Windows says it *protected
your PC*, click *More info*, then *Run anyway*: this warning appears for any
script downloaded from the internet.

**macOS**: open *Terminal* (in Applications → Utilities). Type `cd ` with a
space after it, drag the `lca_viewer` folder into the Terminal window, and
press Enter. Then type the following command and press Enter:

```bash
sh setup.sh
```

**Linux**: in a terminal, in that folder, run `sh setup.sh`.

The setup asks up to four things. Press Enter to take the suggested answer
(shown in brackets), which is the right one in most cases:

1. **Install Miniforge now? [Y/n]**: this is only asked if conda isn't on your
   computer. Miniforge is the free, open-source distribution of conda (the
   tool that installs Python and scientific packages). It goes in your user
   folder (`miniforge3`), needs no admin rights, and leaves the rest of your
   computer as it is. It takes about 5 minutes. If you would rather install it
   yourself, answer `n`, get it from
   [conda-forge.org/download](https://conda-forge.org/download/), then run
   the setup again.
2. **Which Python environment to use**: by default it creates a new conda
   environment named `lca_viewer`, kept apart from any other Python on your
   computer. If you have run the setup before, it suggests the one you used
   then.
3. **Where the ecoinvent archive is** (optional): drag the `.7z` file into
   the window and press Enter, or paste its path. A folder where you already
   unpacked it works too. **Press Enter without a path to skip ecoinvent**:
   the app then starts with no database, and you load those of your
   Brightway projects from it.
4. **The ecoinvent version and system model** (only with an archive): these
   are read from the file name, so you only need to confirm them.

Then it does the rest on its own. It installs Python and the packages in the
new environment, which takes about 5 minutes. With an archive, it unpacks
it, imports it into Brightway (about 10 minutes) and builds the model the app
reads. Then it opens the app in your browser. If it stops halfway, run it again: the steps
already done are skipped.

### 3. Start the app next time

- **Windows**: double-click **`start.cmd`**.
- **macOS / Linux**: run `sh start.sh` in the folder, as in step 2.

The app opens in your browser at http://localhost:8765, on the list of
databases it can load: click **Open** next to ecoinvent (a few seconds), or
**Build and open** next to a database of your Brightway projects. It
runs as long as that window is open: close the window, or press Ctrl+C in it,
to stop the app.

### If something goes wrong

- **The Miniforge download or installation failed**: install it yourself
  from [conda-forge.org/download](https://conda-forge.org/download/) (on
  Windows, *Just Me* is the right choice in the installer), then run the setup
  again.
- **Installing the packages failed**: check the internet connection, run the
  setup again, and choose *Create a new conda environment*.
- **The setup window closes right away** on Windows: open the folder, type
  `cmd` in the address bar and press Enter, then type `setup.cmd` and press
  Enter. The window then stays open, and you can read the message.
- **Port 8765 already in use**: start the app on another port with
  `start.cmd --port 8800` (or `sh start.sh --port 8800`), then open
  http://localhost:8800.
- **Another ecoinvent version or system model**: run the setup again with
  the other archive. Each release gets its own model, listed in **Load
  database**; open several and switch between them in the top bar.

### Removing it

Delete the `lca_viewer` folder: the imported data and the models are in it,
in `data/`. Then delete the `lca_viewer` Python environment:

- **Windows**: open the *Miniforge Prompt* from the Start menu and run
  `conda env remove -n lca_viewer`.
- **macOS / Linux**: run `~/miniforge3/bin/conda env remove -n lca_viewer`.

Miniforge itself stays installed: other projects can use it, and the setup
reuses it if you install lca_viewer again.

## Manual setup, step by step

The commands that the setup runs for you, for when you prefer to run each one
yourself. `lca-viewer setup` does steps 3 to 7 in one go, from an
environment that is already active. Run every command from the `lca_viewer`
folder.

### 1. Install conda

Install Miniforge from [conda-forge.org/download](https://conda-forge.org/download/)
(Anaconda or Miniconda work too). Then open a terminal where conda works:

- **Windows**: the *Miniforge Prompt*, from the Start menu. Then go to the
  folder with `cd`, e.g. `cd %USERPROFILE%\Documents\lca_viewer`.
- **macOS / Linux**: a new terminal window. The installer offers to set up
  conda in it.

### 2. Create the Python environment

```bash
conda create -n lca_viewer -c conda-forge python=3.12 pip
```

```bash
conda activate lca_viewer
```

```bash
pip install -e ".[brightway,fast]" fastapi uvicorn py7zr
```

On a Mac with an Apple chip, use `".[brightway]"` instead of
`".[brightway,fast]"` (the fast solver needs an Intel processor).

### 3. Unpack the ecoinvent archive

Put the unpacked files in `data/ei391_cutoff/` inside the `lca_viewer` folder
(`data/` is never committed to git, so your licensed data stays local).
Replace the path with where your download is:

```bash
python -m py7zr x "C:/Users/you/Downloads/ecoinvent 3.9.1_cutoff_ecoSpold02.7z" data/ei391_cutoff
```

7-Zip or another unpacking tool works too. Check that you now have a
`data/ei391_cutoff/datasets/` folder full of `.spold` files.

### 4. Tell Brightway where to keep its data

The import goes into a Brightway folder inside the project, so it doesn't
mix with any other Brightway projects on your computer. Do this in **every
new terminal** before `lca-viewer import` or `lca-viewer build`.

In PowerShell:

```powershell
New-Item -ItemType Directory -Force data\brightway; $env:BRIGHTWAY2_DIR = "$PWD\data\brightway"
```

In the Anaconda Prompt (cmd):

```bat
mkdir data\brightway & set BRIGHTWAY2_DIR=%CD%\data\brightway
```

In bash (macOS, Linux):

```bash
mkdir -p data/brightway && export BRIGHTWAY2_DIR=$PWD/data/brightway
```

### 5. Import the database

```bash
lca-viewer import ecospold2 data/ei391_cutoff/datasets --project lca_viewer_ei391 --database ecoinvent-3.9.1-cutoff
```

This takes about 10 minutes: the first run also sets up the biosphere and
the 762 impact methods. It ends with `imported ecoinvent-3.9.1-cutoff into
project lca_viewer_ei391`. You can then delete `data/ei391_cutoff/`.

The project and database names are yours to choose, but keep the ecoinvent
version and system model in the database name (`3.9.1`, `cutoff`): they are
used to link Agribalyse to it later.

### 6. Build the model

```bash
lca-viewer build --project lca_viewer_ei391 --database ecoinvent-3.9.1-cutoff --output data/cache/ei391_cutoff_all
```

This takes under a minute and writes the matrices the app reads
(about 200 MB) to `data/cache/ei391_cutoff_all/`.

### 7. Start the app

```bash
lca-viewer serve --model data/cache/ei391_cutoff_all --title "ecoinvent 3.9.1 cut-off" --default-root "market for wheat grain, feed|GLO" --open
```

The app opens in your browser after a few seconds, at
**http://localhost:8765**. Stop it with Ctrl+C in the terminal.

`--default-root` is the functional unit shown when the app opens, as
`activity name|location`. Leave it out to start from a search. Without
`--model`, the app starts with no database and offers the models listed in
`data/models.json` (by `lca-viewer setup`) and your Brightway projects under
**Load database**.

**Next time**, only this step is needed: open a terminal in the
`lca_viewer` folder, run `conda activate lca_viewer`, then the
`lca-viewer serve …` command above. `BRIGHTWAY2_DIR` is not needed to serve.

### If something goes wrong

- **`lca-viewer` is not recognised**: the environment isn't active. Run
  `conda activate lca_viewer` (step 2).
- **`warning: BRIGHTWAY2_DIR is not set`** during the import: step 4 was not
  done in this terminal, so the import went to Brightway's default folder.
  Stop it with Ctrl+C, do step 4, and import again.
- **`Database 'ecoinvent-3.9.1-cutoff' already exists`**: it is already
  imported. Go on to step 6. The message shows how to delete it if you want
  to import it again.

## Using the explorer

- **Tabs**: one functional unit per tab (**+** for a new one). Drag a tab
  onto the edge of a pane to compare side by side or one above the other,
  or use the split buttons at the right of the tab bar; the side panel
  (charts, grouping) follows the pane clicked last and collapses with ».
- **Search** a functional unit at the top of the tab (words of the name or product,
  a location code like `fr`, or *Filters*); ↑/↓ and Enter to choose.
- Pick the **impact method** and a display **cut-off** next to it.
- **List / Tree** switch the left panel between a table and a node-and-edge
  view. Expand with ▸ or ±.
- **Group / Merge**: Ctrl/⌘-click sibling rows, then *Group* (bundle them
  under one row) or *Merge* (one process with their inventories added,
  common inputs summed); or use the *Grouping* tab (quick groups, group by
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
- *Whole supply chain* answers "how much electricity / transport / … anywhere
  upstream?" using the named groups, and "how much N2O outside them?" with
  flow groups (*Add as a group* on a substance in *Flow contribution*). Click a group (e.g. *other*) to list
  the processes it is made of; right-click one and *Go to in tree* to open
  the tree where it sits (along its largest supply path).

## Loading a database, e.g. from your Brightway projects

The app starts with no database loaded. **Load database** (top bar) lists:

- **Built models**, ready in a few seconds: ecoinvent from the setup, and
  every database loaded from Brightway before.
- **Brightway projects**: the projects of lca_viewer's own Brightway folder
  (`data/brightway`), of Brightway's default folder (where Brightway 2.5 and
  Activity Browser keep them unless told otherwise) and of `BRIGHTWAY2_DIR`.
  *Other folder…* takes any other Brightway data folder (the one with a
  `projects.db` file). Pick a database and click **Build and open**: the app
  builds the matrices it reads from the database and every database it uses
  (one or two minutes for ecoinvent), keeps them in `data/cache/` and adds
  them to *Built models*. A database changed in Brightway since then (or a
  database it links to, or the impact methods) is offered as *Rebuild and
  open*; an open database is closed first. The app does not watch the
  projects: an open database stays as it was built until it is rebuilt.

Several databases can be open at once: switch with the picker in the top bar
(each keeps its own tabs), and **Close** the ones you no longer need to free
their memory. Things to know:

- Your projects are only read, never changed. Projects made with Brightway 2
  (bw2data 3) are listed but can't be opened: upgrade them in Activity
  Browser 3 or Brightway 2.5 first.
- The impact methods are those of the project. Foreground databases work as
  they are: their model includes the background databases they use.
- Other formats (an ecoinvent ecoSpold2 release, a SimaPro CSV export) are
  imported into a Brightway project first, e.g. Agribalyse below.

## Adding Agribalyse (or another SimaPro CSV database)

Agribalyse is imported into the same Brightway project as ecoinvent (it
uses ecoinvent processes), then loaded in the app next to ecoinvent. This needs ecoinvent 3.9.1
imported first, with the quick start or the manual setup. Details and caveats:
[docs/data-import.md](docs/data-import.md).

**1. Get the Agribalyse 3.2 SimaPro CSV.** There is no ready-made Brightway
download. Fill in the form on the
[SimaPro Agribalyse page](https://simapro.com/products/agribalyse-agricultural-database/)
(an ecoinvent licence is required). Unzip the export into `data/agribalyse/`,
e.g. `data/agribalyse/AGB32_final.CSV`.

**2. Import it**, linked to ecoinvent. Run this in the `lca_viewer`
environment, with `BRIGHTWAY2_DIR` set (manual setup, steps 2 and 4). It
takes a few minutes and needs internet access (unit definitions are fetched
online).

```bash
lca-viewer import simapro-csv data/agribalyse/AGB32_final.CSV --project lca_viewer_ei391 --database agribalyse-3.2 --link-to ecoinvent-3.9.1-cutoff
```

When some inputs or emissions can't be linked, nothing is written and they
are listed in `data/import-agribalyse-3.2-unlinked.csv`. Check the list, then
re-run with `--unlinked drop` to import without them.

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

## Licence

lca_viewer is licensed under the [PolyForm Strict License 1.0.0](LICENSE):
you may use it for noncommercial purposes only, and you may not distribute
it, change it or make new works based on it. For any other use, ask the
copyright holder. The libraries in `src/lca_viewer/static/vendor/` keep their
own licences (MIT, Apache-2.0).

### Licence of the data

ecoinvent and Agribalyse are licensed. Only people covered by an ecoinvent
licence may use the explorer on them, and results must not be shared beyond
what that licence allows. Keep the archives, the Brightway project and the
built models in `data/`, which git ignores: never commit or publish them.

## Development

Install the test tools too (`pip install pytest httpx`), then:

```bash
python -m pytest tests -q
```

Add `--reload` to `lca-viewer serve` to restart it when Python code changes;
front-end files (`src/lca_viewer/static/`) only need a browser reload
(Ctrl+Shift+R). More in [docs/environment.md](docs/environment.md) and
[docs/README.md](docs/README.md).
