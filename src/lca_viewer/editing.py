"""Changes to Brightway projects made from the app: create, delete and restore projects, delete
their databases, and write, edit and delete datasets (the *Create dataset* panel).

These use bw2data on the Brightway data folder of ``BRIGHTWAY2_DIR``: the server runs them
in a separate ``lca-viewer`` process (``lca-viewer project …``, ``lca-viewer dataset …``),
as it does for imports and builds.

A dataset is written as SimaPro would enter it: products (several with allocation
percentages), avoided products, inputs from the technosphere and from nature, emissions and
waste to treatment, each exchange with a comment and an optional lognormal uncertainty, and
the process documentation. A dataset with co-products is a multifunctional process of
Brightway's ``multifunctional`` library, allocated with its ``manual_allocation`` strategy into
read-only single-output processes, one per product, as for an imported SimaPro CSV.

A dataset written here can be written again in place (``write_dataset`` with ``code``) and deleted
(``delete_dataset``), changing the products down to one or up to several: the nodes of the products keep
their codes, so the datasets using them stay linked, and a product that is used elsewhere cannot be
removed, nor a dataset with a product used elsewhere. Each change is one SQLite transaction, and the
database is processed again before it ends, so the matrices a model is built from are never out of step with
the data. Datasets that were not written here (ecoinvent…) are not changed, only read (copied).
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


def init_folder() -> None:
    """The Brightway data folder, made as Brightway makes it the first time it runs:
    ``projects.db`` and an empty ``default`` project."""
    import bw2data as bd

    print(f"Brightway projects: {', '.join(sorted(p.name for p in bd.projects))}", flush=True)


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


def delete_database(project: str, name: str) -> None:
    """Delete a database from a project, unless another database of the project links to it."""
    import bw2data as bd

    if project not in bd.projects:
        raise SystemExit(f"No project {project!r}")
    bd.projects.set_current(project)
    if name not in bd.databases:
        raise SystemExit(f"No database {name!r} in project {project!r}")
    users = sorted(d for d, meta in bd.databases.items() if d != name and name in meta.get("depends", []))
    if users:
        raise SystemExit(f"{name} is used by {', '.join(users)}: remove {'it' if len(users) == 1 else 'them'} first")
    del bd.databases[name]
    print(f"Deleted database {name} from project {project}", flush=True)


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
    one (SimaPro imports, allocated processes), else the process itself. The product is the
    functional exchange's (a waste treatment's is a technosphere exchange); SimaPro's "waste to
    treatment" are production exchanges too, but not functional."""
    functional = [e.input for e in node.exchanges() if e.get("functional")]
    if len(functional) == 1:
        return functional[0]
    produced = [e.input for e in node.production() if e.get("functional") is None and e.get("amount")]
    if len(produced) == 1 and produced[0].key != node.key:
        return produced[0]
    return node


def documentation_text(process: dict) -> str:
    """The process comment followed by its documentation, as shown in the app's process pop-up."""
    doc = process.get("documentation") or {}
    parts = [process.get("comment") or ""]
    parts += [f"{label}: {doc[key]}" for key, label in DOCUMENTATION.items() if doc.get(key)]
    return "\n\n".join(p for p in parts if p.strip())


def _atomic():
    """One SQLite transaction on the project's database: what is written inside it is all kept, or none."""
    from bw2data.backends import sqlite3_lci_db

    return sqlite3_lci_db.atomic()


def _dataset_node(bd, database: str, code: str):
    """The node of the dataset of a process: its parent, for a process allocated from a multi-output one;
    for a product, the dataset producing it."""
    from bw2data.backends.schema import ExchangeDataset as ED
    from bw2data.errors import UnknownObject

    try:
        node = bd.get_node(database=database, code=code)
        if node.get("type") == "product":
            producer = ED.select(ED.output_database, ED.output_code).where(
                ED.input_database == database, ED.input_code == code, ED.type == "production").order_by(ED.id).first()
            if producer:
                node = bd.get_node(database=producer.output_database, code=producer.output_code)
        if node.get("type") == "readonly_process" and node.get("mf_parent_key"):
            node = bd.get_node(database=node["mf_parent_key"][0], code=node["mf_parent_key"][1])
    except UnknownObject:
        raise ValueError(f"No process {code!r} in {database!r}") from None
    return node


def _own_keys(node) -> set:
    """The nodes that go with a dataset: itself, its allocated processes and its products."""
    own = {node.key}
    for e in node.exchanges():
        if e.get("functional"):
            own.add(e.input.key)
        if e.get("mf_allocated_process_code"):
            own.add((node["database"], e["mf_allocated_process_code"]))
    return own


def _used_by(bd, keys, own: set) -> dict:
    """The datasets of the project that take one of the nodes ``keys`` as input, other than the
    dataset itself (``own``): ``{(database, code): name}``. Every database of the project counts, and
    an allocated process stands for its parent."""
    from bw2data.backends.schema import ExchangeDataset as ED
    from bw2data.errors import UnknownObject

    users = {}
    for key in keys:
        query = ED.select(ED.output_database, ED.output_code).where(
            ED.input_database == key[0], ED.input_code == key[1], ED.type != "biosphere")
        for user in {(e.output_database, e.output_code) for e in query}:
            if user in own:
                continue
            try:
                node = _dataset_node(bd, *user)
            except (ValueError, UnknownObject):
                continue
            if node.key not in own:
                users[node.key] = f"{node['name']} ({node['database']})"
    return users


def _refuse(what: str, users: dict) -> None:
    if users:
        names = sorted(users.values())
        listed = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
        raise ValueError(f"{what} is used by {listed}: remove it from {'them' if len(names) > 1 else 'it'} first")


def _write_exchanges(bd, node, exchanges: list[dict]) -> None:
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


def _new_product(db, p: dict, location: str):
    product = db.new_node(code=uuid4().hex, type="product", name=p["name"].strip(), unit=p.get("unit") or "unit",
                          location=location)
    product.save()
    return product


def _functional_edge(node, product, p: dict, flags: dict | None = None) -> None:
    node.new_edge(input=product, type="production", amount=float(p["amount"]), functional=True,
                  comment=p.get("comment") or "", properties={"manual_allocation": float(p.get("allocation") or 100)},
                  **(flags or {})).save()


def _finish(db, node, several: bool, process: bool = True) -> dict:
    """Save the node once its exchanges are written (which sets its type), allocate it, and process the
    database (unless that is done later, once for several changes)."""
    node.save()
    if several:
        node.allocate("manual_allocation")
        codes = [e["mf_allocated_process_code"] for e in node.exchanges() if e.get("functional")]
    else:
        codes = [node["code"]]
    if process:
        db.process()
    return {"database": db.name, "code": node["code"], "codes": codes}


def check_spec(spec: dict) -> tuple:
    """What does not need the project to tell a spec is wrong: ``(database, process, products, exchanges)``."""
    process, products, exchanges = spec["process"], spec["products"], spec.get("exchanges", [])
    name = (spec.get("database") or "").strip()
    if not name:
        raise ValueError("Choose a database to write the dataset into")
    if not (process.get("name") or "").strip():
        raise ValueError("The process needs a name")
    if not products or any(not (p.get("name") or "").strip() or not float(p.get("amount") or 0) for p in products):
        raise ValueError("Each product needs a name and an amount")
    if len(products) > 1:
        shares = [float(p.get("allocation") or 0) for p in products]
        if any(s < 0 for s in shares) or abs(sum(shares) - 100) > 1e-6:
            raise ValueError(f"The allocation percentages of the products must add up to 100 (they add up to {sum(shares):g})")
    if spec.get("code") and spec.get("new_database"):
        raise ValueError("A dataset is edited in the database it is in")
    return name, process, products, exchanges


def write_dataset(project: str, spec: dict) -> dict:
    """Write a dataset into ``spec["database"]`` of ``project``, or write one again in place;
    returns ``{"database", "code", "codes"}``: the dataset's code and the codes of the processes the
    app can use (one per product).

    ``spec``: ``{"database", "new_database", "code", "process": {"name", "location", "comment",
    "documentation": {field: text}}, "products": [{"code", "name", "amount", "unit", "allocation",
    "comment"}], "exchanges": [{"section", "database", "code", "amount", "comment", "sd2"}]}``.

    With ``code`` (a process of the dataset, written by this app, in ``database``), the dataset is
    replaced by ``spec`` in one transaction: if anything fails it stays as it was. Its nodes keep their
    codes, so the datasets that use it stay linked: the products of the form that carry a ``code`` are
    those of the dataset (renamed, changed), the others are new co-products, and the products left
    out are deleted, if nothing uses them. A process with one product can get co-products: it then
    becomes a multi-output process (needing a database of the multifunctional backend), and its old
    node is kept as its first product, for whoever used it. Products can also be taken away down to one.
    """
    import bw2data as bd

    try:
        import multifunctional  # noqa: F401  (registers the backend for datasets with co-products)
    except ImportError:
        pass

    bd.projects.set_current(project, update=False)
    return _write(bd, project, spec)


def _write(bd, project: str, spec: dict, created: list | None = None, processed: bool = True) -> dict:
    """:func:`write_dataset` in the current project; ``created`` collects the databases made here and
    ``processed`` False leaves processing the database to the caller."""
    try:
        import multifunctional  # noqa: F401
    except ImportError:
        multifunctional = None
    name, process, products, exchanges = check_spec(spec)
    several = len(products) > 1

    if spec.get("new_database"):
        if name in bd.databases:
            raise ValueError(f"Database {name!r} already exists in project {project!r}")
        if multifunctional is None:
            raise ValueError("Writing datasets needs Brightway's multifunctional library (pip install multifunctional)")
        db = bd.Database(name, backend="multifunctional")
        db.register(default_allocation="manual_allocation", format="lca_viewer dataset editor")
        if created is not None:
            created.append(name)
    else:
        if name not in bd.databases:
            raise ValueError(f"No database {name!r} in project {project!r}")
        db = bd.Database(name)
        if several and db.backend != "multifunctional":
            raise ValueError(f"{name} cannot hold a process with co-products (backend {db.backend!r}): "
                             f"write it into a new database, made with Brightway's multifunctional backend")

    with _atomic():
        if spec.get("code"):
            return _replace(bd, db, spec["code"], process, products, exchanges, processed)
        location = (process.get("location") or "").strip() or "GLO"
        common = _node_data(process, location)
        code = uuid4().hex
        if several:
            node = db.new_node(code=code, type="multifunctional", default_allocation="manual_allocation", **common)
            node.save()
            for p in products:
                _functional_edge(node, _new_product(db, p, location), p)
        else:
            p = products[0]
            node = db.new_node(code=code, type="process", **_product_data(p), **common)
            node.save()
            node.new_edge(input=node, type="production", amount=float(p["amount"]), functional=True,
                          comment=p.get("comment") or "").save()
        _write_exchanges(bd, node, exchanges)
        return _finish(db, node, several, processed)


def _node_data(process: dict, location: str, created: str | None = None) -> dict:
    """What a dataset's node holds of the process fields of the form, and when it was written."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "name": process["name"].strip(),
        "location": location,
        "comment": documentation_text(process),
        "documentation": {k: v for k, v in (process.get("documentation") or {}).items() if v},
        # the comment as typed: ``comment`` has the documentation after it, as in the process pop-up
        "lca_viewer": {"created": created or now, "comment": process.get("comment") or "",
                       **({"modified": now} if created else {})},
    }


def _product_data(p: dict) -> dict:
    """What a process that is its own product holds of it."""
    return {"unit": p.get("unit") or "unit", "reference product": p["name"].strip(), "production amount": float(p["amount"])}


def _replace(bd, db, code: str, process: dict, products: list[dict], exchanges: list[dict], processed: bool = True) -> dict:
    """:func:`write_dataset` of a dataset already written (inside its transaction)."""
    from bw2data.errors import UnknownObject

    node = _dataset_node(bd, db.name, code)
    if not node.get("lca_viewer"):
        raise ValueError(f"{node['name']} was not written by lca_viewer: it cannot be edited, copy it instead")
    key = node.key
    functional = [e for e in node.exchanges() if e.get("functional")]
    self_linked = len(functional) == 1 and functional[0].input.key == key  # the process is its own product
    existing = {e.input["code"]: e for e in functional}  # code of the product -> its exchange
    rows = [dict(p) for p in products]
    several = len(rows) > 1
    if self_linked:
        if not several:
            rows[0]["code"] = key[1]
        elif [r.get("code") for r in rows].count(key[1]) != 1:
            raise ValueError("The product of a process cannot be taken away while it is replaced by others: rename it")
    given = [r["code"] for r in rows if r.get("code")]
    if len(set(given)) != len(given) or any(c not in existing for c in given):
        raise ValueError(f"The products of {node['name']} do not match its dataset: open it again")

    removed = [c for c in existing if c not in given]
    for c in removed:
        _refuse(f"The product {existing[c].input['name']!r} of {node['name']}",
                _used_by(bd, [(key[0], c)], _own_keys(node)))
    flags = {c: {k: v for k, v in e._data.items() if k in ("mf_allocated", "mf_allocated_process_code", "mf_manual_input_product")}
             for c, e in existing.items() if not self_linked}

    for c in removed:  # products nothing uses, and the allocated process producing each
        child = flags[c].get("mf_allocated_process_code")
        try:
            if child:
                bd.get_node(database=key[0], code=child).delete()
        except UnknownObject:
            pass
        existing[c].input.delete()

    location = (process.get("location") or "").strip() or "GLO"
    first = rows[0]
    if self_linked and several:  # the process becomes a product, and a multi-output process takes its place
        product0 = node
        node.exchanges().delete()
        for field in ("reference product", "production amount", "comment", "documentation", "lca_viewer"):
            node._data.pop(field, None)
        node._data.update(type="product", name=next(r for r in rows if r.get("code") == key[1])["name"].strip(),
                          location=location)
        product0["unit"] = next(r for r in rows if r.get("code") == key[1]).get("unit") or "unit"
        product0.save()
        node = db.new_node(code=uuid4().hex, type="multifunctional", default_allocation="manual_allocation",
                           **_node_data(process, location))
        node.save()
    else:
        previous = node["lca_viewer"].get("created")
        node._data.update(_node_data(process, location, previous))
        if several:
            for field in ("reference product", "production amount", "unit"):
                node._data.pop(field, None)
        else:
            node._data.update(_product_data(first))
        node.exchanges().delete()

    if self_linked and not several:
        node.new_edge(input=node, type="production", amount=float(first["amount"]), functional=True,
                      comment=first.get("comment") or "").save()
    else:
        for r in rows:
            c = r.get("code")
            if self_linked and c == key[1]:
                product, kept = product0, {}
            elif c:
                product, kept = existing[c].input, flags[c]
                product._data.update(name=r["name"].strip(), unit=r.get("unit") or "unit", location=location)
                product.save()
            else:
                product, kept = _new_product(db, r, location), {}
            _functional_edge(node, product, r, kept)
    _write_exchanges(bd, node, exchanges)
    return _finish(db, node, several, processed)


def delete_dataset(project: str, database: str, code: str) -> dict:
    """Delete the dataset of a process of ``database``: the process, its allocated processes and its
    products. Only a dataset written by this app, that no other dataset of the project uses (it
    would lose an input, and its results with it). Returns ``{"database", "deleted"}``."""
    import bw2data as bd

    try:
        import multifunctional  # noqa: F401
    except ImportError:
        pass

    bd.projects.set_current(project, update=False)
    return _delete(bd, database, code)


def _delete(bd, database: str, code: str, processed: bool = True) -> dict:
    """:func:`delete_dataset` in the current project."""
    if database not in bd.databases:
        raise ValueError(f"No database {database!r}")
    with _atomic():
        node = _dataset_node(bd, database, code)
        if not node.get("lca_viewer"):
            raise ValueError(f"{node['name']} was not written by lca_viewer: it cannot be deleted from here")
        own = _own_keys(node)
        _refuse(node["name"], _used_by(bd, own, own))
        children = [bd.get_node(database=d, code=c) for d, c in own if (d, c) != node.key
                    and bd.get_node(database=d, code=c).get("type") == "readonly_process"]
        others = [bd.get_node(database=d, code=c) for d, c in own if (d, c) != node.key
                  and (d, c) not in {n.key for n in children}]
        name = node["name"]
        for n in [*children, node, *others]:
            n.delete()
        if processed:
            bd.Database(database).process()
    return {"database": database, "deleted": name}


def apply_changes(project: str, changes: list[dict]) -> dict:
    """Make staged changes in one transaction, in order, then process the databases they touched once.

    Each change: ``{"kind": "create" | "edit", "title", "spec"}`` (see :func:`write_dataset`) or
    ``{"kind": "delete", "title", "database", "code"}``. Later changes see the earlier ones (a dataset
    can be deleted after the one that used it). If one fails, none is made, the databases created
    for the changes are removed again, and the error says which one it was.
    Returns ``{"applied", "databases"}``.
    """
    import bw2data as bd

    try:
        import multifunctional  # noqa: F401
    except ImportError:
        pass

    bd.projects.set_current(project, update=False)
    created, touched = [], []
    try:
        with _atomic():
            for change in changes:
                try:
                    if change["kind"] == "delete":
                        done = _delete(bd, change["database"], change["code"], processed=False)
                    elif change["kind"] in ("create", "edit"):
                        spec = dict(change["spec"])
                        if spec.get("new_database") and spec.get("database") in created:
                            spec["new_database"] = False  # made by an earlier change of this batch
                        done = _write(bd, project, spec, created, processed=False)
                    else:
                        raise ValueError(f"Unknown change {change['kind']!r}")
                except Exception as e:
                    raise ValueError(f"{change.get('title') or change['kind']}: {e}") from e
                if done["database"] not in touched:
                    touched.append(done["database"])
            for name in touched:
                bd.Database(name).process()
    except Exception:
        for name in created:
            if name in bd.databases:
                del bd.databases[name]
        raise
    return {"applied": len(changes), "databases": touched}
