#!/usr/bin/env sh
# Install Miniforge (conda from conda-forge) for the current user, called by setup.sh.
# No admin rights needed; shell startup files and the default Python are left unchanged.
# Installs into $MINIFORGE_DIR, or ~/miniforge3.
dir="${MINIFORGE_DIR:-$HOME/miniforge3}"
name="Miniforge3-$(uname)-$(uname -m).sh"
installer="${TMPDIR:-/tmp}/$name"
echo "Downloading Miniforge (about 90 MB)..."
if ! curl -L --fail --progress-bar -o "$installer" "https://github.com/conda-forge/miniforge/releases/latest/download/$name"; then
  echo "The download failed. Check the internet connection, or install Miniforge yourself:"
  echo "https://conda-forge.org/download/"
  exit 1
fi
echo "Installing Miniforge in $dir (a few minutes)..."
bash "$installer" -b -p "$dir"
status=$?
rm -f "$installer"
if [ $status -ne 0 ] || [ ! -x "$dir/bin/python" ]; then
  echo "The installation failed. Install Miniforge yourself: https://conda-forge.org/download/"
  exit 1
fi
echo "Miniforge installed."
