"""Start LCA Explorer, setting it up first if that was never done.

Run by lca_explorer.cmd with whatever Python it found: the one the setup recorded in
data/python.txt, else conda's base Python or any Python 3.7+, so it uses the standard library only.

    lca_explorer.cmd              set up if needed, then start the app and open the browser
    lca_explorer.cmd setup        run the setup again (e.g. to choose another Python environment)
    lca_explorer.cmd --port 8800  other arguments go to ``lca-viewer serve``

The app runs in the background, its output in data/server.log: the terminal closes once it
answers, and the app stops when its last browser tab closes (or with Shut down in the page).
Started again while it runs, lca_explorer opens a tab on it instead.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap  # noqa: E402  (same folder)

LOG = ROOT / "data" / "server.log"
PORTS = range(8765, 8815)  # where ``lca-viewer serve`` looks for a free port


def app_answers(url) -> bool:
    """Whether LCA Explorer, started by lca_explorer (it stops with its tabs), answers at ``url``
    (127.0.0.1, not localhost: Windows tries IPv6 first, where the server is not)."""
    try:
        with urllib.request.urlopen(url + "/api/app", timeout=0.5) as r:
            return json.loads(r.read().decode("utf-8")).get("stops_with_browser") is True
    except (OSError, ValueError, AttributeError):  # nothing there, or another program
        return False


def port_used(port) -> bool:
    """Whether a program listens on ``port``: binding it fails (at once, whereas on Windows asking a
    port where nothing listens takes a second)."""
    import socket

    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def running_app():
    """The port of an LCA Explorer that lca_explorer started and that still runs, or None."""
    for port in PORTS:
        if port_used(port) and app_answers("http://127.0.0.1:%d" % port):
            return port
    return None


def start_in_background(python, serve_args):
    """``lca-viewer serve --open`` with no window, writing to :data:`LOG`; closing the terminal
    does not stop it."""
    env = bootstrap.env_vars(python)
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "w", encoding="utf-8")
    options = dict(cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    command = [str(python), "-m", "lca_viewer.cli", "serve", "--open"] + serve_args
    try:
        if os.name != "nt":
            return subprocess.Popen(command, start_new_session=True, **options)
        # A hidden console of its own, not this terminal's (the imports it runs get no window either);
        # out of the terminal's job too when allowed, so that closing the terminal leaves it running
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            return subprocess.Popen(command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
        except OSError:
            return subprocess.Popen(command, creationflags=flags, **options)
    finally:
        log.close()


def log_text() -> str:
    try:
        return LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def wait_until_ready(server):
    """The address the server answers at, once it does; None if it stopped first."""
    while server.poll() is None:
        found = re.search(r"serving (http://\S+?):(\d+)", log_text())
        if found and app_answers("http://127.0.0.1:" + found.group(2)):
            return "http://localhost:" + found.group(2)
        time.sleep(0.3)
    return None


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
    if not serve_args:
        port = running_app()
        if port:
            url = "http://localhost:%d" % port
            print("LCA Explorer is already running: opening it at %s" % url)
            webbrowser.open(url)
            return 0
    print("\nLCA_explorer will open in a browser window...")
    server = start_in_background(python, serve_args)
    try:
        url = wait_until_ready(server)
    except KeyboardInterrupt:
        server.terminate()
        return 0
    if url is None:
        lines = log_text().strip().splitlines()
        print("LCA Explorer stopped while starting:\n")
        print("\n".join(lines[-25:]) or "(no output)")
        print("\nThe whole log: %s" % LOG)
        return 1
    print("LCA Explorer is running at %s: it opens in your browser, and stops when you close its" % url)
    print("last tab (or click Shut down). This window can close. Its log: %s" % LOG)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
