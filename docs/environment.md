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
- Model cache: `data/cache/ei391_cutoff/` (delete to rebuild).

```bash
conda activate lca_viewer
export BRIGHTWAY2_DIR=$PWD/data/brightway
python scripts/import_ecoinvent.py      # once
python scripts/validate_wheat.py        # phase 0 checks
python -m pytest tests -q               # unit tests on toy systems
```
