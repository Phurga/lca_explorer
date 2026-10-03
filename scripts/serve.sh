#!/usr/bin/env bash
# Start the explorer on http://127.0.0.1:8765 (no conda activation needed).
# From your own computer, tunnel the port first:  ssh -N -L 8765:127.0.0.1:8765 <this machine>
cd "$(dirname "$0")/.."
exec /home/user1/miniconda3/envs/lca_viewer/bin/lca-viewer serve \
  --model data/cache/ei391_cutoff_all \
  --title "ecoinvent 3.9.1 cut-off" \
  --default-root "market for wheat grain, feed|GLO" \
  --port "${PORT:-8765}" "$@"
