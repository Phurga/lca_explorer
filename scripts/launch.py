"""Start LCA Explorer, setting it up first if that was never done.

Run by lca_explorer.cmd with whatever Python it found: the one the setup recorded in
data/python.txt, else conda's base Python or any Python 3.7+, so it uses the standard library only.

    lca_explorer.cmd              set up if needed, then start the app and open the browser
    lca_explorer.cmd setup        run the setup again (e.g. to choose another Python environment)
    lca_explorer.cmd --port 8800  other arguments go to ``lca-viewer serve``
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap  # noqa: E402  (same folder)


def app_python():
    """The Python of the installed app, from the last setup, or None if there is none."""
    if not bootstrap.LAST.exists():
        return None
    python = Path(bootstrap.LAST.read_text(encoding="utf-8").strip())
    info = bootstrap.probe(python) if python.exists() else None
    return python if info and info[1] else None


def main(args) -> int:
    python = app_python()
    wants_setup = args[:1] == ["setup"]
    serve_args = [] if wants_setup else args
    if python is None or wants_setup:
        if python is None:
            print("LCA Explorer is not set up yet: setting it up first.\n")
        if bootstrap.main(args[1:] if wants_setup else []):
            return 1
        python = app_python()
        if python is None:
            print("\nThe setup did not finish: run lca_explorer again to resume it.")
            return 1
    print("\nStarting LCA Explorer...")
    try:
        return subprocess.run([str(python), "-m", "lca_viewer.cli", "serve", "--open", *serve_args],
                              cwd=ROOT, env=bootstrap.env_vars(python)).returncode
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
