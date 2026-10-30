"""First-time setup, step 0: choose or create the Python environment, install lca_viewer in
it, then run ``lca-viewer setup`` there.

Run by launch.py (itself started by lca_explorer.cmd) with whatever Python was found (conda's
base one, or any Python 3.7+), so it uses the standard library only. ``--yes`` takes the default answers; other
arguments are passed on to ``lca-viewer setup``.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAST = ROOT / "data" / "python.txt"  # written by lca-viewer setup
ENV_NAME = "lca_viewer"
WINDOWS = os.name == "nt"
YES = False  # set by main() from --yes


def env_python(prefix) -> Path:
    return Path(prefix) / ("python.exe" if WINDOWS else "bin/python")


def env_vars(python) -> dict:
    """Environment to run ``python`` with: a conda env on Windows needs its DLL folders on PATH
    when it is not activated (same as lca_explorer.cmd)."""
    env = dict(os.environ)
    if WINDOWS:
        prefix = Path(python).parent
        env["PATH"] = os.pathsep.join([str(prefix), str(prefix / "Library" / "bin"), str(prefix / "Scripts"),
                                       env.get("PATH", "")])
    return env


def probe(python):
    """((major, minor), lca_viewer installed) for a Python, or None if it doesn't run."""
    code = "import sys, importlib.util; print('%d.%d' % sys.version_info[:2], importlib.util.find_spec('lca_viewer') is not None)"
    try:
        out = subprocess.run([str(python), "-c", code], capture_output=True, text=True, env=env_vars(python), timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode:
        return None
    version, installed = out.stdout.split()
    return tuple(int(x) for x in version.split(".")), installed == "True"


def find_conda():
    names = ("miniconda3", "anaconda3", "miniforge3", "mambaforge")
    bases = [Path(sys.prefix)] + [Path(p) for p in [os.environ.get("MINIFORGE_DIR")] if p] + [Path.home() / n for n in names]
    if WINDOWS:
        for var in ("LOCALAPPDATA", "ProgramData"):
            if os.environ.get(var):
                bases += [Path(os.environ[var]) / n for n in names]
    else:
        bases += [Path("/opt") / n for n in names]
    candidates = [os.environ.get("CONDA_EXE"), shutil.which("conda")]
    for base in bases:
        candidates += [base / "Scripts" / "conda.exe", base / "condabin" / "conda.bat", base / "bin" / "conda"]
    return next((str(c) for c in candidates if c and Path(c).is_file()), None)


def conda_envs(conda) -> list:
    """Prefixes of the conda environments, without the base one."""
    out = subprocess.run([conda, "env", "list", "--json"], capture_output=True, text=True)
    if out.returncode:
        return []
    return [Path(p) for p in json.loads(out.stdout)["envs"] if not (Path(p) / "condabin").is_dir()]


def choose(options, default: int) -> int:
    print("\nWhich Python environment should lca_viewer use?")
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        answer = "" if YES else input(f"Choose [{default + 1}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print(f"  Type a number from 1 to {len(options)}")


def main(argv) -> int:
    global YES
    YES = "--yes" in argv
    sys.stdout.reconfigure(line_buffering=True)  # keep the order with the subprocesses' output when piped
    print("lca_viewer setup\n")
    conda = find_conda()
    options, default = [], None

    last = Path(LAST.read_text(encoding="utf-8").strip()) if LAST.exists() else None
    if last and (probe(last) or (None, False))[1]:
        options.append((f"The one used last time ({last})", lambda: last))
        default = 0
    else:
        last = None

    envs = conda_envs(conda) if conda else []
    for prefix in envs:
        python = env_python(prefix)
        info = probe(python) if python != last else None
        if info and info[0] >= (3, 10):
            installed = ", lca_viewer installed" if info[1] else ""
            options.append((f"Conda environment '{prefix.name}' (Python {info[0][0]}.{info[0][1]}{installed})",
                            lambda python=python: python))
            if prefix.name == ENV_NAME and default is None:
                default = len(options) - 1

    def create_conda():
        print(f"\nCreating the conda environment '{ENV_NAME}' (a few minutes)...")
        subprocess.run([conda, "create", "-y", "-n", ENV_NAME, "--override-channels", "-c", "conda-forge",
                        "python=3.12", "pip"], check=True)
        return next(env_python(p) for p in conda_envs(conda) if p.name == ENV_NAME)

    if conda and not any(p.name == ENV_NAME for p in envs):
        options.append((f"Create a new conda environment '{ENV_NAME}' (Python 3.12, recommended)", create_conda))
        if default is None:
            default = len(options) - 1

    venv = ROOT / ".venv"
    venv_python = venv / ("Scripts/python.exe" if WINDOWS else "bin/python")
    if venv_python.exists() and venv_python != last:
        options.append((f"The .venv in this folder ({venv})", lambda: venv_python))
    elif not venv.exists() and sys.version_info >= (3, 10):
        def create_venv():
            print(f"\nCreating {venv}...")
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
            return venv_python

        options.append((f"Create a .venv in this folder with Python {sys.version_info[0]}.{sys.version_info[1]} "
                        f"({sys.executable})", create_venv))
    if default is None:
        default = len(options) - 1 if options else 0

    def other():
        while True:
            python = Path(input("Path of the Python executable: ").strip().strip("'\""))
            info = probe(python)
            if info and info[0] >= (3, 10):
                return python
            print("  That doesn't run Python 3.10 or later.")

    options.append(("Another Python 3.10+: type its path", other))
    if len(options) == 1:
        print("No conda and no Python 3.10+ found. Installing Miniforge is the simplest:\n"
              "  https://conda-forge.org/download/\n"
              "then run the setup again. Or type the path of a Python 3.10+ below.")
    python = Path(options[choose(options, default)][1]())

    # The fast solver (pypardiso) needs Intel MKL: x86-64 only
    extras = "brightway,fast" if platform.machine().lower() in ("x86_64", "amd64") else "brightway"
    print(f"\nInstalling lca_viewer and its dependencies into {python} (a few minutes the first time)...")
    install = subprocess.run([str(python), "-m", "pip", "install", "-e", f".[{extras}]", "fastapi", "uvicorn", "py7zr"],
                             cwd=ROOT, env=env_vars(python))
    if install.returncode:
        print("\nThe installation failed (see above). Running the setup again and choosing a new "
              "environment usually fixes it.")
        return 1

    return subprocess.run([str(python), "-m", "lca_viewer.cli", "setup", *[a for a in argv if a != "--yes"]],
                          cwd=ROOT, env=env_vars(python)).returncode


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
