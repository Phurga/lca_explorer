# lca_viewer

Process explorer for LCA results: a SimaPro-like supply chain tree you can
expand, collapse and group (manually, by theme or by query), and contribution
charts built from what the tree shows. Reads Brightway 2.5 projects
(ecoinvent ecoSpold2 via Brightway import).

Status and design decisions: [docs/](docs/README.md). Development setup:
[docs/environment.md](docs/environment.md).

## Using the explorer on the server (SSH access)

The app runs on the server and is only reachable from the server itself
(`127.0.0.1:8765`). You view it in your own browser through an SSH tunnel.
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

### Good to know

- **Port 8765 already used on your computer?** Pick another local port, e.g.
  `ssh -L 8800:127.0.0.1:8765 …` and open http://localhost:8800.
- **Stop the server** (from an SSH session):
  `pkill -f "lca-viewer serve"` — this stops it for everyone.
- **Logs**: `/tmp/lca_viewer.log`.
- **Account**: log in as `user1` (the only account on this server). The
  script uses files under `/home/user1` (code, the `lca_viewer` conda env,
  the cached ecoinvent model), which other accounts cannot read.
- **Licence**: the database is ecoinvent 3.9.1 cut-off. Only people covered
  by an ecoinvent licence may use the explorer, and results must not be
  shared beyond what that licence allows.

### Using it

- **Search** a functional unit in the top bar (words of the name or product,
  a location code like `fr`, or *Filters*); ↑/↓ and Enter to choose.
- Pick the **impact method** and a display **cut-off** next to it.
- **List / Tree** switch the left panel between a table and a node-and-edge
  view. Expand with ▸ or ±; *Expand above x %* opens all hotspots.
- **Group / Merge**: Ctrl/⌘-click sibling rows, then *Group* (bundle them
  under one row) or *Merge* (one process with their inventories added,
  common inputs summed); or use the *Grouping* tab (quick groups, group by
  location / activity type / ISIC, or your own filter).
- **Right-click** a process for its description, metadata and main emissions.
- **×** on a row (hover) moves it to *others*, out of the tree and the chart;
  click an *others* row to see what it contains and bring processes back
  (also the way to show processes hidden by the cut-off). Changing the
  cut-off clears these choices; **Reset view** clears them and collapses the
  tree. Both can be undone from the notice that appears.
- **Full / Short / Custom names** (bar under the toolbar) apply to the tree
  and the chart.
- **Save ▾**: download the view as a file; the arrow menu loads a saved view
  back (or copies it as JSON).
- The **contribution chart** shows exactly the rows visible in the tree:
  expand, collapse or group to change it. Export as PNG or CSV.
- *Whole supply chain* answers "how much electricity / transport / … anywhere
  upstream?" using the named groups.
