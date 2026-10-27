#!/usr/bin/env sh
# Start the explorer on macOS and Linux (after setup.sh) and open it in the browser. Stop with Ctrl+C.
cd "$(dirname "$0")" || exit 1
if [ ! -f data/python.txt ]; then
  echo "Run sh setup.sh first."
  exit 1
fi
exec "$(cat data/python.txt)" -m lca_viewer.cli serve --open "$@"
