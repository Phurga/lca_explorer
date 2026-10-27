"""Changes to Brightway projects made from the app: create, delete and restore projects, and
write new datasets (the *Create dataset* panel).

These use bw2data on the Brightway data folder of ``BRIGHTWAY2_DIR``: the server runs them
in a separate ``lca-viewer`` process (``lca-viewer project …``, ``lca-viewer dataset …``),
as it does for imports and builds.

A dataset is written as SimaPro would enter it: products, avoided products, inputs from the
technosphere and from nature, emissions and waste to treatment, each exchange with a comment
and an optional lognormal uncertainty, and the process documentation.

Co-products follow the system model, as in ecoinvent. Consequential: the dataset has one
product, the determining one, and its co-products are avoided products (the marginal supply
they replace). Attributional (cut-off, APOS): products are allocatable, recyclable (cut off)
or waste (sent to treatment), and the allocatable ones share the burdens by a value per unit
(amount × value: a price, a mass…) or by percentages entered. A dataset with several allocatable products is a
multifunctional process of Brightway's ``multifunctional`` library, allocated with the shares
of that rule (its ``manual_allocation`` strategy) into read-only single-output processes, one
per product, as for an imported SimaPro CSV.
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
# Allocation of co-products -> the product property (per unit) it uses: by a value per unit
# (a price, a mass, an energy content…: amount × value), or by percentages entered (manual)
ALLOCATION_RULES = {"value": "allocation value", "manual": None}
# Classes of the products of a dataset, as ecoinvent's cut-off classifies by-products: allocatable
# products share the burdens; recyclable ones are cut off (no burden, supplying no one); waste is
# sent to a treatment process
PRODUCT_CLASSES = ("allocatable", "recyclable", "waste")


def _split_products(products: list[dict], rule: str, system_model: str):
    """The allocatable products with their allocation shares (%), the recyclable ones (cut off)
    and the waste ones, checked.

    In a consequential model a dataset has one product, the determining one: its co-products
    are avoided products (the marginal supply they replace), as in ecoinvent. In an attributional
    one the allocatable products share the burdens by ``rule``: by their amount times a value per
    unit (``property``: a price, a mass…), or by percentages entered (manual)."""
    if rule not in ALLOCATION_RULES:
        raise ValueError(f"Unknown allocation rule {rule!r}")
    if any((p.get("class") or "allocatable") not in PRODUCT_CLASSES for p in products):
        raise ValueError(f"A product class is one of {', '.join(PRODUCT_CLASSES)}")
    if system_model == "consequential" and len(products) > 1:
        raise ValueError("In a consequential model a dataset has one product, the determining one: enter its "
                         "co-products as avoided products (the marginal supply they replace)")
    allocatable = [p for p in products if (p.get("class") or "allocatable") == "allocatable"]
    if not allocatable:
        raise ValueError("At least one product must be allocatable (the reference product)")
    if len(allocatable) == 1:
        shares = [100.0]
    elif ALLOCATION_RULES[rule] is None:
        shares = [float(p.get("allocation") or 0) for p in allocatable]
        if any(s < 0 for s in shares) or abs(sum(shares) - 100) > 1e-6:
            raise ValueError(f"The allocation percentages of the products must add up to 100 (they add up to {sum(shares):g})")
    else:
        values = [float(p["property"]) if p.get("property") not in (None, "") else math.nan for p in allocatable]
        if any(not v >= 0 for v in values):
            raise ValueError("Give each allocatable product its allocation value per unit, 0 or more")
        factors = [abs(float(p["amount"])) * v for p, v in zip(allocatable, values)]
        if not sum(factors):
            raise ValueError("The allocation values of the products add up to 0: nothing to allocate by")
        shares = [100 * f / sum(factors) for f in factors]
    recyclable = [p for p in products if p.get("class") == "recyclable"]
    waste = [p for p in products if p.get("class") == "waste"]
    if any(not p.get("treatment") for p in waste):
        raise ValueError("Choose the treatment of each waste product")
    return allocatable, shares, recyclable, waste


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


def _edited(db, code: str, several: bool):
    """The dataset written from the app that ``code`` names in ``db``, emptied of its exchanges
    to be written again; its node, and its production edges by product code (for co-products).

    The node and its product nodes keep their codes, so the datasets using them stay linked
    (deleting a node would delete those links too)."""
    import bw2data as bd

    try:
        node = bd.get_node(database=db.name, code=code)
    except bd.errors.UnknownObject:
        raise ValueError(f"No dataset {code!r} in {db.name}: it was deleted") from None
    if not isinstance(node.get("lca_viewer"), dict):
        raise ValueError(f"{node['name']} was not written from the app: copy it instead of editing it")
    if several != (node.get("type") == "multifunctional"):
        raise ValueError("A dataset cannot switch between one product and co-products: the processes using it "
                         "would lose their supplier. Copy it into a new dataset instead")
    production = {}
    if several:
        production = {e["input"][1]: dict(e._data) for e in node.exchanges() if e["type"] == "production"}
    node.exchanges().delete()
    return node, production


def write_dataset(project: str, spec: dict) -> dict:
    """Write a dataset into ``spec["database"]`` of ``project``; returns ``{"database", "codes"}``,
    the codes of the processes the app can use (one per product).

    ``spec``: ``{"database", "new_database", "code", "system_model", "allocation", "process":
    {"name", "location", "comment", "documentation": {field: text}}, "products": [{"name",
    "amount", "unit", "class", "allocation", "property", "treatment": {"database", "code"},
    "comment", "code"}], "exchanges": [{"section", "database", "code", "amount", "comment", "sd2"}]}``.
    ``system_model``: "attributional" (default) or "consequential"; ``allocation``: a key of
    :data:`ALLOCATION_RULES` (default manual, with each product's ``allocation`` percentage;
    otherwise each allocatable product's ``property`` per unit). Products are allocatable
    (default), recyclable (cut off: kept in the dataset's record only) or waste (sent to the
    ``treatment`` process). With ``code``, the dataset of that code, written from the app, is
    replaced (edited); its products keep their ``code``, products without one are added and the
    others deleted.
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
    system_model = spec.get("system_model") or "attributional"
    if system_model not in ("attributional", "consequential"):
        raise ValueError(f"Unknown system model {system_model!r}")
    rule = spec.get("allocation") or "manual"
    products, shares, recyclable, waste = _split_products(products, rule, system_model)
    several = len(products) > 1

    editing = spec.get("code")
    if editing and spec.get("new_database"):
        raise ValueError("An edited dataset stays in its database")
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

    # Exchanges checked before anything is written: (edge type, node linked to, amount, edge fields)
    edges = []
    for e in exchanges:
        if e["section"] not in SECTIONS:
            raise ValueError(f"Unknown exchange section {e['section']!r}")
        kind, sign = SECTIONS[e["section"]]
        try:
            other = bd.get_node(database=e["database"], code=e["code"])
        except bd.errors.UnknownObject:
            raise ValueError(f"An exchange links to a process or flow that is gone ({e['database']}, {e['code']})") from None
        if kind != "biosphere":
            other = _supplier(other)
        amount = sign * float(e["amount"])
        edges.append((kind, other, amount, {"comment": e.get("comment") or "", **_lognormal(amount, e.get("sd2"))}))
    for p in waste:  # waste products: sent to their treatment, marked to be read back as products
        try:
            treatment = _supplier(bd.get_node(database=p["treatment"]["database"], code=p["treatment"]["code"]))
        except bd.errors.UnknownObject:
            raise ValueError(f"The treatment of {p['name']} is gone: choose another one") from None
        edges.append(("technosphere", treatment, -float(p["amount"]),
                      {"comment": p.get("comment") or "", "lca_viewer_product": {"name": p["name"].strip(), "unit": p.get("unit") or "unit"}}))

    location = (process.get("location") or "").strip() or "GLO"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    common = {
        "name": process["name"].strip(),
        "location": location,
        "comment": documentation_text(process),
        "documentation": {k: v for k, v in (process.get("documentation") or {}).items() if v},
        # What the form had and Brightway does not keep, read back to edit the dataset
        "lca_viewer": {"created": now, "comment": process.get("comment") or "", "system_model": system_model,
                       "allocation": rule, "recyclable": [{k: p.get(k) for k in ("name", "amount", "unit", "comment")}
                                                          for p in recyclable]},
    }
    from bw2data.backends import sqlite3_lci_db

    with sqlite3_lci_db.transaction():  # an edit fails whole, never leaving the dataset half written
        if editing:
            node, old_production = _edited(db, editing, several)
            common["lca_viewer"] = {**common["lca_viewer"], "created": node["lca_viewer"].get("created"), "modified": now}
            code = editing
        else:
            node, old_production, code = None, {}, uuid4().hex
        # An edited node is saved once it has its products again (multifunctional checks them on save)
        if several:
            if node is None:
                node = db.new_node(code=code, type="multifunctional", default_allocation="manual_allocation", **common)
                node.save()
            else:
                node._data.update(common)
            kept = set()
            for p, share in zip(products, shares):
                fields = {"name": p["name"].strip(), "unit": p.get("unit") or "unit", "location": location}
                if p.get("code") in old_production:
                    product = bd.get_node(database=name, code=p["code"])
                    product._data.update(fields)
                    kept.add(p["code"])
                else:
                    product = db.new_node(code=uuid4().hex, type="product", **fields)
                product.save()
                # The allocated process of an existing product keeps its code (multifunctional's mf_* keys)
                allocated = {k: v for k, v in old_production.get(p.get("code"), {}).items()
                             if k in ("mf_allocated", "mf_allocated_process_code", "mf_manual_input_product")}
                # Allocated by the shares, whatever the rule: the property is kept to read it back
                properties = {"manual_allocation": share}
                if ALLOCATION_RULES[rule]:
                    properties[ALLOCATION_RULES[rule]] = float(p["property"])
                node.new_edge(input=product, type="production", amount=float(p["amount"]), functional=True,
                              comment=p.get("comment") or "", properties=properties, **allocated).save()
            for gone in set(old_production) - kept:
                product = bd.get_node(database=name, code=gone)
                users = {(e.output["name"], e.output["location"]) for e in product.upstream()
                         if e["type"] != "production" and e.output.get("mf_parent_key") != node.key}
                print(f"Deleted the product {product['name']}" + (f": {len(users)} datasets lose this input ("
                      + "; ".join(f"{n} [{loc}]" for n, loc in sorted(users)) + ")" if users else ""), flush=True)
                product.delete()
        else:
            p = products[0]
            fields = {"unit": p.get("unit") or "unit", "reference product": p["name"].strip(),
                      "production amount": float(p["amount"]), **common}
            if node is None:
                node = db.new_node(code=code, type="process", **fields)
                node.save()
            else:
                node._data.update(fields)
            node.new_edge(input=node, type="production", amount=float(p["amount"]), functional=True,
                          comment=p.get("comment") or "").save()
        node.save()

        for kind, other, amount, extra in edges:
            node.new_edge(input=other, type=kind, amount=amount, **extra).save()

        node = bd.get_node(database=name, code=code)
        if several:
            node.allocate("manual_allocation")
    codes = [n["code"] for n in db if tuple(n.get("mf_parent_key") or ()) == (name, code)] if several else [code]
    db.process()
    return {"database": name, "codes": codes}
