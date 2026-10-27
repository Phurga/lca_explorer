#!/usr/bin/env sh
# First-time setup on macOS and Linux: sh setup.sh [path/to/ecoinvent.7z, optional]
# Finds conda (or offers to install Miniforge), then runs scripts/bootstrap.py, which sets up
# the Python environment and the data.
cd "$(dirname "$0")" || exit 1
MINIFORGE_DIR="${MINIFORGE_DIR:-$HOME/miniforge3}"
export MINIFORGE_DIR
py=""

# conda's base Python: from there the setup also finds conda
for candidate in "${CONDA_EXE:+${CONDA_EXE%/bin/conda}/bin/python}" "$MINIFORGE_DIR/bin/python" \
                 "$HOME/miniconda3/bin/python" "$HOME/anaconda3/bin/python" \
                 "/opt/miniforge3/bin/python" "/opt/miniconda3/bin/python" "/opt/anaconda3/bin/python"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then py="$candidate"; break; fi
done

if [ -z "$py" ]; then
  echo "conda was not found on this computer. lca_viewer needs it to install Python and its packages."
  echo "The setup can install Miniforge (free; conda from conda-forge) in $MINIFORGE_DIR."
  echo "It needs no admin rights and leaves your other software as it is."
  echo
  printf "Install Miniforge now? [Y/n] "
  read -r answer
  case "$answer" in
    [nN]*) ;;
    *) sh scripts/install_miniforge.sh && py="$MINIFORGE_DIR/bin/python" ;;
  esac
fi

# Otherwise a Python 3.10+ already installed
if [ -z "$py" ]; then
  for candidate in python3 python; do
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then py="$candidate"; break; fi
  done
fi

if [ -z "$py" ]; then
  echo "No Python found. Install Miniforge from https://conda-forge.org/download/"
  echo "then run sh setup.sh again."
  exit 1
fi
exec "$py" scripts/bootstrap.py "$@"
