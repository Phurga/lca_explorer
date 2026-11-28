# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Time the two ways a database is loaded, step by step (docs/project-cache.md, *Measured*).

    python scripts/time_loading.py build PROJECT DATABASE SCRATCH_CACHE [--methods-file names.json] [--serial]
    python scripts/time_loading.py open CACHE DATABASE

``build``: a build into a project cache (give a scratch folder, never the app's own cache, to time a first build);
each line of the build's log with the time since the start and since the line before, then the time spent in its
main functions. ``--serial``: the exchanges read in the build process itself, not by workers.
``open``: opening a database built in ``CACHE``: ``project_cache.load``, the API (``create_app``), ``/api/info``,
a first ``/api/rows`` and the expansion of its root.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import defaultdict

totals: dict[str, float] = defaultdict(float)
calls: dict[str, int] = defaultdict(int)


def wrap(owner, name: str, label: str | None = None) -> None:
    """Add the time of each call of ``owner.name`` to ``totals[label]``."""
    f = getattr(owner, name)
    label = label or name

    def timed(*a, **k):
        t = time.perf_counter()
        try:
            return f(*a, **k)
        finally:
            totals[label] += time.perf_counter() - t
            calls[label] += 1

    setattr(owner, name, timed)


def report(start: float, peak: float | None = None) -> None:
    print(f"total {time.perf_counter() - start:.1f}s" + (f", peak RSS {peak / 1e9:.2f} GB" if peak else ""))
    for k, v in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {k:36s} {v:7.2f}s  x{calls[k]}")


def build(args) -> None:
    import psutil

    start = time.perf_counter()
    import bw2calc as bc

    from lca_viewer import brightway, exchange_store, project_cache

    wrap(bc.LCA, "load_lci_data", "bw2calc load_lci_data")
    wrap(exchange_store, "counts", "exchanges per activity (index)")
    for name in ("_product_rows", "activity_frame", "flow_frame", "method_factors", "_unlinked_factors"):
        wrap(brightway, name)
    for name in ("_database_graph", "_read_matrices", "_solve_segment", "_read_exchanges_parts"):
        if hasattr(project_cache, name):
            wrap(project_cache, name)
    if args.serial:
        project_cache.EXCHANGE_WORKERS = 0
    peak = [0]

    def watch():
        me = psutil.Process()
        while True:
            peak[0] = max(peak[0], me.memory_info().rss)
            time.sleep(0.2)

    threading.Thread(target=watch, daemon=True).start()
    methods = [tuple(m) for m in json.load(open(args.methods_file, encoding="utf-8"))] if args.methods_file else None
    last = [start]

    def log(message):
        now = time.perf_counter()
        print(f"[{now - start:7.1f}s +{now - last[0]:6.1f}] {message}", flush=True)
        last[0] = now

    project_cache.build(args.project, args.database, methods, args.cache, log=log)
    log("done")
    report(start, peak[0])


def open_(args) -> None:
    import numpy as np
    import pandas as pd

    start = time.perf_counter()
    from fastapi.testclient import TestClient

    from lca_viewer import project_cache, server

    print(f"imports {time.perf_counter() - start:.2f}s")
    wrap(project_cache, "_read_segment", "read A.npz B.npz")
    wrap(project_cache, "_project_files", "flows, methods, unlinked.pkl")
    wrap(pd, "read_pickle")
    wrap(server, "derived_activities")
    wrap(server, "_layout")
    steps = []

    def step(label, f):
        t = time.perf_counter()
        out = f()
        steps.append(f"{label} {time.perf_counter() - t:.2f}s")
        return out

    model = step("project_cache.load", lambda: project_cache.load(args.cache, args.database))
    client = TestClient(step("create_app", lambda: server.create_app(model, title=args.database)))
    info = step("/api/info", lambda: client.get("/api/info").json())
    root = int(np.argmax(np.diff(model.A.tocsc().indptr)))  # the process with the most inputs
    state = {"root": [root], "method": info["default_method"]}
    rows = step("first /api/rows", lambda: client.post("/api/rows", json=state).json()["rows"])
    step("expanded", lambda: client.post("/api/rows", json={**state, "expanded": [rows[0]["id"]]}).json())
    print(" | ".join(steps) + f" | {model.size} products, {len(model.methods)} methods")
    report(start)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("build")
    p.add_argument("project")
    p.add_argument("database")
    p.add_argument("cache")
    p.add_argument("--methods-file", help="JSON list of method names (default: those of the cache, else all)")
    p.add_argument("--serial", action="store_true", help="read the exchanges in the build process (no workers)")
    p.set_defaults(func=build)
    p = sub.add_parser("open")
    p.add_argument("cache")
    p.add_argument("database")
    p.set_defaults(func=open_)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
