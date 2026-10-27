"""Changes to Brightway projects made from the app: create, delete and restore projects, and
write new datasets (the *Create dataset* panel).

These use bw2data on the Brightway data folder of ``BRIGHTWAY2_DIR``: the server runs them
in a separate ``lca-viewer`` process (``lca-viewer project …``, ``lca-viewer dataset …``),
as it does for imports and builds.

A dataset is written as SimaPro would enter it: products (several with allocation
percentages), avoided products, inputs from the technosphere and from nature, emissions and
waste to treatment, each exchange with a comment and an optional lognormal uncertainty, and
the process documentation. A dataset with co-products is a multifunctional process of
Brightway's ``multifunctional`` library, allocated with its ``manual_allocation`` strategy into
read-only single-output processes, one per product, as for an imported SimaPro CSV.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from uuid import uuid4

# Process documentation, as in SimaPro's Documentation tab: field -> label
DOCUMENTATION = {
    "technology": "Technology",
    "time period": "Time period",
    "geography": "Geography",
    "data sources": "Data sources and literature",
    "collection method": "Collection method",
    "data treatment": "Data treatment",
    "allocation rules": "Allocation rules",
    "verification": "Verification",
    "data entry by": "Data entry by",
}
# Exchange sections of a dataset -> (Brightway edge type, sign of the amount entered)
SECTIONS = {
    "avoided": ("substitution", 1),
    "technosphere": ("technosphere", 1),
    "resource": ("biosphere", 1),
    "emission": ("biosphere", 1),
    # As in ecoinvent: a treatment process produces -1 unit of waste, so its users take a negative amount
    "waste": ("technosphere", -1),
}


def create_project(name: str, setup: bool = False) -> None:
    """A new Brightway 2.5 project; with ``setup``, the biosphere and impact methods bundled with
    bw2io (ecoinvent 3.9), needed to import ecoSpold2 or SimaPro CSV databases into it."""
    import bw2data as bd

    if name in bd.projects:
        raise SystemExit(f"Project {name!r} already exists")
    bd.projects.set_current(name)  # creates it
    print(f"Created project {name}", flush=True)
    if setup:
        from .importing import setup_project

        print("Installing the biosphere and the impact methods (a few minutes)", flush=True)
        setup_project(name)
        print(f"{len(bd.methods)} impact methods installed", flush=True)


def delete_project(name: str) -> None:
    """Delete a project and its folder."""
    import bw2data as bd

    if name not in bd.projects:
        raise SystemExit(f"No project {name!r}")
    others = [p.name for p in bd.projects if p.name != name]
    if not others:
        raise SystemExit("Brightway keeps at least one project in a folder: create another one first")
    bd.projects.set_current(others[0], update=False)
    bd.projects.delete_project(name, delete_dir=True)
    print(f"Deleted project {name}", flush=True)


def restore_project(path, name: str | None = None) -> str:
    """A project from a Brightway backup archive (``.tar.gz``, e.g. BAFU from files.brightway.dev)."""
    import bw2data as bd
    import bw2io as bi

    if name and name in bd.projects:
        raise SystemExit(f"Project {name!r} already exists: choose another name")
    print("Restoring the project (a minute or two for a large one)", flush=True)
    try:
        restored = bi.restore_project_directory(path, project_name=name or None, overwrite_existing=False)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print(f"Restored project {restored}", flush=True)
    return restored


def _lognormal(amount: float, sd2) -> dict:
    """SimaPro's lognormal uncertainty (the squared geometric standard deviation, SD²) in Brightway's terms."""
    if not sd2:
        return {}
    sd2 = float(sd2)
    if sd2 <= 1 or not amount:
        raise ValueError(f"SD² must be greater than 1 and the amount not zero (got {sd2} for {amount})")
    return {"uncertainty type": 2, "loc": math.log(abs(amount)), "scale": 0.5 * math.log(sd2), "negative": amount < 0}


def _supplier(node):
    """The node a technosphere exchange links to: a process's product node when it has a separate
    one (SimaPro imports, allocated processes), else the process itself."""
    produced = [e.input for e in node.production()]
    if len(produced) == 1 and produced[0].key != node.key:
        return produced[0]
    return node


def documentation_text(process: dict) -> str:
    """The process comment followed by its documentation, as shown in the app's process pop-up."""
    doc = process.get("documentation") or {}
    parts = [process.get("comment") or ""]
    parts += [f"{label}: {doc[key]}" for key, label in DOCUMENTATION.items() if doc.get(key)]
    return "\n\n".join(p for p in parts if p.strip())


def write_dataset(project: str, spec: dict) -> dict:
    """Write a dataset into ``spec["database"]`` of ``project``; returns ``{"database", "codes"}``,
    the codes of the processes the app can use (one per product).

    ``spec``: ``{"database", "new_database", "process": {"name", "location", "comment",
    "documentation": {field: text}}, "products": [{"name", "amount", "unit", "allocation",
    "comment"}], "exchanges": [{"section", "database", "code", "amount", "comment", "sd2"}]}``.
    """
    import bw2data as bd

    try:
        import multifunctional  # noqa: F401  (registers the backend for datasets with co-products)
    except ImportError:
        multifunctional = None

    bd.projects.set_current(project, update=False)
    process, products, exchanges = spec["process"], spec["products"], spec.get("exchanges", [])
    name = (spec.get("database") or "").strip()
    if not name:
        raise ValueError("Choose a database to write the dataset into")
    if not (process.get("name") or "").strip():
        raise ValueError("The process needs a name")
    if not products or any(not (p.get("name") or "").strip() or not float(p.get("amount") or 0) for p in products):
        raise ValueError("Each product needs a name and an amount")
    several = len(products) > 1
    if several:
        shares = [float(p.get("allocation") or 0) for p in products]
        if any(s < 0 for s in shares) or abs(sum(shares) - 100) > 1e-6:
            raise ValueError(f"The allocation percentages of the products must add up to 100 (they add up to {sum(shares):g})")

    if spec.get("new_database"):
        if name in bd.databases:
            raise ValueError(f"Database {name!r} already exists in project {project!r}")
        if multifunctional is None:
            raise ValueError("Writing datasets needs Brightway's multifunctional library (pip install multifunctional)")
        db = bd.Database(name, backend="multifunctional")
        db.register(default_allocation="manual_allocation", format="lca_viewer dataset editor")
    else:
        if name not in bd.databases:
            raise ValueError(f"No database {name!r} in project {project!r}")
        db = bd.Database(name)
        if several and db.backend != "multifunctional":
            raise ValueError(f"{name} cannot hold a process with co-products (backend {db.backend!r}): "
                             f"write it into a new database, made with Brightway's multifunctional backend")

    location = (process.get("location") or "").strip() or "GLO"
    common = {
        "name": process["name"].strip(),
        "location": location,
        "comment": documentation_text(process),
        "documentation": {k: v for k, v in (process.get("documentation") or {}).items() if v},
        "lca_viewer": {"created": datetime.now(timezone.utc).isoformat(timespec="seconds")},
    }
    code = uuid4().hex
    if several:
        node = db.new_node(code=code, type="multifunctional", default_allocation="manual_allocation", **common)
        node.save()
        for p in products:
            product = db.new_node(code=uuid4().hex, type="product", name=p["name"].strip(), unit=p.get("unit") or "unit",
                                  location=location)
            product.save()
            node.new_edge(input=product, type="production", amount=float(p["amount"]), functional=True,
                          comment=p.get("comment") or "",
                          properties={"manual_allocation": float(p["allocation"])}).save()
    else:
        p = products[0]
        node = db.new_node(code=code, type="process", unit=p.get("unit") or "unit",
                           **{"reference product": p["name"].strip(), "production amount": float(p["amount"])}, **common)
        node.save()
        node.new_edge(input=node, type="production", amount=float(p["amount"]), functional=True,
                      comment=p.get("comment") or "").save()

    for e in exchanges:
        if e["section"] not in SECTIONS:
            raise ValueError(f"Unknown exchange section {e['section']!r}")
        kind, sign = SECTIONS[e["section"]]
        other = bd.get_node(database=e["database"], code=e["code"])
        if kind != "biosphere":
            other = _supplier(other)
        amount = sign * float(e["amount"])
        node.new_edge(input=other, type=kind, amount=amount, comment=e.get("comment") or "",
                      **_lognormal(amount, e.get("sd2"))).save()

    node = bd.get_node(database=name, code=code)
    if several:
        node.allocate("manual_allocation")
        codes = [n["code"] for n in db if tuple(n.get("mf_parent_key") or ()) == (name, code)]
    else:
        codes = [code]
    db.process()
    return {"database": name, "codes": codes}
