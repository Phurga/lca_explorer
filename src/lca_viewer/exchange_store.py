# SPDX-FileCopyrightText: 2026 Adrien Burg and LCA Explorer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The metadata of exchanges (comment, uncertainty, pedigree, …), kept in SQLite files and read one activity at a time.

Kept apart from :mod:`model` and :mod:`brightway` so that the processes reading the exchanges of a build in parallel
(:func:`read_part`) import only this: no pandas, scipy or Brightway.

A file holds the exchanges of a run of consecutive activities. Its ``activity`` column is the position of the activity
in that run; ``row``, what the exchange is linked to (a row of the model, or a Brightway node id in a project cache).
"""

from __future__ import annotations

import json
import pickle
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np

# One column per common field, named as in Brightway, other fields in ``extra`` (JSON). Comments repeat a lot
# (ecoinvent 3.9: 100 MB, 8 MB distinct), so are stored once; pedigree scores in the usual order are stored as
# "2,3,5,5,1".
EXCHANGE_COLUMNS = {
    "amount": "REAL", "unit": "TEXT", "uncertainty type": "INTEGER", "loc": "REAL", "scale": "REAL",
    "scale without pedigree": "REAL", "shape": "REAL", "minimum": "REAL", "maximum": "REAL",
    "formula": "TEXT", "variable name": "TEXT",
}
PEDIGREE = ("reliability", "completeness", "temporal correlation", "geographical correlation",
            "further technological correlation")

# What the activity pop-up shows of an exchange, as Brightway records it
EXCHANGE_FIELDS = [
    "amount", "unit", "comment", "formula", "variable name", "uncertainty type", "loc", "scale",
    "scale without pedigree", "shape", "minimum", "maximum", "pedigree",
]
PRODUCTION_FIELDS = EXCHANGE_FIELDS + ["production volume", "properties"]


def exchange_data(data: dict) -> dict:
    """Metadata of one exchange shown in the activity pop-up; the product properties (price,
    carbon content, …) and production volume are kept for production exchanges only."""
    out = {}
    for k in PRODUCTION_FIELDS if data.get("type") == "production" else EXCHANGE_FIELDS:
        v = data.get(k)
        if v is None:
            continue
        if isinstance(v, np.ndarray):  # some imports (e.g. BAFU) store numpy values
            v = v.tolist()
        elif isinstance(v, np.generic):
            v = v.item()
        if (isinstance(v, (str, list, tuple, dict)) and not v) or (isinstance(v, float) and v != v):
            continue  # empty, or NaN (not valid JSON)
        out[k] = v
    return out


def write(path, exchanges) -> int:
    """Write ``exchanges``, ``(activity, type, row, data)`` tuples, to the SQLite file ``path``; returns how many.

    The file is written once and never changed: no journal, and no VACUUM (it has no free pages)."""
    comments: dict[str, int] = {}

    def record(activity, kind, row, data):
        data = dict(data)
        comment = data.pop("comment", None)
        pedigree = data.get("pedigree")
        if isinstance(pedigree, dict) and set(pedigree) == set(PEDIGREE) and all(type(v) is int for v in pedigree.values()):
            pedigree = ",".join(str(pedigree[k]) for k in PEDIGREE)
            del data["pedigree"]
        else:
            pedigree = None
        values = [data.pop(k, None) for k in EXCHANGE_COLUMNS]
        comment_id = None if comment is None else comments.setdefault(comment, len(comments))
        extra = json.dumps(data, default=str) if data else None
        return (int(activity), kind, int(row), comment_id, pedigree, *values, extra)

    columns = ", ".join(f'"{k}" {t}' for k, t in EXCHANGE_COLUMNS.items())
    path = Path(path)
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        db.execute(f"CREATE TABLE exchanges (activity INTEGER, type TEXT, row INTEGER, comment INTEGER, "
                   f"pedigree TEXT, {columns}, extra TEXT)")
        cursor = db.executemany(f"INSERT INTO exchanges VALUES ({', '.join('?' * (len(EXCHANGE_COLUMNS) + 6))})",
                                (record(*e) for e in exchanges))
        count = cursor.rowcount
        db.execute("CREATE TABLE comments (id INTEGER PRIMARY KEY, text TEXT)")
        db.executemany("INSERT INTO comments VALUES (?, ?)", ((i, c) for c, i in comments.items()))
        db.execute("CREATE INDEX by_activity ON exchanges (activity)")
        db.commit()
    return count


def read(path, activity: int) -> list[dict]:
    """The exchanges of ``activity`` (its position in the file) in dataset order, as ``{"type", "row", **data}``."""
    columns = ", ".join(f'e."{k}"' for k in EXCHANGE_COLUMNS)
    with closing(sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)) as db:
        rows = db.execute(
            f"SELECT e.type, e.row, c.text, e.pedigree, {columns}, e.extra FROM exchanges e "
            f"LEFT JOIN comments c ON c.id = e.comment WHERE e.activity = ? ORDER BY e.rowid", (activity,))
        out = []
        for kind, row, comment, pedigree, *values, extra in rows:
            d = {"type": kind, "row": row}
            d.update((k, v) for k, v in zip(EXCHANGE_COLUMNS, values) if v is not None)
            if comment is not None:
                d["comment"] = comment
            if pedigree is not None:
                d["pedigree"] = dict(zip(PEDIGREE, map(int, pedigree.split(","))))
            if extra:
                d.update(json.loads(extra))
            out.append(d)
        return out


# ---------------------------------------------------------------- reading a Brightway project's exchanges

def lower_priority() -> None:
    """Run this process below the normal priority: the processes reading exchanges give way to the build's solve and
    to the app (the page stays responsive while a build reads exchanges on most cores)."""
    import os
    import sys

    try:
        if sys.platform == "win32":
            import ctypes

            BELOW_NORMAL_PRIORITY_CLASS = 0x4000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
    except (OSError, AttributeError):
        pass  # at the normal priority, then


def counts(brightway_db, databases) -> dict[tuple[str, str], int]:
    """How many exchanges each activity ``(database, code)`` of ``databases`` has, in the project's
    ``lci/databases.db`` (from its index: a fraction of a second for ecoinvent)."""
    marks = ", ".join("?" * len(databases))
    with closing(sqlite3.connect(f"{Path(brightway_db).resolve().as_uri()}?mode=ro", uri=True)) as db:
        return {(d, c): n for d, c, n in db.execute(
            f"SELECT output_database, output_code, COUNT(*) FROM exchangedataset "
            f"WHERE output_database IN ({marks}) GROUP BY output_database, output_code", list(databases))}


def read_part(brightway_db, path, activities) -> int:
    """Read the exchanges of ``activities``, ``(database, code)`` of consecutive activities, from the project's
    ``lci/databases.db`` into the SQLite file ``path``; returns how many. ``row``: the Brightway node id of the input.
    Run in a process of its own by a build (one per run of activities), or in the build process for a small database.

    Raw SQLite, not Brightway's ORM: 3 to 4 times faster, and the ``data`` of an exchange is unpickled as bw2data's
    ``PickleField`` does. Exchanges whose input is not a node of the project are left out (as an inner join of the
    ORM did); those the model does not have are left out when read (:meth:`LCAModel.exchange_details`)."""
    with closing(sqlite3.connect(f"{Path(brightway_db).resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.execute("CREATE TEMP TABLE mine (database TEXT, code TEXT)")  # rowid - 1: the position in the run
        db.executemany("INSERT INTO mine VALUES (?, ?)", activities)
        # CROSS JOIN: SQLite keeps ``mine`` as the outer loop, so the exchanges come activity after activity, each
        # activity's in the order they were written (the index on the outputs keeps the rowid order)
        rows = db.execute(
            "SELECT m.rowid - 1, e.type, i.id, e.input_database, e.input_code, e.data FROM mine m "
            "CROSS JOIN exchangedataset e ON e.output_database = m.database AND e.output_code = m.code "
            "JOIN activitydataset i ON i.database = e.input_database AND i.code = e.input_code")
        return write(path, ((a, kind, node, {**exchange_data(pickle.loads(data)), "input": [db_, code]})
                            for a, kind, node, db_, code, data in rows))
