"""Databases loaded while the server runs, each under /db/<key>/ (Load database)."""

import pickle
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from lca_viewer.cli import _per_model
from lca_viewer.server import app_for, create_library, model_key
from lca_viewer.sources import _safe_filename, brightway_projects, register


@pytest.fixture
def two_models(tmp_path, linear_chain_model, loop_model):
    linear_chain_model.save(tmp_path / "bread")
    loop_model.save(tmp_path / "energy system")
    return [
        {"path": str(tmp_path / "bread"), "title": "Bread"},
        {"path": str(tmp_path / "energy system"), "title": "Energy", "default_root": "coal|world"},
    ]


def wait(client, job):
    for _ in range(200):
        if job["status"] != "running":
            return job
        time.sleep(0.02)
        job = client.get(f"/api/jobs/{job['id']}").json()
    raise AssertionError("job still running")


def test_each_model_has_its_own_api(two_models, tmp_path):
    client = TestClient(app_for(two_models, tmp_path))
    assert client.get("/api/databases").json() == [
        {"key": "bread", "title": "Bread", "activities": 3},
        {"key": "energy-system", "title": "Energy", "activities": 2},
    ]
    root = client.get("/", follow_redirects=False)
    assert root.status_code in (302, 307) and root.headers["location"] == "/db/bread/"

    bread = client.get("/db/bread/api/info").json()
    energy = client.get("/db/energy-system/api/info").json()
    assert (bread["title"], bread["activities"], bread["default_root"]) == ("Bread", 3, None)
    assert (energy["title"], energy["activities"], energy["default_root"]["name"]) == ("Energy", 2, "coal")
    # The page and the shared static files are served for every model
    assert client.get("/db/energy-system/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    found = client.post("/db/bread/api/search", json={"text": "flour"}).json()
    assert [r["name"] for r in found["results"]] == ["flour"]


def test_starts_empty_then_loads_and_unloads(two_models, tmp_path):
    data = tmp_path / "data"
    register(data, two_models[1]["path"], "Energy", "coal|world")  # outside data/: kept as a relative path
    client = TestClient(create_library(data))
    assert client.get("/api/databases").json() == []
    assert client.get("/", follow_redirects=False).status_code == 200  # the page asks which database to load
    assert client.get("/db/energy-system/").status_code == 200  # an address of a database not loaded: same page
    assert client.get("/db/energy-system/api/info").status_code == 404

    sources = client.get("/api/sources").json()
    assert [(m["title"], m["key"], m["built"]) for m in sources["models"]] == [("Energy", None, True)]
    job = wait(client, client.post("/api/load", json={"path": sources["models"][0]["path"]}).json())
    assert job["status"] == "done" and job["key"] == "energy-system"
    assert client.get("/db/energy-system/api/info").json()["default_root"]["name"] == "coal"
    assert client.get("/api/sources").json()["models"][0]["key"] == "energy-system"
    # Loading it again gives the same database
    assert wait(client, client.post("/api/load", json={"path": sources["models"][0]["path"]}).json())["key"] == "energy-system"

    # Only models of the catalog can be loaded
    failed = wait(client, client.post("/api/load", json={"path": two_models[0]["path"]}).json())
    assert failed["status"] == "failed" and "models.json" in failed["error"]

    assert client.post("/api/unload", json={"key": "energy-system"}).json() == []
    assert client.get("/db/energy-system/api/info").status_code == 404
    assert client.post("/api/unload", json={"key": "energy-system"}).status_code == 404


def make_brightway(folder, projects):
    """A Brightway data folder as bw2data 4 writes it: ``{project: (is 2.5, {database: {type: n}})}``."""
    import json

    folder.mkdir(parents=True)
    with sqlite3.connect(folder / "projects.db") as con:
        con.execute('CREATE TABLE projectdataset ("id" INTEGER PRIMARY KEY, "is_sourced" INTEGER, "revision" INTEGER, '
                    '"data" BLOB, "name" TEXT, "full_hash" INTEGER)')
        for i, (name, (twofive, databases)) in enumerate(projects.items()):
            con.execute("INSERT INTO projectdataset VALUES (?, 0, NULL, ?, ?, ?)",
                        (i, pickle.dumps({"25": True} if twofive else {}), name, i % 2))
            project = folder / _safe_filename(name, bool(i % 2))
            (project / "lci").mkdir(parents=True)
            (project / "databases.json").write_text(json.dumps(
                {db: {"depends": [], "modified": "2026-01-01T00:00:00", "backend": "sqlite"} for db in databases}))
            (project / "methods.json").write_text(json.dumps({"('IPCC', 'GWP100')": {}}))
            with sqlite3.connect(project / "lci" / "databases.db") as lci:
                lci.execute('CREATE TABLE activitydataset ("id" INTEGER PRIMARY KEY, "data" BLOB, "code" TEXT, '
                            '"database" TEXT, "location" TEXT, "name" TEXT, "product" TEXT, "type" TEXT)')
                for db, types in databases.items():
                    for kind, n in types.items():
                        lci.executemany("INSERT INTO activitydataset (data, code, database, type) VALUES (x'', ?, ?, ?)",
                                        [(f"{kind}{k}", db, kind) for k in range(n)])


def test_brightway_projects_are_listed_without_brightway(tmp_path):
    make_brightway(tmp_path / "bw", {
        "my project": (True, {"biosphere3": {"emission": 4, "natural resource": 1},
                              "ei": {"processwithreferenceproduct": 3}, "agb": {"process": 2, "product": 2}}),
        "old": (False, {"db": {"process": 1}}),
    })
    projects = {p["name"]: p for p in brightway_projects(tmp_path / "bw")}
    assert projects["my project"]["twofive"] and not projects["old"]["twofive"]
    assert projects["my project"]["methods"] == 1
    assert [(d["name"], d["processes"], d["flows"]) for d in projects["my project"]["databases"]] == [
        ("agb", 2, 0), ("biosphere3", 0, 5), ("ei", 3, 0)]
    with pytest.raises(FileNotFoundError):
        brightway_projects(tmp_path / "nothing")

    client = TestClient(create_library(tmp_path / "data"))
    listed = client.get("/api/brightway", params={"folder": str(tmp_path / "bw")}).json()
    assert [d["model"] for d in listed["projects"][0]["databases"]] == [None, None, None]
    assert client.get("/api/brightway", params={"folder": str(tmp_path)}).json()["projects"] == []  # no project yet
    assert client.get("/api/brightway", params={"folder": str(tmp_path / "nothing")}).status_code == 404
    # What cannot be loaded is said before building
    for project, database, message in [("old", "db", "Brightway 2"), ("my project", "biosphere3", "no processes"),
                                       ("my project", "nope", "No database")]:
        job = wait(client, client.post("/api/load", json={"brightway": str(tmp_path / "bw"), "project": project,
                                                           "database": database}).json())
        assert job["status"] == "failed" and message in job["error"]


def test_model_keys():
    assert model_key("data/cache/agb32") == "agb32"
    assert model_key("C:/x/ei 3.9.1 (cut-off)/") == "ei-3.9.1-cut-off"


def test_per_model_options():
    assert _per_model(None, 2, "--default-root") == [None, None]
    assert _per_model(["a|GLO"], 2, "--default-root") == ["a|GLO", "a|GLO"]
    assert _per_model(["a|GLO", "b|FR"], 2, "--default-root") == ["a|GLO", "b|FR"]
    with pytest.raises(SystemExit):
        _per_model(["a", "b", "c"], 2, "--default-root")
