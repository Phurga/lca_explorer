# Development environment

- Conda env **`lca_viewer`** (Python 3.11): `bw2data 4.7`, `bw2calc 2.5`,
  `bw2io 0.9.17`, `pypardiso`, `py7zr`, `pytest`; package installed editable
  (`pip install -e ".[brightway,fast,dev]"`).
  The user suggested reusing an `analyses` env, but no env of that name
  exists on this machine (existing: `ablca_raymond`, `aburg`,
  `env_qdassibat`, spyder envs), so a dedicated env was created.
- Brightway data: `export BRIGHTWAY2_DIR=$PWD/data/brightway` (kept inside the
  repo folder, gitignored, so this project does not touch other Brightway
  projects on the machine).
- Model cache: `data/cache/ei391_cutoff_all/` (all 762 methods, used by the
  app) and `data/cache/ei391_cutoff/` (26 methods, used by the validation
  script). Delete to rebuild.
- Extra packages: `fastapi`, `uvicorn`, `httpx`, `playwright` (screenshots).

```bash
conda activate lca_viewer
export BRIGHTWAY2_DIR=$PWD/data/brightway
python scripts/import_ecoinvent.py      # once
python scripts/validate_wheat.py        # phase 0 checks
python -m pytest tests -q               # unit tests on toy systems

# build the app's model (all methods, ~5 s) and serve it on http://127.0.0.1:8765
lca-viewer build --project lca_viewer_ei391 --database ecoinvent-3.9.1-cutoff --output data/cache/ei391_cutoff_all
lca-viewer serve --model data/cache/ei391_cutoff_all --default-root "market for wheat grain, feed|GLO"
```
