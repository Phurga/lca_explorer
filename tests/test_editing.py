"""Datasets written from the Create dataset panel, and project management (needs Brightway)."""

import math

import numpy as np
import pytest

from lca_viewer import editing, sources

GWP = ("toy", "gwp")


@pytest.fixture
def project(tmp_path):
    """A Brightway folder with one project: a biosphere, a background database and a method."""
    pytest.importorskip("multifunctional")
    import bw2data as bd
    from bw2data.tests import _close_sqlite_handles

    bd.config.dont_warn = True
    bd.config.is_test = True
    _close_sqlite_handles()
    bd.projects.change_base_directories(base_dir=tmp_path, base_logs_dir=tmp_path, project_name="toy", update=False)
    bd.projects._is_temp_dir = True
    bd.Database("bio").write({
        ("bio", "co2"): {"name": "Carbon dioxide, fossil", "categories": ("air",), "unit": "kilogram", "type": "emission"},
        ("bio", "water"): {"name": "Water, river", "categories": ("natural resource", "in water"),
                           "unit": "cubic meter", "type": "natural resource"},
    })

    def process(code, name, co2, production=1.0):
        return ("bg", code), {"name": name, "reference product": name, "location": "FR", "unit": "kilogram",
                              "type": "process", "exchanges": [
                                  {"input": ("bg", code), "amount": production, "type": "production"},
                                  {"input": ("bio", "co2"), "amount": co2, "type": "biosphere"}]}

    bd.Database("bg").write(dict([process("elec", "electricity", 0.1), process("feed", "feed", 0.5),
                                  process("manure", "manure treatment", 0.2, production=-1.0)]))
    bd.Method(GWP).register(unit="kg CO2-eq")
    bd.Method(GWP).write([(("bio", "co2"), 1.0)])
    yield tmp_path
    _close_sqlite_handles()


def spec(**changes):
    out = {
        "database": "my datasets", "new_database": True,
        "process": {"name": "dairy farm", "location": "FR", "comment": "Toy farm",
                    "documentation": {"technology": "Grass-based", "time period": "2020-2024"}},
        "products": [{"name": "milk", "amount": 10, "unit": "kilogram", "allocation": 80, "comment": "raw milk"},
                     {"name": "meat", "amount": 2, "unit": "kilogram", "allocation": 20}],
        "exchanges": [
            {"section": "technosphere", "database": "bg", "code": "elec", "amount": 5, "comment": "milking", "sd2": 1.21},
            {"section": "technosphere", "database": "bg", "code": "feed", "amount": 4},
            {"section": "waste", "database": "bg", "code": "manure", "amount": 3, "comment": "slurry"},
            {"section": "emission", "database": "bio", "code": "co2", "amount": 3},
            {"section": "resource", "database": "bio", "code": "water", "amount": 0.2},
        ],
    }
    out.update(changes)
    return out


def test_dataset_with_coproducts_is_allocated_and_builds(project):
    import bw2data as bd

    from lca_viewer.brightway import from_brightway

    result = editing.write_dataset("toy", spec())
    assert result["database"] == "my datasets" and len(result["codes"]) == 2
    db = bd.Database("my datasets")
    assert db.backend == "multifunctional"
    parent = next(n for n in db if n["type"] == "multifunctional")
    assert "Technology: Grass-based" in parent["comment"] and parent["documentation"]["time period"] == "2020-2024"
    elec = next(e for e in parent.technosphere() if e.input["name"] == "electricity")
    assert elec["comment"] == "milking" and elec["uncertainty type"] == 2
    assert math.isclose(elec["scale"], 0.5 * math.log(1.21))
    manure = next(e for e in parent.technosphere() if e.input["name"] == "manure treatment")
    assert manure["amount"] == -3  # waste to treatment: negative, as in ecoinvent

    # Each allocated process carries its share of every input and emission
    model = from_brightway("toy", "my datasets", [GWP])
    acts = model.activities
    shared = 5 * 0.1 + 4 * 0.5 + 3 * 0.2 + 3  # electricity, feed, manure treatment, direct CO2
    milk = acts.index[(acts["database"] == "my datasets") & (acts["reference_product"] == "milk")][0]
    meat = acts.index[(acts["database"] == "my datasets") & (acts["reference_product"] == "meat")][0]
    assert np.isclose(model.score(milk)[0], 0.8 * shared / 10)
    assert np.isclose(model.score(meat)[0], 0.2 * shared / 2)


def test_single_product_dataset_in_an_existing_database(project):
    from lca_viewer.brightway import from_brightway

    editing.write_dataset("toy", spec(products=[{"name": "milk", "amount": 1, "unit": "kilogram"}]))
    second = spec(new_database=False, process={"name": "cheese making", "location": "FR"},
                  products=[{"name": "cheese", "amount": 1, "unit": "kilogram"}],
                  exchanges=[{"section": "technosphere", "database": "bg", "code": "elec", "amount": 2},
                             {"section": "avoided", "database": "bg", "code": "feed", "amount": 1}])
    editing.write_dataset("toy", second)
    model = from_brightway("toy", "my datasets", [GWP])
    cheese = model.activities.index[model.activities["name"] == "cheese making"][0]
    assert np.isclose(model.score(cheese)[0], 2 * 0.1 - 1 * 0.5)  # avoided feed is credited


def test_dataset_errors(project):
    with pytest.raises(ValueError, match="add up to 100"):
        editing.write_dataset("toy", spec(products=[{"name": "a", "amount": 1, "allocation": 50},
                                                    {"name": "b", "amount": 1, "allocation": 40}]))
    with pytest.raises(ValueError, match="multifunctional backend"):
        editing.write_dataset("toy", spec(database="bg", new_database=False))
    with pytest.raises(ValueError, match="SD²"):
        editing.write_dataset("toy", spec(exchanges=[{"section": "technosphere", "database": "bg", "code": "elec",
                                                      "amount": 1, "sd2": 0.5}]))


def test_read_dataset_gives_back_what_was_entered(project):
    result = editing.write_dataset("toy", spec())
    read = sources.read_dataset(project, "toy", "my datasets", result["codes"][0])  # an allocated process: its parent
    assert read["editable"] and read["process"]["comment"] == "Toy farm"
    assert read["process"]["documentation"] == spec()["process"]["documentation"]
    assert [(p["name"], p["amount"], p["allocation"]) for p in read["products"]] == [("milk", 10, 80), ("meat", 2, 20)]
    exchanges = [(e["section"], e["node"]["code"], e["amount"], e["sd2"]) for e in read["exchanges"]]
    assert exchanges == [("technosphere", "elec", 5, 1.21), ("technosphere", "feed", 4, None), ("waste", "manure", 3, None),
                         ("emission", "co2", 3, None), ("resource", "water", 0.2, None)]


def test_read_a_background_process_to_copy_it(project):
    import bw2data as bd

    bd.Database("bg").write({**{n.key: {**n._data, "exchanges": [e._data for e in n.exchanges()]} for n in bd.Database("bg")},
        ("bg", "chlorine"): {"name": "chlor-alkali", "reference product": "chlorine", "location": "FR", "unit": "kilogram",
                             "type": "process", "exchanges": [
                                 {"input": ("bg", "chlorine"), "amount": 1, "type": "production"},
                                 {"input": ("bg", "feed"), "amount": -1.1, "type": "technosphere"},  # by-product
                                 {"input": ("bg", "manure"), "amount": -0.5, "type": "technosphere"}]}})  # waste
    read = sources.read_dataset(project, "toy", "bg", "chlorine")
    assert not read["editable"] and [p["name"] for p in read["products"]] == ["chlorine"]
    assert [(e["section"], e["node"]["code"], e["amount"]) for e in read["exchanges"]] == [("avoided", "feed", 1.1),
                                                                                           ("waste", "manure", 0.5)]


def test_edit_a_dataset_in_place(project):
    import bw2data as bd

    from lca_viewer.brightway import from_brightway

    first = editing.write_dataset("toy", spec())
    meat = next(c for c in first["codes"] if bd.get_node(database="my datasets", code=c)["reference product"] == "meat")
    burger = editing.write_dataset("toy", spec(new_database=False, process={"name": "burger"},
                                               products=[{"name": "burger", "amount": 1, "unit": "kilogram"}],
                                               exchanges=[{"section": "technosphere", "database": "my datasets",
                                                           "code": meat, "amount": 0.5}]))["codes"][0]
    read = sources.read_dataset(project, "toy", "my datasets", first["codes"][0])
    edited = spec(new_database=False, code=read["code"], process={**read["process"], "name": "dairy farm v2"},
                  products=[{**p, "allocation": 50} for p in read["products"]],
                  exchanges=[{"section": "emission", "database": "bio", "code": "co2", "amount": 10}])
    second = editing.write_dataset("toy", edited)
    assert set(second["codes"]) == set(first["codes"])  # same processes: their users stay linked
    model = from_brightway("toy", "my datasets", [GWP])
    acts = model.activities
    row = acts.index[acts["name"] == "burger"][0]
    assert np.isclose(model.score(row)[0], 0.5 * 0.5 * 10 / 2)  # half of the farm's 10 kg CO2, over 2 kg of meat
    assert bd.get_node(database="my datasets", code=read["code"])["lca_viewer"]["modified"]

    with pytest.raises(ValueError, match="cannot switch"):
        editing.write_dataset("toy", spec(new_database=False, code=burger, products=edited["products"]))
    with pytest.raises(ValueError, match="copy it instead"):
        editing.write_dataset("toy", spec(database="bg", new_database=False, code="elec",
                                          products=[{"name": "electricity", "amount": 1}]))


def test_allocation_rules_and_product_classes(project):
    from lca_viewer.brightway import from_brightway

    products = [{"name": "milk", "amount": 10, "unit": "kilogram", "property": 0.5},
                {"name": "meat", "amount": 2, "unit": "kilogram", "property": 5},
                {"name": "hide", "amount": 1, "unit": "kilogram", "class": "recyclable"},
                {"name": "slurry", "amount": 4, "unit": "kilogram", "class": "waste", "treatment": {"database": "bg", "code": "manure"}}]
    result = editing.write_dataset("toy", spec(allocation="value", products=products,
                                               exchanges=[{"section": "emission", "database": "bio", "code": "co2", "amount": 3}]))
    model = from_brightway("toy", "my datasets", [GWP])
    acts = model.activities
    shared = 3 + 4 * 0.2  # direct CO2, and the slurry's treatment; the hide is cut off
    milk = acts.index[(acts["database"] == "my datasets") & (acts["reference_product"] == "milk")][0]
    meat = acts.index[(acts["database"] == "my datasets") & (acts["reference_product"] == "meat")][0]
    assert np.isclose(model.score(milk)[0], shared / 3 / 10)  # revenue: milk 10 × 0.5 = 5, meat 2 × 5 = 10
    assert np.isclose(model.score(meat)[0], shared * 2 / 3 / 2)
    assert len(result["codes"]) == 2

    read = sources.read_dataset(project, "toy", "my datasets", result["codes"][0])
    assert (read["allocation"], read["system_model"]) == ("value", "attributional")
    assert [(p["name"], p["class"], p["amount"], p["property"]) for p in read["products"]] == [
        ("milk", "allocatable", 10, 0.5), ("meat", "allocatable", 2, 5), ("hide", "recyclable", 1, None), ("slurry", "waste", 4, None)]
    assert read["products"][3]["treatment"]["code"] == "manure"
    assert [e["section"] for e in read["exchanges"]] == ["emission"]  # the slurry is a product, not a waste input


def test_allocation_errors_and_consequential_datasets(project):
    two = [{"name": "milk", "amount": 10, "property": 0.5}, {"name": "meat", "amount": 2}]
    with pytest.raises(ValueError, match="value per unit"):
        editing.write_dataset("toy", spec(allocation="value", products=two))
    with pytest.raises(ValueError, match="add up to 0"):
        editing.write_dataset("toy", spec(allocation="value", products=[{**p, "property": 0} for p in two]))
    with pytest.raises(ValueError, match="Unknown allocation"):
        editing.write_dataset("toy", spec(allocation="economic"))
    with pytest.raises(ValueError, match="treatment"):
        editing.write_dataset("toy", spec(products=[{"name": "milk", "amount": 1}, {"name": "slurry", "amount": 1, "class": "waste"}]))
    with pytest.raises(ValueError, match="consequential"):
        editing.write_dataset("toy", spec(system_model="consequential"))
    # Consequential: the determining product, the co-product substituting its marginal supply
    result = editing.write_dataset("toy", spec(system_model="consequential", products=[{"name": "milk", "amount": 10}],
                                               exchanges=[{"section": "avoided", "database": "bg", "code": "feed", "amount": 2}]))
    read = sources.read_dataset(project, "toy", "my datasets", result["codes"][0])
    assert read["system_model"] == "consequential" and [e["section"] for e in read["exchanges"]] == ["avoided"]
    assert sources.guess_system_model(["ecoinvent-3.12-consequential", "biosphere3"]) == "consequential"
    assert sources.guess_system_model(["ecoinvent-3.12-cutoff", "ecoinvent-3.12-consequential"]) is None


def test_search_reads_the_project_files(project):
    found = sources.search_nodes(project, "toy", "electricity fr")
    assert [(n["name"], n["unit"]) for n in found] == [("electricity", "kilogram")]
    assert [n["name"] for n in sources.search_nodes(project, "toy", "carbon air", "emission")] == ["Carbon dioxide, fossil"]
    assert [n["name"] for n in sources.search_nodes(project, "toy", "water", "resource")] == ["Water, river"]
    assert sources.search_nodes(project, "toy", "water", "emission") == []


def test_create_and_delete_projects(project):
    import bw2data as bd

    editing.create_project("second")
    assert "second" in bd.projects
    with pytest.raises(SystemExit):
        editing.create_project("second")
    editing.delete_project("second")
    assert "second" not in bd.projects and [p["name"] for p in sources.brightway_projects(project)] == ["toy"]
    with pytest.raises(SystemExit, match="at least one project"):
        editing.delete_project("toy")


def test_through_the_api(project, tmp_path):
    """The page's requests: changes run as ``lca-viewer`` in a separate process on the folder."""
    import time

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    def wait(job):
        for _ in range(600):
            if job["status"] != "running":
                return job
            time.sleep(0.1)
            job = client.get(f"/api/jobs/{job['id']}").json()
        raise AssertionError("job still running")

    client = TestClient(create_library(tmp_path / "data"))
    folder = str(project)
    found = client.get("/api/brightway/search", params={"folder": folder, "project": "toy", "q": "feed"}).json()
    assert [n["code"] for n in found] == ["feed"]

    job = wait(client.post("/api/brightway/dataset", json={"folder": folder, "project": "toy", "spec": spec()}).json())
    assert job["status"] == "done", job
    assert job["result"]["database"] == "my datasets" and len(job["result"]["codes"]) == 2
    # bw2data adds a "default" project to a folder that has none, when imported
    projects = client.get("/api/brightway", params={"folder": folder}).json()["projects"]
    listed = next(p for p in projects if p["name"] == "toy")["databases"]
    assert ("my datasets", 2) in [(d["name"], d["processes"]) for d in listed]  # the two allocated processes
    read = client.get("/api/brightway/dataset", params={"folder": folder, "project": "toy", "database": "my datasets",
                                                        "code": job["result"]["codes"][0]}).json()
    assert read["editable"] and len(read["products"]) == 2
    assert client.get("/api/brightway/dataset", params={"folder": folder, "project": "toy", "database": "bg",
                                                        "code": "nothing"}).status_code == 404
    failed = wait(client.post("/api/brightway/dataset", json={"folder": folder, "project": "toy",
                                                             "spec": spec(products=[])}).json())
    assert failed["status"] == "failed" and "name and an amount" in failed["error"]

    assert wait(client.post("/api/brightway/projects", json={"folder": folder, "name": "second"}).json())["status"] == "done"
    assert client.post("/api/brightway/projects", json={"folder": folder, "name": "second"}).status_code == 400
    assert client.post("/api/brightway/projects/delete",
                       json={"folder": folder, "project": "second", "confirm": "sec"}).status_code == 400
    job = wait(client.post("/api/brightway/projects/delete",
                           json={"folder": folder, "project": "second", "confirm": "second"}).json())
    assert job["status"] == "done" and "second" not in [p["name"] for p in sources.brightway_projects(project)]

    uploaded = client.post("/api/upload", params={"name": "my export.CSV"}, content=b"{CSV}").json()
    assert uploaded["size"] == 5 and uploaded["path"].endswith("my-export.CSV")
    assert client.post("/api/brightway/import", json={"folder": folder, "project": "toy", "format": "simapro-csv",
                                                      "path": str(tmp_path / "nothing.csv"), "database": "x"}).status_code == 404
    adapters = client.get("/api/adapters", params={"link_to": ["ecoinvent-3.9.1-cutoff"]}).json()
    assert adapters["default"] == ["simapro-ecoinvent-3.9.1-cutoff"]
