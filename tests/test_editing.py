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


def test_import_without_the_default_biosphere(tmp_path, monkeypatch):
    """Ticked in the page, the default biosphere and methods of a version are installed first
    (``--defaults``); unticked, nothing is installed (``--no-setup``)."""
    import subprocess
    import time

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    seen = []

    class Done:
        stdout = iter(["imported\n"])

        def __init__(self, command, **kwargs):
            seen.append(command)

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Done)
    monkeypatch.setattr(sources, "brightway_projects", lambda folder: [{"name": "p", "databases": []}])
    client = TestClient(create_library(tmp_path / "data"))
    export = tmp_path / "export.csv"
    export.write_text("{CSV}")
    for i, defaults in enumerate(("3.10", None)):
        Done.stdout = iter(["imported\n"])
        job = client.post("/api/brightway/import", json={"folder": str(tmp_path / "bw"), "project": "p", "format": "simapro-csv",
                                                          "path": str(export), "database": f"x{i}",
                                                          "biosphere": "biosphere3", "defaults": defaults}).json()
        for _ in range(100):
            if job["status"] != "running":
                break
            time.sleep(0.05)
            job = client.get(f"/api/jobs/{job['id']}").json()
        assert job["status"] == "done", job
    assert seen[0][seen[0].index("--defaults") + 1] == "3.10" and "--no-setup" not in seen[0]
    assert "--no-setup" in seen[1] and "--defaults" not in seen[1]
    bad = client.post("/api/brightway/import", json={"folder": str(tmp_path / "bw"), "project": "p", "format": "simapro-csv",
                                                     "path": str(export), "database": "y", "defaults": "--unlinked"})
    assert bad.status_code == 400


def test_delete_database(project):
    import bw2data as bd

    with pytest.raises(SystemExit, match="used by bg"):
        editing.delete_database("toy", "bio")
    with pytest.raises(SystemExit, match="No database"):
        editing.delete_database("toy", "nothing")
    editing.delete_database("toy", "bg")
    editing.delete_database("toy", "bio")
    assert "bg" not in bd.databases and "bio" not in bd.databases
    assert [d["name"] for p in sources.brightway_projects(project) for d in p["databases"]] == []


def test_delete_database_through_the_api(project, tmp_path):
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
    body = {"folder": str(project), "project": "toy"}
    used = client.post("/api/brightway/databases/delete", json={**body, "database": "bio"})
    assert used.status_code == 400 and "bg" in used.json()["detail"]
    assert client.post("/api/brightway/databases/delete", json={**body, "database": "nothing"}).status_code == 404
    assert client.post("/api/brightway/databases/delete", json={**body, "project": "nope", "database": "bg"}).status_code == 404
    job = wait(client.post("/api/brightway/databases/delete", json={**body, "database": "bg"}).json())
    assert job["status"] == "done", job
    names = [d["name"] for p in sources.brightway_projects(project) if p["name"] == "toy" for d in p["databases"]]
    assert names == ["bio"]


def test_add_defaults_from_the_repository(tmp_path, monkeypatch):
    """The Brightway repository option: ``lca-viewer defaults <version> --project …`` in a separate process."""
    import subprocess
    import time

    from fastapi.testclient import TestClient

    from lca_viewer.server import create_library

    seen = []

    class Done:
        stdout = iter(["Downloading the biosphere\n"])

        def __init__(self, command, **kwargs):
            seen.append(command)

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Done)
    monkeypatch.setattr(sources, "brightway_projects", lambda folder: [{"name": "p", "databases": []}])
    client = TestClient(create_library(tmp_path / "data"))
    job = client.post("/api/brightway/defaults/install", json={"folder": str(tmp_path / "bw"), "project": "p", "version": "3.10"}).json()
    for _ in range(100):
        if job["status"] != "running":
            break
        time.sleep(0.05)
        job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done" and "3.10" in job["message"], job
    assert seen[0][seen[0].index("defaults") + 1] == "3.10" and seen[0][seen[0].index("--project") + 1] == "p"
    assert client.post("/api/brightway/defaults/install",
                       json={"folder": str(tmp_path / "bw"), "project": "p", "version": "--x"}).status_code == 400
    assert client.post("/api/brightway/defaults/install",
                       json={"folder": str(tmp_path / "bw"), "project": "nope", "version": "3.10"}).status_code == 404
